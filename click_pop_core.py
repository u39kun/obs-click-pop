def find_display_for_point(x, y, displays):
    """Return the display dict whose bounds contain (x, y), or None.

    Each display dict must have keys: x, y, w, h (origin and logical size).
    """
    for d in displays:
        if d["x"] <= x < d["x"] + d["w"] and d["y"] <= y < d["y"] + d["h"]:
            return d
    return None


def map_coords(x, y, canvas_w, canvas_h, monitor_w, monitor_h, circle_size,
               crop_left=0, crop_top=0, capture_pos_x=0, capture_pos_y=0,
               capture_scale_x=None, capture_scale_y=None):
    """Map mouse coordinates to OBS canvas coordinates, centered on the circle.

    When *capture_scale_x/y* are provided (i.e. a Display Capture with known
    crop/position/scale was found), the mapping accounts for cropping and
    the item's transform in the scene.  Otherwise falls back to simple
    proportional mapping across the full monitor.

    Returns (obs_x, obs_y).
    """
    if capture_scale_x is None:
        capture_scale_x = canvas_w / monitor_w
    if capture_scale_y is None:
        capture_scale_y = canvas_h / monitor_h

    cropped_x = x - crop_left
    cropped_y = y - crop_top
    obs_x = capture_pos_x + cropped_x * capture_scale_x - circle_size / 2
    obs_y = capture_pos_y + cropped_y * capture_scale_y - circle_size / 2
    return (obs_x, obs_y)


def allocate_slot(prefix, max_circles, active_clicks):
    """Find a free slot or evict the oldest entry for *prefix*.

    *active_clicks* is a list of ``(source_name, expire_time)`` tuples and
    **may be mutated** (the evicted entry is removed in-place so the caller
    doesn't double-count it).

    Returns ``(slot_name, evicted_name | None)``.
    """
    # Try to find a free slot
    for i in range(max_circles):
        candidate = f"{prefix}{i}"
        in_use = any(n == candidate for n, _ in active_clicks)
        if not in_use:
            return (candidate, None)

    # All slots busy — evict the oldest matching this prefix
    for i, (n, _) in enumerate(active_clicks):
        if n.startswith(prefix):
            active_clicks.pop(i)
            return (n, n)

    # Fallback (shouldn't happen if active_clicks is consistent)
    return (f"{prefix}0", None)


def expire_circles(active_clicks, now):
    """Partition *active_clicks* into still-active and expired.

    Returns ``(still_active, expired_names)`` where *still_active* has the
    same ``(name, expire_time)`` shape and *expired_names* is a plain list
    of source names.
    """
    still_active = []
    expired_names = []
    for name, expire_t in active_clicks:
        if now >= expire_t:
            expired_names.append(name)
        else:
            still_active.append((name, expire_t))
    return (still_active, expired_names)
