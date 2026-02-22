"""Tests for multi-screen support: display hit-testing and coordinate mapping."""

import pytest
from click_pop_core import find_display_for_point, map_coords


# ---------------------------------------------------------------------------
# Display layouts used across tests
# ---------------------------------------------------------------------------

DUAL_SIDE_BY_SIDE = [
    {"id": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "retina_scale": 1.0},
    {"id": 2, "x": 1920, "y": 0, "w": 2560, "h": 1440, "retina_scale": 1.0},
]

DUAL_VERTICAL = [
    {"id": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "retina_scale": 1.0},
    {"id": 2, "x": 0, "y": 1080, "w": 1920, "h": 1080, "retina_scale": 1.0},
]

DUAL_RETINA_MIXED = [
    {"id": 1, "x": 0, "y": 0, "w": 1680, "h": 1050, "retina_scale": 2.0},
    {"id": 2, "x": 1680, "y": 0, "w": 1920, "h": 1080, "retina_scale": 1.0},
]

TRIPLE_L_SHAPE = [
    {"id": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "retina_scale": 1.0},
    {"id": 2, "x": 1920, "y": 0, "w": 1920, "h": 1080, "retina_scale": 1.0},
    {"id": 3, "x": 0, "y": 1080, "w": 1920, "h": 1080, "retina_scale": 1.0},
]

SINGLE = [
    {"id": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "retina_scale": 1.0},
]


# ---------------------------------------------------------------------------
# find_display_for_point tests
# ---------------------------------------------------------------------------

class TestFindDisplayForPoint:
    """Test that clicks are correctly attributed to the right display."""

    def test_click_on_primary(self):
        d = find_display_for_point(100, 200, DUAL_SIDE_BY_SIDE)
        assert d is DUAL_SIDE_BY_SIDE[0]

    def test_click_on_secondary_right(self):
        d = find_display_for_point(2000, 500, DUAL_SIDE_BY_SIDE)
        assert d is DUAL_SIDE_BY_SIDE[1]

    def test_click_on_secondary_below(self):
        d = find_display_for_point(500, 1200, DUAL_VERTICAL)
        assert d is DUAL_VERTICAL[1]

    def test_click_at_boundary_between_monitors(self):
        """x=1920 is the first pixel of the secondary monitor."""
        d = find_display_for_point(1920, 500, DUAL_SIDE_BY_SIDE)
        assert d is DUAL_SIDE_BY_SIDE[1]

    def test_click_at_last_pixel_primary(self):
        """x=1919 is the last pixel of the primary monitor."""
        d = find_display_for_point(1919, 500, DUAL_SIDE_BY_SIDE)
        assert d is DUAL_SIDE_BY_SIDE[0]

    def test_click_in_dead_zone(self):
        """Click at (1920, 1200) — beyond secondary in DUAL_SIDE_BY_SIDE."""
        # Secondary is 2560x1440 starting at (1920, 0), so (1920, 1200)
        # is inside it. But let's test an actual dead zone.
        # In L-shaped layout, (1920, 1200) is in the gap.
        d = find_display_for_point(1920, 1200, TRIPLE_L_SHAPE)
        assert d is None

    def test_click_outside_all_monitors(self):
        d = find_display_for_point(-10, -10, DUAL_SIDE_BY_SIDE)
        assert d is None

    def test_single_monitor_center(self):
        d = find_display_for_point(960, 540, SINGLE)
        assert d is SINGLE[0]

    def test_single_monitor_origin(self):
        d = find_display_for_point(0, 0, SINGLE)
        assert d is SINGLE[0]

    def test_empty_display_list(self):
        d = find_display_for_point(100, 100, [])
        assert d is None

    def test_triple_monitor_third_display(self):
        d = find_display_for_point(500, 1500, TRIPLE_L_SHAPE)
        assert d is TRIPLE_L_SHAPE[2]

    def test_click_at_secondary_origin(self):
        d = find_display_for_point(1920, 0, DUAL_SIDE_BY_SIDE)
        assert d is DUAL_SIDE_BY_SIDE[1]

    def test_click_at_secondary_far_edge(self):
        """Last pixel of secondary: (1920+2560-1, 1440-1) = (4479, 1439)."""
        d = find_display_for_point(4479, 1439, DUAL_SIDE_BY_SIDE)
        assert d is DUAL_SIDE_BY_SIDE[1]

    def test_click_just_past_secondary(self):
        """One pixel beyond secondary: x=4480."""
        d = find_display_for_point(4480, 0, DUAL_SIDE_BY_SIDE)
        assert d is None


