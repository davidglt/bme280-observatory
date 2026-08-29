#!/usr/bin/env python3
# -*- coding: ascii -*-
"""BME280 reader via CH341A/CH341T USB-I2C adapter on Windows 11 (NYX).

Backend priority (same as probe_bme280_ch341a.py):
  1. i2cpy  -- high-level CH341 wrapper (pip install i2cpy). Preferred.
  2. ctypes -- direct calls to CH341DLLA64.DLL. Fallback.

Publishes temperature, humidity and pressure to Mosquitto / Home Assistant
over MQTT.  Configuration via sensor/config.ini (see config.example.ini).
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

# Degree symbol: ASCII 167
DEG = chr(167)

_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH         = os.path.join(_ROOT, "config.ini")
CONFIG_EXAMPLE_PATH = os.path.join(_ROOT, "config.example.ini")

_DLL_CANDIDATES = [
    "CH341DLLA64.DLL",                                          # NYX / Windows 11 64-bit
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                 "System32", "CH341DLLA64.DLL"),
    "CH341DLL64.dll",
    "CH341DLL.dll",
]


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
# Backend 1: i2cpy (preferred)
# ---------------------------------------------------------------------------

class I2CpyBackend:
    """High-level wrapper using i2cpy (pip install i2cpy)."""

    def __init__(self):
        import i2cpy
        self._i2c = i2cpy.I2C(driver="ch341")
        log.info("i2cpy backend initialised (CH341 driver)")

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
# Backend 2: ctypes / CH341DLLA64.DLL (fallback)
# ---------------------------------------------------------------------------

class CtypesBackend:
    """Direct DLL calls to CH341DLLA64.DLL."""

    def __init__(self):
        self._dll = None
        for name in _DLL_CANDIDATES:
            try:
                self._dll = ctypes.WinDLL(name)
                log.info("CH341 DLL loaded: %s", name)
                break
            except OSError:
                continue
        if self._dll is None:
            raise RuntimeError("CH341 DLL not found. Tried: %s" % ", ".join(_DLL_CANDIDATES))
        self._bind()
        if not self._dll.CH341OpenDevice(0):
            raise RuntimeError("Cannot open CH341 device 0. Check USB cable and driver.")
        self._dll.CH341SetStream(0, 1)   # 1 = 100 kHz standard
        log.info("CH341 ctypes backend open, I2C 100 kHz")

    def _bind(self) -> None:
        d = self._dll
        d.CH341OpenDevice.argtypes  = [ctypes.c_ulong]
        d.CH341OpenDevice.restype   = ctypes.c_void_p
        d.CH341CloseDevice.argtypes = [ctypes.c_ulong]
        d.CH341CloseDevice.restype  = None
        d.CH341SetStream.argtypes   = [ctypes.c_ulong, ctypes.c_ulong]
        d.CH341SetStream.restype    = ctypes.c_bool
        d.CH341ReadI2C.argtypes     = [
            ctypes.c_ulong, ctypes.c_ubyte, ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte),
        ]
        d.CH341ReadI2C.restype      = ctypes.c_bool
        d.CH341StreamI2C.argtypes   = [
            ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ubyte),
        ]
        d.CH341StreamI2C.restype    = ctypes.c_bool

    def read_reg(self, addr: int, reg: int, length: int) -> bytes:
        data = bytearray()
        for off in range(length):
            val = ctypes.c_ubyte(0)
            ok  = self._dll.CH341ReadI2C(
                0,
                ctypes.c_ubyte(addr << 1),
                ctypes.c_ubyte((reg + off) & 0xFF),
                ctypes.byref(val),
            )
            if not ok:
                raise IOError("CH341ReadI2C failed addr=0x%02X reg=0x%02X" % (addr, reg + off))
            data.append(val.value)
        return bytes(data)

    def write_reg(self, addr: int, reg: int, data: bytes) -> None:
        buf   = bytes([(addr << 1) & 0xFE, reg]) + bytes(data)
        out   = (ctypes.c_ubyte * len(buf))(*buf)
        dummy = (ctypes.c_ubyte * 1)()
        ok    = self._dll.CH341StreamI2C(0, len(buf), out, 0, dummy)
        if not ok:
            raise IOError("CH341StreamI2C write failed addr=0x%02X reg=0x%02X" % (addr, reg))

    def close(self) -> None:
        try:
            self._dll.CH341CloseDevice(0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

def _make_backend():
    try:
        return I2CpyBackend()
    except ImportError:
        log.info("i2cpy not installed, falling back to ctypes backend")
    except Exception as exc:
        log.warning("i2cpy backend failed (%s), falling back to ctypes", exc)
    return CtypesBackend()


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
        """Return dict: temperature (%sC), humidity (%%), pressure (hPa)."""
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
            "humidity":    round(humi, 2),
            "pressure":    round(pres, 2),
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

    backend = _make_backend()
    sensor  = BME280(backend, address=address)

    client = mqtt.Client(client_id="bme280-observatory")
    if username:
        client.username_pw_set(username, password)
    client.connect(broker, port, keepalive=60)
    client.loop_start()

    log.info("Publishing every %ds -> %s  (broker %s:%d)", interval, prefix, broker, port)
    try:
        while True:
            data = sensor.read()
            for key, val in data.items():
                client.publish("%s/%s" % (prefix, key), str(val), qos=qos, retain=retain)
            log.info(
                "T=%.2f%sC  H=%.2f%%  P=%.2fhPa",
                data["temperature"], DEG, data["humidity"], data["pressure"],
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        client.loop_stop()
        backend.close()


if __name__ == "__main__":
    run()
