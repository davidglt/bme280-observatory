#!/usr/bin/env bash
# setup.sh – Instalación del agente BME280 en Raspberry Pi / Debian
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== BME280 Observatory Setup ==="

# 1. Habilitar I2C si no está activo
if ! lsmod | grep -q i2c_dev; then
    echo "[*] Habilitando I2C..."
    sudo raspi-config nonint do_i2c 0 2>/dev/null || \
        echo "   (no es RPi; activa I2C manualmente)"
fi

# 2. Instalar dependencias del sistema
echo "[*] Instalando dependencias del sistema..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-pip python3-venv i2c-tools

# 3. Detectar sensor
echo "[*] Buscando BME280 en el bus I2C..."
i2cdetect -y 1 || true

# 4. Crear entorno virtual e instalar paquetes Python
echo "[*] Creando entorno virtual..."
python3 -m venv "$PROJECT_DIR/.venv"
source "$PROJECT_DIR/.venv/bin/activate"
pip install --upgrade pip -q
pip install -r "$PROJECT_DIR/requirements.txt" -q

# 5. Copiar configuración de ejemplo si no existe
if [ ! -f "$PROJECT_DIR/sensor/config.ini" ]; then
    cp "$PROJECT_DIR/sensor/config.example.ini" "$PROJECT_DIR/sensor/config.ini"
    echo "[!] Edita sensor/config.ini con tus parámetros MQTT y dirección I2C"
fi

# 6. Instalar servicios systemd
echo "[*] Instalando servicios systemd..."
for SVC in bme280-mqtt bme280-ascom; do
    sudo cp "$SCRIPT_DIR/$SVC.service" /etc/systemd/system/
done
sudo systemctl daemon-reload
sudo systemctl enable bme280-mqtt bme280-ascom

echo ""
echo "=== Instalación completada ==="
echo "  1. Edita sensor/config.ini"
echo "  2. sudo systemctl start bme280-mqtt"
echo "  3. sudo systemctl start bme280-ascom   (opcional)"
echo "  4. Verifica: sudo journalctl -u bme280-mqtt -f"
