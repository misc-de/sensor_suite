#!/usr/bin/env python3
import sys, math, os, struct, socket as _socket, json
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio

SERVICE     = "com.nokia.SensorService"
SOCKET_PATH = "/run/sensord.sock"
_HDR        = struct.Struct('<I')
_ACCEL      = struct.Struct('<Qfffi')

CONFIG_DIR  = os.path.expanduser("~/.config/de.cais.Wasserwage")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

_T = {
    "title":    {"de": "Wasserwage",         "en": "Spirit Level"},
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

def _(key, lang):
    return _T.get(key, {}).get(lang, key)


def load_settings():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
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


class AccelBackend:
    def __init__(self):
        self._bus = self._sock = self._watch_id = self._session_id = None
        self._buf = b""
        self._x = self._y = self._z = 0
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
                       GLib.Variant("(ii)", (self._session_id, 50)))
            self._call("/SensorManager/accelerometersensor",
                       "local.AccelerometerSensor", "start",
                       GLib.Variant("(i)", (self._session_id,)))
            self._available = True
        except Exception as e:
            print(f"AccelBackend not available: {e}")

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
                    self._x, self._y, self._z = x, y, z
                self._buf = self._buf[need:]
                if self._callback:
                    self._callback(self._x, self._y, self._z)
        except BlockingIOError:
            pass
        except Exception as e:
            print(f"Accel socket error: {e}")
            return False
        return True

    def set_callback(self, cb):  self._callback = cb

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


def _bubble_color(tilt):
    if tilt < 1.0:  return 0.18, 0.78, 0.32
    if tilt < 3.0:  return 0.95, 0.72, 0.05
    return 0.88, 0.18, 0.12


def _draw_bubble(cr, bx, by, br, r, g, b):
    cr.arc(bx, by, br, 0, 2*math.pi)
    cr.set_source_rgba(r, g, b, 0.28); cr.fill()
    cr.arc(bx, by, br, 0, 2*math.pi)
    cr.set_source_rgba(r, g, b, 0.95); cr.set_line_width(2.5); cr.stroke()
    cr.arc(bx, by, br*0.30, 0, 2*math.pi)
    cr.set_source_rgba(1, 1, 1, 0.25); cr.fill()


def _rounded_rect(cr, x, y, w, h, rad):
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad,     rad, -math.pi/2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi/2)
    cr.arc(x + rad,     y + h - rad, rad, math.pi/2, math.pi)
    cr.arc(x + rad,     y + rad,     rad, math.pi, 3*math.pi/2)
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
        self._cal_roll  += self._roll; self._cal_pitch += self._pitch
        self._roll = self._pitch = self._target_roll = self._target_pitch = 0.0
        self.queue_draw()

    def get_cal(self):         return (self._cal_roll, self._cal_pitch)
    def set_cal(self, roll, pitch):
        self._cal_roll = roll; self._cal_pitch = pitch

    @property
    def total_tilt(self): return math.sqrt(self._roll**2 + self._pitch**2)

    def _draw(self, area, cr, w, h):
        cx, cy  = w / 2, h / 2
        radius  = min(w, h) / 2 * 0.85
        br      = radius * 0.14
        roll    = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._roll))
        pitch   = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._pitch))
        bx      = cx + (roll  / self.MAX_ANGLE) * (radius - br)
        by      = cy - (pitch / self.MAX_ANGLE) * (radius - br)
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

    def set_raw_angle(self, a):  self._target = a - self._cal_offset

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
        cx, cy  = w / 2, h / 2
        tw      = w * 0.86
        th      = min(h * 0.54, 68.0)
        br      = th * 0.42
        tx, ty  = cx - tw/2, cy - th/2
        max_tr  = tw/2 - br
        r, g, b = _bubble_color(self.tilt)

        _rounded_rect(cr, tx, ty, tw, th, th/2)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.0); cr.stroke()

        cr.move_to(cx, ty + th*0.10); cr.line_to(cx, ty + th*0.90)
        cr.set_source_rgba(r, g, b, 0.70); cr.set_line_width(2.0); cr.stroke()

        for deg in (1.0, 2.0, 3.0):
            off = (deg / self.MAX_ANGLE) * max_tr
            f   = 0.22 if deg != 2.0 else 0.16
            for sign in (-1, 1):
                x = cx + sign * off
                cr.move_to(x, ty + th*f); cr.line_to(x, ty + th*(1-f))
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

    def set_raw_angle(self, a):  self._target = a - self._cal_offset

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
        cx, cy  = w / 2, h / 2
        th      = h * 0.82
        tw      = min(w * 0.68, 48.0)
        br      = tw * 0.42
        tx, ty  = cx - tw/2, cy - th/2
        max_tr  = th/2 - br
        r, g, b = _bubble_color(self.tilt)

        _rounded_rect(cr, tx, ty, tw, th, tw/2)
        cr.set_source_rgba(0.1, 0.1, 0.12, 0.96); cr.fill_preserve()
        cr.set_source_rgba(r, g, b, 0.35); cr.set_line_width(2.0); cr.stroke()

        cr.move_to(tx + tw*0.10, cy); cr.line_to(tx + tw*0.90, cy)
        cr.set_source_rgba(r, g, b, 0.70); cr.set_line_width(2.0); cr.stroke()

        for deg in (1.0, 2.0, 3.0):
            off = (deg / self.MAX_ANGLE) * max_tr
            f   = 0.22 if deg != 2.0 else 0.16
            for sign in (-1, 1):
                y = cy + sign * off
                cr.move_to(tx + tw*f, y); cr.line_to(tx + tw*(1-f), y)
            cr.set_source_rgba(0.45, 0.45, 0.45, 0.35); cr.set_line_width(1.0); cr.stroke()

        a  = max(-self.MAX_ANGLE, min(self.MAX_ANGLE, self._angle))
        by = cy - (a / self.MAX_ANGLE) * max_tr
        _draw_bubble(cr, cx, by, br, r, g, b)


