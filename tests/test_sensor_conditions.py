#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Created: 2026-09-27
# Author: David González López-Tercero <davidglt@dragonit.es>
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for BME280 sensing, MQTT publication, and SharpCap conditions."""

import json
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from sensor import bme280_ch341t_v3 as sensor
from sharpcap import reading_utils
from sharpcap import sharpcap_conditions as http_conditions


class FakeBackend:
    def __init__(self, chip_id):
        self.chip_id = chip_id

    def read_reg(self, address, register, length):
        if register == sensor.BME280.REG_ID:
            return bytes([self.chip_id])
        if register == 0x88:
            return bytes(length)
        if register == 0xA1:
            return b"\0"
        if register == 0xE1:
            return bytes(length)
        raise AssertionError(f"Unexpected register read: {register:#x}")


class FakeStopEvent:
    def __init__(self, stop_after_waits):
        self.stop_after_waits = stop_after_waits
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waits.append(timeout)
        if len(self.waits) >= self.stop_after_waits:
            self.stopped = True
        return self.stopped


class SensorConditionsTests(unittest.TestCase):
    def test_i2c_backend_open_failure_raises_retryable_error(self):
        i2cpy = mock.Mock(I2C=mock.Mock(side_effect=OSError("adapter unavailable")))
        with mock.patch.dict(sys.modules, {"i2cpy": i2cpy}):
            with self.assertRaisesRegex(RuntimeError, "Could not open CH341T_V3"):
                sensor.I2CpyBackend()

    def test_bmp280_is_rejected_instead_of_reporting_fake_humidity(self):
        with self.assertRaisesRegex(RuntimeError, "BMP280 sensors do not provide humidity"):
            sensor.BME280(FakeBackend(0x58))

    def test_bme280_chip_id_is_accepted(self):
        device = sensor.BME280(FakeBackend(0x60))
        self.assertEqual(device._addr, 0x76)

    def test_latest_reading_includes_epoch_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest_reading.json"
            data = {
                "temperature": 18.5,
                "humidity": 65.2,
                "pressure": 1013.4,
            }
            with (
                mock.patch.object(sensor, "LOG_DIR", directory),
                mock.patch.object(sensor, "LATEST_JSON", str(path)),
            ):
                sensor._write_latest(data, 0.0)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertAlmostEqual(payload["timestamp_epoch"], time.time(), delta=2)
        self.assertEqual(payload["temperature_c"], 18.5)

    def test_sensor_reading_validation_rejects_implausible_values(self):
        valid = {"temperature": 18.5, "humidity": 65.2, "pressure": 1013.4}
        sensor.validate_reading(valid)

        for key, value in (
            ("temperature", -41),
            ("temperature", float("nan")),
            ("humidity", 101),
            ("pressure", 0),
        ):
            with self.subTest(key=key, value=value):
                sample = valid.copy()
                sample[key] = value
                with self.assertRaises(sensor.InvalidReadingError):
                    sensor.validate_reading(sample)

    def test_mqtt_json_payload_keeps_values_and_timestamp_in_one_sample(self):
        data = {"temperature": 18.5, "humidity": 65.2, "pressure": 1013.4}

        payload = json.loads(
            sensor.build_reading_payload(data, 12.3, 1000.0)
        )

        self.assertEqual(
            payload,
            {
                "timestamp_epoch": 1000.0,
                "temperature_c": 18.5,
                "humidity_pct": 65.2,
                "pressure_hpa": 1013.4,
                "pressure_altitude_m": 12.3,
            },
        )

    def test_sensor_service_retries_initialization_and_read_errors(self):
        config = sensor.configparser.ConfigParser()
        config.read_dict(
            {
                "bme280": {"i2c_address": "0x76", "interval_seconds": "30"},
                "mqtt": {
                    "host": "broker.local",
                    "port": "1883",
                    "username": "",
                    "password": "",
                    "topic_prefix": "homeassistant/nyx",
                    "qos": "1",
                    "retain": "true",
                },
            }
        )
        stop_event = FakeStopEvent(stop_after_waits=4)
        first_backend = mock.Mock()
        recovered_backend = mock.Mock()
        final_backend = mock.Mock()
        disconnected_sensor = mock.Mock()
        disconnected_sensor.read.side_effect = OSError("temporary I2C read error")
        recovered_sensor = mock.Mock()
        recovered_sensor.read.return_value = {
            "temperature": 18.5,
            "humidity": 65.2,
            "pressure": 1013.4,
        }
        client = mock.Mock()
        client.publish.return_value.rc = sensor.mqtt.MQTT_ERR_SUCCESS

        with (
            mock.patch.object(sensor, "_load_config", return_value=config),
            mock.patch.object(sensor.threading, "Event", return_value=stop_event),
            mock.patch.object(sensor.signal, "getsignal", return_value=signal.SIG_DFL),
            mock.patch.object(sensor.signal, "signal"),
            mock.patch.object(
                sensor,
                "I2CpyBackend",
                side_effect=[
                    RuntimeError("temporary I2C adapter error"),
                    first_backend,
                    recovered_backend,
                    final_backend,
                ],
            ),
            mock.patch.object(
                sensor,
                "BME280",
                side_effect=[
                    RuntimeError("temporary I2C initialization error"),
                    disconnected_sensor,
                    recovered_sensor,
                ],
            ) as sensor_class,
            mock.patch.object(sensor.mqtt, "Client", return_value=client),
            mock.patch.object(sensor, "_write_latest") as write_latest,
            mock.patch.object(sensor, "pressure_altitude_isa", return_value=12.3),
        ):
            sensor.run()

        self.assertEqual(sensor_class.call_count, 3)
        self.assertEqual(stop_event.waits, [2, 4, 8, 30])
        first_backend.close.assert_called_once()
        recovered_backend.close.assert_called_once()
        final_backend.close.assert_called_once()
        client.disconnect.assert_called_once()
        client.loop_stop.assert_called_once()
        write_latest.assert_called_once()
        published_topics = [
            call.args[0] for call in client.publish.call_args_list
        ]
        self.assertIn("homeassistant/nyx/reading", published_topics)

    def test_sharpcap_mqtt_retries_initial_connection_failure(self):
        config = sensor.configparser.ConfigParser()
        config.read_dict(
            {
                "mqtt": {
                    "host": "broker.local",
                    "port": "1883",
                    "topic_prefix": "homeassistant/nyx",
                }
            }
        )
        client = mock.Mock()
        client.connect.side_effect = [OSError("broker unavailable"), None]

        with (
            mock.patch.object(http_conditions.mqtt, "Client", return_value=client),
            mock.patch.object(http_conditions.time, "sleep") as sleep,
        ):
            http_conditions.mqtt_thread(config)

        self.assertEqual(client.connect.call_count, 2)
        client.connect.assert_called_with("broker.local", 1883, keepalive=60)
        client.disconnect.assert_called_once()
        sleep.assert_called_once_with(2)

    def test_sharpcap_server_reads_the_shared_ini_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            config_path.write_text(
                "[mqtt]\nhost = broker.local\nport = 1884\n"
                "topic_prefix = homeassistant/nyx\n"
                "[sharpcap]\nhttp_port = 5381\n",
                encoding="utf-8",
            )
            config = http_conditions.load_config(config_path)

        self.assertEqual(
            config.getint("sharpcap", "http_port"),
            5381,
        )
        self.assertEqual(config.getint("mqtt", "port"), 1884)
        self.assertEqual(
            config.get("mqtt", "topic_prefix"),
            "homeassistant/nyx",
        )

    def test_home_assistant_views_use_configured_nyx_entity_ids(self):
        repository = Path(__file__).resolve().parent.parent
        expected_entities = (
            "sensor.nyx_temperature",
            "sensor.nyx_humidity",
            "sensor.nyx_pressure",
            "sensor.nyx_pressure_altitude",
        )
        dashboard = (repository / "homeassistant" / "dashboard.yaml").read_text(
            encoding="utf-8"
        )
        automations = (repository / "homeassistant" / "automations.yaml").read_text(
            encoding="utf-8"
        )
        influx = (repository / "homeassistant" / "influxdb.yaml").read_text(
            encoding="utf-8"
        )
        mqtt_config = (
            repository / "homeassistant" / "configuration.yaml"
        ).read_text(encoding="utf-8")
        standalone_sensors = (
            repository / "homeassistant" / "sensor.yaml"
        ).read_text(encoding="utf-8")

        for entity in expected_entities:
            with self.subTest(entity=entity):
                self.assertIn(entity, dashboard)
        self.assertIn("sensor.nyx_humidity", automations)
        self.assertIn("sensor.nyx_temperature", automations)
        self.assertIn("sensor.nyx_*", influx)
        self.assertEqual(mqtt_config.count("expire_after: 120"), 4)
        self.assertEqual(standalone_sensors.count("expire_after: 120"), 4)
        self.assertNotIn("sensor.observatory_", dashboard)
        self.assertNotIn("sensor.observatorio_", automations)

    def test_http_conditions_are_unavailable_without_a_complete_sample(self):
        with mock.patch.multiple(
            http_conditions,
            _latest={"Temperature": 18.0, "Humidity": None, "Pressure": 1013.0},
            _latest_received_at=time.monotonic(),
            _latest_sample_timestamp=time.time(),
        ):
            self.assertIsNone(http_conditions.current_conditions_payload())

    def test_http_conditions_are_unavailable_when_sample_is_stale(self):
        with mock.patch.multiple(
            http_conditions,
            _latest={"Temperature": 18.0, "Humidity": 60.0, "Pressure": 1013.0},
            _latest_received_at=time.monotonic(),
            _latest_sample_timestamp=time.time() - http_conditions.MAX_READING_AGE_S - 1,
        ):
            self.assertIsNone(http_conditions.current_conditions_payload())

    def test_http_conditions_return_a_complete_fresh_sample(self):
        expected = {"Temperature": 18.0, "Humidity": 60.0, "Pressure": 1013.0}
        with mock.patch.multiple(
            http_conditions,
            _latest=expected,
            _latest_received_at=time.monotonic(),
            _latest_sample_timestamp=time.time(),
        ):
            self.assertEqual(
                http_conditions.current_conditions_payload(),
                expected,
            )

    def test_http_server_parses_only_complete_fresh_mqtt_samples(self):
        sample = {
            "timestamp_epoch": time.time(),
            "temperature_c": 18.0,
            "humidity_pct": 60.0,
            "pressure_hpa": 1013.0,
            "pressure_altitude_m": 0.0,
        }
        payload, timestamp = http_conditions.parse_reading_message(
            json.dumps(sample).encode()
        )
        self.assertEqual(
            payload,
            {"Temperature": 18.0, "Humidity": 60.0, "Pressure": 1013.0},
        )
        self.assertEqual(timestamp, sample["timestamp_epoch"])

        sample["humidity_pct"] = None
        with self.assertRaisesRegex(ValueError, "incomplete, invalid or stale"):
            http_conditions.parse_reading_message(json.dumps(sample).encode())

    def test_reading_utils_rejects_missing_stale_and_non_finite_data(self):
        reading = {
            "timestamp_epoch": 1000.0,
            "temperature_c": 18.0,
            "humidity_pct": 60.0,
            "pressure_hpa": 1013.0,
            "pressure_altitude_m": 0.0,
        }
        self.assertTrue(reading_utils.is_reading_fresh(reading, now=1001.0))
        self.assertFalse(reading_utils.is_reading_fresh(reading, now=1121.0))

        for invalid_reading in (
            {key: value for key, value in reading.items() if key != "timestamp_epoch"},
            dict(reading, timestamp_epoch=float("nan")),
            dict(reading, humidity_pct=float("inf")),
        ):
            with self.subTest(reading=invalid_reading):
                self.assertFalse(
                    reading_utils.is_reading_fresh(invalid_reading, now=1001.0)
                )


if __name__ == "__main__":
    unittest.main()
