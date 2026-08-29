# BME280 Observatory

Integración del sensor BME280 (temperatura, humedad, presión) con el observatorio astronómico.

## Arquitectura

```
BME280 (I²C)
    │
    ├── bme280_mqtt.py  ──►  MQTT Broker  ──►  Home Assistant
    │                                              │
    │                                         InfluxDB (registro histórico)
    │
    └── bme280_ascom.py ──►  ASCOM ObservingConditions (cliente N.I.N.A., KStars…)
```

## Componentes

| Archivo | Descripción |
|---|---|
| `sensor/bme280_mqtt.py` | Lee el BME280 y publica en MQTT |
| `sensor/bme280_ascom.py` | Servidor ASCOM ObservingConditions (HTTP/Alpaca) |
| `homeassistant/configuration.yaml` | Integración MQTT → HA |
| `homeassistant/influxdb.yaml` | Persistencia en InfluxDB |
| `scripts/setup.sh` | Instalación en Raspberry Pi / Linux |

## Hardware

- Sensor: Bosch BME280 (I²C, dirección 0x76 o 0x77)
- Host: Raspberry Pi (cualquier modelo con I²C)
- Broker MQTT: Mosquitto (local o remoto)
- Base de datos: InfluxDB 2.x

## Instalación rápida

```bash
git clone https://github.com/davidglt/bme280-observatory
cd bme280-observatory
pip install -r requirements.txt
cp sensor/config.example.ini sensor/config.ini
# Editar sensor/config.ini con tus parámetros
python sensor/bme280_mqtt.py
```

## Requisitos

Ver `requirements.txt`. Python 3.9+.

## Licencia

MIT
