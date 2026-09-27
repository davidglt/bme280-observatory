# BME280 Observatory — NYX Windows 11 + CH341T_V3

Reads temperature, humidity, and pressure from a **BME280** sensor connected via **I2C** to a **CH341T_V3** (USB→I2C) adapter on **Windows 11 (NYX)**. Publishes data over **MQTT** to **Home Assistant** and exposes it as a custom channel in **SharpCap** (Observing Conditions).

## Required Hardware

| Component | Description |
|---|---|
| Sensor | BME280 on GY-BMEP 4-pin module (I2C, address 0x76 or 0x77) |
| USB Adapter | CH341T_V3 (USB→I2C, driver CH341PAR_INST.EXE) |
| PC | Windows 11 — NYX |
| Network | MQTT broker (Mosquitto on Home Assistant or standalone) |

The reader requires a BME280 (chip ID `0x60`). A BMP280 is rejected because it
does not measure humidity; the service never publishes a synthetic zero
humidity value.

## Architecture

```
┌──────────────┐     I2C/USB ┌──────────────────────────────┐
│   BME280     │◄───────────►│  CH341T_V3 (i2cpy)           │
│  (0x76)      │             │  bme280_ch341t_v3.py (30 s)  │
└──────────────┘             └─────────────┬────────────────┘
                                           │
                    ┌──────────────────────┤
                    │                      │
                    ▼                      ▼
           MQTT (paho-mqtt)     HTTP :5380 (http.server)
           Mosquitto / HA       sharpcap_conditions.py
                │                      │
                ▼                      ▼
         Home Assistant            SharpCap Pro
         (dashboard,           (Observing Conditions
          automations)          Custom HTTP Source)
```

## MQTT Topics

| Topic | Type | Example |
|---|---|---|
| `homeassistant/nyx/temperature` | float | `18.34` |
| `homeassistant/nyx/humidity` | float | `72.10` |
| `homeassistant/nyx/pressure` | float | `1013.25` (absolute, hPa) |
| `homeassistant/nyx/pressure_altitude` | float | `312.5` (ISA altitude, m) |
| `homeassistant/nyx/reading` | JSON | Complete sample with values and Unix timestamp |

Home Assistant continues to use the individual numeric topics. The SharpCap
HTTP service consumes the retained `/reading` JSON topic so the values and
timestamp are always from one sensor sample. It returns
`503 Service Unavailable` when no complete sample has arrived or the last
sample is more than 120 seconds old.
Home Assistant MQTT entities expire after 120 seconds without an update.
The SharpCap CSV logger also skips missing, malformed, non-finite, or older
than 120-second `latest_reading.json` data.

The sensor service retries CH341T_V3/BME280 initialization and I2C read errors
with exponential backoff (2 to 60 seconds). It rejects readings outside the
physical operating ranges before writing or publishing them. Shutdown signals
close the MQTT client and I2C backend cleanly.

## Barometric Correction

Pressure is reported as the absolute sensor reading (not reduced to sea level).
The **ISA pressure altitude** is derived from the raw pressure using:

```
h = 44330.77 × [1 − (P / 1013.25)^0.1902632]
```

This gives an altitude that varies with weather, not the geographic elevation.
Set your observatory's actual altitude in `altitude_m` (`config.ini`) if you add
sea-level reduction to a downstream automation.

## Observatory Altitude

The observatory (NYX) is located at **700 m** above sea level.

## Dew Point

Calculated downstream from temperature and humidity via the Magnus formula:

```
α  = ln(RH/100) + 17.625×T / (243.04 + T)
dp = 243.04 × α / (17.625 − α)
```

Condensation risk when `T − dp < 3 °C`.

## Project Structure

