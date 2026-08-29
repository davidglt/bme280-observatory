#!/usr/bin/env python3
# -*- coding: ascii -*-

"""
BME280 / BMP280 probe for Windows 11 + CH341T (GY-BMEP 4-pin module).

Two backend modes are tried in order:
  1. i2cpy  -- high-level library that handles CH341 address format correctly.
  2. ctypes -- direct DLL calls with explicit (addr << 1) address format.

Expected chip IDs:
  BME280 -> 0x60  (temperature + humidity + pressure)
  BMP280 -> 0x58  (temperature + pressure only)

Usage:
  pip install i2cpy
  python probe_bme280_ch341a.py

If i2cpy is not installed the script falls back to the ctypes backend
automatically.

Wiring for GY-BMEP 4-pin (CH341T_V3 connector):
  VCC  -> 3.3 V
  GND  -> GND
  SDA  -> SDA
  SCL  -> SCL

I2C address:
  SDO tied to GND (internal pull-down on 4-pin module) -> 0x76
"""

import ctypes
import os
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

DLL_CANDIDATES = [
    "CH341DLLA64.DLL",
    "CH341DLL64.dll",
    "CH341DLL.dll",
    os.path.join(
        os.environ.get("WINDIR", r"C:\Windows"), "System32", "CH341DLLA64.DLL"
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hex8(v):
    return f"0x{v:02X}"


# ---------------------------------------------------------------------------
# Backend 1: i2cpy
# ---------------------------------------------------------------------------

class I2CpyBackend:
    """Uses the i2cpy library (pip install i2cpy)."""

    def __init__(self):
        import i2cpy
        self._i2c = i2cpy.I2C(driver="ch341")
        print("[OK] i2cpy backend initialised (CH341 driver)")

    def read_reg_byte(self, addr, reg):
        data = self._i2c.readfrom_mem(addr, reg, 1)
        return data[0]

    def read_regs(self, addr, start_reg, length):
        return bytes(self._i2c.readfrom_mem(addr, start_reg, length))

    def write_reg(self, addr, reg, data):
        self._i2c.writeto_mem(addr, reg, bytes(data))

    def close(self):
        self._i2c.close()


# ---------------------------------------------------------------------------
# Backend 2: ctypes (CH341DLLA64.DLL)
# ---------------------------------------------------------------------------

class CtypesBackend:
    """Direct DLL calls.  Address must be left-shifted: CH341ReadI2C expects
    the 8-bit wire address (7-bit addr << 1) in its iDevice parameter."""

    def __init__(self):
        self.dll = None
        last_error = None

        for candidate in DLL_CANDIDATES:
            try:
                self.dll = ctypes.WinDLL(candidate)
                print(f"[OK] Loaded CH341 DLL: {candidate}")
                break
            except OSError as exc:
                last_error = exc

        if self.dll is None:
            raise RuntimeError(f"Unable to load CH341 DLL: {last_error}")

        self._bind()
        self._open()
        self._set_speed()

    def _bind(self):
        self.dll.CH341OpenDevice.argtypes = [ctypes.c_ulong]
        self.dll.CH341OpenDevice.restype  = ctypes.c_void_p

        self.dll.CH341CloseDevice.argtypes = [ctypes.c_ulong]
        self.dll.CH341CloseDevice.restype  = None

        self.dll.CH341SetStream.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        self.dll.CH341SetStream.restype  = ctypes.c_bool

        self.dll.CH341ReadI2C.argtypes = [
            ctypes.c_ulong,
            ctypes.c_ubyte,
            ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte),
        ]
        self.dll.CH341ReadI2C.restype = ctypes.c_bool

        self.dll.CH341StreamI2C.argtypes = [
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ubyte),
        ]
        self.dll.CH341StreamI2C.restype = ctypes.c_bool

    def _open(self):
        handle = self.dll.CH341OpenDevice(0)
        if not handle:
            raise RuntimeError("Unable to open CH341 device")
        print("[OK] CH341 device opened")

    def _set_speed(self):
        ok = self.dll.CH341SetStream(0, 0x01)
        if not ok:
            raise RuntimeError("Unable to configure CH341 I2C speed")
        print("[OK] CH341 configured for standard I2C speed")

    def read_reg_byte(self, addr, reg):
        value = ctypes.c_ubyte(0)
        # CH341ReadI2C expects the 8-bit wire address (addr << 1)
        ok = self.dll.CH341ReadI2C(
            0,
            ctypes.c_ubyte(addr << 1),
            ctypes.c_ubyte(reg),
            ctypes.byref(value),
        )
        if not ok:
            raise IOError(f"CH341ReadI2C failed addr={hex8(addr)} reg={hex8(reg)}")
        return value.value

    def read_regs(self, addr, start_reg, length):
        data = bytearray()
        for offset in range(length):
            data.append(self.read_reg_byte(addr, (start_reg + offset) & 0xFF))
        return bytes(data)

    def write_reg(self, addr, reg, data):
        # CH341StreamI2C write: first byte = wire address for write (addr<<1)
        write_data = bytes([(addr << 1) & 0xFE, reg]) + bytes(data)
        out_buf = (ctypes.c_ubyte * len(write_data))(*write_data)
        in_buf  = (ctypes.c_ubyte * 1)()
        ok = self.dll.CH341StreamI2C(
            0, len(write_data), out_buf, 0, in_buf
        )
        if not ok:
            raise IOError(f"CH341StreamI2C write failed addr={hex8(addr)} reg={hex8(reg)}")

    def close(self):
        if self.dll is not None:
            self.dll.CH341CloseDevice(0)


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

