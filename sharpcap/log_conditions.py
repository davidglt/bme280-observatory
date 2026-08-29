# -*- coding: utf-8 -*-
# SharpCap startup script: Observatory Conditions Logger
# SPDX-FileCopyrightText: 2026 David Gonzalez Lopez-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Installation:
#   SharpCap > File > Settings > Startup Scripts > Add > select this file
#
# What it does:
#   - Every 60 s reads logs/latest_reading.json written by bme280_ch341t_v3.py
#   - Always appends to:
#       C:\astro\bme280-observatory\logs\sharpcap_conditions.csv  (repo log acumulado)
#   - Appends to session CSV following these rules:
#       1. At startup: only activates if SharpCap has already created TODAY's
#          folder (YYYY-MM-DD). Folders from previous days are ignored.
#       2. Once active: keeps writing to the same folder even past midnight,
#          until SharpCap creates a new folder for the new date.
#       3. If no session folder exists at startup, logs [repo only] until
#          SharpCap creates today's folder.
#     This script never creates folders.
#
#   - Exposes conditions() in the SharpCap scripting console (Alt+F11)
#     Type  conditions()  at any time to see the latest reading.
#
# Requires: bme280_ch341t_v3.py running as a background service (Task Scheduler)

import clr
import json
import os
import datetime

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon
from System.Threading import Thread, ThreadStart, ApartmentState

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT         = r"C:\astro\bme280-observatory"
_LATEST_JSON       = os.path.join(_REPO_ROOT, "logs", "latest_reading.json")
_REPO_CSV          = os.path.join(_REPO_ROOT, "logs", "sharpcap_conditions.csv")

_CSV_HEADER        = "timestamp,temperature_c,humidity_pct,pressure_hpa,pressure_altitude_m\n"
_SAMPLE_INTERVAL_S = 60


def _today_capture_csv():
    """Return the CSV path for TODAY's SharpCap folder if it already exists.

    Only matches the current calendar date — folders from previous days
    are intentionally ignored. Returns None if today's folder doesn't exist.
    Never creates any folder.
    """
    try:
        root = SharpCap.CaptureFolder
        if root and root.strip():
            today = datetime.date.today().strftime("%Y-%m-%d")
            session_dir = os.path.join(root, today)
            if os.path.isdir(session_dir):
                return os.path.join(session_dir, "sharpcap_conditions.csv")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Logging  (same format as ppec_auto_enable.py)
# ---------------------------------------------------------------------------

def _log(level, msg):
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    line = "{}  {:8s}  [BME280]  {}".format(ts, level, msg)
    print(line)
    try:
        SharpCap.WriteToLog(line)
    except Exception:
        pass

def _info(msg):  _log("INFO",    msg)
def _error(msg): _log("ERROR",   msg)
def _warn(msg):  _log("WARNING", msg)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_latest():
    try:
        with open(_LATEST_JSON, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _append_csv(path, row):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_header = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as fh:
            if write_header:
                fh.write(_CSV_HEADER)
            fh.write(row + "\n")
    except Exception as exc:
        _error("CSV write error: %s" % exc)


def _format_row(reading):
    return "%s,%.2f,%.3f,%.3f,%.1f" % (
        reading["timestamp"],
        reading["temperature_c"],
        reading["humidity_pct"],
        reading["pressure_hpa"],
        reading["pressure_altitude_m"],
    )


def _format_display(reading):
    return (
        "Observatory Conditions\n"
        "----------------------\n"
        "Time    : %s\n"
        "Temp    : %.2f degC\n"
        "Humidity: %.3f %%\n"
        "Pressure: %.3f hPa\n"
        "Alt ISA : %.1f m\n"
        "\n"
        "(ISA pressure altitude varies with weather)"
    ) % (
        reading["timestamp"],
        reading["temperature_c"],
        reading["humidity_pct"],
        reading["pressure_hpa"],
        reading["pressure_altitude_m"],
    )


# ---------------------------------------------------------------------------
# Public console command
# ---------------------------------------------------------------------------

def conditions():
    """Show latest observatory conditions in a dialog.

    Call from the SharpCap scripting console (Alt+F11):
        conditions()
    """
    reading = _read_latest()
    if reading is None:
        MessageBox.Show(
            "No data available.\nIs bme280_ch341t_v3.py running?",
            "Observatory Conditions",
            MessageBoxButtons.OK,
            MessageBoxIcon.Warning,
        )
    else:
        MessageBox.Show(
            _format_display(reading),
            "Observatory Conditions",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information,
        )


# ---------------------------------------------------------------------------
# Background sampling loop
# ---------------------------------------------------------------------------

def _sampling_loop():
    import time
    _info("=" * 52)
    _info("Observatory Conditions Logger starting...")
    _info("Repo CSV    : %s" % _REPO_CSV)
    _info("Session CSV : waiting for SharpCap to create today's folder")
    _info("Sampling every %ds. Type  conditions()  to query." % _SAMPLE_INTERVAL_S)
    _info("=" * 52)
    # _active_csv is None until SharpCap creates today's folder.
    # Once set it stays valid across midnight until a new dated folder appears.
    # At startup we never inherit folders from previous days.
    _active_csv = None
    while True:
        reading = _read_latest()
        if reading is not None:
            row = _format_row(reading)
            _append_csv(_REPO_CSV, row)
            # Check if SharpCap has created today's folder (never picks up old dates)
            today_csv = _today_capture_csv()
            if today_csv and today_csv != _active_csv:
                _info("Session CSV: %s" % today_csv)
                _active_csv = today_csv
            if _active_csv:
                _append_csv(_active_csv, row)
                _info("T=%.2fC  H=%.3f%%  P=%.3fhPa  Alt=%.1fm  [repo+session]" % (
                    reading["temperature_c"],
                    reading["humidity_pct"],
                    reading["pressure_hpa"],
                    reading["pressure_altitude_m"],
                ))
            else:
                _info("T=%.2fC  H=%.3f%%  P=%.3fhPa  Alt=%.1fm  [repo only]" % (
                    reading["temperature_c"],
                    reading["humidity_pct"],
                    reading["pressure_hpa"],
                    reading["pressure_altitude_m"],
                ))
        else:
            _warn("latest_reading.json not found. Next check in %ds..." % _SAMPLE_INTERVAL_S)
        time.sleep(_SAMPLE_INTERVAL_S)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

t = Thread(ThreadStart(_sampling_loop))
t.IsBackground = True
t.ApartmentState = ApartmentState.STA
t.Start()
