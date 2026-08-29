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
"""
import configparser
import json
import logging
import math
import os
import struct
import sys
import time

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


def pressure_altitude_isa(pressure_hpa: float) -> float:
    """Return ISA pressure altitude in metres for a given absolute pressure.

    Formula: h = 44330.77 * [1 - (P / 1013.25)^0.1902632]
    Reference: ICAO Standard Atmosphere / NCAR Aircraft Processing Algorithms.
    NOTE: This is NOT the geographic elevation of the observatory;
    it varies with weather.  Use altitude_m in config.ini for sea-level
    pressure reduction.
    """
    return round(44330.77 * (1.0 - math.pow(pressure_hpa / _ISA_P0, 0.1902632)), 1)


def _write_latest(data: dict, alt: float) -> None:
    """Atomically write latest sensor reading to logs/latest_reading.json."""
    os.makedirs(LOG_DIR, exist_ok=True)
    payload = {
        "timestamp":           time.strftime("%Y-%m-%d %H:%M:%S"),
        "temperature_c":       data["temperature"],
        "humidity_pct":        data["humidity"],
        "pressure_hpa":        data["pressure"],
        "pressure_altitude_m": alt,
    }
    tmp = LATEST_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, LATEST_JSON)


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
        except ImportError:
            log.error("i2cpy is not installed. Run: pip install i2cpy")
            sys.exit(1)
        try:
            self._i2c = i2cpy.I2C(driver="ch341")
        except Exception as exc:
            log.error("Could not open CH341T_V3 device via i2cpy: %s", exc)
            log.error("Check USB cable and that the CH341 driver is installed.")
            sys.exit(1)
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
        if chip_id not in (0x60, 0x58):
            raise RuntimeError(
                "Unexpected chip ID 0x%02X at 0x%02X" % (chip_id, address)
            )
        self._has_hum = (chip_id == 0x60)
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
        if not self._has_hum:
            humi = 0.0
        else:
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
                h    *= 1.0 - self.H1 * h / 524288.0
                humi  = max(0.0, min(100.0, h))

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

    broker   = cfg.get(    "mqtt", "host",         fallback="localhost")
    port     = cfg.getint( "mqtt", "port",          fallback=1883)
    username = cfg.get(    "mqtt", "username",      fallback="")
    password = cfg.get(    "mqtt", "password",      fallback="")
    prefix   = cfg.get(    "mqtt", "topic_prefix",  fallback="observatory/bme280")
    qos      = cfg.getint( "mqtt", "qos",           fallback=1)
    retain   = cfg.getboolean("mqtt", "retain",     fallback=True)

    backend = I2CpyBackend()
    sensor  = BME280(backend, address=address)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bme280-observatory")
    if username:
        client.username_pw_set(username, password)
    client.connect(broker, port, keepalive=60)
    client.loop_start()

    log.info("Publishing every %ds -> %s  (broker %s:%d)", interval, prefix, broker, port)
    try:
        while True:
            data = sensor.read()
            alt  = pressure_altitude_isa(data["pressure"])
            for key, val in data.items():
                client.publish("%s/%s" % (prefix, key), str(val), qos=qos, retain=retain)
            client.publish("%s/pressure_altitude" % prefix, str(alt), qos=qos, retain=retain)
            _write_latest(data, alt)
            log.info(
                "T=%.2f%sC  H=%.3f%%  P=%.3fhPa  Alt=%.1fm",
                data["temperature"], DEG, data["humidity"], data["pressure"], alt,
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        client.loop_stop()
        backend.close()


if __name__ == "__main__":
    run()
