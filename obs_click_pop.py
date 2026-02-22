import obspython as obs
import sys
import time
import os
from collections import deque

from click_pop_core import map_coords, allocate_slot, expire_circles, find_display_for_point


# ---------------------------------------------------------------------------
# Display detection
# ---------------------------------------------------------------------------

def _detect_all_displays():
    """Return a list of display descriptors for all connected monitors.

    Each descriptor is a dict with keys:
      id            – platform display identifier
      x, y          – origin in virtual desktop space (logical points)
      w, h          – logical resolution
      retina_scale  – backing scale factor (2.0 on macOS Retina, else 1.0)

    Falls back to a single 1920x1080 display if detection fails.
    """
    import subprocess
    displays = []
    try:
        if sys.platform == "win32":
            displays = _detect_displays_win32()
        elif sys.platform == "darwin":
            displays = _detect_displays_macos()
        else:
            displays = _detect_displays_linux()
    except Exception:
        pass
    if not displays:
        displays = [{"id": 0, "x": 0, "y": 0, "w": 1920, "h": 1080,
                      "retina_scale": 1.0}]
    return displays


def _detect_displays_macos():
    """Enumerate displays on macOS via Quartz."""
    import Quartz
    max_displays = 16
    (err, display_ids, count) = Quartz.CGGetActiveDisplayList(max_displays, None, None)
    if err != 0:
        return []
    displays = []
    for did in display_ids[:count]:
        bounds = Quartz.CGDisplayBounds(did)
        w, h = int(bounds.size.width), int(bounds.size.height)
        x, y = int(bounds.origin.x), int(bounds.origin.y)
        retina_scale = 1.0
        try:
            mode = Quartz.CGDisplayCopyDisplayMode(did)
            if mode is not None:
                pw = Quartz.CGDisplayModeGetPixelWidth(mode)
                if pw and w > 0:
                    retina_scale = pw / w
        except Exception:
            pass
        displays.append({"id": did, "x": x, "y": y, "w": w, "h": h,
                          "retina_scale": retina_scale})
    return displays


def _detect_displays_win32():
    """Enumerate displays on Windows via ctypes."""
    import ctypes
    import ctypes.wintypes

    displays = []
    user32 = ctypes.windll.user32

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_ulong,      # hMonitor
        ctypes.c_ulong,      # hdcMonitor
        ctypes.POINTER(ctypes.wintypes.RECT),  # lprcMonitor
        ctypes.c_double,     # dwData
    )

    def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        r = lprcMonitor[0]
        displays.append({
            "id": hMonitor,
            "x": r.left, "y": r.top,
            "w": r.right - r.left, "h": r.bottom - r.top,
            "retina_scale": 1.0,
        })
        return 1  # continue enumeration

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return displays


def _detect_displays_linux():
    """Enumerate displays on Linux via xrandr."""
    import subprocess, re
    out = subprocess.check_output(["xrandr", "--query"], text=True, timeout=5)
    displays = []
    idx = 0
    for m in re.finditer(r"(\d+)x(\d+)\+(\d+)\+(\d+)", out):
        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        displays.append({"id": idx, "x": x, "y": y, "w": w, "h": h,
                          "retina_scale": 1.0})
        idx += 1
    return displays


def _detect_screen_size():
    """Return (width, height) of the primary screen, or (1920, 1080).

    Thin wrapper for backward compatibility — uses the first display from
    ``_detect_all_displays()``.
    """
    global _retina_scale, _all_displays
    _all_displays = _detect_all_displays()
    if _all_displays:
        d = _all_displays[0]
        _retina_scale = d.get("retina_scale", 1.0)
        return (d["w"], d["h"])
    return (1920, 1080)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
_listener = None          # pynput Listener thread
_click_queue = deque()    # thread‑safe (deque.append / popleft are atomic in CPython)
_timer_active = False
_active_clicks = []       # list of (source_name, expire_time)
_retina_scale = 1.0       # macOS Retina backing scale factor (2.0 on HiDPI)
_all_displays = []        # list of display descriptors from _detect_all_displays()
_captured_display = None  # display dict for the monitor being captured (or None)

