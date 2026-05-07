#!/usr/bin/env python3
import sys
import math
import os
import struct
import socket as _socket
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

IIO_BASE = "/sys/bus/iio/devices"
MAGN_KEYWORDS = ("magn", "compass", "ak09", "ak8", "mmc56", "mmc34",
                 "lis3mdl", "lsm303", "bmm", "qmc", "icp", "hmc")

HADESS_BUS      = "net.hadess.SensorProxy"
HADESS_PATH     = "/net/hadess/SensorProxy"
HADESS_IFACE    = "net.hadess.SensorProxy"
HADESS_COMPASS  = "net.hadess.SensorProxy.Compass"


def _read_sysfs(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def find_iio_magnetometer() -> str | None:
    if not os.path.isdir(IIO_BASE):
        return None
    for entry in sorted(os.listdir(IIO_BASE)):
        dev_path = os.path.join(IIO_BASE, entry)
        name = _read_sysfs(os.path.join(dev_path, "name")) or ""
        if any(k in name.lower() for k in MAGN_KEYWORDS):
            if os.path.exists(os.path.join(dev_path, "in_magn_x_raw")):
                return dev_path
    return None


class IIOBackend:
    name = "IIO-Sysfs"

    def __init__(self, device_path: str):
        self._path = device_path
        s = _read_sysfs(os.path.join(device_path, "in_magn_x_scale"))
        self._scale = float(s) if s else 1.0
        self.label = _read_sysfs(os.path.join(device_path, "name")) or device_path

    def read_heading(self):
        rx = _read_sysfs(os.path.join(self._path, "in_magn_x_raw"))
        ry = _read_sysfs(os.path.join(self._path, "in_magn_y_raw"))
        if rx is None or ry is None:
            return None
        x = int(rx) * self._scale
        y = int(ry) * self._scale
        return (math.degrees(math.atan2(-y, x)) % 360, -1)

    def close(self):
        pass


class HadessBackend:
    name = "hadess D-Bus"

    def __init__(self):
        self._proxy = None
        self._compass_proxy = None
        self.label = "net.hadess.SensorProxy"
        self._heading = 0.0

        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                HADESS_BUS, HADESS_PATH, HADESS_IFACE, None,
            )
            self._proxy.call_sync("ClaimCompass", None, Gio.DBusCallFlags.NONE, 2000, None)
            self._compass_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                HADESS_BUS, HADESS_PATH, HADESS_COMPASS, None,
            )
            has = self._compass_proxy.get_cached_property("HasCompass")
            if not has or not has.get_boolean():
                raise RuntimeError("No compass via HasCompass property")
            self._compass_proxy.connect("g-properties-changed", self._on_props_changed)
        except Exception as e:
            print(f"hadess D-Bus not available: {e}")
            self._proxy = None
            self._compass_proxy = None

    @property
    def available(self) -> bool:
        return self._compass_proxy is not None

    def _on_props_changed(self, proxy, changed, invalidated):
        v = changed.lookup_value("CompassHeading", None)
        if v is not None:
            self._heading = v.get_double()

    def read_heading(self):
        if self._compass_proxy is None:
            return None
        v = self._compass_proxy.get_cached_property("CompassHeading")
        if v is not None:
            self._heading = v.get_double()
        return (self._heading % 360, -1)

    def close(self):
        if self._proxy:
            try:
                self._proxy.call_sync("ReleaseCompass", None, Gio.DBusCallFlags.NONE, 1000, None)
            except Exception:
                pass


