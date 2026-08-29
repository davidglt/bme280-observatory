#!/usr/bin/env python3
"""
bme280_ascom.py  –  Servidor ASCOM Alpaca ObservingConditions basado en BME280.

Expone la API ASCOM Remote / Alpaca para que clientes como N.I.N.A., Cartes du
Ciel, KStars o el ASCOM Platform puedan consultar las condiciones del observatorio.

Endpoints principales:
  GET /api/v1/observingconditions/0/temperature
  GET /api/v1/observingconditions/0/humidity
  GET /api/v1/observingconditions/0/pressure
  GET /api/v1/observingconditions/0/dewpoint
  GET /api/v1/observingconditions/0/skytemperature  (no soportado → excepción)
  GET /management/v1/description
  GET /management/v1/configureddevices

Uso:
  python bme280_ascom.py [--config /ruta/config.ini]
"""

import argparse
import configparser
import logging
import math
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import smbus2
import bme280
from flask import Flask, jsonify, request

DEFAULT_CONFIG = Path(__file__).parent / "config.ini"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("bme280_ascom")

# ──────────────────────────────────────────────────────────────────────────────
# Estado compartido (actualizado por hilo de lectura)
_lock = threading.Lock()
_state = {
    "temperature": None,
    "humidity": None,
    "pressure": None,
    "dewpoint": None,
    "last_update": None,
    "error": None,
}
_client_id_counter = 0


def dewpoint(temp_c: float, rh: float) -> float:
    """Magnus formula. Rango válido: -40 a +60°C."""
    a, b = 17.625, 243.04
    alpha = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return round(b * alpha / (a - alpha), 2)


def sea_level_pressure(p: float, alt: float, t: float) -> float:
    return p * math.exp(alt / (29.3 * (t + 273.15)))


