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
#   - Appends one CSV row to:
#       C:\astro\bme280-observatory\logs\sharpcap_conditions.csv  (repo log acumulado)
#       Desktop\SharpCap Captures\YYYY-MM-DD\sharpcap_conditions.csv  (junto a las capturas)
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

_REPO_ROOT     = r"C:\astro\bme280-observatory"
_LATEST_JSON   = os.path.join(_REPO_ROOT, "logs", "latest_reading.json")
_REPO_CSV      = os.path.join(_REPO_ROOT, "logs", "sharpcap_conditions.csv")
_CAPTURES_ROOT = os.path.join(os.path.expanduser("~"), "Desktop", "SharpCap Captures")

_CSV_HEADER        = "timestamp,temperature_c,humidity_pct,pressure_hpa,pressure_altitude_m\n"
_SAMPLE_INTERVAL_S = 60


def _session_csv():
    """Return the CSV path for today's SharpCap captures subfolder.

    SharpCap creates  Desktop\SharpCap Captures\YYYY-MM-DD\  for each session.
    We write our CSV there so conditions data lives alongside the captures.
    The folder is created if it doesn't exist yet (session started before
    SharpCap creates it, or no captures taken).
    """
    date_folder = datetime.date.today().strftime("%Y-%m-%d")
    session_dir = os.path.join(_CAPTURES_ROOT, date_folder)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "sharpcap_conditions.csv")


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
    _info("Repo CSV   : %s" % _REPO_CSV)
    _info("Session dir: %s" % os.path.join(_CAPTURES_ROOT, datetime.date.today().strftime("%Y-%m-%d")))
    _info("Sampling every %ds. Type  conditions()  to query." % _SAMPLE_INTERVAL_S)
    _info("=" * 52)
    while True:
        reading = _read_latest()
        if reading is not None:
            row = _format_row(reading)
            _append_csv(_REPO_CSV, row)
            # Resolve session CSV on every tick so a midnight rollover
            # automatically starts writing to the new YYYY-MM-DD folder.
            _append_csv(_session_csv(), row)
            _info("T=%.2fC  H=%.3f%%  P=%.3fhPa  Alt=%.1fm" % (
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