# Settings with defaults
_settings = {
    "left_image": "",
    "right_image": "",
    "duration_ms": 350,
    "circle_size": 60,
    "monitor_w": 1920,
    "monitor_h": 1080,
    "max_circles": 5,
    "capture_source": "",
    "override_monitor": False,
}

# ---------------------------------------------------------------------------
# OBS Script Boilerplate
# ---------------------------------------------------------------------------

def script_description():
    return (
        "<h2>Click Pop</h2>"
        "<p>Renders a circle in the OBS scene on every mouse click — "
        "visible only in recordings / streams, <b>not</b> on the actual desktop.</p>"
        "<p>Requires the <code>pynput</code> Python package.</p>"
    )


def script_properties():
    props = obs.obs_properties_create()

    obs.obs_properties_add_path(
        props, "left_image", "Left‑click image",
        obs.OBS_PATH_FILE, "PNG (*.png)", None,
    )
    obs.obs_properties_add_path(
        props, "right_image", "Right‑click image",
        obs.OBS_PATH_FILE, "PNG (*.png)", None,
    )
    obs.obs_properties_add_int(
        props, "duration_ms", "Circle duration (ms)", 100, 2000, 50,
    )
    obs.obs_properties_add_int(
        props, "circle_size", "Circle diameter (px)", 20, 300, 5,
    )

    override_prop = obs.obs_properties_add_bool(
        props, "override_monitor", "Override monitor dimensions",
    )
    obs.obs_property_set_modified_callback(override_prop, _on_override_toggle)

    p_w = obs.obs_properties_add_int(
        props, "monitor_w", "Monitor width (px)", 640, 7680, 1,
    )
    p_h = obs.obs_properties_add_int(
        props, "monitor_h", "Monitor height (px)", 480, 4320, 1,
    )
    # Hide manual dimension fields when override is off
    obs.obs_property_set_visible(p_w, _settings["override_monitor"])
    obs.obs_property_set_visible(p_h, _settings["override_monitor"])

    obs.obs_properties_add_int(
        props, "max_circles", "Max circles per click type", 1, 20, 1,
    )
    capture_list = obs.obs_properties_add_list(
        props, "capture_source",
        "Display Capture source (blank = no crop adjust)",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING,
    )
    obs.obs_property_list_add_string(capture_list, "(none)", "")
    _populate_capture_list(capture_list)
    obs.obs_properties_add_button(
        props, "btn_refresh", "Refresh Displays", _on_refresh_displays,
    )
    obs.obs_properties_add_button(
        props, "btn_start", "Start Listener", _on_start,
    )
    obs.obs_properties_add_button(
        props, "btn_stop", "Stop Listener", _on_stop,
    )

    # Show detected displays as informational text
    _add_display_info(props)

    return props


def _on_override_toggle(props, prop, settings):
    """Show/hide manual monitor dimension fields when checkbox is toggled."""
    override = obs.obs_data_get_bool(settings, "override_monitor")
    p_w = obs.obs_properties_get(props, "monitor_w")
    p_h = obs.obs_properties_get(props, "monitor_h")
    obs.obs_property_set_visible(p_w, override)
    obs.obs_property_set_visible(p_h, override)
    return True


def _add_display_info(props):
    """Add informational text showing detected displays."""
    if not _all_displays:
        return
    lines = ["Detected displays:"]
    for i, d in enumerate(_all_displays):
        retina = d.get("retina_scale", 1.0)
        label = f"  Display {i + 1}: {d['w']}x{d['h']} @ ({d['x']},{d['y']})"
        if retina != 1.0:
            label += f" [{retina:.0f}x Retina]"
        if _captured_display is d:
            label += " [CAPTURED]"
        lines.append(label)
    obs.obs_properties_add_text(
        props, "_display_info", "\n".join(lines), obs.OBS_TEXT_INFO,
    )