class SensorfwBackend:
    SOCKET_PATH = "/run/sensord.sock"
    _HDR  = struct.Struct('<I')
    _CMP  = struct.Struct('<Qiiii')
    _SERVICE  = "com.nokia.SensorService"
    _MGR_PATH = "/SensorManager"
    _MGR_IF   = "local.SensorManager"
    _CMP_PATH = "/SensorManager/compasssensor"
    _CMP_IF   = "local.CompassSensor"

    def __init__(self):
        self._bus = self._sock = self._watch_id = self._session_id = None
        self._buf        = b""
        self._heading    = 0.0
        self._level      = 0
        self._available  = False
        self.label       = "sensorfwd"

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            pid = os.getpid()
            self._dbus(self._MGR_PATH, self._MGR_IF, "loadPlugin",
                       GLib.Variant("(s)", ("compasssensor",)))
            res = self._dbus(self._MGR_PATH, self._MGR_IF, "requestSensor",
                             GLib.Variant("(sx)", ("compasssensor", pid)),
                             reply_type=GLib.VariantType.new("(i)"))
            self._session_id = res.get_child_value(0).get_int32()
            self._sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            self._sock.connect(self.SOCKET_PATH)
            self._sock.send(struct.pack('<i', self._session_id))
            self._sock.recv(1)
            self._sock.setblocking(False)
            self._watch_id = GLib.io_add_watch(
                self._sock.fileno(), GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
                self._on_socket)
            self._dbus(self._CMP_PATH, self._CMP_IF, "setInterval",
                       GLib.Variant("(ii)", (self._session_id, 100)))
            self._dbus(self._CMP_PATH, self._CMP_IF, "start",
                       GLib.Variant("(i)", (self._session_id,)))
            self._available = True
            print(f"Backend: sensorfwd socket (session {self._session_id})")
        except Exception as e:
            print(f"sensorfwd not available: {e}")
            if self._sock:
                self._sock.close()
                self._sock = None

    def _dbus(self, path, iface, method, args=None, reply_type=None):
        return self._bus.call_sync(
            self._SERVICE, path, iface, method, args,
            reply_type, Gio.DBusCallFlags.NONE, 3000, None)

    def _on_socket(self, fd, condition) -> bool:
        if condition & (GLib.IO_ERR | GLib.IO_HUP):
            print("sensorfwd socket disconnected")
            return False
        try:
            chunk = self._sock.recv(4096)
            if not chunk:
                return False
            self._buf += chunk
            while len(self._buf) >= self._HDR.size:
                (count,) = self._HDR.unpack_from(self._buf)
                need = self._HDR.size + count * self._CMP.size
                if len(self._buf) < need:
                    break
                for i in range(count):
                    off = self._HDR.size + i * self._CMP.size
                    ts, deg, _raw, _north, lvl = self._CMP.unpack_from(self._buf, off)
                    self._heading = deg % 360
                    self._level = lvl
                self._buf = self._buf[need:]
        except BlockingIOError:
            pass
        except Exception as e:
            print(f"sensorfwd socket error: {e}")
            return False
        return True

    @property
    def available(self) -> bool:
        return self._available

    def read_heading(self) -> tuple[float, int] | None:
        return (self._heading, self._level)

    def close(self):
        if self._watch_id:
            GLib.source_remove(self._watch_id)
        if self._sock:
            self._sock.close()
        if self._bus and self._session_id is not None:
            pid = os.getpid()
            for method, path, iface, args in [
                ("stop",          self._CMP_PATH, self._CMP_IF,
                 GLib.Variant("(i)",   (self._session_id,))),
                ("releaseSensor", self._MGR_PATH, self._MGR_IF,
                 GLib.Variant("(six)", ("compasssensor", self._session_id, pid))),
            ]:
                try:
                    self._dbus(path, iface, method, args)
                except Exception:
                    pass


