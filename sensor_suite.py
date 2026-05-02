#!/usr/bin/env python3
import sys, math, os, struct, socket as _socket, json
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

# ── Constants ──────────────────────────────────────────────────────────────────
IIO_BASE       = "/sys/bus/iio/devices"
MAGN_KEYWORDS  = ("magn", "compass", "ak09", "ak8", "mmc56", "mmc34",
                  "lis3mdl", "lsm303", "bmm", "qmc", "icp", "hmc")
HADESS_BUS     = "net.hadess.SensorProxy"
HADESS_PATH    = "/net/hadess/SensorProxy"
HADESS_IFACE   = "net.hadess.SensorProxy"
HADESS_COMPASS = "net.hadess.SensorProxy.Compass"
SENSOR_SVC     = "com.nokia.SensorService"
SOCKET_PATH    = "/run/sensord.sock"
_HDR           = struct.Struct('<I')
_CMP           = struct.Struct('<Qiiii')
_ACCEL         = struct.Struct('<Qfffi')
CONFIG_DIR     = os.path.expanduser("~/.config/de.cais.SensorSuite")
CONFIG_FILE    = os.path.join(CONFIG_DIR, "settings.json")

# ── i18n ───────────────────────────────────────────────────────────────────────
_T = {
    "level":    {"de": "eben",               "en": "level"},
    "slight":   {"de": "leicht geneigt",     "en": "slightly tilted"},
    "tilted":   {"de": "geneigt",            "en": "tilted"},
    "cal_done": {"de": "Nullpunkt gesetzt",  "en": "Zero point set"},
    "s_ttl":    {"de": "Einstellungen",      "en": "Settings"},
    "s_appear": {"de": "Darstellung",        "en": "Appearance"},
    "s_theme":  {"de": "Design",             "en": "Theme"},
    "s_t_auto": {"de": "Automatisch",        "en": "Automatic"},
    "s_t_lt":   {"de": "Hell",               "en": "Light"},
    "s_t_dk":   {"de": "Dunkel",             "en": "Dark"},
    "s_lang":   {"de": "Sprache",            "en": "Language"},
    "s_cal":    {"de": "Kalibrierung",       "en": "Calibration"},
    "s_c_ttl":  {"de": "Nullpunkt setzen",   "en": "Set zero point"},
    "s_c_sub":  {"de": "Aktuelle Lage als Referenz übernehmen",
                 "en": "Use current position as reference"},
    "s_c_btn":  {"de": "Kalibrieren",        "en": "Calibrate"},
    "s_ac":     {"de": "Automatisch beim Start", "en": "Auto-calibrate on startup"},
    "s_ac_sub": {"de": "Gerät beim Start kurz flach halten",
                 "en": "Keep device flat briefly at startup"},
    "cal_tap":  {"de": "Bildschirm antippen – Nullpunkt setzen",
                 "en": "Tap screen to set zero point"},
}

def _(key, lang): return _T.get(key, {}).get(lang, key)

def load_settings():
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("settings must be a JSON object")
        return data
    except Exception:
        return {"theme": "auto", "lang": "en", "auto_cal": True}

def save_settings(s):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(s, f, indent=2)

def apply_theme(theme: str):
    Adw.StyleManager.get_default().set_color_scheme({
        "dark":  Adw.ColorScheme.FORCE_DARK,
        "light": Adw.ColorScheme.FORCE_LIGHT,
    }.get(theme, Adw.ColorScheme.DEFAULT))

def _read_sysfs(path):
    try:
        with open(path) as f: return f.read().strip()
    except OSError: return None


# ── Compass backends ───────────────────────────────────────────────────────────

def find_iio_magnetometer():
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

    def __init__(self, device_path):
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

    def close(self): pass


class HadessBackend:
    name = "hadess D-Bus"

    def __init__(self):
        self._proxy = self._compass_proxy = None
        self.label  = "net.hadess.SensorProxy"
        self._heading = 0.0
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                HADESS_BUS, HADESS_PATH, HADESS_IFACE, None)
            self._proxy.call_sync("ClaimCompass", None, Gio.DBusCallFlags.NONE, 2000, None)
            self._compass_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                HADESS_BUS, HADESS_PATH, HADESS_COMPASS, None)
            has = self._compass_proxy.get_cached_property("HasCompass")
            if not has or not has.get_boolean():
                raise RuntimeError("No compass")
            self._compass_proxy.connect("g-properties-changed", self._on_props_changed)
        except Exception as e:
            print(f"hadess D-Bus not available: {e}")
            self._proxy = self._compass_proxy = None

    @property
    def available(self): return self._compass_proxy is not None

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
                self._proxy.call_sync("ReleaseCompass", None,
                                      Gio.DBusCallFlags.NONE, 1000, None)
            except Exception: pass