# ──────────────────────────────────────────────────────────────────────────────
def sensor_loop(i2c_bus: int, i2c_address: int, altitude: float, interval: int):
    bus = smbus2.SMBus(i2c_bus)
    cal = bme280.load_calibration_params(bus, i2c_address)
    log.info("Hilo de sensor iniciado (bus=%d addr=0x%02X)", i2c_bus, i2c_address)
    while True:
        try:
            data = bme280.sample(bus, i2c_address, cal)
            t = round(data.temperature, 2)
            h = round(data.humidity, 2)
            p = round(sea_level_pressure(data.pressure, altitude, t), 2)
            dp = dewpoint(t, h)
            with _lock:
                _state["temperature"] = t
                _state["humidity"] = h
                _state["pressure"] = p
                _state["dewpoint"] = dp
                _state["last_update"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                _state["error"] = None
        except Exception as exc:
            with _lock:
                _state["error"] = str(exc)
            log.error("Error sensor: %s", exc)
        time.sleep(interval)


# ──────────────────────────────────────────────────────────────────────────────
def alpaca_response(value=None, error_number=0, error_message=""):
    """Formato estándar de respuesta ASCOM Alpaca."""
    return jsonify({
        "ClientTransactionID": request.args.get("ClientTransactionID", 0, type=int),
        "ServerTransactionID": int(time.time() * 1000) % 2**31,
        "ErrorNumber": error_number,
        "ErrorMessage": error_message,
        "Value": value,
    })


NOT_IMPLEMENTED = 0x0400  # ASCOM error: Not implemented


def create_app(cfg: configparser.ConfigParser) -> Flask:
    app = Flask(__name__)
    dev_num = int(cfg["ascom"]["device_number"])
    dev_name = cfg["ascom"]["device_name"]
    base = f"/api/v1/observingconditions/{dev_num}"

    # ── Management endpoints ──────────────────────────────────────────────────
    @app.get("/management/v1/description")
    def mgmt_description():
        return jsonify({
            "ServerName": "BME280 Observatory ASCOM Alpaca",
            "Manufacturer": "davidglt",
            "ManufacturerVersion": "1.0.0",
            "Location": "Observatorio personal",
        })

    @app.get("/management/v1/configureddevices")
    def mgmt_devices():
        return jsonify([{
            "DeviceName": dev_name,
            "DeviceType": "ObservingConditions",
            "DeviceNumber": dev_num,
            "UniqueID": "bme280-observatory-001",
        }])

    # ── Properties ────────────────────────────────────────────────────────────
    @app.get(f"{base}/temperature")
    def get_temperature():
        with _lock:
            v = _state["temperature"]
        if v is None:
            return alpaca_response(error_number=NOT_IMPLEMENTED,
                                   error_message="Sensor no disponible")
        return alpaca_response(value=v)

    @app.get(f"{base}/humidity")
    def get_humidity():
        with _lock:
            v = _state["humidity"]
        if v is None:
            return alpaca_response(error_number=NOT_IMPLEMENTED,
                                   error_message="Sensor no disponible")
        return alpaca_response(value=v)

    @app.get(f"{base}/pressure")
    def get_pressure():
        with _lock:
            v = _state["pressure"]
        if v is None:
            return alpaca_response(error_number=NOT_IMPLEMENTED,
                                   error_message="Sensor no disponible")
        return alpaca_response(value=v)

    @app.get(f"{base}/dewpoint")
    def get_dewpoint():
        with _lock:
            v = _state["dewpoint"]
        if v is None:
            return alpaca_response(error_number=NOT_IMPLEMENTED,
                                   error_message="Sensor no disponible")
        return alpaca_response(value=v)

    @app.get(f"{base}/skytemperature")
    @app.get(f"{base}/skyquality")
    @app.get(f"{base}/starfwhm")
    @app.get(f"{base}/windspeed")
    @app.get(f"{base}/winddirection")
    @app.get(f"{base}/windgust")
    @app.get(f"{base}/rainrate")
    @app.get(f"{base}/cloudcover")
    def not_implemented():
        return alpaca_response(error_number=NOT_IMPLEMENTED,
                               error_message="Propiedad no implementada en BME280")

    @app.get(f"{base}/connected")
    def get_connected():
        with _lock:
            ok = _state["error"] is None and _state["temperature"] is not None
        return alpaca_response(value=ok)

    @app.get(f"{base}/name")
    def get_name():
        return alpaca_response(value=dev_name)

    @app.get(f"{base}/description")
    def get_description():
        return alpaca_response(value="BME280 sensor de temperatura, humedad y presión")

    @app.get(f"{base}/driverinfo")
    def get_driverinfo():
        return alpaca_response(value="bme280_ascom.py v1.0.0 — github.com/davidglt/bme280-observatory")

    @app.get(f"{base}/interfaceversion")
    def get_interfaceversion():
        return alpaca_response(value=1)

    @app.get(f"{base}/averageperiod")
    def get_averageperiod():
        return alpaca_response(value=0.0)

    @app.put(f"{base}/averageperiod")
    def set_averageperiod():
        return alpaca_response(value=None)

    @app.get(f"{base}/sensordescription")
    def get_sensor_description():
        sensor_name = request.args.get("SensorName", "").lower()
        descriptions = {
            "temperature": "Temperatura ambiente (°C)",
            "humidity":    "Humedad relativa (%)",
            "pressure":    "Presión barométrica nivel del mar (hPa)",
            "dewpoint":    "Punto de rocío calculado (Magnus) (°C)",
        }
        return alpaca_response(
            value=descriptions.get(sensor_name, "Sensor no disponible")
        )

    @app.get(f"{base}/timesincelastupdate")
    def get_timesincelastupdate():
        with _lock:
            lu = _state["last_update"]
        if lu is None:
            return alpaca_response(value=99999.0)
        delta = (datetime.now(timezone.utc) -
                 datetime.fromisoformat(lu)).total_seconds()
        return alpaca_response(value=round(delta, 1))

    return app


# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BME280 ASCOM Alpaca ObservingConditions")
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    if not cfg.read(args.config):
        log.error("Archivo de configuración no encontrado: %s", args.config)
        sys.exit(1)

    i2c_address = int(cfg["bme280"]["i2c_address"], 16)
    i2c_bus     = int(cfg["bme280"]["i2c_bus"])
    altitude    = float(cfg["bme280"]["altitude_m"])
    interval    = int(cfg["bme280"]["interval_seconds"])
    port        = int(cfg["ascom"]["port"])

    # Hilo de lectura del sensor
    t = threading.Thread(
        target=sensor_loop,
        args=(i2c_bus, i2c_address, altitude, interval),
        daemon=True
    )
    t.start()

    app = create_app(cfg)
    log.info("Servidor ASCOM Alpaca en http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