def script_defaults(settings):
    here = os.path.dirname(os.path.abspath(__file__))
    obs.obs_data_set_default_string(
        settings, "left_image", os.path.join(here, "click_circle.png"),
    )
    obs.obs_data_set_default_string(
        settings, "right_image", os.path.join(here, "click_circle_right.png"),
    )
    obs.obs_data_set_default_int(settings, "duration_ms", 350)
    obs.obs_data_set_default_int(settings, "circle_size", 60)
    mon_w, mon_h = _detect_screen_size()
    obs.obs_data_set_default_int(settings, "monitor_w", mon_w)
    obs.obs_data_set_default_int(settings, "monitor_h", mon_h)
    obs.obs_data_set_default_int(settings, "max_circles", 5)
    obs.obs_data_set_default_string(settings, "capture_source", "")
    obs.obs_data_set_default_bool(settings, "override_monitor", False)


def script_update(settings):
    _settings["left_image"] = obs.obs_data_get_string(settings, "left_image")
    _settings["right_image"] = obs.obs_data_get_string(settings, "right_image")
    _settings["duration_ms"] = obs.obs_data_get_int(settings, "duration_ms")
    _settings["circle_size"] = obs.obs_data_get_int(settings, "circle_size")
    _settings["override_monitor"] = obs.obs_data_get_bool(settings, "override_monitor")
    _settings["monitor_w"] = obs.obs_data_get_int(settings, "monitor_w")
    _settings["monitor_h"] = obs.obs_data_get_int(settings, "monitor_h")
    _settings["max_circles"] = obs.obs_data_get_int(settings, "max_circles")
    _settings["capture_source"] = obs.obs_data_get_string(settings, "capture_source")
    # Re-resolve which display is being captured when settings change
    _refresh_displays()
    # Auto-set monitor dimensions from captured display when not overridden
    if not _settings["override_monitor"]:
        if _captured_display is not None:
            _settings["monitor_w"] = _captured_display["w"]
            _settings["monitor_h"] = _captured_display["h"]


def script_unload():
    _stop_listener()
    _cleanup_sources()


# ---------------------------------------------------------------------------
# Listener management
# ---------------------------------------------------------------------------

def _refresh_displays():
    """Re-enumerate displays and resolve the captured display."""
    global _all_displays, _retina_scale
    _all_displays = _detect_all_displays()
    # Update _retina_scale from the primary display for backward compat
    if _all_displays:
        _retina_scale = _all_displays[0].get("retina_scale", 1.0)
    try:
        _resolve_captured_display()
    except Exception:
        pass
    # Log detected configuration for debugging multi-monitor issues
    for i, d in enumerate(_all_displays):
        obs.script_log(obs.LOG_INFO,
                       f"Click Pop: display {i}: {d['w']}x{d['h']} "
                       f"@ ({d['x']},{d['y']}) retina={d.get('retina_scale',1.0)}")
    if _captured_display:
        obs.script_log(obs.LOG_INFO,
                       f"Click Pop: captured display: "
                       f"{_captured_display['w']}x{_captured_display['h']} "
                       f"@ ({_captured_display['x']},{_captured_display['y']})")
    else:
        obs.script_log(obs.LOG_INFO,
                       "Click Pop: no captured display resolved — "
                       "clicks on all displays will show circles")


def _on_refresh_displays(props, prop):
    _refresh_displays()
    # Repopulate the Display Capture source dropdown
    capture_list = obs.obs_properties_get(props, "capture_source")
    if capture_list is not None:
        obs.obs_property_list_clear(capture_list)
        obs.obs_property_list_add_string(capture_list, "(none)", "")
        _populate_capture_list(capture_list)
    n = len(_all_displays)
    cap = "yes" if _captured_display else "no"
    obs.script_log(obs.LOG_INFO,
                   f"Click Pop: refreshed — {n} display(s), captured={cap}")
    return True


def _on_start(props, prop):
    _start_listener()
    return True


def _on_stop(props, prop):
    _stop_listener()
    return True


def _start_listener():
    global _listener, _timer_active
    if _listener is not None:
        return  # already running

    try:
        from pynput.mouse import Listener, Button
    except ImportError:
        obs.script_log(obs.LOG_ERROR, "pynput is not installed. Run: pip install pynput")
        return

    def on_click(x, y, button, pressed):
        if pressed:
            is_left = (button == Button.left)
            _click_queue.append((x, y, is_left, time.time()))

    _listener = Listener(on_click=on_click)
    _listener.daemon = True
    _listener.start()

    if not _timer_active:
        obs.timer_add(_poll_clicks, 16)  # ~60 fps polling
        _timer_active = True

    obs.script_log(obs.LOG_INFO, "Click Pop: listener started")


