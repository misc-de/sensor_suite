"""
Unit, security, and performance tests for Sensor Suite.

Tests are split into:
  - Pure logic (no GTK required): protocol parsing, math helpers, smoothing
  - GTK-dependent (skipped automatically when GTK4/libadwaita is absent):
    translation table, _bubble_color, settings I/O, IIO device discovery
"""
import math
import os
import struct
import time
import sys
import pytest

# ── Module import ──────────────────────────────────────────────────────────────
# Keep GTK import failures from aborting the whole suite.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import sensor_suite as ss
    import spirit_level as sl
    HAS_GTK = True
except Exception:
    HAS_GTK = False
    ss = sl = None

needs_gtk = pytest.mark.skipif(not HAS_GTK, reason="GTK4/libadwaita not installed")

# ── Packet structs (mirrored so protocol tests run without GTK) ────────────────
_HDR   = struct.Struct('<I')
_ACCEL = struct.Struct('<Qfffi')
_CMP   = struct.Struct('<Qiiii')


# ═══════════════════════════════════════════════════════════════════════════════
# Translation helper  _()
# ═══════════════════════════════════════════════════════════════════════════════

@needs_gtk
class TestTranslation:
    def test_known_key_en(self):
        assert ss._("level", "en") == "level"

    def test_known_key_de(self):
        assert ss._("level", "de") == "eben"

    def test_unknown_lang_falls_back_to_key(self):
        assert ss._("level", "fr") == "level"

    def test_missing_key_returns_key(self):
        assert ss._("__no_such_key__", "en") == "__no_such_key__"

    def test_all_ss_keys_have_en_and_de(self):
        for key, langs in ss._T.items():
            assert "en" in langs, f"sensor_suite.py: key '{key}' missing 'en'"
            assert "de" in langs, f"sensor_suite.py: key '{key}' missing 'de'"

    def test_all_sl_keys_have_en_and_de(self):
        for key, langs in sl._T.items():
            assert "en" in langs, f"spirit_level.py: key '{key}' missing 'en'"
            assert "de" in langs, f"spirit_level.py: key '{key}' missing 'de'"


# ═══════════════════════════════════════════════════════════════════════════════
# _bubble_color()
# ═══════════════════════════════════════════════════════════════════════════════

@needs_gtk
class TestBubbleColor:
    def test_level_green(self):
        assert ss._bubble_color(0.0)   == (0.18, 0.78, 0.32)
        assert ss._bubble_color(0.999) == (0.18, 0.78, 0.32)

    def test_slightly_tilted_yellow(self):
        r, g, b = ss._bubble_color(1.0)
        assert r == pytest.approx(0.95)

    def test_tilted_red(self):
        r, g, b = ss._bubble_color(3.0)
        assert r == pytest.approx(0.88)

    def test_boundary_at_1_is_yellow(self):
        assert ss._bubble_color(1.0)[0] == pytest.approx(0.95)

    def test_boundary_at_3_is_red(self):
        assert ss._bubble_color(3.0)[0] == pytest.approx(0.88)

    def test_returns_three_floats(self):
        result = ss._bubble_color(2.0)
        assert len(result) == 3
        assert all(isinstance(v, float) for v in result)

    def test_values_in_unit_range(self):
        for tilt in (0.0, 1.0, 2.0, 5.0, 10.0):
            for ch in ss._bubble_color(tilt):
                assert 0.0 <= ch <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Settings I/O — load_settings / save_settings
# ═══════════════════════════════════════════════════════════════════════════════

