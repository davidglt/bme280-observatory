# BME280 Observatory — NYX Windows 11 + CH341A

Lectura de temperatura, humedad y presión desde un sensor **BME280** conectado vía **I²C** al adaptador **CH341A** (USB→I²C/SPI) en **Windows 11 (NYX)**. Publica los datos por **MQTT** a **Home Assistant** y los expone como canal personalizado en **SharpCap** (Observing Conditions).

## Hardware requerido

| Componente | Descripción |
|---|---|
| Sensor | BME280 (I²C, dirección 0x76 o 0x77) |
| Adaptador USB | CH341A (modo I²C/SPI, driver CH341PAR_INST.EXE) |
| PC | Windows 11 — NYX |
| Red | Broker MQTT (Mosquitto en Home Assistant o externo) |

## Estructura del proyecto

```
bme280-observatory/
├── sensor/
│   └── bme280_ch341a.py        # Lectura BME280 vía CH341A (libch341)
├── mqtt/
│   └── publisher.py            # Publica en MQTT → Home Assistant
├── sharpcap/
│   └── sharpcap_conditions.py  # Expone datos a SharpCap vía HTTP local
├── homeassistant/
│   └── sensor.yaml             # Configuración MQTT sensor HA
├── scripts/
│   └── run_observatory.bat     # Lanzador Windows (NYX)
├── requirements.txt
└── README.md
```

## Instalación en Windows 11 (NYX)

### 1. Driver CH341A

1. Descarga e instala **CH341PAR_INST.EXE** (WCH oficial).
2. Conecta el adaptador USB; debe aparecer como _USB-SERIAL CH340_ o _CH341 USB→I2C_.
3. Verifica en Administrador de dispositivos → Puertos (COM y LPT) o Controladores de bus USB.

### 2. Entorno Python

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuración

Copia `config.example.yaml` a `config.yaml` y edita:

```yaml
sensor:
  address: 0x76        # 0x76 o 0x77 según puente del módulo
  interval_sec: 10

mqtt:
  broker: "192.168.1.X"   # IP broker MQTT (Home Assistant)
  port: 1883
  topic_prefix: "observatory/bme280"
  username: ""
  password: ""

sharpcap:
  http_port: 5380          # Puerto HTTP local para SharpCap
```

### 4. Ejecución

```powershell
# Manual
python sensor\bme280_ch341a.py

# O usar el lanzador completo
scripts\run_observatory.bat
```

## Integración MQTT → Home Assistant

Agrega en `configuration.yaml` de HA o usa `homeassistant/sensor.yaml`:

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

## Integración SharpCap

SharpCap lee condiciones de observación desde un endpoint HTTP local. El script `sharpcap/sharpcap_conditions.py` levanta un servidor en `http://localhost:5380/conditions` con el formato JSON esperado por SharpCap:

```json
{
  "Temperature": 18.5,
  "Humidity": 65.2,
  "Pressure": 1013.4
}
```

En SharpCap → **Tools → Observing Conditions → Custom HTTP Source** → `http://localhost:5380/conditions`.

## Dependencias

Ver `requirements.txt`. Principales:
- `smbus2` — comunicación I²C
- `bme280` (RPi.bme280 o equivalente) o driver directo via `libch341`
- `paho-mqtt` — cliente MQTT
- `flask` — servidor HTTP para SharpCap
- `pyyaml` — configuración

## Licencia

MIT — David González López-Tercero