class SensorfwBackend:
    _SERVICE  = SENSOR_SVC
    _MGR_PATH = "/SensorManager"
    _MGR_IF   = "local.SensorManager"
    _CMP_PATH = "/SensorManager/compasssensor"
    _CMP_IF   = "local.CompassSensor"

    def __init__(self):
        self._bus = self._sock = self._watch_id = self._session_id = None
        self._buf = b""
        self._heading = 0.0
        self._level   = 0
        self._available = False
        self.label = "sensorfwd"
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
            self._sock.connect(SOCKET_PATH)
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
            print(f"Compass: sensorfwd (session {self._session_id})")
        except Exception as e:
            print(f"sensorfwd compass not available: {e}")
            if self._sock:
                self._sock.close()
                self._sock = None

    def _dbus(self, path, iface, method, args=None, reply_type=None):
        return self._bus.call_sync(
            self._SERVICE, path, iface, method, args,
            reply_type, Gio.DBusCallFlags.NONE, 3000, None)

    def _on_socket(self, fd, condition) -> bool:
        if condition & (GLib.IO_ERR | GLib.IO_HUP):
            return False
        try:
            chunk = self._sock.recv(4096)
            if not chunk: return False
            self._buf += chunk
            while len(self._buf) >= _HDR.size:
                (count,) = _HDR.unpack_from(self._buf)
                need = _HDR.size + count * _CMP.size
                if len(self._buf) < need: break
                for i in range(count):
                    off = _HDR.size + i * _CMP.size
                    _, deg, _raw, _north, lvl = _CMP.unpack_from(self._buf, off)
                    self._heading = deg % 360
                    self._level = lvl
                self._buf = self._buf[need:]
        except BlockingIOError: pass
        except Exception as e:
            print(f"sensorfwd socket error: {e}")
            return False
        return True

    @property
    def available(self): return self._available

    def read_heading(self): return (self._heading, self._level)

    def close(self):
        if self._watch_id: GLib.source_remove(self._watch_id)
        if self._sock: self._sock.close()
        if self._bus and self._session_id is not None:
            pid = os.getpid()
            for method, path, iface, args in [
                ("stop",          self._CMP_PATH, self._CMP_IF,
                 GLib.Variant("(i)",   (self._session_id,))),
                ("releaseSensor", self._MGR_PATH, self._MGR_IF,
                 GLib.Variant("(six)", ("compasssensor", self._session_id, pid))),
            ]:
                try: self._dbus(path, iface, method, args)
                except Exception: pass


class SensorPoller:
    POLL_MS = 100

    def __init__(self, on_heading_changed):
        self._cb = on_heading_changed
        self._timer = self._backend = None
        iio_path = find_iio_magnetometer()
        if iio_path:
            self._backend = IIOBackend(iio_path)
        else:
            hb = HadessBackend()
            if hb.available:
                self._backend = hb
            else:
                sf = SensorfwBackend()
                if sf.available:
                    self._backend = sf
        if self._backend:
            self._timer = GLib.timeout_add(self.POLL_MS, self._tick)
        else:
            print("No compass backend → demo mode")

    def _tick(self):
        result = self._backend.read_heading()
        if result is not None:
            heading, level = result
            self._cb(heading, level, True)
        return True

    @property
    def available(self): return self._backend is not None

    @property
    def label(self): return self._backend.label if self._backend else ""

    def release(self):
        if self._timer: GLib.source_remove(self._timer)
        if self._backend: self._backend.close()


# ── Accelerometer backend (shared, fan-out) ────────────────────────────────────

class AccelBackend:
    def __init__(self):
        self._callbacks  = []
        self._bus = self._sock = self._watch_id = self._session_id = None
        self._buf = b""
        self._available  = False
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
            print(f"Accel: sensorfwd (session {self._session_id})")
        except Exception as e:
            print(f"AccelBackend not available: {e}")

    def _call(self, path, iface, method, args=None, reply_type=None):
        return self._bus.call_sync(SENSOR_SVC, path, iface, method, args,
                                   reply_type, Gio.DBusCallFlags.NONE, 3000, None)

    def _on_socket(self, fd, condition) -> bool:
        if condition & (GLib.IO_ERR | GLib.IO_HUP): return False
        try:
            self._buf += self._sock.recv(4096)
            while len(self._buf) >= 4:
                (count,) = _HDR.unpack_from(self._buf)
                need = 4 + count * _ACCEL.size
                if len(self._buf) < need: break
                last_xyz = None
                for i in range(count):
                    _, x, y, z, _ = _ACCEL.unpack_from(self._buf, 4 + i * _ACCEL.size)
                    last_xyz = (x, y, z)
                self._buf = self._buf[need:]
                if last_xyz:
                    for cb in self._callbacks:
                        cb(*last_xyz)
        except BlockingIOError: pass
        except Exception as e:
            print(f"Accel socket error: {e}")
            return False
        return True

    def add_callback(self, cb):
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    @property
    def available(self): return self._available

    def close(self):
        if self._watch_id: GLib.source_remove(self._watch_id)
        if self._sock:     self._sock.close()
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


# ── Drawing widgets ────────────────────────────────────────────────────────────