```
bme280-observatory/
├── sensor/
│   ├── bme280_ch341t_v3.py     # BME280 reading via CH341T_V3 + MQTT publishing
│   └── config.example.ini      # Configuration template (no secrets)
├── sharpcap/
│   ├── sharpcap_conditions.py  # Local-network HTTP endpoint for conditions
│   ├── reading_utils.py        # Shared sample freshness validation
│   └── log_conditions.py       # Fresh-reading logger for SharpCap
├── homeassistant/
│   ├── configuration.yaml      # MQTT sensor block for configuration.yaml
│   └── sensor.yaml             # Standalone MQTT sensor file (!include sensor.yaml)
├── scripts/
│   ├── probe_bme280_ch341t_v3.py  # Hardware diagnostic: CH341T_V3 + BME280
│   ├── run_observatory.bat        # Windows launcher (NYX)
│   ├── run_sensor_task.bat        # Task Scheduler launcher
│   ├── install_task.bat           # Register scheduled task
│   ├── uninstall_task.bat         # Remove scheduled task
│   └── bme280-observatory.xml     # Task Scheduler task definition
├── requirements/
│   └── requirements.txt
├── tests/
│   └── test_sensor_conditions.py
└── README.md
```

## Installation on Windows 11 (NYX)

### 1. CH341T_V3 Driver

1. Download and install **CH341PAR_INST.EXE** (official WCH).
2. Plug in the USB adapter; it should appear in Device Manager → _Universal Serial Bus controllers_ as **CH341T**.
3. Install i2cpy: `pip install i2cpy`.

### 2. Python Environment

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/requirements.txt
```

### 3. Configuration

Copy `sensor/config.example.ini` to `sensor/config.ini` and edit the I2C, MQTT, and SharpCap values.
`sensor/config.ini` is excluded from the repository (see `.gitignore`).

### 4. Hardware Diagnostic

Before starting the service, verify the hardware chain:

```powershell
python scripts\probe_bme280_ch341t_v3.py
```

All 5 phases (detection, reset, calibration, raw, compensated) must complete without errors.

### 5. Running

```powershell
# Manual
python sensor\bme280_ch341t_v3.py

# Full launcher (sensor + SharpCap)
scripts\run_observatory.bat
```

## MQTT → Home Assistant Integration

Add to your HA `configuration.yaml`, or include `homeassistant/sensor.yaml` with
`mqtt: !include sensor.yaml`:

```yaml
mqtt:
  sensor:
    - name: NYX Temperature
      unique_id: "nyx_temperature"
      state_topic: "homeassistant/nyx/temperature"
      device_class: temperature
      unit_of_measurement: "°C"
    - name: NYX Humidity
      unique_id: "nyx_humidity"
      state_topic: "homeassistant/nyx/humidity"
      device_class: humidity
      unit_of_measurement: "%"
    - name: NYX Pressure
      unique_id: "nyx_pressure"
      state_topic: "homeassistant/nyx/pressure"
      device_class: pressure
      unit_of_measurement: "hPa"
    - name: NYX Pressure Altitude
      unique_id: "nyx_pressure_altitude"
      state_topic: "homeassistant/nyx/pressure_altitude"
      icon: mdi:altimeter
      unit_of_measurement: "m"
```

Expected entity IDs: `sensor.nyx_temperature`, `sensor.nyx_humidity`,
`sensor.nyx_pressure`, `sensor.nyx_pressure_altitude`.

## SharpCap Integration

SharpCap reads observing conditions from a local HTTP endpoint.
The `sharpcap/sharpcap_conditions.py` service listens on all network interfaces
on the configured port (default `5380`). On the observatory PC, SharpCap can use
`http://localhost:5380/conditions`; another trusted local-network client can
use `http://<observatory-PC-IP>:5380/conditions`. Allow the configured port
through the Windows firewall only for the trusted local network.

The endpoint serves only complete readings newer than 120 seconds with the JSON
format expected by SharpCap:

```json
{
  "Temperature": 18.5,
  "Humidity": 65.2,
  "Pressure": 1013.4
}
```

In SharpCap → **Tools → Observing Conditions → Custom HTTP Source** → `http://localhost:5380/conditions`.

## Dependencies

See `requirements/requirements.txt`. Main packages:
- `i2cpy` — I2C communication with CH341T_V3
- `paho-mqtt` — MQTT client

The SharpCap conditions server reads the same INI configuration as the sensor;
it does not require a separate YAML file or PyYAML.

## Author

David González López-Tercero

## License

Copyright © 2026 David González López-Tercero.

This project is licensed under the GNU General Public License v3.0 or later
(GPL-3.0-or-later). See [LICENSE](LICENSE) for the full text.