class SettingsWindow(Adw.PreferencesWindow):

    def __init__(self, parent, settings, lang, on_change):
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("s_ttl", lang))
        self._settings  = settings
        self._on_change = on_change

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
        lang_row.connect("notify::selected", self._on_lang)
        grp.add(lang_row)

        grp2 = Adw.PreferencesGroup(title=_("s_cal", lang))
        page.add(grp2)

        ac_row = Adw.ActionRow(title=_("s_ac", lang))
        ac_row.set_subtitle(_("s_ac_sub", lang))
        ac_sw = Gtk.Switch()
        ac_sw.set_valign(Gtk.Align.CENTER)
        ac_sw.set_active(settings.get("auto_cal", True))
        ac_sw.connect("notify::active", self._on_auto_cal)
        ac_row.add_suffix(ac_sw)
        ac_row.set_activatable_widget(ac_sw)
        grp2.add(ac_row)

    def _on_theme(self, row, _):
        theme = ["auto", "light", "dark"][row.get_selected()]
        self._settings["theme"] = theme
        save_settings(self._settings)
        apply_theme(theme)

    def _on_lang(self, row, _):
        self._settings["lang"] = ["de", "en"][row.get_selected()]
        save_settings(self._settings)
        self._on_change()

    def _on_auto_cal(self, sw, _):
        self._settings["auto_cal"] = sw.get_active()
        save_settings(self._settings)