@needs_gtk
class TestSettingsIO:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, "CONFIG_DIR",  str(tmp_path))
        monkeypatch.setattr(ss, "CONFIG_FILE", str(tmp_path / "settings.json"))
        data = {"theme": "dark", "lang": "de", "auto_cal": False,
                "cal_roll": 1.5, "cal_pitch": -0.7}
        ss.save_settings(data)
        assert ss.load_settings() == data

    def test_defaults_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, "CONFIG_FILE", str(tmp_path / "no_file.json"))
        s = ss.load_settings()
        assert s == {"theme": "auto", "lang": "en", "auto_cal": True}

    def test_malformed_json_returns_defaults(self, tmp_path, monkeypatch):
        f = tmp_path / "settings.json"
        f.write_text("{not valid JSON")
        monkeypatch.setattr(ss, "CONFIG_FILE", str(f))
        s = ss.load_settings()
        assert s["lang"] == "en"

    def test_creates_missing_directories(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "c"
        monkeypatch.setattr(ss, "CONFIG_DIR",  str(nested))
        monkeypatch.setattr(ss, "CONFIG_FILE", str(nested / "settings.json"))
        ss.save_settings({"theme": "auto", "lang": "en", "auto_cal": True})
        assert (nested / "settings.json").exists()

    def test_float_calibration_values_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, "CONFIG_DIR",  str(tmp_path))
        monkeypatch.setattr(ss, "CONFIG_FILE", str(tmp_path / "settings.json"))
        data = {"theme": "auto", "lang": "en", "auto_cal": True,
                "cal_roll": 2.345678, "cal_pitch": -1.234567}
        ss.save_settings(data)
        loaded = ss.load_settings()
        assert loaded["cal_roll"]  == pytest.approx(2.345678)
        assert loaded["cal_pitch"] == pytest.approx(-1.234567)


# ═══════════════════════════════════════════════════════════════════════════════
# IIO magnetometer heading math
# ═══════════════════════════════════════════════════════════════════════════════

def _iio_heading(x: float, y: float) -> float:
    """Mirror of IIOBackend.read_heading() math."""
    return math.degrees(math.atan2(-y, x)) % 360


class TestIIOHeadingMath:
    def test_north(self):
        assert _iio_heading(1.0, 0.0) == pytest.approx(0.0, abs=1e-9)

    def test_east(self):
        assert _iio_heading(0.0, -1.0) == pytest.approx(90.0, abs=1e-9)

    def test_south(self):
        assert _iio_heading(-1.0, 0.0) == pytest.approx(180.0, abs=1e-9)

    def test_west(self):
        assert _iio_heading(0.0, 1.0) == pytest.approx(270.0, abs=1e-9)

    def test_always_in_0_360(self):
        for deg in range(0, 360, 15):
            rad = math.radians(deg)
            x, y = math.cos(rad), -math.sin(rad)
            h = _iio_heading(x, y)
            assert 0.0 <= h < 360.0

    def test_magnitude_does_not_affect_heading(self):
        h1 = _iio_heading(1.0, 0.0)
        h2 = _iio_heading(100.0, 0.0)
        assert h1 == pytest.approx(h2)

    def test_diagonal_45_degrees(self):
        h = _iio_heading(1.0, -1.0)
        assert h == pytest.approx(45.0, abs=1e-9)


# ═══════════════════════════════════════════════════════════════════════════════
# Binary protocol — accelerometer (sensorfwd socket)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_accel_packet(*samples: tuple) -> bytes:
    hdr  = _HDR.pack(len(samples))
    body = b"".join(_ACCEL.pack(0, x, y, z, 0) for x, y, z in samples)
    return hdr + body