def _stop_listener():
    global _listener, _timer_active
    if _listener is not None:
        _listener.stop()
        _listener = None

    if _timer_active:
        obs.timer_remove(_poll_clicks)
        _timer_active = False

    obs.script_log(obs.LOG_INFO, "Click Pop: listener stopped")


# ---------------------------------------------------------------------------
# Display Capture detection — crop / position / scale
# ---------------------------------------------------------------------------

_DISPLAY_CAPTURE_PREFIXES = (
    "xshm_input",         # Linux X11 (xshm_input, xshm_input_v2, …)
    "monitor_capture",    # Windows
    "screen_capture",     # macOS (ScreenCaptureKit)
    "display_capture",    # macOS (legacy)
)


def _populate_capture_list(prop):
    """Add current-scene Display Capture sources to a combo-box property.

    Uses ``obs_scene_save_transform_states`` to discover source names (avoids
    ``obs_scene_enum_items`` which has a broken SWIG wrapper in OBS ≤32.0.x).
    """
    try:
        scene_src = obs.obs_frontend_get_current_scene()
        scene = obs.obs_scene_from_source(scene_src)
        obs.obs_source_release(scene_src)
        if scene is None:
            return

        # Parse the transform-states JSON to get scene-item IDs, then look
        # up each item by ID (avoids obs_scene_enum_items which has a broken
        # SWIG wrapper in OBS ≤32.0.x).
        import json
        data = obs.obs_scene_save_transform_states(scene, True)
        json_str = obs.obs_data_get_json(data)
        obs.obs_data_release(data)
        if not json_str:
            return

        parsed = json.loads(json_str)
        for scene_info in parsed.get("scenes_and_groups", []):
            for item_info in scene_info.get("items", []):
                item_id = item_info.get("id")
                if item_id is None:
                    continue
                item = obs.obs_scene_find_sceneitem_by_id(scene, item_id)
                if item is None:
                    continue
                source = obs.obs_sceneitem_get_source(item)
                src_id = obs.obs_source_get_unversioned_id(source)
                if src_id.startswith(_DISPLAY_CAPTURE_PREFIXES):
                    name = obs.obs_source_get_name(source)
                    obs.obs_property_list_add_string(prop, name, name)
    except Exception as exc:
        obs.script_log(obs.LOG_INFO,
                       f"Click Pop: capture list populate failed: {exc}")


def _get_filter_crop(source):
    """Read crop values from a Crop/Pad filter on *source*, if any.

    Iterates the source's filter list via ``obs_source_backup_filters``
    (avoids the broken callback-based ``obs_source_enum_filters``).

    Returns ``(left, top, right, bottom)`` or ``(0, 0, 0, 0)`` if no
    crop filter is found.
    """
    import json
    filters = obs.obs_source_backup_filters(source)
    count = obs.obs_data_array_count(filters)
    left = top = right = bottom = 0
    for i in range(count):
        fdata = obs.obs_data_array_item(filters, i)
        fjson = obs.obs_data_get_json(fdata)
        obs.obs_data_release(fdata)
        if not fjson:
            continue
        fobj = json.loads(fjson)
        fid = fobj.get("id", "")
        if fid == "crop_filter":
            settings = fobj.get("settings", {})
            left = settings.get("left", 0)
            top = settings.get("top", 0)
            right = settings.get("right", 0)
            bottom = settings.get("bottom", 0)
            break
    obs.obs_data_array_release(filters)
    return (left, top, right, bottom)


