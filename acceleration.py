#!/usr/bin/env python3
import sys, math, os, struct, socket as _socket, json
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

from gps import (GeoLocationBackend, GPS_OK_COLOR, GPS_WAITING_COLOR,
                 NO_GPS_SIGNAL, format_speed)

SERVICE     = "com.nokia.SensorService"
SOCKET_PATH = "/run/sensord.sock"
_HDR        = struct.Struct('<I')
_ACCEL      = struct.Struct('<Qfffi')

_SMOOTH = 0.28
CONFIG_DIR  = os.path.expanduser("~/.config/de.cais.SensorSuite")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")


def load_settings():
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(settings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(settings, f, indent=2)


class AccelBackend:
    def __init__(self):
        self._bus = self._sock = self._watch_id = self._session_id = None
        self._buf = b""
        self._available = False
        self._callback  = None
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            pid = os.getpid()
            self._call("/SensorManager", "local.SensorManager", "loadPlugin",
                       GLib.Variant("(s)", ("accelerometersensor",)))
            res = self._call("/SensorManager", "local.SensorManager", "requestSensor",
                             GLib.Variant("(sx)", ("accelerometersensor", pid)),
                             GLib.VariantType.new("(i)"))
            self._session_id = res.get_child_value(0).get_int32()
            self._sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            self._sock.connect(SOCKET_PATH)
            self._sock.send(struct.pack('<i', self._session_id))
            self._sock.recv(1)
            self._sock.setblocking(False)
            self._watch_id = GLib.io_add_watch(
                self._sock.fileno(), GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
                self._on_socket)
            self._call("/SensorManager/accelerometersensor",
                       "local.AccelerometerSensor", "setInterval",
                       GLib.Variant("(ii)", (self._session_id, 33)))
            self._call("/SensorManager/accelerometersensor",
                       "local.AccelerometerSensor", "start",
                       GLib.Variant("(i)", (self._session_id,)))
            self._available = True
        except Exception as e:
            print(f"AccelBackend: {e}")

    def _call(self, path, iface, method, args=None, reply_type=None):
        return self._bus.call_sync(SERVICE, path, iface, method, args,
                                   reply_type, Gio.DBusCallFlags.NONE, 3000, None)

    def _on_socket(self, fd, condition) -> bool:
        if condition & (GLib.IO_ERR | GLib.IO_HUP):
            return False
        try:
            self._buf += self._sock.recv(4096)
            while len(self._buf) >= 4:
                (count,) = _HDR.unpack_from(self._buf)
                need = 4 + count * _ACCEL.size
                if len(self._buf) < need:
                    break
                for i in range(count):
                    _, x, y, z, _ = _ACCEL.unpack_from(self._buf, 4 + i * _ACCEL.size)
                    if self._callback:
                        self._callback(x / 1000.0, y / 1000.0, z / 1000.0)
                self._buf = self._buf[need:]
        except BlockingIOError:
            pass
        except Exception as e:
            print(f"Accel error: {e}")
            return False
        return True

    def set_callback(self, cb): self._callback = cb

    @property
    def available(self): return self._available

    def close(self):
        if self._watch_id:   GLib.source_remove(self._watch_id)
        if self._sock:       self._sock.close()
        if self._bus and self._session_id is not None:
            pid = os.getpid()
            for m, p, i, a in [
                ("stop", "/SensorManager/accelerometersensor",
                 "local.AccelerometerSensor", GLib.Variant("(i)", (self._session_id,))),
                ("releaseSensor", "/SensorManager", "local.SensorManager",
                 GLib.Variant("(six)", ("accelerometersensor", self._session_id, pid))),
            ]:
                try: self._call(p, i, m, a)
                except Exception: pass


class GForceWidget(Gtk.DrawingArea):
    MAX_G = 2.0

    def __init__(self):
        super().__init__()
        self._x = self._y = self._z = 0.0
        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        self.set_vexpand(True)

    def update(self, x: float, y: float, z: float):
        self._x, self._y, self._z = x, y, z
        self.queue_draw()

    @staticmethod
    def _text_center(cr, text, tx, ty):
        ext = cr.text_extents(text)
        cr.move_to(tx - ext[2] / 2 - ext[0], ty - ext[3] / 2 - ext[1])
        cr.show_text(text)

    def _draw(self, area, cr, w, h):
        mag    = math.sqrt(self._x**2 + self._y**2 + self._z**2)
        dev    = abs(mag - 1.0)
        cx, cy = w / 2, h / 2

        if dev < 0.06:    r, g, b = 0.18, 0.78, 0.32
        elif dev < 0.40:  r, g, b = 0.95, 0.72, 0.05
        else:             r, g, b = 0.88, 0.18, 0.12

        margin   = min(w, h) * 0.185
        radius   = min(w, h) / 2 - margin
        if radius < 20:
            return

        fs_val   = max(radius * 0.150, 12.0)
        fs_ring  = max(radius * 0.090,  8.0)
        lc       = margin * 0.52

        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.5); cr.stroke()

        for ring_g, alpha, lw in ((0.5, 0.18, 0.9), (1.0, 0.42, 1.5)):
            r_px = (ring_g / self.MAX_G) * radius
            cr.arc(cx, cy, r_px, 0, 2 * math.pi)
            cr.set_source_rgba(r, g, b, alpha); cr.set_line_width(lw); cr.stroke()
            rx = cx + r_px * 0.690; ry = cy - r_px * 0.690
            cr.select_font_face("Sans", 0, 0); cr.set_font_size(fs_ring)
            cr.set_source_rgba(0.55, 0.55, 0.55, 0.70)
            self._text_center(cr, f"{ring_g:.1f}g", rx, ry)

        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            cr.move_to(cx + dx * radius * 0.06, cy + dy * radius * 0.06)
            cr.line_to(cx + dx * radius * 0.94, cy + dy * radius * 0.94)
        cr.set_source_rgba(0.40, 0.40, 0.40, 0.35); cr.set_line_width(1.0); cr.stroke()

        dot_r  = radius * 0.115
        nx     = self._x / self.MAX_G
        ny     = self._y / self.MAX_G
        dist_n = math.sqrt(nx**2 + ny**2)
        limit  = 1.0 - dot_r / radius
        if dist_n > limit:
            nx *= limit / dist_n; ny *= limit / dist_n
        dot_sx = cx + nx * radius
        dot_sy = cy - ny * radius

        cr.arc(dot_sx, dot_sy, dot_r, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.22); cr.fill()
        cr.arc(dot_sx, dot_sy, dot_r, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.95); cr.set_line_width(2.5); cr.stroke()
        cr.arc(dot_sx, dot_sy, dot_r * 0.35, 0, 2 * math.pi)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.25); cr.fill()

        def value_label(value_str, tx, ty):
            cr.select_font_face("Sans", 0, 0); cr.set_font_size(fs_val)
            cr.set_source_rgba(0.92, 0.92, 0.92, 1.0)
            self._text_center(cr, value_str, tx, ty)

        value_label(f"{self._x:+.2f}g", cx + radius + lc, cy)
        value_label(f"{self._y:+.2f}g", cx, cy - radius - lc)
        value_label(f"{self._z:+.2f}g", cx - radius - lc, cy)
        value_label(f"{mag:.2f}g",      cx, cy + radius + lc)