class CompassWidget(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self._heading = 0.0
        self._has_sensor = False
        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        self.set_vexpand(True)

    def set_heading(self, degrees, has_sensor=True):
        self._heading = degrees % 360
        self._has_sensor = has_sensor
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 * 0.88
        color  = self.get_color()
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
            is_major    = deg % 10 == 0
            tick_len    = radius * (0.12 if is_cardinal else (0.08 if is_major else 0.04))
            r_outer = radius * 0.95
            r_inner = r_outer - tick_len
            x1 = cx + r_outer * math.cos(angle);  y1 = cy + r_outer * math.sin(angle)
            x2 = cx + r_inner * math.cos(angle);  y2 = cy + r_inner * math.sin(angle)
            if is_cardinal:
                cr.set_source_rgba(0.95, 0.3, 0.25, 1.0);  cr.set_line_width(2.5)
            elif is_major:
                cr.set_source_rgba(fg_r, fg_g, fg_b, 0.7);  cr.set_line_width(1.5)
            else:
                cr.set_source_rgba(fg_r, fg_g, fg_b, 0.35); cr.set_line_width(1.0)
            cr.move_to(x1, y1); cr.line_to(x2, y2); cr.stroke()

        cr.set_font_size(radius * 0.13)
        for label, deg in [("N", 0), ("E", 90), ("S", 180), ("W", 270)]:
            angle = math.radians(deg - self._heading - 90)
            tx = cx + radius * 0.75 * math.cos(angle)
            ty = cy + radius * 0.75 * math.sin(angle)
            ext = cr.text_extents(label)
            cr.move_to(tx - ext.width / 2, ty + ext.height / 2)
            cr.set_source_rgba(0.95, 0.3, 0.25, 1.0) if label == "N" \
                else cr.set_source_rgba(fg_r, fg_g, fg_b, 0.9)
            cr.show_text(label)

        nlen = radius * 0.55
        nw   = radius * 0.045
        for color_rgba, base_angle in [
            ((0.92, 0.25, 0.2, 1.0),      math.radians(-self._heading - 90)),
            ((0.95, 0.95, 0.95, 0.92),    math.radians(-self._heading + 90)),
        ]:
            a = base_angle
            cr.move_to(cx + nlen * math.cos(a),          cy + nlen * math.sin(a))
            cr.line_to(cx + nw   * math.cos(a+math.pi/2), cy + nw   * math.sin(a+math.pi/2))
            cr.line_to(cx + nlen * 0.35 * math.cos(a+math.pi), cy + nlen * 0.35 * math.sin(a+math.pi))
            cr.line_to(cx + nw   * math.cos(a-math.pi/2), cy + nw   * math.sin(a-math.pi/2))
            cr.close_path()
            cr.set_source_rgba(*color_rgba)
            cr.fill()

        cr.arc(cx, cy, radius * 0.055, 0, 2 * math.pi)
        cr.set_source_rgba(0.2, 0.2, 0.22, 1.0); cr.fill()
        cr.arc(cx, cy, radius * 0.04,  0, 2 * math.pi)
        cr.set_source_rgba(0.85, 0.85, 0.85, 1.0); cr.fill()

        if not self._has_sensor:
            cr.set_font_size(radius * 0.07)
            msg = "No sensor"
            ext = cr.text_extents(msg)
            cr.move_to(cx - ext.width / 2, cy + radius * 0.35)
            cr.set_source_rgba(1.0, 0.75, 0.1, 0.85)
            cr.show_text(msg)


def _bubble_color(tilt):
    if tilt < 1.0:  return 0.18, 0.78, 0.32
    if tilt < 3.0:  return 0.95, 0.72, 0.05
    return 0.88, 0.18, 0.12

def _draw_bubble(cr, bx, by, br, r, g, b):
    cr.arc(bx, by, br, 0, 2*math.pi)
    cr.set_source_rgba(r, g, b, 0.28); cr.fill()
    cr.arc(bx, by, br, 0, 2*math.pi)
    cr.set_source_rgba(r, g, b, 0.95); cr.set_line_width(2.5); cr.stroke()
    cr.arc(bx, by, br * 0.30, 0, 2*math.pi)
    cr.set_source_rgba(1, 1, 1, 0.25); cr.fill()

def _rounded_rect(cr, x, y, w, h, rad):
    cr.new_sub_path()
    cr.arc(x+w-rad, y+rad,   rad, -math.pi/2, 0)
    cr.arc(x+w-rad, y+h-rad, rad, 0, math.pi/2)
    cr.arc(x+rad,   y+h-rad, rad, math.pi/2, math.pi)
    cr.arc(x+rad,   y+rad,   rad, math.pi, 3*math.pi/2)
    cr.close_path()


class LevelWidget(Gtk.DrawingArea):
    MAX_ANGLE = 15.0

    def __init__(self):
        super().__init__()
        self._roll = self._pitch = 0.0
        self._cal_roll = self._cal_pitch = 0.0
        self._target_roll = self._target_pitch = 0.0
        self.set_draw_func(self._draw)
        self.set_hexpand(True); self.set_vexpand(True)

    def set_raw_tilt(self, roll, pitch):
        self._target_roll  = roll  - self._cal_roll
        self._target_pitch = pitch - self._cal_pitch

    def smooth_tick(self):
        dr = (self._target_roll  - self._roll)  * 0.20
        dp = (self._target_pitch - self._pitch) * 0.20
        if abs(dr) > 0.01 or abs(dp) > 0.01:
            self._roll += dr; self._pitch += dp; self.queue_draw()

    def calibrate(self):
        self._cal_roll  += self._roll;  self._cal_pitch += self._pitch
        self._roll = self._pitch = self._target_roll = self._target_pitch = 0.0
        self.queue_draw()

    def get_cal(self):       return (self._cal_roll, self._cal_pitch)
    def set_cal(self, r, p): self._cal_roll = r; self._cal_pitch = p

    @property
    def total_tilt(self): return math.sqrt(self._roll**2 + self._pitch**2)

    def _draw(self, area, cr, w, h):
        cx, cy = w/2, h/2
        radius = min(w, h) / 2 * 0.85
        br     = radius * 0.14
        roll   = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._roll))
        pitch  = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._pitch))
        bx     = cx + (roll  / self.MAX_ANGLE) * (radius - br)
        by     = cy - (pitch / self.MAX_ANGLE) * (radius - br)
        r, g, b = _bubble_color(self.total_tilt)

        cr.arc(cx, cy, radius, 0, 2*math.pi)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.5); cr.stroke()

        cr.arc(cx, cy, radius * 0.13, 0, 2*math.pi)
        cr.set_source_rgba(r, g, b, 0.18); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.55); cr.set_line_width(1.5); cr.stroke()

        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
            s, e = radius * 0.17, radius * 0.92
            cr.move_to(cx+dx*s, cy+dy*s); cr.line_to(cx+dx*e, cy+dy*e)
        cr.set_source_rgba(0.45, 0.45, 0.45, 0.4); cr.set_line_width(1.0); cr.stroke()
        _draw_bubble(cr, bx, by, br, r, g, b)