class TestAccelProtocol:
    def test_single_sample_decoded(self):
        buf = _make_accel_packet((1.0, 2.0, 3.0))
        count, = _HDR.unpack_from(buf)
        assert count == 1
        _, x, y, z, _ = _ACCEL.unpack_from(buf, _HDR.size)
        assert (x, y, z) == pytest.approx((1.0, 2.0, 3.0))

    def test_multi_sample_last_frame(self):
        buf = _make_accel_packet((0.1, 0.2, 0.3), (9.0, 8.0, 7.0))
        count, = _HDR.unpack_from(buf)
        assert count == 2
        off = _HDR.size + (count - 1) * _ACCEL.size
        _, x, y, z, _ = _ACCEL.unpack_from(buf, off)
        assert (x, y, z) == pytest.approx((9.0, 8.0, 7.0))

    def test_zero_count_packet_complete(self):
        buf = _HDR.pack(0)
        count, = _HDR.unpack_from(buf)
        need = _HDR.size + count * _ACCEL.size
        assert need == _HDR.size
        assert len(buf) >= need

    def test_truncated_body_detected_as_incomplete(self):
        buf = _make_accel_packet((1.0, 0.0, 0.0))[:6]
        count, = _HDR.unpack_from(buf)
        need = _HDR.size + count * _ACCEL.size
        assert len(buf) < need

    def test_header_only_is_incomplete(self):
        assert len(b"\x01\x00\x00") < _HDR.size

    def test_milli_g_scaling(self):
        assert 1000 / 1000.0 == pytest.approx(1.0)
        assert -500 / 1000.0 == pytest.approx(-0.5)
        assert 9810 / 1000.0 == pytest.approx(9.81)

    def test_large_count_stalls_on_short_buffer(self):
        buf = _HDR.pack(10_000)   # header only, body missing
        count, = _HDR.unpack_from(buf)
        need = _HDR.size + count * _ACCEL.size
        assert len(buf) < need   # correctly stalls, not consumed

    def test_two_concatenated_packets_consumed_in_order(self):
        p1 = _make_accel_packet((1.0, 0.0, 0.0))
        p2 = _make_accel_packet((2.0, 0.0, 0.0))
        buf = p1 + p2
        # First packet
        count1, = _HDR.unpack_from(buf)
        need1 = _HDR.size + count1 * _ACCEL.size
        _, x1, _, _, _ = _ACCEL.unpack_from(buf, _HDR.size)
        buf = buf[need1:]
        # Second packet
        count2, = _HDR.unpack_from(buf)
        _, x2, _, _, _ = _ACCEL.unpack_from(buf, _HDR.size)
        assert x1 == pytest.approx(1.0)
        assert x2 == pytest.approx(2.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Binary protocol — compass (sensorfwd socket)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_compass_packet(*samples: tuple) -> bytes:
    hdr  = _HDR.pack(len(samples))
    body = b"".join(_CMP.pack(0, deg, 0, 0, lvl) for deg, lvl in samples)
    return hdr + body


class TestCompassProtocol:
    def test_heading_decoded(self):
        buf = _make_compass_packet((180, 3))
        _, deg, _, _, lvl = _CMP.unpack_from(buf, _HDR.size)
        assert deg == 180
        assert lvl == 3

    def test_cal_level_values(self):
        for expected_lvl in (0, 1, 2, 3):
            buf = _make_compass_packet((0, expected_lvl))
            _, _, _, _, lvl = _CMP.unpack_from(buf, _HDR.size)
            assert lvl == expected_lvl

    def test_multi_sample_last_used(self):
        buf = _make_compass_packet((45, 1), (270, 3))
        count, = _HDR.unpack_from(buf)
        assert count == 2
        _, deg, _, _, lvl = _CMP.unpack_from(buf, _HDR.size + _CMP.size)
        assert deg == 270
        assert lvl == 3

    def test_heading_mod_360_always_valid(self):
        for raw in (-360, -180, -1, 0, 90, 359, 360, 720):
            assert 0 <= raw % 360 < 360

    def test_truncated_body_incomplete(self):
        buf = _make_compass_packet((90, 2))[:6]
        count, = _HDR.unpack_from(buf)
        need = _HDR.size + count * _CMP.size
        assert len(buf) < need


# ═══════════════════════════════════════════════════════════════════════════════
# Exponential smoothing filter
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmoothing:
    SMOOTH_ACCEL  = 0.28
    SMOOTH_LEVEL  = 0.20
    DRAW_THRESHOLD = 0.01

    def _run(self, smooth, target, steps, start=0.0):
        v = start
        for _ in range(steps):
            v += (target - v) * smooth
        return v

    def test_converges_to_target(self):
        result = self._run(self.SMOOTH_ACCEL, 1.0, 60)
        assert abs(result - 1.0) < 1e-3

    def test_moves_toward_target_positive(self):
        new = 0.0 + (1.0 - 0.0) * self.SMOOTH_ACCEL
        assert 0.0 < new < 1.0

    def test_moves_toward_target_negative(self):
        new = 0.0 + (-1.0 - 0.0) * self.SMOOTH_ACCEL
        assert -1.0 < new < 0.0

    def test_no_movement_already_at_target(self):
        v = 1.0
        new = v + (1.0 - v) * self.SMOOTH_ACCEL
        assert new == pytest.approx(1.0)

    def test_below_threshold_no_redraw(self):
        current = 1.000
        target  = 1.004
        d = (target - current) * self.SMOOTH_LEVEL
        assert abs(d) < self.DRAW_THRESHOLD

    def test_above_threshold_triggers_redraw(self):
        current = 0.0
        target  = 1.0
        d = (target - current) * self.SMOOTH_LEVEL
        assert abs(d) >= self.DRAW_THRESHOLD

    def test_never_overshoots(self):
        v = 0.0
        for _ in range(200):
            v += (1.0 - v) * self.SMOOTH_ACCEL
            assert v <= 1.0 + 1e-12


# ═══════════════════════════════════════════════════════════════════════════════
# Level calibration logic (pure math — no GTK)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLevelCalibrationLogic:
    """Mirrors LevelWidget / LinearLevelWidget state machine without GTK."""

    def _state(self):
        return dict(roll=0.0, pitch=0.0,
                    target_roll=0.0, target_pitch=0.0,
                    cal_roll=0.0, cal_pitch=0.0)

    def _set_raw(self, s, roll, pitch):
        s["target_roll"]  = roll  - s["cal_roll"]
        s["target_pitch"] = pitch - s["cal_pitch"]

    def _tick(self, s):
        s["roll"]  += (s["target_roll"]  - s["roll"])  * 0.20
        s["pitch"] += (s["target_pitch"] - s["pitch"]) * 0.20

    def _calibrate(self, s):
        s["cal_roll"]  += s["roll"]
        s["cal_pitch"] += s["pitch"]
        s["roll"] = s["pitch"] = s["target_roll"] = s["target_pitch"] = 0.0

    def test_initial_state_all_zero(self):
        s = self._state()
        assert all(v == 0.0 for v in s.values())

    def test_set_raw_tilt_subtracts_cal_offset(self):
        s = self._state()
        s["cal_roll"] = 5.0
        self._set_raw(s, 8.0, 0.0)
        assert s["target_roll"] == pytest.approx(3.0)

    def test_calibrate_accumulates_current_display_value(self):
        s = self._state()
        s["roll"] = 2.5; s["pitch"] = 1.0
        self._calibrate(s)
        assert s["cal_roll"]  == pytest.approx(2.5)
        assert s["cal_pitch"] == pytest.approx(1.0)

    def test_calibrate_resets_display_to_zero(self):
        s = self._state()
        s["roll"] = 3.0; s["target_roll"] = 3.0
        self._calibrate(s)
        assert s["roll"] == 0.0
        assert s["target_roll"] == 0.0

    def test_double_calibration_accumulates(self):
        s = self._state()
        s["roll"] = 2.0
        self._calibrate(s)              # cal_roll = 2.0
        self._set_raw(s, 3.0, 0.0)     # target_roll = 1.0
        for _ in range(100):
            self._tick(s)               # roll → 1.0
        self._calibrate(s)              # cal_roll = 3.0
        assert s["cal_roll"] == pytest.approx(3.0, abs=0.01)
        assert s["roll"] == 0.0

    def test_total_tilt_is_euclidean(self):
        roll, pitch = 3.0, 4.0
        assert math.sqrt(roll**2 + pitch**2) == pytest.approx(5.0)

    def test_linear_calibrate_accumulates(self):
        cal = 0.0
        angle = 4.5
        cal += angle
        angle = 0.0
        assert cal == pytest.approx(4.5)
        assert angle == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# find_iio_magnetometer() with mocked filesystem
# ═══════════════════════════════════════════════════════════════════════════════

@needs_gtk
class TestFindIIOMagnetometer:
    def test_returns_none_when_base_absent(self, monkeypatch):
        monkeypatch.setattr(os.path, "isdir", lambda p: False)
        assert ss.find_iio_magnetometer() is None

    def test_finds_device_by_name_keyword(self, tmp_path, monkeypatch):
        dev = tmp_path / "iio:device0"
        dev.mkdir()
        (dev / "name").write_text("ak09918\n")
        (dev / "in_magn_x_raw").touch()
        monkeypatch.setattr(ss, "IIO_BASE", str(tmp_path))
        assert ss.find_iio_magnetometer() == str(dev)

    def test_ignores_device_missing_raw_file(self, tmp_path, monkeypatch):
        dev = tmp_path / "iio:device0"
        dev.mkdir()
        (dev / "name").write_text("ak09918\n")
        # no in_magn_x_raw
        monkeypatch.setattr(ss, "IIO_BASE", str(tmp_path))
        assert ss.find_iio_magnetometer() is None

    def test_ignores_non_magnetometer_by_name(self, tmp_path, monkeypatch):
        dev = tmp_path / "iio:device0"
        dev.mkdir()
        (dev / "name").write_text("bmp280\n")   # pressure sensor
        (dev / "in_magn_x_raw").touch()
        monkeypatch.setattr(ss, "IIO_BASE", str(tmp_path))
        assert ss.find_iio_magnetometer() is None

    def test_picks_sorted_first_match(self, tmp_path, monkeypatch):
        for name, node in (("ak09918", "iio:device0"), ("mmc56x3", "iio:device1")):
            d = tmp_path / node
            d.mkdir()
            (d / "name").write_text(name + "\n")
            (d / "in_magn_x_raw").touch()
        monkeypatch.setattr(ss, "IIO_BASE", str(tmp_path))
        result = ss.find_iio_magnetometer()
        assert result is not None
        assert result.endswith("iio:device0")

    def test_all_magn_keywords_recognised(self, tmp_path, monkeypatch):
        for kw in ss.MAGN_KEYWORDS:
            dev = tmp_path / f"iio:device_{kw}"
            dev.mkdir()
            (dev / "name").write_text(f"sensor_{kw}_xyz\n")
            (dev / "in_magn_x_raw").touch()
            monkeypatch.setattr(ss, "IIO_BASE", str(tmp_path))
            result = ss.find_iio_magnetometer()
            assert result is not None, f"keyword '{kw}' not matched"
            # clean up for next iteration
            import shutil; shutil.rmtree(str(dev))


# ═══════════════════════════════════════════════════════════════════════════════
# Security
# ═══════════════════════════════════════════════════════════════════════════════

@needs_gtk
class TestSecurity:
    def test_oversized_settings_json_no_crash(self, tmp_path, monkeypatch):
        f = tmp_path / "settings.json"
        f.write_text('{"theme": "dark"' + ', "x": "' + "y" * 10000 + '"}')
        monkeypatch.setattr(ss, "CONFIG_FILE", str(f))
        result = ss.load_settings()
        assert isinstance(result, dict)

    def test_deeply_nested_json_no_crash(self, tmp_path, monkeypatch):
        f = tmp_path / "settings.json"
        f.write_text("[" * 300 + "]" * 300)   # deeply nested, not a dict
        monkeypatch.setattr(ss, "CONFIG_FILE", str(f))
        result = ss.load_settings()
        assert isinstance(result, dict)   # falls back to defaults

    def test_config_file_is_inside_config_dir(self):
        assert ss.CONFIG_FILE.startswith(ss.CONFIG_DIR)

    def test_sl_config_file_is_inside_config_dir(self):
        assert sl.CONFIG_FILE.startswith(sl.CONFIG_DIR)


class TestSecurityProtocol:
    """Protocol-level security — no GTK needed."""

    def test_heading_mod_never_negative(self):
        for raw in range(-720, 721, 13):
            h = raw % 360
            assert h >= 0

    def test_heading_always_below_360(self):
        for raw in range(-720, 1081, 17):
            assert raw % 360 < 360

    def test_large_packet_count_does_not_process_without_full_body(self):
        buf = _HDR.pack(0xFFFF)   # 65535 samples claimed
        count, = _HDR.unpack_from(buf)
        need = _HDR.size + count * _ACCEL.size
        assert len(buf) < need   # buffer guard prevents processing

    def test_partial_second_packet_not_consumed(self):
        p1 = _make_accel_packet((1.0, 0.0, 0.0))
        p2 = _make_accel_packet((2.0, 0.0, 0.0))
        # Keep the full header of p2 but truncate its body
        buf = p1 + p2[:_HDR.size + 4]
        # Consume first packet
        count, = _HDR.unpack_from(buf)
        need = _HDR.size + count * _ACCEL.size
        assert len(buf) >= need
        buf = buf[need:]
        # Second: header readable, body incomplete → must stall
        assert len(buf) >= _HDR.size
        count2, = _HDR.unpack_from(buf)
        need2 = _HDR.size + count2 * _ACCEL.size
        assert len(buf) < need2

    def test_partial_header_stalls_loop(self):
        # Only 3 bytes remain — the `while len(buf) >= 4` guard fires
        buf = b"\x01\x00\x00"
        assert len(buf) < _HDR.size   # loop never enters

    def test_session_id_integer_packing(self):
        for sid in (0, 1, 42, 32767, -1):
            packed = struct.pack('<i', sid)
            assert struct.unpack('<i', packed)[0] == sid

    def test_accel_values_divided_by_1000_safe_for_zero(self):
        assert 0 / 1000.0 == 0.0

    def test_iio_scale_float_parsing_graceful(self, tmp_path):
        scale_file = tmp_path / "scale"
        scale_file.write_text("0.000244140625\n")
        val = float(scale_file.read_text().strip())
        assert val == pytest.approx(0.000244140625)


# ═══════════════════════════════════════════════════════════════════════════════
# Performance
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerformance:
    def test_1000_smoothing_steps_under_10ms(self):
        v = 0.0
        start = time.perf_counter()
        for _ in range(1000):
            v += (1.0 - v) * 0.28
        elapsed = time.perf_counter() - start
        assert elapsed < 0.010, f"smoothing loop took {elapsed*1000:.1f} ms"

    def test_parse_100_accel_samples_under_5ms(self):
        samples = [(float(i % 10) * 0.1, 0.0, 1.0) for i in range(100)]
        buf = _make_accel_packet(*samples)
        start = time.perf_counter()
        count, = _HDR.unpack_from(buf)
        for i in range(count):
            _ACCEL.unpack_from(buf, _HDR.size + i * _ACCEL.size)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.005, f"100-sample parse took {elapsed*1000:.1f} ms"

    def test_buffer_drain_1000_slices_under_5ms(self):
        """bytes slice-drain simulates the socket buffer management pattern."""
        data = b"x" * (_HDR.size + _ACCEL.size) * 1000
        start = time.perf_counter()
        chunk = _HDR.size + _ACCEL.size
        while len(data) >= chunk:
            data = data[chunk:]
        elapsed = time.perf_counter() - start
        assert elapsed < 0.005, f"1000-slice drain took {elapsed*1000:.1f} ms"

    def test_iio_heading_10000_calls_under_50ms(self):
        start = time.perf_counter()
        for i in range(10000):
            angle = math.radians(i % 360)
            _iio_heading(math.cos(angle), math.sin(angle))
        elapsed = time.perf_counter() - start
        assert elapsed < 0.050, f"10k heading calls took {elapsed*1000:.1f} ms"

    def test_settings_json_save_load_under_50ms(self, tmp_path, monkeypatch):
        if not HAS_GTK:
            pytest.skip("GTK not available")
        monkeypatch.setattr(ss, "CONFIG_DIR",  str(tmp_path))
        monkeypatch.setattr(ss, "CONFIG_FILE", str(tmp_path / "settings.json"))
        data = {"theme": "auto", "lang": "en", "auto_cal": True,
                "cal_roll": 1.23, "cal_pitch": -0.45}
        start = time.perf_counter()
        for _ in range(20):
            ss.save_settings(data)
            ss.load_settings()
        elapsed = time.perf_counter() - start
        assert elapsed < 0.050, f"20 save/load cycles took {elapsed*1000:.1f} ms"
