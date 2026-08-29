#!/usr/bin/env python3
"""BME280 reader via CH341A USB-I2C adapter on Windows 11 (NYX).

Uses smbus2 with CH341 backend (ch341dll on Windows).
Publishes readings to MQTT and exposes them for SharpCap.
"""
import time
import logging
import struct
import ctypes
import os
import sys
import yaml
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class CH341I2C:
    """Thin wrapper around CH341DLL for I2C communication on Windows."""

    def __init__(self):
        dll_names = ["CH341DLL.dll", "CH341DLL64.dll", "ch341dll.dll"]
        self._dll = None
        for name in dll_names:
            try:
                self._dll = ctypes.WinDLL(name)
                log.info("Loaded CH341 DLL: %s", name)
                break
            except OSError:
                continue
        if self._dll is None:
            raise RuntimeError(
                "CH341 DLL not found. Install CH341PAR_INST.EXE driver and ensure DLL is in PATH."
            )
        if not self._dll.CH341OpenDevice(0):
            raise RuntimeError("Cannot open CH341 device (index 0). Check USB connection.")
        # Set I2C speed: 0=low(20kHz), 1=standard(100kHz), 2=fast(400kHz)
        self._dll.CH341SetStream(0, 1)

    def write_read(self, addr: int, write_bytes: bytes, read_len: int) -> bytes:
        """I2C write then read."""
        buf_out = (ctypes.c_ubyte * len(write_bytes))(*write_bytes)
        buf_in = (ctypes.c_ubyte * read_len)()
        ret = self._dll.CH341StreamI2C(
            0, len(write_bytes), buf_out, read_len, buf_in
        )
        if ret == 0:
            raise IOError(f"CH341 I2C error addr=0x{addr:02X}")
        return bytes(buf_in)

    def close(self):
        self._dll.CH341CloseDevice(0)


class BME280:
    """BME280 driver over I2C via CH341A."""

    def __init__(self, i2c: CH341I2C, address: int = 0x76):
        self._i2c = i2c
        self._addr = address
        self._load_calibration()

    def _read_reg(self, reg: int, length: int) -> bytes:
        return self._i2c.write_read(self._addr, bytes([reg]), length)

    def _load_calibration(self):
        raw = self._read_reg(0x88, 24)
        (self.dig_T1, self.dig_T2, self.dig_T3,
         self.dig_P1, self.dig_P2, self.dig_P3,
         self.dig_P4, self.dig_P5, self.dig_P6,
         self.dig_P7, self.dig_P8, self.dig_P9) = struct.unpack("<HhhHhhhhhhhh", raw)
        h1 = self._read_reg(0xA1, 1)[0]
        h_raw = self._read_reg(0xE1, 7)
        self.dig_H1 = h1
        self.dig_H2, self.dig_H3 = struct.unpack("<hB", h_raw[:3])
        e4, e5, e6 = h_raw[3], h_raw[4], h_raw[5]
        self.dig_H4 = (e4 << 4) | (e5 & 0x0F)
        self.dig_H5 = (e5 >> 4) | (e6 << 4)
        self.dig_H6 = struct.unpack("b", bytes([h_raw[6]]))[0]

    def _read_raw(self):
        # Force measurement: osrs_h=1, osrs_t=1, osrs_p=1, mode=forced
        self._i2c.write_read(self._addr, bytes([0xF2, 0x01]), 0)
        self._i2c.write_read(self._addr, bytes([0xF4, 0x25]), 0)
        time.sleep(0.1)
        raw = self._read_reg(0xF7, 8)
        praw = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        traw = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        hraw = (raw[6] << 8) | raw[7]
        return praw, traw, hraw

    def read(self) -> dict:
        praw, traw, hraw = self._read_raw()
        # Temperature compensation
        v1 = (traw / 16384.0 - self.dig_T1 / 1024.0) * self.dig_T2
        v2 = ((traw / 131072.0 - self.dig_T1 / 8192.0) ** 2) * self.dig_T3
        t_fine = v1 + v2
        temperature = t_fine / 5120.0
        # Pressure compensation
        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * self.dig_P6 / 32768.0 + v1 * self.dig_P5 * 2.0
        v2 = v2 / 4.0 + self.dig_P4 * 65536.0
        v1 = (self.dig_P3 * v1 * v1 / 524288.0 + self.dig_P2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * self.dig_P1
        if v1 == 0:
            pressure = 0.0
        else:
            p = 1048576.0 - praw
            p = ((p - v2 / 4096.0) * 6250.0) / v1
            v1 = self.dig_P9 * p * p / 2147483648.0
            v2 = p * self.dig_P8 / 32768.0
            pressure = (p + (v1 + v2 + self.dig_P7) / 16.0) / 100.0
        # Humidity compensation
        h = t_fine - 76800.0
        if h == 0:
            humidity = 0.0
        else:
            h = (hraw - (self.dig_H4 * 64.0 + self.dig_H5 / 16384.0 * h)) * (
                self.dig_H2 / 65536.0 * (1.0 + self.dig_H6 / 67108864.0 * h *
                (1.0 + self.dig_H3 / 67108864.0 * h)))
            h = h * (1.0 - self.dig_H1 * h / 524288.0)
            humidity = max(0.0, min(100.0, h))
        return {
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "pressure": round(pressure, 2),
        }


def run():
    cfg = load_config(CONFIG_PATH)
    s_cfg = cfg["sensor"]
    m_cfg = cfg["mqtt"]

    i2c = CH341I2C()
    sensor = BME280(i2c, address=int(str(s_cfg.get("address", 0x76)), 16)
                   if isinstance(s_cfg.get("address"), str) else s_cfg.get("address", 0x76))

    client = mqtt.Client(client_id="bme280-observatory")
    if m_cfg.get("username"):
        client.username_pw_set(m_cfg["username"], m_cfg.get("password", ""))
    client.connect(m_cfg["broker"], m_cfg.get("port", 1883), keepalive=60)
    client.loop_start()

    prefix = m_cfg.get("topic_prefix", "observatory/bme280")
    interval = s_cfg.get("interval_sec", 10)

    log.info("Publishing BME280 data every %ds to %s", interval, prefix)
    try:
        while True:
            data = sensor.read()
            for key, val in data.items():
                client.publish(f"{prefix}/{key}", str(val), retain=True)
            log.info("T=%.2f°C  H=%.2f%%  P=%.2fhPa", data["temperature"], data["humidity"], data["pressure"])
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        client.loop_stop()
        i2c.close()


if __name__ == "__main__":
    run()
