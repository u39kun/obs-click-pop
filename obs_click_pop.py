import obspython as obs
import sys
import time
import os
from collections import deque

from click_pop_core import map_coords, allocate_slot, expire_circles


def _detect_screen_size():
    """Return (width, height) of the primary screen, or (1920, 1080)."""
    global _retina_scale
    import subprocess
    try:
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        elif sys.platform == "darwin":
            # Prefer Quartz for logical (point) dimensions – these match
            # the coordinate space that pynput reports on macOS.
            # system_profiler returns physical pixels on Retina displays,
            # which would be 2x the logical size and break positioning.
            try:
                import Quartz
                display_id = Quartz.CGMainDisplayID()
                bounds = Quartz.CGDisplayBounds(display_id)
                w, h = int(bounds.size.width), int(bounds.size.height)
                # Detect Retina backing scale factor so we can convert
                # pynput logical-point coords to physical pixels later.
                try:
                    mode = Quartz.CGDisplayCopyDisplayMode(display_id)
                    if mode is not None:
                        pw = Quartz.CGDisplayModeGetPixelWidth(mode)
                        if pw and w > 0:
                            _retina_scale = pw / w
                except Exception:
                    pass
                return (w, h)
            except Exception:
                pass
            out = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"],
                text=True, timeout=5,
            )
            import re
            m = re.search(r"Resolution:\s+(\d+)\s*x\s*(\d+)", out)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                return (w, h)
        else:
            out = subprocess.check_output(
                ["xrandr", "--query"], text=True, timeout=5,
            )
            import re
            m = re.search(r"(\d+)x(\d+)\+0\+0", out)
            if m:
                return (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass
    return (1920, 1080)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
_listener = None          # pynput Listener thread
_click_queue = deque()    # thread‑safe (deque.append / popleft are atomic in CPython)
_timer_active = False
_active_clicks = []       # list of (source_name, expire_time)
_retina_scale = 1.0       # macOS Retina backing scale factor (2.0 on HiDPI)

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
    obs.obs_properties_add_int(
        props, "monitor_w", "Monitor width (px)", 640, 7680, 1,
    )
    obs.obs_properties_add_int(
        props, "monitor_h", "Monitor height (px)", 480, 4320, 1,
    )
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
        props, "btn_start", "Start Listener", _on_start,
    )
    obs.obs_properties_add_button(
        props, "btn_stop", "Stop Listener", _on_stop,
    )
    return props


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


def script_update(settings):
    _settings["left_image"] = obs.obs_data_get_string(settings, "left_image")
    _settings["right_image"] = obs.obs_data_get_string(settings, "right_image")
    _settings["duration_ms"] = obs.obs_data_get_int(settings, "duration_ms")
    _settings["circle_size"] = obs.obs_data_get_int(settings, "circle_size")
    _settings["monitor_w"] = obs.obs_data_get_int(settings, "monitor_w")
    _settings["monitor_h"] = obs.obs_data_get_int(settings, "monitor_h")
    _settings["max_circles"] = obs.obs_data_get_int(settings, "max_circles")
    _settings["capture_source"] = obs.obs_data_get_string(settings, "capture_source")


def script_unload():
    _stop_listener()
    _cleanup_sources()


# ---------------------------------------------------------------------------
# Listener management
# ---------------------------------------------------------------------------

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
    """Create or reuse an image source and position it at (x, y)."""
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
    # same pixel space.  _retina_scale is 1.0 on non-Retina / non-macOS.
    phys_x = x * _retina_scale
    phys_y = y * _retina_scale
    phys_mon_w = _settings["monitor_w"] * _retina_scale
    phys_mon_h = _settings["monitor_h"] * _retina_scale

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
