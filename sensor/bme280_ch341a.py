#!/usr/bin/env python3
"""BME280 reader via CH341A/CH341T USB-I2C adapter on Windows 11 (NYX).

Uses CH341DLLA64.DLL (WCH 64-bit) directly via ctypes.
Reads temperature, humidity and pressure; publishes to MQTT.
"""
import configparser
import ctypes
import logging
import os
import struct
import time

import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "sensor", "config.ini")
CONFIG_EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "config.example.ini")

# Degree symbol: ASCII 167 (ordinal of 'º')
DEG = chr(167)

# DLL candidates: NYX has CH341DLLA64.DLL in System32
_DLL_CANDIDATES = [
    "CH341DLLA64.DLL",   # NYX / Windows 11 64-bit (confirmed present)
    "CH341DLL.dll",
    "CH341DLL64.dll",
    "ch341dll.dll",
]


def _load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else CONFIG_EXAMPLE_PATH
    cfg.read(path, encoding="utf-8")
    return cfg


class CH341I2C:
    """Thin ctypes wrapper around CH341 DLL for I2C master communication."""

    def __init__(self, device_index: int = 0, speed: int = 1):
        """
        speed: 0 = 20 kHz, 1 = 100 kHz (standard), 2 = 400 kHz (fast)
        """
        self._dll = None
        self._dev = device_index
        for name in _DLL_CANDIDATES:
            try:
                self._dll = ctypes.WinDLL(name)
                log.info("CH341 DLL loaded: %s", name)
                break
            except OSError:
                continue
        if self._dll is None:
            raise RuntimeError(
                "CH341 DLL not found. Candidates tried: %s" % ", ".join(_DLL_CANDIDATES)
            )
        ret = self._dll.CH341OpenDevice(device_index)
        if not ret:
            raise RuntimeError(
                "Cannot open CH341 device index %d. Check USB cable and driver." % device_index
            )
        self._dll.CH341SetStream(device_index, speed)
        log.info("CH341 device %d open, I2C speed index %d", device_index, speed)

    def write_read(self, addr: int, register: int, read_len: int) -> bytes:
        """Write one register byte then read read_len bytes from I2C addr."""
        # CH341StreamI2C expects the 7-bit address in the write buffer header.
        # Write buffer: [addr<<1 | 0x00, register]
        # Read buffer:  read_len bytes; CH341 performs repeated-start internally.
        write_buf = (ctypes.c_ubyte * 2)(addr << 1, register)
        read_buf = (ctypes.c_ubyte * max(read_len, 1))()
        ret = self._dll.CH341StreamI2C(
            self._dev,
            ctypes.c_ulong(2),
            write_buf,
            ctypes.c_ulong(read_len),
            read_buf,
        )
        if not ret:
            raise IOError("CH341 I2C error: addr=0x%02X reg=0x%02X" % (addr, register))
        return bytes(read_buf[:read_len])

    def write_byte(self, addr: int, register: int, value: int) -> None:
        """Write a single byte to a register (no read phase)."""
        write_buf = (ctypes.c_ubyte * 3)(addr << 1, register, value)
        ret = self._dll.CH341StreamI2C(
            self._dev,
            ctypes.c_ulong(3),
            write_buf,
            ctypes.c_ulong(0),
            None,
        )
        if not ret:
            raise IOError(
                "CH341 I2C write error: addr=0x%02X reg=0x%02X val=0x%02X" % (addr, register, value)
            )

    def close(self) -> None:
        try:
            self._dll.CH341CloseDevice(self._dev)
        except Exception:
            pass


