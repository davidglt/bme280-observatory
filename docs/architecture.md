# Arquitectura del sistema

## Flujo de datos

```
┌──────────────┐     I²C      ┌──────────────────────┐
│  BME280      │◄────────────►│  Raspberry Pi         │
│  (0x76/0x77) │              │                       │
└──────────────┘              │  bme280_mqtt.py        │───► MQTT ───► Home Assistant
                               │  (cada 30 s)          │              │
                               │                       │              └── InfluxDB 2.x
                               │  bme280_ascom.py      │───► HTTP/Alpaca ──► N.I.N.A.
                               │  (Flask, puerto 11111)│                   KStars
                               └──────────────────────┘                   ASCOM Platform
```

## Topics MQTT

| Topic | Tipo | Ejemplo |
|---|---|---|
| `observatory/bme280/temperature` | float | `18.34` |
| `observatory/bme280/humidity` | float | `72.10` |
| `observatory/bme280/pressure` | float | `1013.25` (nivel del mar) |
| `observatory/bme280/pressure_raw` | float | `950.42` (en el observatorio) |
| `observatory/bme280/status` | JSON | `{"temperature":18.34,...,"timestamp":"2026-08-29T21:15:00+00:00"}` |

## API ASCOM Alpaca

Base URL: `http://<ip-rpi>:11111`

| Endpoint | Descripción |
|---|---|
| `GET /api/v1/observingconditions/0/temperature` | Temperatura (°C) |
| `GET /api/v1/observingconditions/0/humidity` | Humedad (%) |
| `GET /api/v1/observingconditions/0/pressure` | Presión (hPa, nivel del mar) |
| `GET /api/v1/observingconditions/0/dewpoint` | Punto de rocío (°C) |
| `GET /api/v1/observingconditions/0/connected` | Estado de conexión |
| `GET /management/v1/configureddevices` | Descubrimiento Alpaca |

## Corrección barométrica

Se aplica la fórmula hipsométrica estándar para referir la presión al nivel del mar:

```
P_slm = P_obs × exp( altitud / (29.3 × (T_C + 273.15)) )
```

Configura la altitud real de tu observatorio en `altitude_m` (config.ini).

## Punto de rocío

Fórmula de Magnus:

```
α = ln(RH/100) + 17.625×T / (243.04 + T)
dp = 243.04 × α / (17.625 − α)
```

El riesgo de condensación aparece cuando `T − dp < 3°C`.