def _display_uuid_via_ctypes(display_id):
    """Get the UUID string for a CGDirectDisplayID using ctypes.

    PyObjC doesn't expose ``CGDisplayCreateUUIDFromDisplayID`` in all
    environments (notably the Python bundled with OBS), so we call the
    CoreGraphics C function directly via ctypes.

    Returns a UUID string like ``"09FA8E3F-DD10-3AB8-E04B-86F97A791ED1"``
    or ``None`` on failure.
    """
    import ctypes

    # CGDisplayCreateUUIDFromDisplayID lives in the ColorSync framework
    # (not CoreGraphics) on modern macOS.
    cs = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ColorSync.framework/ColorSync")
    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    cs.CGDisplayCreateUUIDFromDisplayID.argtypes = [ctypes.c_uint32]
    cs.CGDisplayCreateUUIDFromDisplayID.restype = ctypes.c_void_p

    cf.CFUUIDCreateString.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFUUIDCreateString.restype = ctypes.c_void_p

    cf.CFStringGetCStringPtr.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    cf.CFStringGetCStringPtr.restype = ctypes.c_char_p

    cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
    cf.CFStringGetLength.restype = ctypes.c_long

    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                      ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool

    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None

    kCFStringEncodingUTF8 = 0x08000100

    uuid_ref = cs.CGDisplayCreateUUIDFromDisplayID(
        ctypes.c_uint32(int(display_id)))
    if not uuid_ref:
        return None

    str_ref = cf.CFUUIDCreateString(None, uuid_ref)
    if not str_ref:
        cf.CFRelease(uuid_ref)
        return None

    result = None
    c_str = cf.CFStringGetCStringPtr(str_ref, kCFStringEncodingUTF8)
    if c_str:
        result = c_str.decode("utf-8")
    else:
        length = cf.CFStringGetLength(str_ref)
        buf = ctypes.create_string_buffer(length * 4 + 1)
        if cf.CFStringGetCString(str_ref, buf, len(buf), kCFStringEncodingUTF8):
            result = buf.value.decode("utf-8")

    cf.CFRelease(str_ref)
    cf.CFRelease(uuid_ref)
    return result


def _resolve_captured_display():
    """Determine which physical display the selected capture source records.

    Reads platform-specific properties from the Display Capture source and
    matches against ``_all_displays``.  Sets the module-level
    ``_captured_display`` to the matching display dict, or leaves it as
    ``None`` if no confident match is found (in which case no clicks are
    discarded — better to show extra circles than miss all of them).
    """
    global _captured_display
    _captured_display = None

    name = _settings.get("capture_source", "")
    if not name or not _all_displays:
        return

    source = obs.obs_get_source_by_name(name)
    if source is None:
        return

    src_id = obs.obs_source_get_unversioned_id(source)
    settings = obs.obs_source_get_settings(source)

    try:
        if sys.platform == "darwin":
            if src_id.startswith("screen_capture"):
                display_val = obs.obs_data_get_int(settings, "display")
                display_uuid = obs.obs_data_get_string(settings, "display_uuid")
                obs.script_log(obs.LOG_INFO,
                               f"Click Pop: resolving screen_capture — "
                               f"src_id={src_id!r}, display={display_val}, "
                               f"display_uuid={display_uuid!r}, "
                               f"known IDs={[int(d['id']) for d in _all_displays]}")

                # 1. Try UUID match via ctypes (primary method for OBS 30+)
                if display_uuid:
                    norm_uuid = display_uuid.strip("{}").upper()
                    try:
                        for d in _all_displays:
                            d_uuid = _display_uuid_via_ctypes(d["id"])
                            if d_uuid and d_uuid.strip("{}").upper() == norm_uuid:
                                _captured_display = d
                                obs.script_log(obs.LOG_INFO,
                                               f"Click Pop: matched display by UUID")
                                return
                    except Exception as exc:
                        obs.script_log(obs.LOG_INFO,
                                       f"Click Pop: UUID matching failed: {exc}")

                # 2. Fall back to CGDirectDisplayID match
                if display_val:
                    for d in _all_displays:
                        if int(d["id"]) == int(display_val):
                            _captured_display = d
                            obs.script_log(obs.LOG_INFO,
                                           f"Click Pop: matched display by ID")
                            return

                # 3. Fall back to source output dimensions
                src_w = obs.obs_source_get_width(source)
                src_h = obs.obs_source_get_height(source)
                if src_w and src_h:
                    for d in _all_displays:
                        phys_w = int(d["w"] * d.get("retina_scale", 1.0))
                        phys_h = int(d["h"] * d.get("retina_scale", 1.0))
                        if phys_w == src_w and phys_h == src_h:
                            _captured_display = d
                            obs.script_log(obs.LOG_INFO,
                                           f"Click Pop: matched display by "
                                           f"dimensions {src_w}x{src_h}")
                            return

                obs.script_log(obs.LOG_INFO,
                               "Click Pop: screen_capture display match failed")
            elif src_id.startswith("display_capture"):
                # Legacy display_capture: "display" is a 0-based index
                display_idx = obs.obs_data_get_int(settings, "display")
                if 0 <= display_idx < len(_all_displays):
                    _captured_display = _all_displays[display_idx]
                    return
        elif sys.platform == "win32":
            # Windows monitor_capture: "monitor" is a 0-based index
            monitor_idx = obs.obs_data_get_int(settings, "monitor")
            if 0 <= monitor_idx < len(_all_displays):
                _captured_display = _all_displays[monitor_idx]
                return
        else:
            # Linux xshm_input: "screen" is typically 0 for first X screen
            screen_idx = obs.obs_data_get_int(settings, "screen")
            if 0 <= screen_idx < len(_all_displays):
                _captured_display = _all_displays[screen_idx]
                return
    except Exception as exc:
        obs.script_log(obs.LOG_INFO,
                       f"Click Pop: _resolve_captured_display error: {exc}")
    finally:
        obs.obs_data_release(settings)
        obs.obs_source_release(source)