def make_backend():
    try:
        backend = I2CpyBackend()
        print("[INFO] Using i2cpy backend")
        return backend
    except ImportError:
        print("[INFO] i2cpy not installed, falling back to ctypes backend")
    except Exception as exc:
        print(f"[WARN] i2cpy backend failed: {exc}, falling back to ctypes")

    backend = CtypesBackend()
    print("[INFO] Using ctypes backend")
    return backend


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


def read_calibration_blocks(bus, addr):
    calib_a  = bus.read_regs(addr, 0x88, 24)
    calib_h1 = bus.read_regs(addr, 0xA1, 1)
    calib_h  = bus.read_regs(addr, 0xE1, 7)
    return calib_a + calib_h1 + calib_h


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


def check_calibration(calib):
    if all(x == 0x00 for x in calib):
        raise RuntimeError("Calibration block is all 0x00")
    if all(x == 0xFF for x in calib):
        raise RuntimeError("Calibration block is all 0xFF")
    print(f"[OK] Calibration: {len(calib)} bytes, first={hex8(calib[0])} last={hex8(calib[-1])}")


def plausibility_notes(p_raw, t_raw, h_raw):
    notes = []
    if p_raw in (0, 0x80000):
        notes.append("pressure raw suspicious")
    if t_raw in (0, 0x80000):
        notes.append("temperature raw suspicious")
    if h_raw == 0:
        notes.append("humidity raw=0 (may be BMP280 with no humidity)")
    return notes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    bus = None
    try:
        print("=== BME280 CH341T probe ===\n")
        bus = make_backend()

        print("\n[1] Device detection")
        found = detect_devices(bus)
        if not found:
            raise RuntimeError(
                "No BME280/BMP280 found at 0x76 or 0x77.\n"
                "Check wiring: VCC=3.3V, GND, SDA, SCL.\n"
                "For 4-pin GY-BMEP: SDO is pulled to GND internally -> address 0x76."
            )

        addr, chip_id = found[0]
        label = "BME280" if chip_id == BME280_CHIP_ID else "BMP280"
        print(f"[OK] Using {label} at {hex8(addr)}")

        print("\n[2] Soft reset and status")
        soft_reset(bus, addr)
        status     = read_status(bus, addr)
        measuring  = (status >> 3) & 0x01
        im_update  = status & 0x01
        print(f"[OK] STATUS={hex8(status)} measuring={measuring} im_update={im_update}")

        print("\n[3] Calibration data")
        calib = read_calibration_blocks(bus, addr)
        check_calibration(calib)

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

        print("\n[OK] Probe completed successfully")

    except Exception as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)

    finally:
        if bus is not None:
            bus.close()


if __name__ == "__main__":
    main()