class LinearLevelWidget(Gtk.DrawingArea):
    MAX_ANGLE = 10.0

    def __init__(self):
        super().__init__()
        self._angle = self._target = self._cal_offset = 0.0
        self.set_draw_func(self._draw)
        self.set_hexpand(True); self.set_vexpand(False)
        self.set_size_request(-1, 116)

    def set_raw_angle(self, a): self._target = a - self._cal_offset

    def smooth_tick(self):
        d = (self._target - self._angle) * 0.20
        if abs(d) > 0.01: self._angle += d; self.queue_draw()

    def calibrate(self):
        self._cal_offset += self._angle
        self._angle = self._target = 0.0; self.queue_draw()

    def get_cal(self):    return self._cal_offset
    def set_cal(self, v): self._cal_offset = v

    @property
    def tilt(self): return abs(self._angle)

    def _draw(self, area, cr, w, h):
        cx, cy = w/2, h/2
        tw     = w * 0.86
        th     = min(h * 0.54, 68.0)
        br     = th * 0.42
        tx, ty = cx - tw/2, cy - th/2
        max_tr = tw/2 - br
        r, g, b = _bubble_color(self.tilt)

        _rounded_rect(cr, tx, ty, tw, th, th/2)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.0); cr.stroke()

        cr.move_to(cx, ty+th*0.10); cr.line_to(cx, ty+th*0.90)
        cr.set_source_rgba(r, g, b, 0.70); cr.set_line_width(2.0); cr.stroke()

        for deg in (1.0, 2.0, 3.0):
            off = (deg / self.MAX_ANGLE) * max_tr
            f   = 0.22 if deg != 2.0 else 0.16
            for sign in (-1, 1):
                x = cx + sign * off
                cr.move_to(x, ty+th*f); cr.line_to(x, ty+th*(1-f))
            cr.set_source_rgba(0.45, 0.45, 0.45, 0.35); cr.set_line_width(1.0); cr.stroke()

        a  = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._angle))
        bx = cx + (a / self.MAX_ANGLE) * max_tr
        _draw_bubble(cr, bx, cy, br, r, g, b)


class VerticalLevelWidget(Gtk.DrawingArea):
    MAX_ANGLE = 10.0

    def __init__(self):
        super().__init__()
        self._angle = self._target = self._cal_offset = 0.0
        self.set_draw_func(self._draw)
        self.set_hexpand(False); self.set_vexpand(True)
        self.set_size_request(68, -1)

    def set_raw_angle(self, a): self._target = a - self._cal_offset

    def smooth_tick(self):
        d = (self._target - self._angle) * 0.20
        if abs(d) > 0.01: self._angle += d; self.queue_draw()

    def calibrate(self):
        self._cal_offset += self._angle
        self._angle = self._target = 0.0; self.queue_draw()

    def get_cal(self):    return self._cal_offset
    def set_cal(self, v): self._cal_offset = v

    @property
    def tilt(self): return abs(self._angle)

    def _draw(self, area, cr, w, h):
        cx, cy = w/2, h/2
        th     = h * 0.82
        tw     = min(w * 0.68, 48.0)
        br     = tw * 0.42
        tx, ty = cx - tw/2, cy - th/2
        max_tr = th/2 - br
        r, g, b = _bubble_color(self.tilt)

        _rounded_rect(cr, tx, ty, tw, th, tw/2)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.0); cr.stroke()

        cr.move_to(tx+tw*0.10, cy); cr.line_to(tx+tw*0.90, cy)
        cr.set_source_rgba(r, g, b, 0.70); cr.set_line_width(2.0); cr.stroke()

        for deg in (1.0, 2.0, 3.0):
            off = (deg / self.MAX_ANGLE) * max_tr
            f   = 0.22 if deg != 2.0 else 0.16
            for sign in (-1, 1):
                y = cy + sign * off
                cr.move_to(tx+tw*f, y); cr.line_to(tx+tw*(1-f), y)
            cr.set_source_rgba(0.45, 0.45, 0.45, 0.35); cr.set_line_width(1.0); cr.stroke()

        a  = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._angle))
        by = cy - (a / self.MAX_ANGLE) * max_tr
        _draw_bubble(cr, cx, by, br, r, g, b)