class SpiritLevelWindow(Adw.ApplicationWindow):

    def __init__(self, settings, **kwargs):
        super().__init__(**kwargs)
        self._settings     = settings
        self._lang         = settings.get("lang", "en")
        self._backend      = None
        self._anim_timer   = None
        self._status_msg   = ""
        self._status_until = None
        self._waiting_cal  = False

        apply_theme(settings.get("theme", "auto"))
        self.set_title(_("title", self._lang))
        self.set_default_size(360, 640)

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar_view.add_top_bar(header)

        cal_btn = Gtk.Button(label="Calibrate")
        cal_btn.connect("clicked", self._on_calibrate_clicked)
        header.pack_start(cal_btn)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic",
                                  tooltip_text="Settings")
        settings_btn.connect("clicked", self._open_settings)
        header.pack_end(settings_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_top(8)
        outer.set_margin_bottom(14)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        toolbar_view.set_content(outer)

        self._cal_hint = Gtk.Label()
        self._cal_hint.add_css_class("heading")
        self._cal_hint.set_justify(Gtk.Justification.CENTER)
        self._cal_hint.set_wrap(True)
        self._cal_revealer = Gtk.Revealer()
        self._cal_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._cal_revealer.set_transition_duration(180)
        self._cal_revealer.set_child(self._cal_hint)
        outer.append(self._cal_revealer)

        self._level_hk = LinearLevelWidget()
        outer.append(self._level_hk)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mid.set_vexpand(True)
        outer.append(mid)

        self._level = LevelWidget()
        mid.append(self._level)

        self._level_quer = VerticalLevelWidget()
        mid.append(self._level_quer)

        self._angle_label = Gtk.Label(label="0.0°")
        self._angle_label.add_css_class("title-1")
        self._angle_label.set_margin_top(4)
        outer.append(self._angle_label)

        self._detail_label = Gtk.Label(label=_("level", self._lang))
        self._detail_label.add_css_class("title-3")
        self._detail_label.add_css_class("dim-label")
        outer.append(self._detail_label)

        axis_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        axis_box.set_halign(Gtk.Align.CENTER)
        outer.append(axis_box)

        self._hk_label = Gtk.Label(label="↔ 0.0°")
        self._hk_label.add_css_class("caption")
        self._hk_label.add_css_class("dim-label")
        axis_box.append(self._hk_label)

        self._quer_label = Gtk.Label(label="↕ 0.0°")
        self._quer_label.add_css_class("caption")
        self._quer_label.add_css_class("dim-label")
        axis_box.append(self._quer_label)

        cal_roll  = settings.get("cal_roll",  0.0)
        cal_pitch = settings.get("cal_pitch", 0.0)
        if cal_roll or cal_pitch:
            self._level.set_cal(cal_roll, cal_pitch)
            self._level_hk.set_cal(cal_roll)
            self._level_quer.set_cal(cal_pitch)

        tap = Gtk.GestureClick()
        tap.connect("released", self._on_tap)
        outer.add_controller(tap)

        self._anim_timer = GLib.timeout_add(16, self._anim_tick)
        GLib.idle_add(self._init_sensor)

    def _init_sensor(self):
        self._backend = AccelBackend()
        if self._backend.available:
            self._backend.set_callback(self._on_accel)
            if self._settings.get("auto_cal", True):
                GLib.timeout_add(2000, self._auto_calibrate)
        else:
            GLib.timeout_add(50, self._demo_tick)
        return False

    def _auto_calibrate(self):
        self._do_calibrate()
        return False

    def _demo_tick(self):
        t = GLib.get_monotonic_time() / 1_000_000
        roll  = math.sin(t * 0.5) * 8
        pitch = math.cos(t * 0.7) * 5
        self._level.set_raw_tilt(roll, pitch)
        self._level_hk.set_raw_angle(roll)
        self._level_quer.set_raw_angle(pitch)
        return True

    def _on_accel(self, x, y, z):
        if z == 0:
            return
        roll  = math.degrees(math.atan2(x, math.sqrt(y*y + z*z)))
        pitch = math.degrees(math.atan2(y, math.sqrt(x*x + z*z)))
        self._level.set_raw_tilt(roll, pitch)
        self._level_hk.set_raw_angle(roll)
        self._level_quer.set_raw_angle(pitch)

    def _anim_tick(self) -> bool:
        self._level.smooth_tick()
        self._level_hk.smooth_tick()
        self._level_quer.smooth_tick()

        tilt = self._level.total_tilt
        lang = self._lang
        self._angle_label.set_text(f"{tilt:.1f}°")
        self._hk_label.set_text(f"↔ {self._level_hk.tilt:.1f}°")
        self._quer_label.set_text(f"↕ {self._level_quer.tilt:.1f}°")

        now = GLib.get_monotonic_time()
        if self._status_until and now < self._status_until:
            self._detail_label.set_text(self._status_msg)
        else:
            self._status_until = None
            if tilt < 1.0:   self._detail_label.set_text(_("level",  lang))
            elif tilt < 3.0: self._detail_label.set_text(_("slight", lang))
            else:            self._detail_label.set_text(_("tilted", lang))
        return True

    def _on_calibrate_clicked(self, _btn):
        dialog = Adw.AlertDialog(
            heading="Calibrate Spirit Level",
            body="Place the device in the reference position, then tap the screen to set the zero point.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("start", "Start")
        dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("start")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda d, r: self._enter_cal_mode() if r == "start" else None)
        dialog.present(self)

    def _enter_cal_mode(self):
        self._waiting_cal = True
        self._cal_hint.set_text(_("cal_tap", self._lang))
        self._cal_revealer.set_reveal_child(True)

    def _on_tap(self, gesture, n_press, x, y):
        if not self._waiting_cal:
            return
        self._waiting_cal = False
        self._cal_revealer.set_reveal_child(False)
        self._do_calibrate()

    def _do_calibrate(self):
        self._level.calibrate()
        self._level_hk.calibrate()
        self._level_quer.calibrate()
        cal_roll, cal_pitch = self._level.get_cal()
        self._settings["cal_roll"]  = cal_roll
        self._settings["cal_pitch"] = cal_pitch
        save_settings(self._settings)
        self._status_msg   = _("cal_done", self._lang)
        self._status_until = GLib.get_monotonic_time() + 2_000_000

    def _open_settings(self, _btn):
        SettingsWindow(
            parent=self,
            settings=self._settings,
            lang=self._lang,
            on_change=self._on_settings_change,
        ).present()

    def _on_settings_change(self):
        new_lang = self._settings.get("lang", "en")
        if new_lang != self._lang:
            self._lang = new_lang
            self.set_title(_("title", new_lang))

    def do_close_request(self):
        if self._anim_timer:
            GLib.source_remove(self._anim_timer)
        if self._backend:
            self._backend.close()
        return False


class SpiritLevelApp(Adw.Application):

    def __init__(self):
        super().__init__(application_id="de.cais.Wasserwage",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        SpiritLevelWindow(settings=load_settings(), application=app).present()


if __name__ == "__main__":
    sys.exit(SpiritLevelApp().run(sys.argv))