class SensorPoller:
    POLL_MS = 100

    def __init__(self, on_heading_changed):
        self._cb = on_heading_changed
        self._timer = None
        self._backend = None

        iio_path = find_iio_magnetometer()
        if iio_path:
            self._backend = IIOBackend(iio_path)
            print(f"Backend: IIO-Sysfs ({self._backend.label})")
        else:
            hb = HadessBackend()
            if hb.available:
                self._backend = hb
                print(f"Backend: hadess D-Bus ({hb.label})")
            else:
                sf = SensorfwBackend()
                if sf.available:
                    self._backend = sf

        if self._backend:
            self._timer = GLib.timeout_add(self.POLL_MS, self._tick)
        else:
            print("No sensor backend found → demo mode")

    def _tick(self) -> bool:
        result = self._backend.read_heading()
        if result is not None:
            heading, level = result
            self._cb(heading, level, True)
        return True

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def label(self) -> str:
        return self._backend.label if self._backend else ""

    def release(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
        if self._backend:
            self._backend.close()


class CompassWidget(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self._heading = 0.0
        self._has_sensor = False
        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        self.set_vexpand(True)

    def set_heading(self, degrees: float, has_sensor: bool = True):
        self._heading = degrees % 360
        self._has_sensor = has_sensor
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 * 0.88

        color = self.get_color()
        fg_r, fg_g, fg_b = color.red, color.green, color.blue

        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgba(0.12, 0.12, 0.14, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(fg_r, fg_g, fg_b, 0.15)
        cr.set_line_width(2)
        cr.stroke()

        for deg in range(0, 360, 5):
            angle = math.radians(deg - self._heading - 90)
            is_cardinal = deg % 90 == 0
            is_major = deg % 10 == 0
            tick_len = radius * (0.12 if is_cardinal else (0.08 if is_major else 0.04))
            r_outer = radius * 0.95
            r_inner = r_outer - tick_len
            x1 = cx + r_outer * math.cos(angle)
            y1 = cy + r_outer * math.sin(angle)
            x2 = cx + r_inner * math.cos(angle)
            y2 = cy + r_inner * math.sin(angle)
            if is_cardinal:
                cr.set_source_rgba(0.95, 0.3, 0.25, 1.0)
                cr.set_line_width(2.5)
            elif is_major:
                cr.set_source_rgba(fg_r, fg_g, fg_b, 0.7)
                cr.set_line_width(1.5)
            else:
                cr.set_source_rgba(fg_r, fg_g, fg_b, 0.35)
                cr.set_line_width(1.0)
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()

        cr.set_font_size(radius * 0.13)
        for label, deg in [("N", 0), ("E", 90), ("S", 180), ("W", 270)]:
            angle = math.radians(deg - self._heading - 90)
            tx = cx + radius * 0.75 * math.cos(angle)
            ty = cy + radius * 0.75 * math.sin(angle)
            ext = cr.text_extents(label)
            cr.move_to(tx - ext.width / 2, ty + ext.height / 2)
            if label == "N":
                cr.set_source_rgba(0.95, 0.3, 0.25, 1.0)
            else:
                cr.set_source_rgba(fg_r, fg_g, fg_b, 0.9)
            cr.show_text(label)

        needle_len = radius * 0.55
        needle_width = radius * 0.045

        angle_n = math.radians(-self._heading - 90)
        cr.move_to(cx + needle_len * math.cos(angle_n), cy + needle_len * math.sin(angle_n))
        cr.line_to(cx + needle_width * math.cos(angle_n + math.pi / 2),
                   cy + needle_width * math.sin(angle_n + math.pi / 2))
        cr.line_to(cx + (needle_len * 0.35) * math.cos(angle_n + math.pi),
                   cy + (needle_len * 0.35) * math.sin(angle_n + math.pi))
        cr.line_to(cx + needle_width * math.cos(angle_n - math.pi / 2),
                   cy + needle_width * math.sin(angle_n - math.pi / 2))
        cr.close_path()
        cr.set_source_rgba(0.92, 0.25, 0.2, 1.0)
        cr.fill()

        angle_s = math.radians(-self._heading + 90)
        cr.move_to(cx + needle_len * math.cos(angle_s), cy + needle_len * math.sin(angle_s))
        cr.line_to(cx + needle_width * math.cos(angle_s + math.pi / 2),
                   cy + needle_width * math.sin(angle_s + math.pi / 2))
        cr.line_to(cx + (needle_len * 0.35) * math.cos(angle_s + math.pi),
                   cy + (needle_len * 0.35) * math.sin(angle_s + math.pi))
        cr.line_to(cx + needle_width * math.cos(angle_s - math.pi / 2),
                   cy + needle_width * math.sin(angle_s - math.pi / 2))
        cr.close_path()
        cr.set_source_rgba(0.95, 0.95, 0.95, 0.92)
        cr.fill()

        cr.arc(cx, cy, radius * 0.055, 0, 2 * math.pi)
        cr.set_source_rgba(0.2, 0.2, 0.22, 1.0)
        cr.fill()
        cr.arc(cx, cy, radius * 0.04, 0, 2 * math.pi)
        cr.set_source_rgba(0.85, 0.85, 0.85, 1.0)
        cr.fill()

        if not self._has_sensor:
            cr.set_font_size(radius * 0.07)
            msg = "No sensor"
            ext = cr.text_extents(msg)
            cr.move_to(cx - ext.width / 2, cy + radius * 0.35)
            cr.set_source_rgba(1.0, 0.75, 0.1, 0.85)
            cr.show_text(msg)


class CompassWindow(Adw.ApplicationWindow):

    _CALIB_STARS = ["○○○", "●○○", "●●○", "●●●"]
    _CALIB_HINT  = [
        "Draw a figure-8 — tilt device in all directions",
        "Good — repeat the figure-8 one or two more times",
        "Almost done — one more round",
        None,
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._target      = 0.0
        self._display     = 0.0
        self._sensor      = None
        self._demo_timer  = None
        self._anim_timer  = None
        self._calibrating         = False
        self._cal_seen_incomplete = False

        self.set_title("Compass")
        self.set_default_size(360, 640)

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        cal_btn = Gtk.Button(label="Calibrate")
        cal_btn.connect("clicked", self._on_calibrate_clicked)
        header.pack_start(cal_btn)

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(self._build_menu())
        header.pack_end(menu_btn)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(24)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        toolbar_view.set_content(content_box)

        self._compass = CompassWidget()
        self._compass.set_size_request(280, 280)
        content_box.append(self._compass)

        self._heading_label = Gtk.Label(label="0°")
        self._heading_label.add_css_class("title-1")
        self._heading_label.set_margin_top(16)
        content_box.append(self._heading_label)

        self._cardinal_label = Gtk.Label(label="North")
        self._cardinal_label.add_css_class("title-3")
        self._cardinal_label.add_css_class("dim-label")
        self._cardinal_label.set_margin_top(4)
        content_box.append(self._cardinal_label)

        self._calib_bar = Adw.Banner()
        self._calib_bar.connect("button-clicked", self._on_calib_bar_button)
        self._calib_bar.set_revealed(False)
        content_box.append(self._calib_bar)

        self._calib_label = Gtk.Label()
        self._calib_label.add_css_class("caption")
        self._calib_label.add_css_class("dim-label")
        self._calib_label.set_margin_top(4)
        content_box.append(self._calib_label)

        self._sensor_label = Gtk.Label()
        self._sensor_label.add_css_class("caption")
        self._sensor_label.add_css_class("dim-label")
        self._sensor_label.set_margin_top(8)
        content_box.append(self._sensor_label)

        self._anim_timer = GLib.timeout_add(16, self._anim_tick)
        GLib.idle_add(self._init_sensor)

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append("About Compass", "app.about")
        return menu

    def _on_calibrate_clicked(self, _btn):
        dialog = Adw.AlertDialog(
            heading="Calibrate Compass",
            body="Hold the device flat and slowly draw a figure-8 in the air until all three stars are filled.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("start", "Start")
        dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("start")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_cal_dialog_response)
        dialog.present(self)

    def _on_cal_dialog_response(self, _dialog, response):
        if response != "start":
            return
        if self._sensor:
            self._sensor.release()
            self._sensor = None
        self._calib_label.set_text(f"Calibration {self._CALIB_STARS[0]}")
        self._calib_bar.set_title("Hold device flat — slowly draw a figure-8 in the air")
        self._calib_bar.set_button_label("Skip")
        self._calib_bar.set_revealed(True)
        self._calibrating = True
        self._cal_seen_incomplete = False
        GLib.timeout_add(300, lambda: self._restart_sensor() or False)

    def _on_calib_bar_button(self, banner):
        banner.set_revealed(False)
        self._calibrating = False

    def _restart_sensor(self):
        self._sensor = SensorPoller(self._on_heading)
        if self._sensor.available:
            self._sensor_label.set_text(self._sensor.label)
        else:
            self._sensor_label.set_text("Demo mode — no magnetometer found")

    def _init_sensor(self):
        self._sensor = SensorPoller(self._on_heading)
        if not self._sensor.available:
            self._sensor_label.set_text("Demo mode — no magnetometer found")
            self._calib_label.set_text("")
            self._demo_timer = GLib.timeout_add(50, self._demo_tick)
            self._compass.set_heading(0.0, has_sensor=False)
        else:
            self._sensor_label.set_text(self._sensor.label)
        return False

    def _anim_tick(self) -> bool:
        diff = (self._target - self._display + 180) % 360 - 180
        self._display = (self._display + diff * 0.18) % 360
        self._compass.set_heading(self._display, self._sensor is not None and self._sensor.available)
        self._heading_label.set_text(f"{self._display:.0f}°")
        self._cardinal_label.set_text(self._to_cardinal(self._display))
        return True

    def _demo_tick(self):
        self._target = (self._target + 1.5) % 360
        return True

    def _on_heading(self, degrees: float, level: int, has_sensor: bool):
        self._target = degrees
        if level < 0:
            return
        lvl = min(level, 3)
        self._calib_label.set_text(f"Calibration {self._CALIB_STARS[lvl]}")

        if self._calibrating:
            hint = self._CALIB_HINT[lvl]
            if hint is not None:
                self._cal_seen_incomplete = True
                self._calib_bar.set_title(f"{self._CALIB_STARS[lvl]}  {hint}")
                self._calib_bar.set_revealed(True)
            elif self._cal_seen_incomplete:
                self._calibrating = False
                self._calib_bar.set_button_label("OK")
                self._calib_bar.set_title("✓ Calibration complete")
                self._calib_bar.set_revealed(True)
                GLib.timeout_add(2500, lambda: self._calib_bar.set_revealed(False) or False)

    @staticmethod
    def _to_cardinal(deg: float) -> str:
        directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                      "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        return directions[round(deg / 22.5) % 16]

    def do_close_request(self):
        if self._anim_timer:
            GLib.source_remove(self._anim_timer)
            self._anim_timer = None
        if self._demo_timer:
            GLib.source_remove(self._demo_timer)
            self._demo_timer = None
        if self._sensor:
            self._sensor.release()
        return False


class CompassApp(Adw.Application):

    def __init__(self):
        super().__init__(
            application_id="de.cais.Kompass",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.connect("activate", self._on_activate)
        self._add_actions()

    def _add_actions(self):
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def _on_activate(self, app):
        CompassWindow(application=app).present()

    def _on_about(self, action, param):
        dialog = Adw.AboutDialog()
        dialog.set_application_name("Compass")
        dialog.set_application_icon("find-location-symbolic")
        dialog.set_developer_name("Chris")
        dialog.set_version("1.0")
        dialog.set_comments("Compass app for Phosh / Linux Mobile")
        dialog.set_license("MIT License")
        dialog.present(self.get_active_window())


def main():
    return CompassApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