class AccelerationWindow(Adw.ApplicationWindow):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Acceleration")
        self.set_default_size(360, 640)

        self._settings = load_settings()
        self._x = self._y = self._z = 0.0
        self._tx = self._ty = self._tz = 0.0
        self._speed_mps = None
        self._backend    = None
        self._geo        = None
        self._anim_timer = None

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)
        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("Settings", "app.settings")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        toolbar_view.set_content(box)

        self._speed_value_label = Gtk.Label(label="--")
        self._speed_value_label.add_css_class("title-1")
        box.append(self._speed_value_label)

        self._speed_unit_label = Gtk.Label()
        self._speed_unit_label.add_css_class("title-4")
        self._speed_unit_label.add_css_class("dim-label")
        box.append(self._speed_unit_label)

        self._speed_status_label = Gtk.Label()
        self._speed_status_label.add_css_class("caption")
        self._speed_status_label.add_css_class("dim-label")
        box.append(self._speed_status_label)

        self._widget = GForceWidget()
        box.append(self._widget)
        self._update_speed_label()

        self._anim_timer = GLib.timeout_add(16, self._anim_tick)
        GLib.idle_add(self._init_sensor)

    def _init_sensor(self):
        self._backend = AccelBackend()
        if self._backend.available:
            self._backend.set_callback(self._on_accel)
            self._tx = self._ty = 0.0
            self._tz = 1.0
        else:
            GLib.timeout_add(33, self._demo_tick)
        self._geo = GeoLocationBackend("de.cais.Beschleunigung")
        if self._geo.available:
            self._geo.add_callback(self._on_location)
        return False

    def _demo_tick(self):
        t = GLib.get_monotonic_time() / 1_000_000
        self._tx = math.sin(t * 1.7) * 0.6
        self._ty = math.cos(t * 1.3) * 0.4
        self._tz = 1.0 + math.sin(t * 2.9) * 0.15
        return True

    def _on_accel(self, x: float, y: float, z: float):
        self._tx, self._ty, self._tz = x, y, z

    def _on_location(self, _altitude_m, speed_mps):
        self._speed_mps = speed_mps
        self._update_speed_label()

    def _anim_tick(self) -> bool:
        self._x += (self._tx - self._x) * _SMOOTH
        self._y += (self._ty - self._y) * _SMOOTH
        self._z += (self._tz - self._z) * _SMOOTH
        self._widget.update(self._x, self._y, self._z)
        self._update_speed_label()
        return True

    def _unit_system(self):
        return self._settings.get("unit_system", "metric")

    def _update_speed_label(self):
        has_speed = self._speed_mps is not None
        value, unit = format_speed(self._speed_mps, self._unit_system())
        color = GPS_OK_COLOR if has_speed else GPS_WAITING_COLOR
        self._speed_value_label.set_markup(
            f'<span foreground="{color}">{GLib.markup_escape_text(value)}</span>')
        self._speed_unit_label.set_text(unit)
        self._speed_status_label.set_text("" if has_speed else NO_GPS_SIGNAL)

    def open_settings(self):
        SettingsWindow(self, self._settings, self._on_settings_change).present()

    def _on_settings_change(self):
        self._update_speed_label()

    def do_close_request(self):
        if self._anim_timer:
            GLib.source_remove(self._anim_timer)
        if self._backend:
            self._backend.close()
        if self._geo:
            self._geo.close()
        return False


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, parent, settings, on_change):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Settings")
        self._settings = settings
        self._on_change = on_change

        page = Adw.PreferencesPage()
        self.add(page)
        group = Adw.PreferencesGroup(title="Units")
        page.add(group)

        units_row = Adw.ComboRow(title="Speed")
        units_row.set_model(Gtk.StringList.new(["Metric", "Miles"]))
        units_row.set_selected(0 if settings.get("unit_system", "metric") == "metric" else 1)
        units_row.connect("notify::selected", self._on_units)
        group.add(units_row)

    def _on_units(self, row, _):
        self._settings["unit_system"] = ["metric", "imperial"][row.get_selected()]
        save_settings(self._settings)
        self._on_change()


class AccelerationApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="de.cais.Beschleunigung",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate",
                     lambda app: AccelerationWindow(application=app).present())
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self._on_settings)
        self.add_action(settings_action)

    def _on_settings(self, _action, _param):
        win = self.get_active_window()
        if win:
            win.open_settings()


if __name__ == "__main__":
    sys.exit(AccelerationApp().run(sys.argv))
