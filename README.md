# Sensor Suite

A GTK4/Libadwaita sensor display app for Linux mobile devices (Phosh, FuriOS, Droidian).

![Sensor Suite](sensors.png)

## Features

- **Compass** — magnetic heading with animated needle, calibration support
- **GPS altitude** — altitude above the compass when a GeoClue GPS fix is available
- **Spirit Level** — 2D bubble level + horizontal and vertical linear levels
- **G-Force** — accelerometer display with X/Y/Z values, total magnitude, and GPS speed
- **Swipe navigation** — switch between Compass, Spirit Level, and G-Force with horizontal swipes
- **Metric / miles units** — switch speed and altitude units in Settings

⚠️ **AI-assisted project**  

## Requirements

- Python 3.10+
- GTK 4 + Libadwaita ≥ 1.5 (`python-gobject`, `gtk4`, `libadwaita`)
- One of the following sensor backends:
  - `iio-sensor-proxy` / `hadess-sensorfw-proxy` (D-Bus)
  - `sensorfwd` (FuriOS / Droidian, via `/run/sensord.sock`)
  - Direct IIO sysfs (`/sys/bus/iio/devices`)
- Optional GPS backend for altitude and speed:
  - GeoClue 2 (`geoclue-2.0`) with location permission for the app
- Without a sensor the app runs in demo mode
- Without a GPS fix, altitude and speed stay visible and show **Kein GPS Signal**

## Install

```bash
./install.sh
```

Copies files to `~/.local/share/sensor-suite/`, installs the icon, and registers the app in your application menu. Does nothing if already installed.

## Uninstall

```bash
./uninstall.sh
```

## Run without installing

```bash
python3 sensor_suite.py
```

## Files

| File | Description |
|---|---|
| `sensor_suite.py` | Main app — Compass, Spirit Level, G-Force in one window |
| `gps.py` | Shared GeoClue GPS backend and unit formatting |
| `compass.py` | Standalone compass app |
| `spirit_level.py` | Standalone spirit level app |
| `acceleration.py` | Standalone G-force / accelerometer app |
| `install.sh` | Install to `~/.local/share` and register desktop entry |
| `uninstall.sh` | Remove installed files |

## Calibration

Tap the **Calibrate** button in the top-left of the header. A confirmation dialog appears before anything starts.

**Compass** — hold the device flat and slowly draw a figure-8 in the air until all three stars are filled. Tap *Skip* in the banner to abort at any time.

**Spirit Level** — place the device in the reference position, then tap the screen to set the zero point. Auto-calibration on startup can be enabled in Settings (⚙ top-right).

## Navigation and Settings

Use the bottom switcher or swipe horizontally to move between Compass, Spirit Level, and G-Force.

Open **Settings** from the menu in the top-right to change the theme, language, and unit system. Metric shows speed in km/h and altitude in meters; miles shows speed in mph and altitude in feet.

## GPS Display

The Compass page shows GPS altitude at the top. The G-Force page shows GPS speed prominently above the acceleration display.

If GeoClue has no location fix or permission is missing, the GPS value is shown as a colored placeholder with **Kein GPS Signal** below it.

## License

MIT
