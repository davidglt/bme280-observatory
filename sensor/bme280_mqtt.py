#!/usr/bin/env python3
"""
bme280_mqtt.py  –  Lee el sensor BME280 y publica en MQTT.

Topics publicados:
  <prefix>/temperature   – °C  (float, 2 decimales)
  <prefix>/humidity      – %HR (float, 2 decimales)
  <prefix>/pressure      – hPa a nivel del mar (float, 2 decimales)
  <prefix>/pressure_raw  – hPa leída directamente
  <prefix>/status        – JSON con todos los valores + timestamp ISO 8601

Uso:
  python bme280_mqtt.py [--config /ruta/config.ini]
"""

import argparse
import configparser
import json
import logging
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import smbus2
import bme280

# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = Path(__file__).parent / "config.ini"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("bme280_mqtt")


# ──────────────────────────────────────────────────────────────────────────────
def sea_level_pressure(pressure_hpa: float, altitude_m: float, temp_c: float) -> float:
    """Corrección barométrica estándar (fórmula hipsométrica)."""
    return pressure_hpa * math.exp(altitude_m / (29.3 * (temp_c + 273.15)))


def read_sensor(bus, address: int, calibration_params):
    """Lee el BME280 y devuelve un dict con los valores."""
    data = bme280.sample(bus, address, calibration_params)
    return {
        "temperature": round(data.temperature, 2),
        "humidity": round(data.humidity, 2),
        "pressure_raw": round(data.pressure, 2),
    }


# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BME280 → MQTT publisher")
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    if not cfg.read(args.config):
        log.error("No se encontró el archivo de configuración: %s", args.config)
        sys.exit(1)

    # Parámetros
    i2c_address  = int(cfg["bme280"]["i2c_address"], 16)
    i2c_bus      = int(cfg["bme280"]["i2c_bus"])
    interval     = int(cfg["bme280"]["interval_seconds"])
    altitude     = float(cfg["bme280"]["altitude_m"])
    prefix       = cfg["mqtt"]["topic_prefix"].rstrip("/")
    qos          = int(cfg["mqtt"]["qos"])
    retain       = cfg["mqtt"].getboolean("retain")

    # MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if cfg["mqtt"].get("username"):
        client.username_pw_set(
            cfg["mqtt"]["username"],
            cfg["mqtt"].get("password", "")
        )
    client.connect(cfg["mqtt"]["host"], int(cfg["mqtt"]["port"]), keepalive=60)
    client.loop_start()
    log.info("Conectado a MQTT %s:%s", cfg['mqtt']['host'], cfg['mqtt']['port'])

    # I²C
    bus = smbus2.SMBus(i2c_bus)
    calibration_params = bme280.load_calibration_params(bus, i2c_address)
    log.info("Sensor BME280 inicializado en bus %d, dirección 0x%02X", i2c_bus, i2c_address)

    try:
        while True:
            try:
                vals = read_sensor(bus, i2c_address, calibration_params)
                vals["pressure"] = round(
                    sea_level_pressure(vals["pressure_raw"], altitude, vals["temperature"]), 2
                )
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

                # Publicar valores individuales
                for field in ("temperature", "humidity", "pressure", "pressure_raw"):
                    client.publish(
                        f"{prefix}/{field}",
                        payload=str(vals[field]),
                        qos=qos, retain=retain
                    )

                # Publicar JSON de estado completo
                status = {**vals, "timestamp": ts, "altitude_m": altitude}
                client.publish(
                    f"{prefix}/status",
                    payload=json.dumps(status),
                    qos=qos, retain=retain
                )

                log.info(
                    "T=%.2f°C  HR=%.2f%%  P=%.2fhPa (raw %.2f)",
                    vals["temperature"], vals["humidity"],
                    vals["pressure"], vals["pressure_raw"]
                )

            except Exception as exc:
                log.error("Error leyendo sensor: %s", exc)

            time.sleep(interval)

    except KeyboardInterrupt:
        log.info("Detenido por el usuario")
    finally:
        client.loop_stop()
        client.disconnect()
        bus.close()


if __name__ == "__main__":
    main()
