import math

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gio


GEOCLUE_BUS = "org.freedesktop.GeoClue2"
GEOCLUE_MANAGER_PATH = "/org/freedesktop/GeoClue2/Manager"
GEOCLUE_MANAGER_IFACE = "org.freedesktop.GeoClue2.Manager"
GEOCLUE_CLIENT_IFACE = "org.freedesktop.GeoClue2.Client"
GEOCLUE_LOCATION_IFACE = "org.freedesktop.GeoClue2.Location"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
GPS_OK_COLOR = "#2ec27e"
GPS_WAITING_COLOR = "#f5c211"
NO_GPS_SIGNAL = "Kein GPS Signal"


def format_altitude(meters, unit_system="metric"):
    if meters is None or not math.isfinite(meters):
        unit = "ft" if unit_system == "imperial" else "m"
        label = "Altitude" if unit_system == "imperial" else "Höhe"
        return f"{label} -- {unit}"
    if unit_system == "imperial":
        return f"Altitude {meters * 3.28084:.0f} ft"
    return f"Höhe {meters:.0f} m"


def format_speed(meters_per_second, unit_system="metric"):
    if meters_per_second is None or not math.isfinite(meters_per_second):
        return ("--", "km/h" if unit_system == "metric" else "mph")
    if unit_system == "imperial":
        return (f"{meters_per_second * 2.236936:.0f}", "mph")
    return (f"{meters_per_second * 3.6:.0f}", "km/h")


class GeoLocationBackend:
    def __init__(self, desktop_id="de.cais.SensorSuite"):
        self._bus = self._manager = self._client = self._location = None
        self._client_path = None
        self._callbacks = []
        self._available = False
        self.altitude = None
        self.speed = None

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._manager = Gio.DBusProxy.new_sync(
                self._bus, Gio.DBusProxyFlags.NONE, None,
                GEOCLUE_BUS, GEOCLUE_MANAGER_PATH, GEOCLUE_MANAGER_IFACE, None)
            res = self._manager.call_sync(
                "GetClient", None, Gio.DBusCallFlags.NONE, 3000, None)
            self._client_path = res.get_child_value(0).get_string()
            self._client = Gio.DBusProxy.new_sync(
                self._bus, Gio.DBusProxyFlags.NONE, None,
                GEOCLUE_BUS, self._client_path, GEOCLUE_CLIENT_IFACE, None)
            self._configure_client(desktop_id)
            self._client.connect("g-signal", self._on_client_signal)
            self._client.call_sync("Start", None, Gio.DBusCallFlags.NONE, 3000, None)
            self._available = True
        except Exception as e:
            print(f"GeoLocationBackend not available: {e}")

    @property
    def available(self):
        return self._available

    def add_callback(self, cb):
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def _configure_client(self, desktop_id):
        self._set_client_property("DesktopId", GLib.Variant("s", desktop_id))
        for name, value in (
            ("RequestedAccuracyLevel", GLib.Variant("u", 8)),
            ("DistanceThreshold", GLib.Variant("u", 0)),
            ("TimeThreshold", GLib.Variant("u", 1)),
        ):
            try:
                self._set_client_property(name, value)
            except Exception:
                pass

    def _set_client_property(self, name, value):
        self._bus.call_sync(
            GEOCLUE_BUS, self._client_path, DBUS_PROPERTIES_IFACE, "Set",
            GLib.Variant("(ssv)", (GEOCLUE_CLIENT_IFACE, name, value)),
            None, Gio.DBusCallFlags.NONE, 3000, None)

    def _on_client_signal(self, _proxy, _sender, signal_name, params):
        if signal_name != "LocationUpdated":
            return
        location_path = params.get_child_value(1).get_string()
        self._read_location(location_path)

    def _read_location(self, location_path):
        self._location = Gio.DBusProxy.new_sync(
            self._bus, Gio.DBusProxyFlags.NONE, None,
            GEOCLUE_BUS, location_path, GEOCLUE_LOCATION_IFACE, None)
        altitude = self._get_location_double("Altitude")
        speed = self._get_location_double("Speed")
        if altitude is not None:
            self.altitude = altitude
        if speed is not None and speed >= 0:
            self.speed = speed
        for cb in self._callbacks:
            cb(self.altitude, self.speed)

    def _get_location_double(self, name):
        value = self._location.get_cached_property(name)
        if value is None:
            return None
        result = value.get_double()
        return result if math.isfinite(result) else None

    def close(self):
        if self._client:
            try:
                self._client.call_sync("Stop", None, Gio.DBusCallFlags.NONE, 1000, None)
            except Exception:
                pass