def _get_capture_transform(scene):
    """Read crop / position / scale from the named Display Capture source.

    Checks both the scene-item crop (Edit Transform) and Crop/Pad filters.

    Returns ``(crop_left, crop_top, pos_x, pos_y, scale_x, scale_y)``
    or ``None`` if no source name is configured or the source isn't found.
    """
    name = _settings.get("capture_source", "")
    if not name:
        return None

    item = obs.obs_scene_find_source_recursive(scene, name)
    if item is None:
        return None

    source = obs.obs_sceneitem_get_source(item)

    # 1. Source-level crop (set via source Properties, e.g. XSHM "Crop Left")
    src_settings = obs.obs_source_get_settings(source)
    src_crop_left = obs.obs_data_get_int(src_settings, "cut_left")
    src_crop_top = obs.obs_data_get_int(src_settings, "cut_top")
    src_crop_right = obs.obs_data_get_int(src_settings, "cut_right")
    src_crop_bottom = obs.obs_data_get_int(src_settings, "cut_bottom")
    obs.obs_data_release(src_settings)

    # 2. Scene-item crop (set via Edit Transform / Alt-drag)
    item_crop = obs.obs_sceneitem_crop()
    obs.obs_sceneitem_get_crop(item, item_crop)

    # 3. Crop/Pad filter crop (set via Filters)
    flt_left, flt_top, flt_right, flt_bottom = _get_filter_crop(source)

    # Total crop offset for coordinate mapping (left/top only)
    crop_left = src_crop_left + item_crop.left + flt_left
    crop_top = src_crop_top + item_crop.top + flt_top

    pos = obs.vec2()
    obs.obs_sceneitem_get_pos(item, pos)

    # Compute effective scale and centering offset.
    #
    # obs_source_get_width/height returns the post-crop output size.  The
    # scene-item crop is applied on top of that before bounds scaling.
    # With SCALE_INNER / SCALE_OUTER, one dimension may not fill the
    # bounds, so OBS centers the content — we must account for that offset.
    bounds_type = obs.obs_sceneitem_get_bounds_type(item)
    offset_x = 0.0
    offset_y = 0.0
    if bounds_type != obs.OBS_BOUNDS_NONE:
        bounds = obs.vec2()
        obs.obs_sceneitem_get_bounds(item, bounds)

        # The size OBS actually scales is the source output minus item crop
        src_w = obs.obs_source_get_width(source) or 1
        src_h = obs.obs_source_get_height(source) or 1
        vis_w = max(src_w - item_crop.left - item_crop.right, 1)
        vis_h = max(src_h - item_crop.top - item_crop.bottom, 1)

        if bounds_type == obs.OBS_BOUNDS_STRETCH:
            scale_x = bounds.x / vis_w
            scale_y = bounds.y / vis_h
        elif bounds_type == obs.OBS_BOUNDS_SCALE_INNER:
            s = min(bounds.x / vis_w, bounds.y / vis_h)
            scale_x = scale_y = s
        elif bounds_type == obs.OBS_BOUNDS_SCALE_OUTER:
            s = max(bounds.x / vis_w, bounds.y / vis_h)
            scale_x = scale_y = s
        elif bounds_type == obs.OBS_BOUNDS_SCALE_TO_WIDTH:
            scale_x = scale_y = bounds.x / vis_w
        elif bounds_type == obs.OBS_BOUNDS_SCALE_TO_HEIGHT:
            scale_x = scale_y = bounds.y / vis_h
        else:
            scale_x = bounds.x / vis_w
            scale_y = bounds.y / vis_h

        # Centering offset (bounds_alignment=0 means centered)
        offset_x = (bounds.x - vis_w * scale_x) / 2.0
        offset_y = (bounds.y - vis_h * scale_y) / 2.0
    else:
        scale = obs.vec2()
        obs.obs_sceneitem_get_scale(item, scale)
        scale_x = scale.x
        scale_y = scale.y

    return (crop_left, crop_top,
            pos.x + offset_x, pos.y + offset_y,
            scale_x, scale_y)


