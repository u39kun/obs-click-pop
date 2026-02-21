"""Tier 2 — §2.3: Source positioning (mock obspython)."""

import pytest
from tests.conftest import Vec2


def test_position_values(obs_script, mock_obs):
    """_show_source sets position via obs_sceneitem_set_pos with a Vec2."""
    mock_obs.obs_scene_find_source.return_value = None  # create path

    obs_script._show_source("test_src", "/img.png", 100.5, 200.5, 80)

    mock_obs.obs_sceneitem_set_pos.assert_called_once()
    pos = mock_obs.obs_sceneitem_set_pos.call_args[0][1]
    assert isinstance(pos, Vec2)
    assert pos.x == pytest.approx(100.5)
    assert pos.y == pytest.approx(200.5)


def test_scale_computation(obs_script, mock_obs):
    """Scale is computed as circle_size / source_width."""
    scene_item = mock_obs.MagicMock(name="item")
    mock_obs.obs_scene_find_source.return_value = None  # create path

    # The created item's source has width 80 (default in conftest)
    # so scale = 160 / 80 = 2.0
    obs_script._show_source("test_src", "/img.png", 0, 0, 160)

    mock_obs.obs_sceneitem_set_scale.assert_called_once()
    scale = mock_obs.obs_sceneitem_set_scale.call_args[0][1]
    assert isinstance(scale, Vec2)
    assert scale.x == pytest.approx(2.0)
    assert scale.y == pytest.approx(2.0)


def test_zero_width_skips_scale(obs_script, mock_obs):
    """When source width is 0, obs_sceneitem_set_scale is not called."""
    mock_obs.obs_scene_find_source.return_value = None  # create path

    # Override source width to 0 for all sources
    mock_obs.obs_source_get_width.side_effect = None
    mock_obs.obs_source_get_width.return_value = 0

    obs_script._show_source("test_src", "/img.png", 0, 0, 80)

    mock_obs.obs_sceneitem_set_scale.assert_not_called()