class BME280:
    """BME280 driver with full Bosch compensation (datasheet section 4.2.3)."""

    CHIP_ID_REG = 0xD0
    RESET_REG   = 0xE0
    CTRL_HUM    = 0xF2
    CTRL_MEAS   = 0xF4
    DATA_REG    = 0xF7

    def __init__(self, i2c: CH341I2C, address: int = 0x76):
        self._i2c = i2c
        self._addr = address
        chip_id = self._read(self.CHIP_ID_REG, 1)[0]
        if chip_id not in (0x60, 0x58):
            raise RuntimeError(
                "Unexpected chip ID 0x%02X at 0x%02X (expected 0x60=BME280 or 0x58=BMP280)"
                % (chip_id, address)
            )
        log.info("BME280 chip ID 0x%02X at I2C 0x%02X", chip_id, address)
        self._has_humidity = (chip_id == 0x60)
        self._load_calibration()

    # --- low-level helpers ---------------------------------------------------

    def _read(self, reg: int, length: int) -> bytes:
        return self._i2c.write_read(self._addr, reg, length)

    def _write(self, reg: int, value: int) -> None:
        self._i2c.write_byte(self._addr, reg, value)

    # --- calibration ---------------------------------------------------------

    def _load_calibration(self) -> None:
        raw = self._read(0x88, 24)
        (self.dig_T1, self.dig_T2, self.dig_T3,
         self.dig_P1, self.dig_P2, self.dig_P3,
         self.dig_P4, self.dig_P5, self.dig_P6,
         self.dig_P7, self.dig_P8, self.dig_P9) = struct.unpack("<HhhHhhhhhhhh", raw)

        self.dig_H1 = self._read(0xA1, 1)[0]
        e = self._read(0xE1, 7)
        self.dig_H2 = struct.unpack("<h", bytes([e[0], e[1]]))[0]
        self.dig_H3 = e[2]
        self.dig_H4 = (e[3] << 4) | (e[4] & 0x0F)
        self.dig_H5 = (e[4] >> 4) | (e[5] << 4)
        self.dig_H6 = struct.unpack("b", bytes([e[6]]))[0]
        log.info("Calibration loaded OK")

    # --- measurement ---------------------------------------------------------

    def _trigger_forced(self) -> None:
        """Trigger one forced-mode measurement and wait for completion."""
        # osrs_h = 1 (x1 oversampling)
        self._write(self.CTRL_HUM, 0x01)
        # osrs_t = 1, osrs_p = 1, mode = forced (01)
        self._write(self.CTRL_MEAS, 0x25)
        time.sleep(0.1)

    def read(self) -> dict:
        """Return compensated temperature (°C), humidity (%RH) and pressure (hPa)."""
        self._trigger_forced()
        raw = self._read(self.DATA_REG, 8)
        praw = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        traw = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        hraw = (raw[6] << 8) | raw[7]

        # --- Temperature (Bosch datasheet 4.2.3) ---
        v1 = (traw / 16384.0 - self.dig_T1 / 1024.0) * self.dig_T2
        v2 = ((traw / 131072.0 - self.dig_T1 / 8192.0) ** 2) * self.dig_T3
        t_fine = v1 + v2
        temperature = t_fine / 5120.0

        # --- Pressure ---
        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * self.dig_P6 / 32768.0 + v1 * self.dig_P5 * 2.0
        v2 = v2 / 4.0 + self.dig_P4 * 65536.0
        v1 = (self.dig_P3 * v1 * v1 / 524288.0 + self.dig_P2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * self.dig_P1
        if v1 == 0.0:
            pressure = 0.0
        else:
            p = 1048576.0 - praw
            p = ((p - v2 / 4096.0) * 6250.0) / v1
            v1 = self.dig_P9 * p * p / 2147483648.0
            v2 = p * self.dig_P8 / 32768.0
            pressure = (p + (v1 + v2 + self.dig_P7) / 16.0) / 100.0

        # --- Humidity ---
        if not self._has_humidity:
            humidity = 0.0
        else:
            h = t_fine - 76800.0
            if h == 0.0:
                humidity = 0.0
            else:
                h = (hraw - (self.dig_H4 * 64.0 + self.dig_H5 / 16384.0 * h)) * (
                    self.dig_H2 / 65536.0 * (
                        1.0 + self.dig_H6 / 67108864.0 * h * (
                            1.0 + self.dig_H3 / 67108864.0 * h
                        )
                    )
                )
                h *= 1.0 - self.dig_H1 * h / 524288.0
                humidity = max(0.0, min(100.0, h))

        return {
            "temperature": round(temperature, 2),
            "humidity":    round(humidity, 2),
            "pressure":    round(pressure, 2),
        }


# ---------------------------------------------------------------------------
# MQTT publishing loop
# ---------------------------------------------------------------------------

def run() -> None:
    cfg = _load_config()

    address  = int(cfg.get("bme280", "i2c_address", fallback="0x76"), 16)
    interval = cfg.getint("bme280", "interval_seconds", fallback=30)

    broker   = cfg.get("mqtt", "host",         fallback="localhost")
    port     = cfg.getint("mqtt", "port",       fallback=1883)
    username = cfg.get("mqtt", "username",      fallback="")
    password = cfg.get("mqtt", "password",      fallback="")
    prefix   = cfg.get("mqtt", "topic_prefix",  fallback="observatory/bme280")
    qos      = cfg.getint("mqtt", "qos",        fallback=1)
    retain   = cfg.getboolean("mqtt", "retain", fallback=True)

    i2c    = CH341I2C()
    sensor = BME280(i2c, address=address)

    client = mqtt.Client(client_id="bme280-observatory")
    if username:
        client.username_pw_set(username, password)
    client.connect(broker, port, keepalive=60)
    client.loop_start()

    log.info("Publishing every %ds -> %s (broker %s:%d)", interval, prefix, broker, port)
    try:
        while True:
            data = sensor.read()
            for key, val in data.items():
                client.publish(f"{prefix}/{key}", str(val), qos=qos, retain=retain)
            log.info(
                "T=%.2f%sC  H=%.2f%%  P=%.2fhPa",
                data["temperature"], DEG, data["humidity"], data["pressure"],
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        client.loop_stop()
        i2c.close()


if __name__ == "__main__":
    run()