# ---------------------------------------------------------------------------
# OBS timer callback — runs on the UI thread
# ---------------------------------------------------------------------------

def _poll_clicks():
    now = time.time()
    duration_s = _settings["duration_ms"] / 1000.0

    # Drain new clicks from the queue
    while _click_queue:
        x, y, is_left, t = _click_queue.popleft()
        _spawn_circle(x, y, is_left, t + duration_s)

    # Expire old circles
    still_active, expired = expire_circles(_active_clicks, now)
    for name in expired:
        _hide_source(name)
    _active_clicks[:] = still_active


def _spawn_circle(x, y, is_left, expire_time):
    """Create or reuse an image source and position it at (x, y).

    Coordinates (x, y) are in virtual-desktop space (as reported by pynput).
    Multi-monitor aware: determines which display was clicked, converts to
    display-local coordinates, and discards clicks on non-captured displays.
    """
    # --- Multi-monitor: determine which display the click landed on ---
    # Only use per-display logic when multiple displays are detected.
    # Single-display setups fall through to the legacy path so that the
    # user's manual monitor_w / monitor_h settings are always respected.
    display = None
    if len(_all_displays) > 1:
        display = find_display_for_point(x, y, _all_displays)

        # If a specific display is being captured, discard clicks on other
        # displays.  Only applies when we have multiple displays — single
        # display should never discard.
        if _captured_display is not None and display is not _captured_display:
            return

    # Use display-specific values when a multi-monitor hit was found,
    # otherwise fall back to settings (preserves single-display behavior).
    if display is not None:
        local_x = x - display["x"]
        local_y = y - display["y"]
        mon_w = display["w"]
        mon_h = display["h"]
        retina = display.get("retina_scale", 1.0)
    else:
        local_x = x
        local_y = y
        mon_w = _settings["monitor_w"]
        mon_h = _settings["monitor_h"]
        retina = _retina_scale

    # Pick a source name from a pool so we can show multiple simultaneous
    prefix = "__click_pop_L_" if is_left else "__click_pop_R_"
    max_c = _settings["max_circles"]

    src_name, evicted = allocate_slot(prefix, max_c, _active_clicks)
    if evicted is not None:
        _hide_source(evicted)

    image_path = _settings["left_image"] if is_left else _settings["right_image"]
    size = _settings["circle_size"]

    # Map mouse coords → OBS canvas coords
    scene_src = obs.obs_frontend_get_current_scene()
    canvas_w = obs.obs_source_get_width(scene_src) or _settings["monitor_w"]
    canvas_h = obs.obs_source_get_height(scene_src) or _settings["monitor_h"]
    scene = obs.obs_scene_from_source(scene_src)
    transform = _get_capture_transform(scene) if scene else None
    obs.obs_source_release(scene_src)

    kwargs = {}
    if transform is not None:
        crop_left, crop_top, pos_x, pos_y, scale_x, scale_y = transform
        kwargs = dict(crop_left=crop_left, crop_top=crop_top,
                      capture_pos_x=pos_x, capture_pos_y=pos_y,
                      capture_scale_x=scale_x, capture_scale_y=scale_y)

    # On macOS Retina, pynput reports logical "points" but OBS and the
    # capture source work in physical pixels (2x on HiDPI).  Scale both
    # the mouse coords and monitor dimensions so everything is in the
    # same pixel space.  retina is 1.0 on non-Retina / non-macOS.
    phys_x = local_x * retina
    phys_y = local_y * retina
    phys_mon_w = mon_w * retina
    phys_mon_h = mon_h * retina

    obs_x, obs_y = map_coords(phys_x, phys_y, canvas_w, canvas_h,
                              phys_mon_w, phys_mon_h,
                              size, **kwargs)

    _show_source(src_name, image_path, obs_x, obs_y, size)
    _active_clicks.append((src_name, expire_time))


