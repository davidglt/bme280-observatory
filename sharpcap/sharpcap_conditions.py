#!/usr/bin/env python3
"""HTTP server exposing fresh BME280 data to local-network consumers.

SharpCap → Tools → Observing Conditions → Custom HTTP Source:
  http://localhost:5380/conditions
"""
import configparser
import json
import logging
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "sensor" / "config.ini"
CONFIG_EXAMPLE_PATH = REPOSITORY_ROOT / "sensor" / "config.example.ini"
DEFAULT_HTTP_PORT = 5380
MAX_READING_AGE_S = 120

_latest = {"Temperature": None, "Humidity": None, "Pressure": None}
_latest_received_at = None
_latest_sample_timestamp = None
_lock = threading.Lock()


def load_config(path):
    """Load the shared INI configuration used by the sensor publisher."""
    config = configparser.ConfigParser()
    if not config.read(path, encoding="utf-8"):
        raise FileNotFoundError(f"Configuration file not found: {path}")
    if not config.has_section("mqtt"):
        raise ValueError(f"Missing [mqtt] section in configuration file: {path}")
    return config


def _config_path():
    return CONFIG_PATH if CONFIG_PATH.is_file() else CONFIG_EXAMPLE_PATH


def current_conditions_payload():
    """Return a complete, fresh reading, or None when data is unavailable."""
    with _lock:
        payload = dict(_latest)
        received_at = _latest_received_at
        sample_timestamp = _latest_sample_timestamp

    age = None if sample_timestamp is None else time.time() - sample_timestamp
    is_fresh = (
        received_at is not None
        and time.monotonic() - received_at <= MAX_READING_AGE_S
        and age is not None
        and -30 <= age <= MAX_READING_AGE_S
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in payload.values()
        )
    )
    return payload if is_fresh else None


class ConditionsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_GET(self):
        if self.path in ("/conditions", "/conditions/"):
            payload = current_conditions_payload()
            if payload is None:
                body = json.dumps(
                    {"error": "No fresh BME280 reading is available."}
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def mqtt_thread(cfg):
    m_cfg = cfg["mqtt"]
    prefix = m_cfg.get("topic_prefix", "homeassistant/nyx")
    key_map = {
        f"{prefix}/temperature": "Temperature",
        f"{prefix}/humidity": "Humidity",
        f"{prefix}/pressure": "Pressure",
    }
    sample_timestamp_topic = f"{prefix}/last_update"

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            for topic in key_map:
                client.subscribe(topic)
            client.subscribe(sample_timestamp_topic)
            log.info("MQTT subscribed to %s/#", prefix)
        else:
            log.error("MQTT connect failed: %s", reason_code)

    def on_message(client, userdata, msg):
        global _latest_received_at, _latest_sample_timestamp

        if msg.topic == sample_timestamp_topic:
            try:
                timestamp = float(msg.payload.decode())
                if not math.isfinite(timestamp):
                    raise ValueError("non-finite sample timestamp")
                with _lock:
                    _latest_sample_timestamp = timestamp
                    _latest_received_at = time.monotonic()
            except (UnicodeDecodeError, ValueError) as exc:
                log.warning(
                    "Ignoring invalid MQTT sample timestamp for %s: %s",
                    msg.topic,
                    exc,
                )
            return

        key = key_map.get(msg.topic)
        if key:
            try:
                value = float(msg.payload.decode())
                if not math.isfinite(value):
                    raise ValueError("non-finite reading")
                with _lock:
                    _latest[key] = value
            except (UnicodeDecodeError, ValueError) as exc:
                log.warning("Ignoring invalid MQTT reading for %s: %s", msg.topic, exc)

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="bme280-sharpcap",
    )
    if m_cfg.get("username"):
        client.username_pw_set(m_cfg["username"], m_cfg.get("password", ""))
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(
        m_cfg.get("host", "localhost"),
        m_cfg.getint("port", fallback=1883),
        keepalive=60,
    )
    client.loop_forever()


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = _config_path()
    cfg = load_config(config_path)
    port = cfg.getint("sharpcap", "http_port", fallback=DEFAULT_HTTP_PORT)

    t = threading.Thread(target=mqtt_thread, args=(cfg,), daemon=True)
    t.start()

    server = HTTPServer(("", port), ConditionsHandler)
    log.info(
        "BME280 conditions server listening on all interfaces on port %d "
        "(configured in %s)",
        port,
        config_path,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped.")


if __name__ == "__main__":
    run()