# ---------------------------------------------------------------------------
# Multi-monitor coordinate mapping integration tests
# ---------------------------------------------------------------------------

class TestMultiScreenCoordMapping:
    """Simulate the full coordinate pipeline for multi-monitor setups.

    These tests reproduce what _spawn_circle() does:
    1. find_display_for_point(global_x, global_y)
    2. local_x = global_x - display["x"]
    3. phys_x = local_x * retina_scale
    4. map_coords(phys_x, phys_y, canvas_w, canvas_h, mon_w*retina, mon_h*retina, size)
    """

    def test_click_on_primary_1080p_canvas(self):
        """Click at center of primary display, 1:1 canvas."""
        display = DUAL_SIDE_BY_SIDE[0]
        gx, gy = 960, 540  # center of primary

        local_x = gx - display["x"]
        local_y = gy - display["y"]
        retina = display["retina_scale"]

        result = map_coords(
            local_x * retina, local_y * retina,
            1920, 1080,
            display["w"] * retina, display["h"] * retina,
            80,
        )
        assert result == pytest.approx((920.0, 500.0))

    def test_click_on_secondary_1440p_with_1080p_canvas(self):
        """Click at center of secondary (2560x1440) with a 1920x1080 canvas."""
        display = DUAL_SIDE_BY_SIDE[1]
        gx, gy = 1920 + 1280, 720  # center of secondary in global coords

        local_x = gx - display["x"]  # 1280
        local_y = gy - display["y"]  # 720

        # canvas is 1920x1080, monitor is 2560x1440
        result = map_coords(
            local_x, local_y,
            1920, 1080,
            2560, 1440,
            80,
        )
        # scale_x = 1920/2560 = 0.75, scale_y = 1080/1440 = 0.75
        # obs_x = 1280 * 0.75 - 40 = 920
        # obs_y = 720 * 0.75 - 40 = 500
        assert result == pytest.approx((920.0, 500.0))

    def test_click_on_retina_primary(self):
        """Click at center of Retina display (2x), 1080p canvas."""
        display = DUAL_RETINA_MIXED[0]
        gx, gy = 840, 525  # center of 1680x1050 logical

        local_x = gx - display["x"]
        local_y = gy - display["y"]
        retina = display["retina_scale"]  # 2.0

        phys_x = local_x * retina  # 1680
        phys_y = local_y * retina  # 1050
        phys_mon_w = display["w"] * retina  # 3360
        phys_mon_h = display["h"] * retina  # 2100

        result = map_coords(phys_x, phys_y, 1920, 1080, phys_mon_w, phys_mon_h, 80)
        # scale_x = 1920/3360 ≈ 0.5714
        # obs_x = 1680 * 0.5714 - 40 ≈ 920
        assert result == pytest.approx((920.0, 500.0), abs=1.0)

    def test_click_on_non_retina_secondary(self):
        """Click at center of non-Retina secondary next to Retina primary."""
        display = DUAL_RETINA_MIXED[1]
        gx, gy = 1680 + 960, 540  # center of secondary in global coords

        local_x = gx - display["x"]  # 960
        local_y = gy - display["y"]  # 540
        retina = display["retina_scale"]  # 1.0

        result = map_coords(
            local_x * retina, local_y * retina,
            1920, 1080,
            display["w"] * retina, display["h"] * retina,
            80,
        )
        assert result == pytest.approx((920.0, 500.0))

    def test_click_on_secondary_with_top_left_origin(self):
        """Secondary at (0, 1080) — click at its origin should map to canvas origin."""
        display = DUAL_VERTICAL[1]
        gx, gy = 0, 1080  # top-left of secondary

        local_x = gx - display["x"]  # 0
        local_y = gy - display["y"]  # 0

        result = map_coords(local_x, local_y, 1920, 1080, 1920, 1080, 80)
        # obs_x = 0 * 1.0 - 40 = -40
        assert result == pytest.approx((-40.0, -40.0))

    def test_click_bottom_right_of_secondary(self):
        """Click at bottom-right of secondary display."""
        display = DUAL_SIDE_BY_SIDE[1]
        gx, gy = 1920 + 2559, 1439  # last pixel

        local_x = gx - display["x"]  # 2559
        local_y = gy - display["y"]  # 1439

        result = map_coords(local_x, local_y, 1920, 1080, 2560, 1440, 80)
        # scale = 0.75
        # obs_x = 2559 * 0.75 - 40 = 1879.25
        # obs_y = 1439 * 0.75 - 40 = 1039.25
        assert result == pytest.approx((1879.25, 1039.25))