class GForceWidget(Gtk.DrawingArea):
    MAX_G = 2.0

    def __init__(self):
        super().__init__()
        self._x = self._y = self._z = 0.0
        self.set_draw_func(self._draw)
        self.set_hexpand(True); self.set_vexpand(True)

    def update(self, x, y, z):
        self._x, self._y, self._z = x, y, z
        self.queue_draw()

    @staticmethod
    def _text_center(cr, text, tx, ty):
        ext = cr.text_extents(text)
        cr.move_to(tx - ext[2]/2 - ext[0], ty - ext[3]/2 - ext[1])
        cr.show_text(text)

    def _draw(self, area, cr, w, h):
        mag    = math.sqrt(self._x**2 + self._y**2 + self._z**2)
        dev    = abs(mag - 1.0)
        cx, cy = w / 2, h / 2

        if dev < 0.06:   r, g, b = 0.18, 0.78, 0.32
        elif dev < 0.40: r, g, b = 0.95, 0.72, 0.05
        else:            r, g, b = 0.88, 0.18, 0.12

        margin = min(w, h) * 0.185
        radius = min(w, h) / 2 - margin
        if radius < 20: return

        fs_axis = max(radius * 0.115, 10.0)
        fs_val  = max(radius * 0.150, 12.0)
        fs_ring = max(radius * 0.090,  8.0)
        lc      = margin * 0.52

        cr.arc(cx, cy, radius, 0, 2*math.pi)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.5); cr.stroke()

        for ring_g, alpha, lw in ((0.5, 0.18, 0.9), (1.0, 0.42, 1.5)):
            r_px = (ring_g / self.MAX_G) * radius
            cr.arc(cx, cy, r_px, 0, 2*math.pi)
            cr.set_source_rgba(r, g, b, alpha); cr.set_line_width(lw); cr.stroke()
            rx = cx + r_px * 0.690;  ry = cy - r_px * 0.690
            cr.select_font_face("Sans", 0, 0); cr.set_font_size(fs_ring)
            cr.set_source_rgba(0.55, 0.55, 0.55, 0.70)
            self._text_center(cr, f"{ring_g:.1f}g", rx, ry)

        for dx, dy in ((-1,0),(1,0),(0,-1),(0,1)):
            cr.move_to(cx+dx*radius*0.06, cy+dy*radius*0.06)
            cr.line_to(cx+dx*radius*0.94, cy+dy*radius*0.94)
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

        cr.arc(dot_sx, dot_sy, dot_r, 0, 2*math.pi)
        cr.set_source_rgba(r, g, b, 0.22); cr.fill()
        cr.arc(dot_sx, dot_sy, dot_r, 0, 2*math.pi)
        cr.set_source_rgba(r, g, b, 0.95); cr.set_line_width(2.5); cr.stroke()
        cr.arc(dot_sx, dot_sy, dot_r*0.35, 0, 2*math.pi)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.25); cr.fill()

        line_gap = (fs_axis + fs_val) * 0.65

        def label_pair(axis, value_str, tx, ty):
            cr.select_font_face("Sans", 0, 1); cr.set_font_size(fs_axis)
            cr.set_source_rgba(r, g, b, 0.90)
            self._text_center(cr, axis, tx, ty - line_gap/2)
            cr.select_font_face("Sans", 0, 0); cr.set_font_size(fs_val)
            cr.set_source_rgba(0.92, 0.92, 0.92, 1.0)
            self._text_center(cr, value_str, tx, ty + line_gap/2)

        label_pair("X",   f"{self._x:+.2f}g", cx + radius + lc, cy)
        label_pair("Y",   f"{self._y:+.2f}g", cx, cy - radius - lc)
        label_pair("Z",   f"{self._z:+.2f}g", cx - radius - lc, cy)
        label_pair("|a|", f"{mag:.2f}g",       cx, cy + radius + lc)


# ── Settings window ────────────────────────────────────────────────────────────

class SettingsWindow(Adw.PreferencesWindow):

    def __init__(self, parent, settings, lang, on_change):
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("s_ttl", lang))
        self._settings = settings

        page = Adw.PreferencesPage()
        self.add(page)

        grp = Adw.PreferencesGroup(title=_("s_appear", lang))
        page.add(grp)

        theme_row = Adw.ComboRow(title=_("s_theme", lang))
        theme_row.set_model(Gtk.StringList.new([
            _("s_t_auto", lang), _("s_t_lt", lang), _("s_t_dk", lang)]))
        theme_row.set_selected({"auto": 0, "light": 1, "dark": 2}.get(
            settings.get("theme", "auto"), 0))
        theme_row.connect("notify::selected", self._on_theme)
        grp.add(theme_row)

        lang_row = Adw.ComboRow(title=_("s_lang", lang))
        lang_row.set_model(Gtk.StringList.new(["Deutsch", "English"]))
        lang_row.set_selected(0 if lang == "de" else 1)
        lang_row.connect("notify::selected",
                         lambda row, _: [self._set_lang(["de","en"][row.get_selected()]),
                                         on_change()])
        grp.add(lang_row)

        grp2 = Adw.PreferencesGroup(title=_("s_cal", lang))
        page.add(grp2)

        ac_row = Adw.ActionRow(title=_("s_ac", lang), subtitle=_("s_ac_sub", lang))
        ac_sw  = Gtk.Switch()
        ac_sw.set_valign(Gtk.Align.CENTER)
        ac_sw.set_active(settings.get("auto_cal", True))
        ac_sw.connect("notify::active",
                      lambda sw, _: [settings.__setitem__("auto_cal", sw.get_active()),
                                     save_settings(settings)])
        ac_row.add_suffix(ac_sw)
        ac_row.set_activatable_widget(ac_sw)
        grp2.add(ac_row)

    def _on_theme(self, row, _):
        theme = ["auto", "light", "dark"][row.get_selected()]
        self._settings["theme"] = theme
        save_settings(self._settings)
        apply_theme(theme)

    def _set_lang(self, lang):
        self._settings["lang"] = lang
        save_settings(self._settings)


