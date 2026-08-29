#!/usr/bin/env python3
"""HTTP server exposing BME280 data as SharpCap Observing Conditions source.

SharpCap → Tools → Observing Conditions → Custom HTTP Source:
  http://localhost:5380/conditions
"""
import threading
import time
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import yaml
import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

_latest = {"Temperature": None, "Humidity": None, "Pressure": None}
_lock = threading.Lock()


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ConditionsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def do_GET(self):
        if self.path in ("/conditions", "/conditions/"):
            with _lock:
                payload = dict(_latest)
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
    prefix = m_cfg.get("topic_prefix", "observatory/bme280")
    key_map = {
        f"{prefix}/temperature": "Temperature",
        f"{prefix}/humidity": "Humidity",
        f"{prefix}/pressure": "Pressure",
    }

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            for topic in key_map:
                client.subscribe(topic)
            log.info("MQTT subscribed to %s/#", prefix)
        else:
            log.error("MQTT connect failed rc=%d", rc)

    def on_message(client, userdata, msg):
        key = key_map.get(msg.topic)
        if key:
            try:
                with _lock:
                    _latest[key] = float(msg.payload.decode())
            except ValueError:
                pass

    client = mqtt.Client(client_id="bme280-sharpcap")
    if m_cfg.get("username"):
        client.username_pw_set(m_cfg["username"], m_cfg.get("password", ""))
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(m_cfg["broker"], m_cfg.get("port", 1883), keepalive=60)
    client.loop_forever()


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(CONFIG_PATH)
    port = cfg.get("sharpcap", {}).get("http_port", 5380)

    t = threading.Thread(target=mqtt_thread, args=(cfg,), daemon=True)
    t.start()

    server = HTTPServer(("", port), ConditionsHandler)
    log.info("SharpCap conditions server listening on http://localhost:%d/conditions", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped.")


if __name__ == "__main__":
    run()
