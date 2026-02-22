"""Tier 1 — §1.1: Coordinate mapping (pure logic, no OBS)."""

import pytest
from click_pop_core import map_coords


@pytest.mark.parametrize(
    "x, y, canvas_w, canvas_h, monitor_w, monitor_h, size, expected",
    [
        # 1. Center of screen → center of canvas
        (960, 540, 1920, 1080, 1920, 1080, 80, (920.0, 500.0)),
        # 2. Top-left corner
        (0, 0, 1920, 1080, 1920, 1080, 80, (-40.0, -40.0)),
        # 3. Bottom-right corner
        (1920, 1080, 1920, 1080, 1920, 1080, 80, (1880.0, 1040.0)),
        # 4. Canvas differs from monitor (scaled)
        (960, 540, 1280, 720, 1920, 1080, 80, (600.0, 320.0)),
        # 5. Different circle size
        (100, 100, 1920, 1080, 1920, 1080, 40, (80.0, 80.0)),
        # 6. 4K monitor → 1080p canvas
        (1920, 1080, 1920, 1080, 3840, 2160, 80, (920.0, 500.0)),
    ],
    ids=[
        "center",
        "top_left",
        "bottom_right",
        "scaled_canvas",
        "small_circle",
        "4k_to_1080p",
    ],
)
def test_map_coords(x, y, canvas_w, canvas_h, monitor_w, monitor_h, size, expected):
    result = map_coords(x, y, canvas_w, canvas_h, monitor_w, monitor_h, size)
    assert result == pytest.approx(expected)


# -------------------------------------------------------------------
# Cropped / bounded Display Capture scenarios
# -------------------------------------------------------------------

@pytest.mark.parametrize(
    "x, y, canvas_w, canvas_h, monitor_w, monitor_h, size, "
    "crop_left, crop_top, pos_x, pos_y, scale_x, scale_y, expected",
    [
        # 1. Right-half crop (left=960 on 1920 monitor), 1:1 scale, click at
        #    monitor x=1440 (480px into the visible region).
        #    obs_x = 0 + (1440-960)*1.0 - 40 = 440
        #    obs_y = 0 + (540-0)*1.0 - 40 = 500
        (1440, 540, 1920, 1080, 1920, 1080, 80,
         960, 0, 0, 0, 1.0, 1.0,
         (440.0, 500.0)),
        # 2. Crop with 0.5x scale and position offset (100, 50).
        #    crop_left=640, click at monitor x=1280 → cropped_x = 640
        #    obs_x = 100 + 640*0.5 - 40 = 380
        #    obs_y = 50 + 540*0.5 - 40 = 280
        (1280, 540, 1920, 1080, 1920, 1080, 80,
         640, 0, 100, 50, 0.5, 0.5,
         (380.0, 280.0)),
        # 3. No crop, no offset, scale = canvas/monitor (same as default path)
        #    This verifies backwards compatibility when explicit scale is given.
        #    obs_x = 0 + 960*1.0 - 40 = 920
        #    obs_y = 0 + 540*1.0 - 40 = 500
        (960, 540, 1920, 1080, 1920, 1080, 80,
         0, 0, 0, 0, 1.0, 1.0,
         (920.0, 500.0)),
        # 4. Top+left crop with 2x scale (zoomed-in sub-region).
        #    crop_left=200, crop_top=100, click at (300, 200)
        #    obs_x = 0 + (300-200)*2.0 - 40 = 160
        #    obs_y = 0 + (200-100)*2.0 - 40 = 160
        (300, 200, 1920, 1080, 1920, 1080, 80,
         200, 100, 0, 0, 2.0, 2.0,
         (160.0, 160.0)),
    ],
    ids=[
        "right_half_crop",
        "crop_with_half_scale_and_offset",
        "no_crop_explicit_scale",
        "crop_with_2x_zoom",
    ],
)
def test_map_coords_cropped(x, y, canvas_w, canvas_h, monitor_w, monitor_h, size,
                            crop_left, crop_top, pos_x, pos_y, scale_x, scale_y,
                            expected):
    result = map_coords(x, y, canvas_w, canvas_h, monitor_w, monitor_h, size,
                        crop_left=crop_left, crop_top=crop_top,
                        capture_pos_x=pos_x, capture_pos_y=pos_y,
                        capture_scale_x=scale_x, capture_scale_y=scale_y)
    assert result == pytest.approx(expected)


# -------------------------------------------------------------------
# macOS Retina scaling scenarios
# -------------------------------------------------------------------
# On Retina, pynput reports logical points (1680x1050) but OBS works
# in physical pixels (3360x2100).  _spawn_circle multiplies mouse
# coords and monitor dims by _retina_scale before calling map_coords,
# so these tests verify map_coords receives the already-scaled values.

@pytest.mark.parametrize(
    "x, y, canvas_w, canvas_h, monitor_w, monitor_h, size, expected",
    [
        # Retina 2x: pynput center (840,525) * 2 = (1680,1050),
        # monitor 1680*2=3360, canvas 3360.  scale = 3360/3360 = 1.0
        # obs_x = 1680*1.0 - 40 = 1640
        (1680, 1050, 3360, 2100, 3360, 2100, 80, (1640.0, 1010.0)),
        # Retina 2x with a smaller canvas (1920x1080):
        # scale_x = 1920/3360 ≈ 0.5714, scale_y = 1080/2100 ≈ 0.5143
        # obs_x = 1680*0.5714 - 40 ≈ 920
        # obs_y = 1050*0.5143 - 40 = 500
        (1680, 1050, 1920, 1080, 3360, 2100, 80, (920.0, 500.0)),
    ],
    ids=[
        "retina_2x_native_canvas",
        "retina_2x_scaled_canvas",
    ],
)
def test_map_coords_retina_prescaled(x, y, canvas_w, canvas_h,
                                     monitor_w, monitor_h, size, expected):
    """Verify map_coords works when coords are pre-scaled to physical pixels."""
    result = map_coords(x, y, canvas_w, canvas_h, monitor_w, monitor_h, size)
    assert result == pytest.approx(expected, abs=1.0)
