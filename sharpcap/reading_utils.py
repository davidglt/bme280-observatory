#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Created: 2026-09-27
# Author: David González López-Tercero <davidglt@dragonit.es>
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation helpers for BME280 readings consumed by SharpCap."""

import math
import time


MAX_READING_AGE_S = 120
CLOCK_SKEW_TOLERANCE_S = 30
READING_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "pressure_altitude_m",
)


def is_reading_fresh(reading, now=None):
    """Return whether a reading has valid numeric data and a recent timestamp."""
    if not isinstance(reading, dict):
        return False

    timestamp = reading.get("timestamp_epoch")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return False
    try:
        timestamp = float(timestamp)
    except (OverflowError, ValueError):
        return False
    if math.isnan(timestamp) or math.isinf(timestamp):
        return False

    current_time = time.time() if now is None else now
    age = current_time - timestamp
    if age < -CLOCK_SKEW_TOLERANCE_S or age > MAX_READING_AGE_S:
        return False

    for field in READING_FIELDS:
        value = reading.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            value = float(value)
        except (OverflowError, ValueError):
            return False
        if math.isnan(value) or math.isinf(value):
            return False

    return True