# ---------------------------------------------------------------------------
# Discard logic tests (clicks on non-captured display)
# ---------------------------------------------------------------------------

class TestDiscardNonCapturedClicks:
    """Verify the logic that discards clicks landing on non-captured displays.

    This tests the pure logic that _spawn_circle() uses — we replicate the
    check without needing OBS.  Note: the discard logic only activates when
    there are multiple displays (len(displays) > 1).
    """

    def _should_discard(self, gx, gy, displays, captured_display):
        """Replicate the discard logic from _spawn_circle."""
        # Single display never discards — matches _spawn_circle behavior
        if len(displays) <= 1:
            return False
        display = find_display_for_point(gx, gy, displays)
        if captured_display is not None and display is not captured_display:
            return True
        return False

    def test_click_on_captured_display_not_discarded(self):
        captured = DUAL_SIDE_BY_SIDE[0]
        assert not self._should_discard(100, 100, DUAL_SIDE_BY_SIDE, captured)

    def test_click_on_other_display_discarded(self):
        captured = DUAL_SIDE_BY_SIDE[0]
        assert self._should_discard(2000, 500, DUAL_SIDE_BY_SIDE, captured)

    def test_click_on_secondary_when_secondary_captured(self):
        captured = DUAL_SIDE_BY_SIDE[1]
        assert not self._should_discard(2000, 500, DUAL_SIDE_BY_SIDE, captured)

    def test_click_on_primary_when_secondary_captured(self):
        captured = DUAL_SIDE_BY_SIDE[1]
        assert self._should_discard(100, 100, DUAL_SIDE_BY_SIDE, captured)

    def test_no_captured_display_never_discards(self):
        """When no capture source is configured, all clicks pass through."""
        assert not self._should_discard(100, 100, DUAL_SIDE_BY_SIDE, None)
        assert not self._should_discard(2000, 500, DUAL_SIDE_BY_SIDE, None)

    def test_click_in_dead_zone_discarded(self):
        """Click in dead zone is discarded (display=None, captured!=None)."""
        captured = TRIPLE_L_SHAPE[0]
        assert self._should_discard(1920, 1200, TRIPLE_L_SHAPE, captured)

    def test_click_in_dead_zone_no_capture_not_discarded(self):
        """Click in dead zone with no capture source passes through."""
        assert not self._should_discard(1920, 1200, TRIPLE_L_SHAPE, None)


# ---------------------------------------------------------------------------
# Single-display regression tests
# ---------------------------------------------------------------------------

class TestSingleDisplayRegression:
    """Ensure single-display setups use the legacy code path unchanged.

    With only one display detected, _spawn_circle should:
    - Never discard clicks
    - Use raw x/y (not display-local, since origin is 0,0 anyway)
    - Use _settings["monitor_w"] / _retina_scale (not display dict)
    """

    def test_single_display_never_discards(self):
        """Even with _captured_display set, single display never discards."""
        captured = SINGLE[0]
        # With a single display, discard logic is skipped entirely
        assert not self._should_discard(100, 100, SINGLE, captured)

    def test_single_display_no_capture_never_discards(self):
        assert not self._should_discard(960, 540, SINGLE, None)

    def test_single_display_edge_coords_never_discards(self):
        """Even coordinates at display edges are never discarded."""
        captured = SINGLE[0]
        assert not self._should_discard(0, 0, SINGLE, captured)
        assert not self._should_discard(1919, 1079, SINGLE, captured)

    def _should_discard(self, gx, gy, displays, captured_display):
        """Replicate _spawn_circle discard logic."""
        if len(displays) <= 1:
            return False
        display = find_display_for_point(gx, gy, displays)
        if captured_display is not None and display is not captured_display:
            return True
        return False

    def test_single_display_uses_raw_coords(self):
        """With one display, x/y pass through unchanged (no subtraction)."""
        # Simulate what _spawn_circle does with len(_all_displays) <= 1
        # Center of a 1680x1050 Retina display
        x, y = 840, 525
        # display is None when len <= 1, so we use the legacy path:
        local_x = x  # no offset subtracted
        local_y = y
        retina = 2.0  # _retina_scale from settings
        mon_w = 1680  # _settings["monitor_w"]
        mon_h = 1050

        result = map_coords(
            local_x * retina, local_y * retina,
            1920, 1080,
            mon_w * retina, mon_h * retina,
            80,
        )
        # Same result as the old single-display code path
        assert result == pytest.approx((920.0, 500.0), abs=1.0)
