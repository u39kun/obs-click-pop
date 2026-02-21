"""Tier 2 — §2.1: OBS reference management (mock obspython)."""

from unittest.mock import call


def test_get_current_scene_releases_source(obs_script, mock_obs):
    """_get_current_scene releases the source from obs_frontend_get_current_scene."""
    obs_script._get_current_scene()
    mock_obs.obs_source_release.assert_called_once_with(mock_obs._scene_source)


def test_spawn_circle_releases_scene_source(obs_script, mock_obs):
    """_spawn_circle releases the scene source obtained for canvas dimensions."""
    obs_script._spawn_circle(100, 100, True, 999.0)
    # _spawn_circle calls obs_frontend_get_current_scene for canvas dims,
    # then _show_source also calls _get_current_scene internally.
    # Both should release their scene source.
    release_calls = mock_obs.obs_source_release.call_args_list
    scene_source_releases = [c for c in release_calls if c == call(mock_obs._scene_source)]
    assert len(scene_source_releases) >= 2  # once in _spawn_circle, once in _show_source


def test_show_source_releases_on_create_path(obs_script, mock_obs):
    """On the create path (brand-new source), obs_data_release and obs_source_release are called."""
    mock_obs.obs_scene_find_source.return_value = None  # not in scene
    mock_obs.obs_get_source_by_name.return_value = None  # not global either

    obs_script._show_source("test_src", "/img.png", 100.0, 200.0, 80)

    mock_obs.obs_data_release.assert_called_once_with(mock_obs._created_settings)
    source_release_calls = mock_obs.obs_source_release.call_args_list
    assert call(mock_obs._created_source) in source_release_calls


def test_show_source_reuses_global_source(obs_script, mock_obs):
    """When source exists globally but not in scene, it is reused (no obs_source_create)."""
    mock_obs.obs_scene_find_source.return_value = None  # not in scene
    # obs_get_source_by_name returns a source (exists globally)

    obs_script._show_source("test_src", "/img.png", 100.0, 200.0, 80)

    mock_obs.obs_source_create.assert_not_called()
    # The global source should still be released after adding to scene
    source_release_calls = mock_obs.obs_source_release.call_args_list
    assert call(mock_obs._global_source) in source_release_calls


def test_show_source_releases_on_update_path(obs_script, mock_obs):
    """On the update path, obs_data_release is called for the settings."""
    from unittest.mock import MagicMock
    scene_item = MagicMock(name="existing_item")
    mock_obs.obs_scene_find_source.return_value = scene_item  # update path

    obs_script._show_source("test_src", "/img.png", 100.0, 200.0, 80)

    mock_obs.obs_data_release.assert_called_once_with(mock_obs._existing_settings)


def test_cleanup_sources_releases_all(obs_script, mock_obs):
    """_cleanup_sources releases and removes all sources it finds."""
    # Set up so obs_scene_find_source and obs_get_source_by_name return mocks
    scene_item = mock_obs._scene_item or mock_obs.MagicMock(name="item")
    mock_obs.obs_scene_find_source.return_value = scene_item
    global_source = mock_obs._global_source
    mock_obs.obs_get_source_by_name.return_value = global_source

    obs_script._cleanup_sources()

    max_c = obs_script._settings["max_circles"]
    expected_count = max_c * 2  # L + R prefixes

    assert mock_obs.obs_sceneitem_remove.call_count == expected_count
    assert mock_obs.obs_source_remove.call_count == expected_count
    # obs_source_release: 1 for _get_current_scene + N for each global source
    assert mock_obs.obs_source_release.call_count == 1 + expected_count
