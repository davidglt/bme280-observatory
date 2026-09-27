#!/usr/bin/env python3
# -*- coding: ascii -*-
# SPDX-FileCopyrightText: 2026 David Gonzalez Lopez-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

"""BME280 reader via CH341T_V3 USB-I2C adapter on Windows 11 (NYX).

Uses i2cpy as the sole backend (pip install i2cpy).

Publishes temperature, humidity, pressure and ISA pressure altitude
to Mosquitto / Home Assistant over MQTT.
Also writes logs/latest_reading.json for local consumers (e.g. SharpCap).
Configuration via sensor/config.ini (see config.example.ini).

MQTT topic prefix (config.ini): homeassistant/nyx
  homeassistant/nyx/temperature
  homeassistant/nyx/humidity
  homeassistant/nyx/pressure
  homeassistant/nyx/pressure_altitude
  homeassistant/nyx/reading (atomic JSON sample)
"""
import configparser
import json
import logging
import math
import os
import signal
import struct
import threading
import time
import tempfile

import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Degree symbol: ASCII 176 (U+00B0)
DEG = chr(176)

_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH         = os.path.join(_ROOT, "config.ini")
CONFIG_EXAMPLE_PATH = os.path.join(_ROOT, "config.example.ini")
LOG_DIR             = os.path.join(_ROOT, "..", "logs")
LATEST_JSON         = os.path.join(LOG_DIR, "latest_reading.json")

# ISA standard sea-level pressure (hPa)
_ISA_P0 = 1013.25
TEMP_MIN_C = -40.0
TEMP_MAX_C = 85.0
HUMIDITY_MIN_PCT = 0.0
HUMIDITY_MAX_PCT = 100.0
PRESSURE_MIN_HPA = 300.0
PRESSURE_MAX_HPA = 1100.0
INITIAL_RETRY_DELAY_S = 2
MAX_RETRY_DELAY_S = 60


class InvalidReadingError(ValueError):
    """Raised when a sensor sample is outside physical plausibility limits."""


def pressure_altitude_isa(pressure_hpa: float) -> float:
    """Return ISA pressure altitude in metres for a given absolute pressure.

    Formula: h = 44330.77 * [1 - (P / 1013.25)^0.1902632]
    Reference: ICAO Standard Atmosphere / NCAR Aircraft Processing Algorithms.
    NOTE: This is NOT the geographic elevation of the observatory;
    it varies with weather.  Use altitude_m in config.ini for sea-level
    pressure reduction.
    """
    return round(44330.77 * (1.0 - math.pow(pressure_hpa / _ISA_P0, 0.1902632)), 1)


def validate_reading(data: dict) -> None:
    """Reject non-finite and physically implausible BME280 samples."""
    limits = {
        "temperature": (TEMP_MIN_C, TEMP_MAX_C),
        "humidity": (HUMIDITY_MIN_PCT, HUMIDITY_MAX_PCT),
        "pressure": (PRESSURE_MIN_HPA, PRESSURE_MAX_HPA),
    }
    for key, (minimum, maximum) in limits.items():
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidReadingError(f"{key} is not a numeric reading.")
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise InvalidReadingError(
                f"{key} reading {value!r} is outside the valid range "
                f"{minimum}..{maximum}."
            )


def build_reading_payload(
    data: dict,
    pressure_altitude_m: float,
    timestamp_epoch: float,
) -> str:
    """Serialize one coherent sensor sample for MQTT consumers."""
    payload = {
        "timestamp_epoch": timestamp_epoch,
        "temperature_c": data["temperature"],
        "humidity_pct": data["humidity"],
        "pressure_hpa": data["pressure"],
        "pressure_altitude_m": pressure_altitude_m,
    }
    return json.dumps(payload, allow_nan=False, separators=(",", ":"))


