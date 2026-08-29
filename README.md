# BME280 Observatory — NYX Windows 11 + CH341T_V3

Lectura de temperatura, humedad y presión desde un sensor **BME280** conectado vía **I²C** al adaptador **CH341T_V3** (USB→I²C) en **Windows 11 (NYX)**. Publica los datos por **MQTT** a **Home Assistant** y los expone como canal personalizado en **SharpCap** (Observing Conditions).

## Hardware requerido

| Componente | Descripción |
|---|---|
| Sensor | BME280 en módulo GY-BMEP 4 pines (I²C, dirección 0x76) |
| Adaptador USB | CH341T_V3 (USB→I²C, driver CH341PAR_INST.EXE) |
| PC | Windows 11 — NYX |
| Red | Broker MQTT (Mosquitto en Home Assistant o externo) |

## Estructura del proyecto

```
bme280-observatory/
├── sensor/
│   ├── bme280_ch341t_v3.py     # Lectura BME280 vía CH341T_V3 + publicación MQTT
│   ├── bme280_ascom.py         # Servidor ASCOM ObservingConditions
│   ├── bme280_mqtt.py          # Loop MQTT independiente
│   └── config.example.ini      # Plantilla de configuración
├── sharpcap/
│   └── sharpcap_conditions.py  # Endpoint HTTP para SharpCap Observing Conditions
├── homeassistant/
│   └── configuration.yaml      # Configuración MQTT sensor HA
├── scripts/
│   ├── probe_bme280_ch341t_v3.py  # Diagnóstico hardware CH341T_V3 + BME280
│   ├── run_observatory.bat        # Lanzador Windows (NYX)
│   ├── setup.sh                   # Setup Linux (referencia)
│   └── bme280-*.service           # Unidades systemd (referencia)
├── requirements/
│   └── requirements.txt
└── README.md
```

## Instalación en Windows 11 (NYX)

### 1. Driver CH341T_V3

1. Descarga e instala **CH341PAR_INST.EXE** (WCH oficial).
2. Conecta el adaptador USB; debe aparecer en Administrador de dispositivos → _Controladores de bus USB_ como **CH341T**.
3. Instala i2cpy: `pip install i2cpy`.

### 2. Entorno Python

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/requirements.txt
```

### 3. Configuración

Copia `sensor/config.example.ini` a `sensor/config.ini` y edita los valores de I²C, MQTT y SharpCap.

### 4. Diagnóstico previo

Antes de arrancar el servicio, verifica la cadena hardware:

```powershell
python scripts\probe_bme280_ch341t_v3.py
```

Debe completar las 5 fases (detección, reset, calibración, raw, compensado) sin errores.

### 5. Ejecución

```powershell
# Manual
python sensor\bme280_ch341t_v3.py

# Lanzador completo (sensor + SharpCap)
scripts\run_observatory.bat
```

## Integración MQTT → Home Assistant

Agrega en `configuration.yaml` de HA o usa `homeassistant/configuration.yaml`:

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

Ver `requirements/requirements.txt`. Principales:
- `i2cpy` — comunicación I²C con CH341T_V3
- `paho-mqtt` — cliente MQTT
- `Flask` — servidor HTTP para SharpCap

## Licencia

GPL-3.0-or-later — David González López-Tercero
