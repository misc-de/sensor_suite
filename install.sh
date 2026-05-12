#!/usr/bin/env bash
set -euo pipefail

APP_ID="de.cais.SensorSuite"
INSTALL_DIR="$HOME/.local/share/sensor-suite"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
DESKTOP_DIR="$HOME/.local/share/applications"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$DESKTOP_DIR/$APP_ID.desktop" ]; then
    echo "Sensor Suite is already installed. Run uninstall.sh first to reinstall."
    exit 0
fi

mkdir -p "$INSTALL_DIR" "$ICON_DIR" "$DESKTOP_DIR"

cp "$SCRIPT_DIR/sensor_suite.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/gps.py"          "$INSTALL_DIR/"
cp "$SCRIPT_DIR/compass.py"      "$INSTALL_DIR/"
cp "$SCRIPT_DIR/spirit_level.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/acceleration.py" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/sensor_suite.py"

cp "$SCRIPT_DIR/sensors.png" "$INSTALL_DIR/sensors.png"

cat > "$DESKTOP_DIR/$APP_ID.desktop" << EOF
[Desktop Entry]
Name=Sensor Suite
Comment=Compass, Spirit Level and G-Force sensor display
Exec=python3 $INSTALL_DIR/sensor_suite.py
Icon=$INSTALL_DIR/sensors.png
Terminal=false
Type=Application
Categories=Utility;
StartupNotify=true
EOF

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

echo "Sensor Suite installed. Launch it from your application menu."