def _write_latest(
    data: dict,
    alt: float,
    timestamp_epoch: float | None = None,
) -> None:
    """Atomically write latest sensor reading to logs/latest_reading.json."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp_epoch = time.time() if timestamp_epoch is None else timestamp_epoch
    payload = {
        "timestamp":           time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(timestamp_epoch),
        ),
        "timestamp_epoch":     timestamp_epoch,
        "temperature_c":       data["temperature"],
        "humidity_pct":        data["humidity"],
        "pressure_hpa":        data["pressure"],
        "pressure_altitude_m": alt,
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=LOG_DIR,
            prefix=".latest_reading.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temporary_path = fh.name
            json.dump(payload, fh, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary_path, LATEST_JSON)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else CONFIG_EXAMPLE_PATH
    cfg.read(path, encoding="utf-8")
    log.info("Config loaded from: %s", path)
    return cfg


# ---------------------------------------------------------------------------
# Backend: i2cpy (sole backend)
# ---------------------------------------------------------------------------

class I2CpyBackend:
    """High-level wrapper using i2cpy (pip install i2cpy)."""

    def __init__(self):
        try:
            import i2cpy
        except ImportError as exc:
            raise ImportError(
                "i2cpy is not installed. Run: pip install i2cpy"
            ) from exc
        try:
            self._i2c = i2cpy.I2C(driver="ch341")
        except Exception as exc:
            raise RuntimeError(
                "Could not open CH341T_V3 device via i2cpy. "
                "Check the USB cable and CH341 driver."
            ) from exc
        log.info("i2cpy backend initialised (CH341T_V3 driver)")

    def read_reg(self, addr: int, reg: int, length: int) -> bytes:
        return bytes(self._i2c.readfrom_mem(addr, reg, length))

    def write_reg(self, addr: int, reg: int, data: bytes) -> None:
        self._i2c.writeto_mem(addr, reg, data)

    def close(self) -> None:
        fn = getattr(self._i2c, "close", None) or getattr(self._i2c, "deinit", None)
        if fn:
            try:
                fn()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# BME280 driver
# ---------------------------------------------------------------------------

class BME280:
    """BME280 with full Bosch compensation (datasheet section 4.2.3)."""

    REG_ID        = 0xD0
    REG_RESET     = 0xE0
    REG_CTRL_HUM  = 0xF2
    REG_CTRL_MEAS = 0xF4
    REG_DATA      = 0xF7

    def __init__(self, backend, address: int = 0x76):
        self._bus  = backend
        self._addr = address
        chip_id    = self._bus.read_reg(address, self.REG_ID, 1)[0]
        if chip_id != 0x60:
            raise RuntimeError(
                "Expected a BME280 chip ID 0x60, got 0x%02X at 0x%02X. "
                "BMP280 sensors do not provide humidity."
                % (chip_id, address)
            )
        log.info("BME280 chip ID 0x%02X at I2C 0x%02X", chip_id, address)
        self._load_calibration()

    def _load_calibration(self) -> None:
        raw = self._bus.read_reg(self._addr, 0x88, 24)
        (
            self.T1, self.T2, self.T3,
            self.P1, self.P2, self.P3,
            self.P4, self.P5, self.P6,
            self.P7, self.P8, self.P9,
        ) = struct.unpack("<HhhHhhhhhhhh", raw)

        self.H1 = self._bus.read_reg(self._addr, 0xA1, 1)[0]
        e = self._bus.read_reg(self._addr, 0xE1, 7)
        self.H2 = struct.unpack("<h", bytes([e[0], e[1]]))[0]
        self.H3 = e[2]
        self.H4 = (e[3] << 4) | (e[4] & 0x0F)
        self.H5 = (e[4] >> 4) | (e[5] << 4)
        self.H6 = struct.unpack("b", bytes([e[6]]))[0]
        log.info("Calibration loaded OK")

    def _forced(self) -> None:
        self._bus.write_reg(self._addr, self.REG_CTRL_HUM,  b"\x01")
        self._bus.write_reg(self._addr, self.REG_CTRL_MEAS, b"\x25")
        time.sleep(0.1)

    def read(self) -> dict:
        """Return dict with temperature (2dp), humidity (3dp), pressure (3dp)."""
        self._forced()
        raw  = self._bus.read_reg(self._addr, self.REG_DATA, 8)
        praw = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        traw = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        hraw = (raw[6] << 8)  |  raw[7]

        # Temperature
        v1     = (traw / 16384.0  - self.T1 / 1024.0)  * self.T2
        v2     = ((traw / 131072.0 - self.T1 / 8192.0) ** 2) * self.T3
        t_fine = v1 + v2
        temp   = t_fine / 5120.0

        # Pressure
        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * self.P6 / 32768.0 + v1 * self.P5 * 2.0
        v2 = v2 / 4.0 + self.P4 * 65536.0
        v1 = (self.P3 * v1 * v1 / 524288.0 + self.P2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * self.P1
        if v1 == 0.0:
            pres = 0.0
        else:
            p  = 1048576.0 - praw
            p  = ((p - v2 / 4096.0) * 6250.0) / v1
            v1 = self.P9 * p * p / 2147483648.0
            v2 = p * self.P8 / 32768.0
            pres = (p + (v1 + v2 + self.P7) / 16.0) / 100.0

        # Humidity
        h = t_fine - 76800.0
        if h == 0.0:
            humi = 0.0
        else:
            h = (hraw - (self.H4 * 64.0 + self.H5 / 16384.0 * h)) * (
                self.H2 / 65536.0 * (
                    1.0 + self.H6 / 67108864.0 * h * (
                        1.0 + self.H3 / 67108864.0 * h
                    )
                )
            )
            h *= 1.0 - self.H1 * h / 524288.0
            humi = max(0.0, min(100.0, h))

        return {
            "temperature": round(temp, 2),
            "humidity":    round(humi, 3),
            "pressure":    round(pres, 3),
        }


# ---------------------------------------------------------------------------
# MQTT publishing loop
# ---------------------------------------------------------------------------

def run() -> None:
    cfg = _load_config()

    address  = int(cfg.get("bme280", "i2c_address",     fallback="0x76"), 16)
    interval = cfg.getint("bme280", "interval_seconds",  fallback=30)
    if interval <= 0:
        raise ValueError("bme280.interval_seconds must be greater than zero.")

    broker   = cfg.get(    "mqtt", "host",         fallback="localhost")
    port     = cfg.getint( "mqtt", "port",          fallback=1883)
    username = cfg.get(    "mqtt", "username",      fallback="")
    password = cfg.get(    "mqtt", "password",      fallback="")
    prefix   = cfg.get(    "mqtt", "topic_prefix",  fallback="homeassistant/nyx")
    qos      = cfg.getint( "mqtt", "qos",           fallback=1)
    retain   = cfg.getboolean("mqtt", "retain",     fallback=True)

    stop_event = threading.Event()
    previous_handlers = {}

    def request_shutdown(signum, frame):
        log.info("Shutdown requested (signal %s).", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, request_shutdown)

    backend = None
    sensor = None
    client = None
    retry_delay = INITIAL_RETRY_DELAY_S
    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="bme280-nyx",
        )
        if username:
            client.username_pw_set(username, password)

        client.on_connect = lambda _client, _userdata, _flags, reason, _props: (
            log.info("MQTT connected to %s:%d.", broker, port)
            if reason == 0
            else log.error("MQTT connection refused: %s", reason)
        )
        client.on_disconnect = lambda _client, _userdata, _flags, reason, _props: (
            log.warning("MQTT disconnected: %s", reason)
        )
        client.connect_async(broker, port, keepalive=60)
        client.loop_start()
        log.info(
            "Publishing every %ds -> %s (broker %s:%d)",
            interval,
            prefix,
            broker,
            port,
        )

        while not stop_event.is_set():
            if sensor is None:
                try:
                    backend = I2CpyBackend()
                    sensor = BME280(backend, address=address)
                except Exception:
                    log.exception(
                        "Could not initialize BME280; retrying in %d seconds.",
                        retry_delay,
                    )
                    if backend is not None:
                        backend.close()
                    backend = None
                    stop_event.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY_S)
                    continue

            try:
                data = sensor.read()
                retry_delay = INITIAL_RETRY_DELAY_S
                validate_reading(data)
            except InvalidReadingError as exc:
                log.warning("Discarding implausible BME280 sample: %s", exc)
                stop_event.wait(interval)
                continue
            except Exception:
                log.exception(
                    "BME280 read failed; reconnecting in %d seconds.",
                    retry_delay,
                )
                if backend is not None:
                    backend.close()
                backend = None
                sensor = None
                stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY_S)
                continue

            timestamp_epoch = time.time()
            alt = pressure_altitude_isa(data["pressure"])
            _write_latest(data, alt, timestamp_epoch)

            reading_payload = build_reading_payload(
                data,
                alt,
                timestamp_epoch,
            )
            failed_topics = []
            for key, val in data.items():
                topics = [(key, str(val))]
                if key == "pressure":
                    topics.append(("pressure_altitude", str(alt)))
                for topic, value in topics:
                    try:
                        info = client.publish(
                            "%s/%s" % (prefix, topic),
                            value,
                            qos=qos,
                            retain=retain,
                        )
                    except Exception:
                        log.exception("Could not publish MQTT topic %s/%s", prefix, topic)
                        failed_topics.append(topic)
                        continue
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        failed_topics.append(topic)

            try:
                info = client.publish(
                    "%s/reading" % prefix,
                    reading_payload,
                    qos=qos,
                    retain=retain,
                )
            except Exception:
                log.exception("Could not publish coherent MQTT reading.")
                failed_topics.append("reading")
            else:
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    failed_topics.append("reading")
            if failed_topics:
                log.error("Could not queue MQTT topics: %s", ", ".join(failed_topics))

            log.info(
                "T=%.2f%sC  H=%.3f%%  P=%.3fhPa  Alt=%.1fm",
                data["temperature"], DEG, data["humidity"], data["pressure"], alt,
            )
            stop_event.wait(interval)
    finally:
        stop_event.set()
        try:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    log.exception("Could not disconnect MQTT client cleanly.")
                finally:
                    try:
                        client.loop_stop()
                    except Exception:
                        log.exception("Could not stop MQTT network loop cleanly.")
        finally:
            if backend is not None:
                backend.close()
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            log.info("BME280 sensor service stopped.")


if __name__ == "__main__":
    run()
