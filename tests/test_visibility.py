"""Tier 2 — §2.2: Source visibility lifecycle (mock obspython)."""

from unittest.mock import call


def test_new_circle_is_visible(obs_script, mock_obs):
    """_show_source sets the scene item visible."""
    mock_obs.obs_scene_find_source.return_value = None  # create path

    obs_script._show_source("test_src", "/img.png", 100.0, 200.0, 80)

    # The created scene item should be set visible
    mock_obs.obs_sceneitem_set_visible.assert_called_once()
    args = mock_obs.obs_sceneitem_set_visible.call_args
    assert args[0][1] is True  # second positional arg = True


def test_expired_circle_is_hidden(obs_script, mock_obs):
    """_hide_source sets the scene item invisible."""
    scene_item = mock_obs.MagicMock(name="item")
    mock_obs.obs_scene_find_source.return_value = scene_item

    obs_script._hide_source("test_src")

    mock_obs.obs_sceneitem_set_visible.assert_called_once_with(scene_item, False)


def test_cleanup_removes_scene_items(obs_script, mock_obs):
    """_cleanup_sources calls obs_sceneitem_remove for each item found."""
    scene_item = mock_obs.MagicMock(name="item")
    mock_obs.obs_scene_find_source.return_value = scene_item

    obs_script._cleanup_sources()

    max_c = obs_script._settings["max_circles"]
    expected_count = max_c * 2  # L + R
    assert mock_obs.obs_sceneitem_remove.call_count == expected_count
