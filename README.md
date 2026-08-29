# BME280 Observatory — NYX Windows 11 + CH341T_V3

Reads temperature, humidity, and pressure from a **BME280** sensor connected via **I²C** to a **CH341T_V3** (USB→I²C) adapter on **Windows 11 (NYX)**. Publishes data over **MQTT** to **Home Assistant** and exposes it as a custom channel in **SharpCap** (Observing Conditions).

## Required Hardware

| Component | Description |
|---|---|
| Sensor | BME280 on GY-BMEP 4-pin module (I²C, address 0x76) |
| USB Adapter | CH341T_V3 (USB→I²C, driver CH341PAR_INST.EXE) |
| PC | Windows 11 — NYX |
| Network | MQTT broker (Mosquitto on Home Assistant or standalone) |

## Project Structure

```
bme280-observatory/
├── sensor/
│   ├── bme280_ch341t_v3.py     # BME280 reading via CH341T_V3 + MQTT publishing
│   └── config.example.ini      # Configuration template
├── sharpcap/
│   └── sharpcap_conditions.py  # HTTP endpoint for SharpCap Observing Conditions
├── homeassistant/
│   └── configuration.yaml      # MQTT sensor configuration for HA
├── scripts/
│   ├── probe_bme280_ch341t_v3.py  # Hardware diagnostic: CH341T_V3 + BME280
│   ├── run_observatory.bat        # Windows launcher (NYX)
│   ├── run_sensor_task.bat        # Task Scheduler launcher
│   ├── install_task.bat           # Register scheduled task
│   ├── uninstall_task.bat         # Remove scheduled task
│   └── bme280-observatory.xml     # Task Scheduler task definition
├── requirements/
│   └── requirements.txt
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

Copy `sensor/config.example.ini` to `sensor/config.ini` and edit the I²C, MQTT, and SharpCap values.

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

Add to your HA `configuration.yaml` or use `homeassistant/configuration.yaml`:

```yaml
mqtt:
  sensor:
    - name: "Observatory Temperature"
      state_topic: "observatory/bme280/temperature"
      unit_of_measurement: "°C"
    - name: "Observatory Humidity"
      state_topic: "observatory/bme280/humidity"
      unit_of_measurement: "%"
    - name: "Observatory Pressure"
      state_topic: "observatory/bme280/pressure"
      unit_of_measurement: "hPa"
```

## SharpCap Integration

SharpCap reads observing conditions from a local HTTP endpoint. The `sharpcap/sharpcap_conditions.py` script serves `http://localhost:5380/conditions` with the JSON format expected by SharpCap:

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
- `i2cpy` — I²C communication with CH341T_V3
- `paho-mqtt` — MQTT client

## License

GPL-3.0-or-later — David González López-Tercero
