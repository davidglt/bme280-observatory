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
#       C:\astro\bme280-observatory\logs\sharpcap_conditions.csv  (repo log)
#       Desktop\SharpCap Captures\sharpcap_conditions_<date>.csv  (per-session)
#   - Adds an "Observatory Conditions" button to the SharpCap toolbar
#     that shows a dialog with the latest reading on demand
#
# Requires: bme280_ch341t_v3.py running as a background service (Task Scheduler)

import clr
import json
import os
import sys
import datetime

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Windows.Forms import (
    MessageBox, MessageBoxButtons, MessageBoxIcon,
    ToolStripButton,
)
from System.Drawing import Image
from System.Threading import Thread, ThreadStart, ApartmentState

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT      = r"C:\astro\bme280-observatory"
_LATEST_JSON    = os.path.join(_REPO_ROOT, "logs", "latest_reading.json")
_REPO_CSV       = os.path.join(_REPO_ROOT, "logs", "sharpcap_conditions.csv")
_CAPTURES_ROOT  = os.path.join(os.path.expanduser("~"), "Desktop", "SharpCap Captures")
_SESSION_DATE   = datetime.date.today().strftime("%Y%m%d")
_SESSION_CSV    = os.path.join(_CAPTURES_ROOT, "sharpcap_conditions_%s.csv" % _SESSION_DATE)

_CSV_HEADER     = "timestamp,temperature_c,humidity_pct,pressure_hpa,pressure_altitude_m\n"
_SAMPLE_INTERVAL_S = 60

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_latest():
    """Return the latest BME280 reading dict, or None if unavailable."""
    try:
        with open(_LATEST_JSON, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _append_csv(path, row):
    """Append a CSV row to path, writing header if the file is new."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_header = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as fh:
            if write_header:
                fh.write(_CSV_HEADER)
            fh.write(row + "\n")
    except Exception as exc:
        SharpCap.ShowNotification("BME280: could not write CSV: %s" % exc)


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
# Toolbar button callback
# ---------------------------------------------------------------------------

def _show_conditions(sender, args):
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
    while True:
        reading = _read_latest()
        if reading is not None:
            row = _format_row(reading)
            _append_csv(_REPO_CSV, row)
            _append_csv(_SESSION_CSV, row)
        time.sleep(_SAMPLE_INTERVAL_S)


# ---------------------------------------------------------------------------
# Entry point — called once by SharpCap at startup
# ---------------------------------------------------------------------------

def _start():
    # Add toolbar button
    try:
        btn = ToolStripButton()
        btn.Text = "Obs. Conditions"
        btn.ToolTipText = "Show current observatory temperature, humidity and pressure"
        btn.Click += _show_conditions
        SharpCap.MainToolBar.Items.Add(btn)
    except Exception as exc:
        SharpCap.ShowNotification("BME280: toolbar button error: %s" % exc)

    # Start background sampling thread
    t = Thread(ThreadStart(_sampling_loop))
    t.IsBackground = True
    t.ApartmentState = ApartmentState.STA
    t.Start()

    SharpCap.ShowNotification("Observatory Conditions logger started (60 s interval)")


_start()
