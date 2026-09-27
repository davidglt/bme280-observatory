import json
import tempfile
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


class SensorConditionsTests(unittest.TestCase):
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
