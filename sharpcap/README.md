# SharpCap Integration — Observatory Conditions Logger

El script `log_conditions.py` registra las condiciones del observatorio
(temperatura, humedad, presion, altitud de presion ISA) mientras SharpCap
esta en ejecucion y anyade un boton a la barra de herramientas para
consultar las condiciones actuales en cualquier momento.

## Requisitos

- La tarea programada `BME280Observatory` debe estar en ejecucion en NYX,
  de forma que `logs/latest_reading.json` se actualice cada 30 s.
- SharpCap 4.x (usa IronPython / Python 2.7 embebido).

## Instalacion

1. En SharpCap: **File > Settings > Startup Scripts**.
2. Pulsa **Add** y selecciona `sharpcap/log_conditions.py`.
3. Reinicia SharpCap.

Al arrancar SharpCap veras la notificacion:
> Observatory Conditions logger started (60 s interval)

Y aparecera el boton **Obs. Conditions** en la barra de herramientas.

## Ficheros generados

| Fichero | Descripcion |
|---|---|
| `C:\astro\bme280-observatory\logs\sharpcap_conditions.csv` | Log historico acumulado en el repo |
| `Desktop\SharpCap Captures\sharpcap_conditions_YYYYMMDD.csv` | Log de sesion en la carpeta de capturas |

### Formato CSV

```
timestamp,temperature_c,humidity_pct,pressure_hpa,pressure_altitude_m
2026-08-29 22:15:00,29.91,33.942,933.103,696.2
2026-08-29 22:16:00,29.85,34.012,933.098,696.3
```

## Boton de la barra de herramientas

Pulsa **Obs. Conditions** en cualquier momento para ver un dialogo con
la ultima lectura del sensor BME280.

## Notas

- La altitud ISA **no** es la altitud geografica del observatorio;
  varia con la presion atmosferica local.
- Si el sensor no esta disponible, el boton muestra un aviso.