# ---------------------------------------------------------------------------
# OBS Source helpers
# ---------------------------------------------------------------------------

def _get_current_scene():
    scene_source = obs.obs_frontend_get_current_scene()
    # obs_scene_from_source does not increment the ref count — no release needed for scene
    scene = obs.obs_scene_from_source(scene_source)
    obs.obs_source_release(scene_source)
    return scene


def _show_source(name, image_path, x, y, size):
    scene = _get_current_scene()
    if scene is None:
        return

    scene_item = obs.obs_scene_find_source(scene, name)

    if scene_item is None:
        # Source not in scene — check if it exists globally (e.g. from a
        # previous session) and reuse it, otherwise create a new one.
        source = obs.obs_get_source_by_name(name)
        if source is None:
            settings = obs.obs_data_create()
            obs.obs_data_set_string(settings, "file", image_path)
            source = obs.obs_source_create("image_source", name, settings, None)
            obs.obs_data_release(settings)
        else:
            # Update image path on reused source (may be stale from a
            # previous session or OBS restart).
            settings = obs.obs_source_get_settings(source)
            obs.obs_data_set_string(settings, "file", image_path)
            obs.obs_source_update(source, settings)
            obs.obs_data_release(settings)
        scene_item = obs.obs_scene_add(scene, source)
        obs.obs_source_release(source)
    else:
        # Update the image path in case it changed
        source = obs.obs_sceneitem_get_source(scene_item)
        settings = obs.obs_source_get_settings(source)
        obs.obs_data_set_string(settings, "file", image_path)
        obs.obs_source_update(source, settings)
        obs.obs_data_release(settings)

    # Position and scale
    pos = obs.vec2()
    pos.x = x
    pos.y = y
    obs.obs_sceneitem_set_pos(scene_item, pos)

    # Scale the source to the desired circle size
    source = obs.obs_sceneitem_get_source(scene_item)
    src_w = obs.obs_source_get_width(source)
    if src_w and src_w > 0:
        s = size / src_w
        scale = obs.vec2()
        scale.x = s
        scale.y = s
        obs.obs_sceneitem_set_scale(scene_item, scale)

    obs.obs_sceneitem_set_visible(scene_item, True)


def _hide_source(name):
    scene = _get_current_scene()
    if scene is None:
        return
    scene_item = obs.obs_scene_find_source(scene, name)
    if scene_item is not None:
        obs.obs_sceneitem_set_visible(scene_item, False)


def _cleanup_sources():
    """Remove all __click_pop_* sources from the current scene on unload."""
    scene = _get_current_scene()
    if scene is None:
        return
    max_c = _settings["max_circles"]
    for prefix in ("__click_pop_L_", "__click_pop_R_"):
        for i in range(max_c):
            name = f"{prefix}{i}"
            scene_item = obs.obs_scene_find_source(scene, name)
            if scene_item is not None:
                obs.obs_sceneitem_remove(scene_item)
            source = obs.obs_get_source_by_name(name)
            if source is not None:
                obs.obs_source_remove(source)
                obs.obs_source_release(source)
    _active_clicks.clear()