# ── Combined window ────────────────────────────────────────────────────────────

class SensorSuiteWindow(Adw.ApplicationWindow):

    _CALIB_STARS = ["○○○", "●○○", "●●○", "●●●"]
    _CALIB_HINT  = [
        "Draw a figure-8 — tilt device in all directions",
        "Good — repeat the figure-8 one or two more times",
        "Almost done — one more round",
        None,
    ]

    def __init__(self, settings, **kwargs):
        super().__init__(**kwargs)
        self._settings      = settings
        self._lang          = settings.get("lang", "de")
        self._accel         = None
        self._compass       = None
        self._anim_timer    = None
        self._demo_timers   = []

        # compass state
        self._cmp_target        = 0.0
        self._cmp_display       = 0.0
        self._calibrating       = False
        self._cal_seen_incomplete = False

        # level state
        self._waiting_cal   = False
        self._status_msg    = ""
        self._status_until  = None

        # gforce smooth values
        self._gx = self._gy = self._gz = 0.0
        self._gtx = self._gty = self._gtz = 0.0

        apply_theme(settings.get("theme", "auto"))
        self.set_title("Sensor Suite")
        self.set_default_size(360, 640)

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # ── Header ──────────────────────────────────────────────────────────
        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        self._cal_btn = Gtk.Button(label="Calibrate")
        self._cal_btn.connect("clicked", self._on_cal_btn_clicked)
        header.pack_start(self._cal_btn)

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(self._build_menu())
        header.pack_end(menu_btn)

        # ── View stack ───────────────────────────────────────────────────────
        self._stack = Adw.ViewStack()
        toolbar_view.set_content(self._stack)

        self._build_compass_page()
        self._build_level_page()
        self._build_gforce_page()

        # ── Bottom switcher bar ──────────────────────────────────────────────
        bar = Adw.ViewSwitcherBar()
        bar.set_stack(self._stack)
        bar.set_reveal(True)
        toolbar_view.add_bottom_bar(bar)

        self._stack.connect("notify::visible-child", lambda *_: self._update_cal_btn())
        self._update_cal_btn()

        # ── Load calibration ─────────────────────────────────────────────────
        cal_roll  = settings.get("cal_roll",  0.0)
        cal_pitch = settings.get("cal_pitch", 0.0)
        if cal_roll or cal_pitch:
            self._level_2d.set_cal(cal_roll, cal_pitch)
            self._level_hk.set_cal(cal_roll)
            self._level_quer.set_cal(cal_pitch)

        # ── Tap gesture for level calibration ────────────────────────────────
        tap = Gtk.GestureClick()
        tap.connect("released", self._on_level_tap)
        self._level_box.add_controller(tap)

        # ── Start ────────────────────────────────────────────────────────────
        self._anim_timer = GLib.timeout_add(16, self._anim_tick)
        GLib.idle_add(self._init_sensors)

    # ── Page builders ──────────────────────────────────────────────────────────

    def _build_compass_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(12)
        box.set_margin_bottom(8)
        box.set_margin_start(16)
        box.set_margin_end(16)

        self._calib_bar = Adw.Banner()
        self._calib_bar.connect("button-clicked", self._on_calib_bar_button)
        self._calib_bar.set_revealed(False)
        box.append(self._calib_bar)

        self._compass_widget = CompassWidget()
        self._compass_widget.set_size_request(260, 260)
        box.append(self._compass_widget)

        self._heading_label = Gtk.Label(label="0°")
        self._heading_label.add_css_class("title-1")
        self._heading_label.set_margin_top(12)
        box.append(self._heading_label)

        self._cardinal_label = Gtk.Label(label="North")
        self._cardinal_label.add_css_class("title-3")
        self._cardinal_label.add_css_class("dim-label")
        self._cardinal_label.set_margin_top(4)
        box.append(self._cardinal_label)

        self._calib_level_label = Gtk.Label()
        self._calib_level_label.add_css_class("caption")
        self._calib_level_label.add_css_class("dim-label")
        self._calib_level_label.set_margin_top(4)
        box.append(self._calib_level_label)

        page = self._stack.add_titled(box, "compass", "Compass")
        page.set_icon_name("find-location-symbolic")

    def _build_level_page(self):
        self._level_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._level_box.set_margin_top(8)
        self._level_box.set_margin_bottom(8)
        self._level_box.set_margin_start(10)
        self._level_box.set_margin_end(10)

        self._cal_hint = Gtk.Label()
        self._cal_hint.add_css_class("heading")
        self._cal_hint.set_justify(Gtk.Justification.CENTER)
        self._cal_hint.set_wrap(True)
        self._cal_revealer = Gtk.Revealer()
        self._cal_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._cal_revealer.set_transition_duration(180)
        self._cal_revealer.set_child(self._cal_hint)
        self._level_box.append(self._cal_revealer)

        self._level_hk = LinearLevelWidget()
        self._level_box.append(self._level_hk)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mid.set_vexpand(True)
        self._level_box.append(mid)

        self._level_2d = LevelWidget()
        mid.append(self._level_2d)

        self._level_quer = VerticalLevelWidget()
        mid.append(self._level_quer)

        self._angle_label = Gtk.Label(label="0.0°")
        self._angle_label.add_css_class("title-1")
        self._angle_label.set_margin_top(4)
        self._level_box.append(self._angle_label)

        self._detail_label = Gtk.Label(label=_("level", self._lang))
        self._detail_label.add_css_class("title-3")
        self._detail_label.add_css_class("dim-label")
        self._level_box.append(self._detail_label)

        axis_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        axis_box.set_halign(Gtk.Align.CENTER)
        self._level_box.append(axis_box)

        self._hk_label = Gtk.Label(label="↔ 0.0°")
        self._hk_label.add_css_class("caption")
        self._hk_label.add_css_class("dim-label")
        axis_box.append(self._hk_label)

        self._quer_label = Gtk.Label(label="↕ 0.0°")
        self._quer_label.add_css_class("caption")
        self._quer_label.add_css_class("dim-label")
        axis_box.append(self._quer_label)

        page = self._stack.add_titled(self._level_box, "spirit_level", "Spirit Level")
        page.set_icon_name("view-grid-symbolic")

    def _build_gforce_page(self):
        self._gforce_widget = GForceWidget()
        page = self._stack.add_titled(self._gforce_widget, "gforce", "G-Force")
        page.set_icon_name("system-run-symbolic")

    # ── Menu ───────────────────────────────────────────────────────────────────

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append("Settings", "app.settings")
        menu.append("About",    "app.about")
        return menu

    def _update_cal_btn(self):
        page = self._stack.get_visible_child_name()
        self._cal_btn.set_visible(page in ("compass", "spirit_level"))

    def _on_cal_btn_clicked(self, _btn):
        page = self._stack.get_visible_child_name()
        if page == "compass":
            dialog = Adw.AlertDialog(
                heading="Calibrate Compass",
                body="Hold the device flat and slowly draw a figure-8 in the air until all three stars are filled.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("start", "Start")
            dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("start")
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda d, r: self.start_compass_calibration() if r == "start" else None)
            dialog.present(self)
        elif page == "spirit_level":
            dialog = Adw.AlertDialog(
                heading="Calibrate Spirit Level",
                body="Place the device in the reference position, then tap the screen to set the zero point.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("start", "Start")
            dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("start")
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda d, r: self._enter_level_cal_mode() if r == "start" else None)
            dialog.present(self)

    def _on_calib_bar_button(self, banner):
        banner.set_revealed(False)
        self._calibrating = False

    # ── Sensor init ────────────────────────────────────────────────────────────

    def _init_sensors(self):
        self._accel = AccelBackend()
        if self._accel.available:
            self._accel.add_callback(self._on_accel)
            if self._settings.get("auto_cal", True):
                GLib.timeout_add(2000, self._auto_calibrate_level)
        else:
            tid = GLib.timeout_add(50, self._demo_accel_tick)
            self._demo_timers.append(tid)

        self._compass = SensorPoller(self._on_compass)
        if not self._compass.available:
            tid = GLib.timeout_add(50, self._demo_compass_tick)
            self._demo_timers.append(tid)
            self._compass_widget.set_heading(0.0, has_sensor=False)

        return False

    # ── Compass callbacks ──────────────────────────────────────────────────────

    def _on_compass(self, degrees, level, has_sensor):
        self._cmp_target = degrees
        if level < 0:
            return
        lvl = min(level, 3)
        self._calib_level_label.set_text(f"Calibration {self._CALIB_STARS[lvl]}")
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
                GLib.timeout_add(2500,
                    lambda: self._calib_bar.set_revealed(False) or False)

    def _demo_compass_tick(self):
        self._cmp_target = (self._cmp_target + 1.5) % 360
        return True

    def start_compass_calibration(self):
        if self._compass:
            self._compass.release()
            self._compass = None
        self._calib_level_label.set_text(f"Calibration {self._CALIB_STARS[0]}")
        self._calib_bar.set_title(
            "Hold device flat — slowly draw a figure-8 in the air")
        self._calib_bar.set_button_label("Skip")
        self._calib_bar.set_revealed(True)
        self._calibrating = True
        self._cal_seen_incomplete = False
        GLib.timeout_add(300, lambda: self._restart_compass() or False)

    def _restart_compass(self):
        self._compass = SensorPoller(self._on_compass)
        if not self._compass.available:
            tid = GLib.timeout_add(50, self._demo_compass_tick)
            self._demo_timers.append(tid)

    # ── Accel / level callbacks ────────────────────────────────────────────────

    def _on_accel(self, x, y, z):
        if z == 0:
            return
        roll  = math.degrees(math.atan2(x, math.sqrt(y*y + z*z)))
        pitch = math.degrees(math.atan2(y, math.sqrt(x*x + z*z)))
        self._level_2d.set_raw_tilt(roll, pitch)
        self._level_hk.set_raw_angle(roll)
        self._level_quer.set_raw_angle(pitch)
        self._gtx = x / 1000.0
        self._gty = y / 1000.0
        self._gtz = z / 1000.0

    def _demo_accel_tick(self):
        t = GLib.get_monotonic_time() / 1_000_000
        roll  = math.sin(t * 0.5) * 8
        pitch = math.cos(t * 0.7) * 5
        self._level_2d.set_raw_tilt(roll, pitch)
        self._level_hk.set_raw_angle(roll)
        self._level_quer.set_raw_angle(pitch)
        self._gtx = math.sin(t * 1.7) * 0.6
        self._gty = math.cos(t * 1.3) * 0.4
        self._gtz = 1.0 + math.sin(t * 2.9) * 0.15
        return True

    def _auto_calibrate_level(self):
        self._do_calibrate_level(show_dialog=False)
        return False

    def _do_calibrate_level(self, show_dialog=True):
        self._level_2d.calibrate()
        self._level_hk.calibrate()
        self._level_quer.calibrate()
        cal_roll, cal_pitch = self._level_2d.get_cal()
        self._settings["cal_roll"]  = cal_roll
        self._settings["cal_pitch"] = cal_pitch
        save_settings(self._settings)
        if show_dialog:
            dialog = Adw.AlertDialog(
                heading="Calibration Complete",
                body="The zero point has been set successfully.",
            )
            dialog.add_response("ok", "OK")
            dialog.set_default_response("ok")
            dialog.present(self)
        else:
            self._status_msg   = _("cal_done", self._lang)
            self._status_until = GLib.get_monotonic_time() + 2_000_000

    def _enter_level_cal_mode(self):
        self._waiting_cal = True
        self._cal_hint.set_text(_("cal_tap", self._lang))
        self._cal_revealer.set_reveal_child(True)
        self._stack.set_visible_child_name("spirit_level")

    def _on_level_tap(self, gesture, n_press, x, y):
        if not self._waiting_cal:
            return
        self._waiting_cal = False
        self._cal_revealer.set_reveal_child(False)
        self._do_calibrate_level()

    # ── Animation tick ─────────────────────────────────────────────────────────

    def _anim_tick(self) -> bool:
        # compass
        diff = (self._cmp_target - self._cmp_display + 180) % 360 - 180
        self._cmp_display = (self._cmp_display + diff * 0.18) % 360
        has = self._compass is not None and self._compass.available
        self._compass_widget.set_heading(self._cmp_display, has)
        self._heading_label.set_text(f"{self._cmp_display:.0f}°")
        self._cardinal_label.set_text(self._to_cardinal(self._cmp_display))

        # level widgets
        self._level_2d.smooth_tick()
        self._level_hk.smooth_tick()
        self._level_quer.smooth_tick()

        tilt = self._level_2d.total_tilt
        self._angle_label.set_text(f"{tilt:.1f}°")
        self._hk_label.set_text(f"↔ {self._level_hk.tilt:.1f}°")
        self._quer_label.set_text(f"↕ {self._level_quer.tilt:.1f}°")

        now = GLib.get_monotonic_time()
        if self._status_until and now < self._status_until:
            self._detail_label.set_text(self._status_msg)
        else:
            self._status_until = None
            lang = self._lang
            if tilt < 1.0:   self._detail_label.set_text(_("level",  lang))
            elif tilt < 3.0: self._detail_label.set_text(_("slight", lang))
            else:            self._detail_label.set_text(_("tilted", lang))

        # gforce smooth
        SMOOTH = 0.28
        self._gx += (self._gtx - self._gx) * SMOOTH
        self._gy += (self._gty - self._gy) * SMOOTH
        self._gz += (self._gtz - self._gz) * SMOOTH
        self._gforce_widget.update(self._gx, self._gy, self._gz)

        return True

    # ── Settings ───────────────────────────────────────────────────────────────

    def open_settings(self):
        SettingsWindow(
            parent=self,
            settings=self._settings,
            lang=self._lang,
            on_change=self._on_settings_change,
        ).present()

    def _on_settings_change(self):
        new_lang = self._settings.get("lang", "de")
        if new_lang != self._lang:
            self._lang = new_lang

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_cardinal(deg):
        directions = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
                      "S","SSW","SW","WSW","W","WNW","NW","NNW"]
        return directions[round(deg / 22.5) % 16]

    def do_close_request(self):
        if self._anim_timer:
            GLib.source_remove(self._anim_timer)
        for tid in self._demo_timers:
            GLib.source_remove(tid)
        if self._accel:
            self._accel.close()
        if self._compass:
            self._compass.release()
        return False


# ── Application ────────────────────────────────────────────────────────────────

class SensorSuiteApp(Adw.Application):

    def __init__(self):
        super().__init__(application_id="de.cais.SensorSuite",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self._on_activate)
        self._add_actions()

    def _add_actions(self):
        for name, handler in [
            ("settings", self._on_settings),
            ("about",    self._on_about),
        ]:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", handler)
            self.add_action(a)

    def _on_activate(self, app):
        SensorSuiteWindow(settings=load_settings(), application=app).present()

    def _on_settings(self, action, param):
        win = self.get_active_window()
        if win: win.open_settings()

    def _on_about(self, action, param):
        dialog = Adw.AboutDialog()
        dialog.set_application_name("Sensor Suite")
        dialog.set_application_icon("find-location-symbolic")
        dialog.set_developer_name("Chris")
        dialog.set_version("1.0")
        dialog.set_comments("Compass · Spirit Level · G-Force")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.present(self.get_active_window())


if __name__ == "__main__":
    sys.exit(SensorSuiteApp().run(sys.argv))
