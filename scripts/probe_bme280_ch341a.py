#!/usr/bin/env python3
# -*- coding: ascii -*-
# SPDX-FileCopyrightText: 2026 David Gonzalez Lopez-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
BME280 / BMP280 probe for Windows 11 + CH341T_V3 (GY-BMEP 4-pin module).

Uses i2cpy as the sole backend (pip install i2cpy).

Expected chip IDs:
  BME280 -> 0x60  (temperature + humidity + pressure)
  BMP280 -> 0x58  (temperature + pressure only)

Usage:
  pip install i2cpy
  python probe_bme280_ch341a.py

Wiring for GY-BMEP 4-pin (CH341T_V3 I2C connector):
  VCC  -> 3.3 V
  GND  -> GND
  SDA  -> SDA
  SCL  -> SCL

I2C address:
  SDO tied to GND (internal pull-down on 4-pin module) -> 0x76
"""

import struct
import sys
import time


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

I2C_ADDR_CANDIDATES = [0x76, 0x77]

REG_ID        = 0xD0
REG_RESET     = 0xE0
REG_STATUS    = 0xF3
REG_CTRL_HUM  = 0xF2
REG_CTRL_MEAS = 0xF4
REG_DATA      = 0xF7

BME280_CHIP_ID = 0x60
BMP280_CHIP_ID = 0x58

# Plausibility ranges for compensated values
TEMP_MIN_C =  -40.0
TEMP_MAX_C =   85.0
HUM_MIN    =    0.0
HUM_MAX    =  100.0
PRES_MIN   =  300.0   # hPa
PRES_MAX   = 1100.0   # hPa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hex8(v):
    return f"0x{v:02X}"


# ---------------------------------------------------------------------------
# Backend: i2cpy (sole backend)
# ---------------------------------------------------------------------------

class I2CpyBackend:
    """Uses the i2cpy library (pip install i2cpy)."""

    def __init__(self):
        try:
            import i2cpy
        except ImportError:
            print("[FAIL] i2cpy is not installed.")
            print("       Run:  pip install i2cpy")
            sys.exit(1)
        try:
            self._i2c = i2cpy.I2C(driver="ch341")
        except Exception as exc:
            print(f"[FAIL] Could not open CH341T_V3 device via i2cpy: {exc}")
            print("       Check USB cable and that the CH341 driver is installed.")
            sys.exit(1)
        print("[OK] i2cpy backend initialised (CH341T_V3 driver)")

    def read_reg_byte(self, addr, reg):
        data = self._i2c.readfrom_mem(addr, reg, 1)
        return data[0]

    def read_regs(self, addr, start_reg, length):
        return bytes(self._i2c.readfrom_mem(addr, start_reg, length))

    def write_reg(self, addr, reg, data):
        self._i2c.writeto_mem(addr, reg, bytes(data))

    def close(self):
        close_fn = getattr(self._i2c, "close", None) or getattr(self._i2c, "deinit", None)
        if close_fn is not None:
            try:
                close_fn()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Probe logic
# ---------------------------------------------------------------------------

def read_chip_id(bus, addr):
    return bus.read_reg_byte(addr, REG_ID)


def soft_reset(bus, addr):
    bus.write_reg(addr, REG_RESET, [0xB6])
    time.sleep(0.02)


def read_status(bus, addr):
    return bus.read_reg_byte(addr, REG_STATUS)


def read_calibration_raw(bus, addr):
    calib_a  = bus.read_regs(addr, 0x88, 24)
    calib_h1 = bus.read_regs(addr, 0xA1, 1)
    calib_h  = bus.read_regs(addr, 0xE1, 7)
    return calib_a, calib_h1, calib_h


def parse_calibration(calib_a, calib_h1, calib_h):
    """Parse calibration bytes into named coefficients (Bosch datasheet 4.2.2)."""
    (T1, T2, T3,
     P1, P2, P3, P4, P5, P6, P7, P8, P9) = struct.unpack("<HhhHhhhhhhhh", calib_a)

    H1 = calib_h1[0]
    e  = calib_h
    H2 = struct.unpack("<h", bytes([e[0], e[1]]))[0]
    H3 = e[2]
    H4 = (e[3] << 4) | (e[4] & 0x0F)
    H5 = (e[4] >> 4) | (e[5] << 4)
    H6 = struct.unpack("b", bytes([e[6]]))[0]

    return dict(T1=T1, T2=T2, T3=T3,
                P1=P1, P2=P2, P3=P3, P4=P4, P5=P5,
                P6=P6, P7=P7, P8=P8, P9=P9,
                H1=H1, H2=H2, H3=H3, H4=H4, H5=H5, H6=H6)


def configure_forced(bus, addr):
    bus.write_reg(addr, REG_CTRL_HUM,  [0x01])
    bus.write_reg(addr, REG_CTRL_MEAS, [0x25])
    time.sleep(0.05)


def read_raw(bus, addr):
    data  = bus.read_regs(addr, REG_DATA, 8)
    p_raw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
    t_raw = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
    h_raw = (data[6] << 8)  |  data[7]
    return p_raw, t_raw, h_raw


def compensate(p_raw, t_raw, h_raw, c, has_hum):
    """Apply Bosch compensation (datasheet section 4.2.3)."""
    # Temperature
    v1     = (t_raw / 16384.0  - c["T1"] / 1024.0)  * c["T2"]
    v2     = ((t_raw / 131072.0 - c["T1"] / 8192.0) ** 2) * c["T3"]
    t_fine = v1 + v2
    temp   = t_fine / 5120.0

    # Pressure
    v1 = t_fine / 2.0 - 64000.0
    v2 = v1 * v1 * c["P6"] / 32768.0 + v1 * c["P5"] * 2.0
    v2 = v2 / 4.0 + c["P4"] * 65536.0
    v1 = (c["P3"] * v1 * v1 / 524288.0 + c["P2"] * v1) / 524288.0
    v1 = (1.0 + v1 / 32768.0) * c["P1"]
    if v1 == 0.0:
        pres = 0.0
    else:
        p  = 1048576.0 - p_raw
        p  = ((p - v2 / 4096.0) * 6250.0) / v1
        v1 = c["P9"] * p * p / 2147483648.0
        v2 = p * c["P8"] / 32768.0
        pres = (p + (v1 + v2 + c["P7"]) / 16.0) / 100.0

    # Humidity
    if not has_hum:
        humi = 0.0
    else:
        h = t_fine - 76800.0
        if h == 0.0:
            humi = 0.0
        else:
            h = (h_raw - (c["H4"] * 64.0 + c["H5"] / 16384.0 * h)) * (
                c["H2"] / 65536.0 * (
                    1.0 + c["H6"] / 67108864.0 * h * (
                        1.0 + c["H3"] / 67108864.0 * h
                    )
                )
            )
            h    *= 1.0 - c["H1"] * h / 524288.0
            humi  = max(0.0, min(100.0, h))

    return round(temp, 2), round(humi, 2), round(pres, 2)


def detect_devices(bus):
    found = []
    for addr in I2C_ADDR_CANDIDATES:
        try:
            chip_id = read_chip_id(bus, addr)
            if chip_id in (BME280_CHIP_ID, BMP280_CHIP_ID):
                found.append((addr, chip_id))
                print(f"[OK] Valid device at {hex8(addr)} chip ID {hex8(chip_id)}")
            else:
                print(f"[--] {hex8(addr)} returned {hex8(chip_id)} (not a BME/BMP280)")
        except Exception as exc:
            print(f"[--] {hex8(addr)}: {exc}")
    return found


def check_calibration(calib_a, calib_h1, calib_h):
    all_bytes = calib_a + calib_h1 + calib_h
    if all(x == 0x00 for x in all_bytes):
        raise RuntimeError("Calibration block is all 0x00")
    if all(x == 0xFF for x in all_bytes):
        raise RuntimeError("Calibration block is all 0xFF")
    print(
        f"[OK] Calibration: {len(all_bytes)} bytes, "
        f"first={hex8(all_bytes[0])} last={hex8(all_bytes[-1])}"
    )


def plausibility_notes(p_raw, t_raw, h_raw):
    notes = []
    if p_raw in (0, 0x80000):
        notes.append("pressure raw suspicious")
    if t_raw in (0, 0x80000):
        notes.append("temperature raw suspicious")
    if h_raw == 0:
        notes.append("humidity raw=0 (may be BMP280 with no humidity)")
    return notes


def plausibility_notes_compensated(temp, humi, pres, has_hum):
    notes = []
    if not (TEMP_MIN_C <= temp <= TEMP_MAX_C):
        notes.append(f"temperature {temp} out of range [{TEMP_MIN_C}, {TEMP_MAX_C}] degC")
    if has_hum and not (HUM_MIN <= humi <= HUM_MAX):
        notes.append(f"humidity {humi} out of range [{HUM_MIN}, {HUM_MAX}] %")
    if not (PRES_MIN <= pres <= PRES_MAX):
        notes.append(f"pressure {pres} out of range [{PRES_MIN}, {PRES_MAX}] hPa")
    return notes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    bus = None
    try:
        print("=== BME280 CH341T_V3 probe ===\n")
        bus = I2CpyBackend()

        print("\n[1] Device detection")
        found = detect_devices(bus)
        if not found:
            print(
                "\n[FAIL] No BME280/BMP280 found at 0x76 or 0x77.\n"
                "  Check wiring: VCC=3.3V, GND, SDA, SCL.\n"
                "  For 4-pin GY-BMEP: SDO is pulled to GND internally -> address 0x76.\n"
                "  Make sure CSB is NOT connected to GND (that would activate SPI mode).\n"
                "  Try unplugging and replugging the CH341T_V3 USB after fixing wiring."
            )
            sys.exit(1)

        addr, chip_id = found[0]
        has_hum = (chip_id == BME280_CHIP_ID)
        label   = "BME280" if has_hum else "BMP280"
        print(f"[OK] Using {label} at {hex8(addr)}")

        print("\n[2] Soft reset and status")
        soft_reset(bus, addr)
        status    = read_status(bus, addr)
        measuring = (status >> 3) & 0x01
        im_update = status & 0x01
        print(f"[OK] STATUS={hex8(status)} measuring={measuring} im_update={im_update}")

        print("\n[3] Calibration data")
        calib_a, calib_h1, calib_h = read_calibration_raw(bus, addr)
        check_calibration(calib_a, calib_h1, calib_h)
        calib = parse_calibration(calib_a, calib_h1, calib_h)

        print("\n[4] Raw measurement test (5 samples)")
        for idx in range(5):
            configure_forced(bus, addr)
            status = read_status(bus, addr)
            p_raw, t_raw, h_raw = read_raw(bus, addr)
            notes = plausibility_notes(p_raw, t_raw, h_raw)
            flag  = "[WARN]" if notes else f"[{idx+1}/5]"
            print(
                f"{flag} STATUS={hex8(status)} "
                f"Praw={p_raw:6d}  Traw={t_raw:6d}  Hraw={h_raw:5d}"
            )
            if notes:
                print("       Notes: " + "; ".join(notes))
            time.sleep(1.0)

        print("\n[5] Compensated readings (5 samples, Bosch datasheet 4.2.3)")
        for idx in range(5):
            configure_forced(bus, addr)
            p_raw, t_raw, h_raw = read_raw(bus, addr)
            temp, humi, pres = compensate(p_raw, t_raw, h_raw, calib, has_hum)
            notes = plausibility_notes_compensated(temp, humi, pres, has_hum)
            flag  = "[WARN]" if notes else f"[{idx+1}/5]"
            if has_hum:
                print(f"{flag} T={temp:6.2f} degC  H={humi:5.2f} %  P={pres:8.2f} hPa")
            else:
                print(f"{flag} T={temp:6.2f} degC  P={pres:8.2f} hPa  (BMP280: no humidity)")
            if notes:
                print("       Notes: " + "; ".join(notes))
            time.sleep(1.0)

        print("\n[OK] Probe completed successfully")

    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)

    finally:
        if bus is not None:
            bus.close()


if __name__ == "__main__":
    main()
