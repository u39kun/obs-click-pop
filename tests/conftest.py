"""Shared fixtures for Tier 1 and Tier 2 tests."""

import sys
import importlib
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Vec2 helper — mimics the obspython vec2 type
# ---------------------------------------------------------------------------

class Vec2:
    """Minimal stand-in for the C-level ``obs.vec2()`` struct."""

    def __init__(self):
        self.x = 0.0
        self.y = 0.0

    def __repr__(self):
        return f"Vec2(x={self.x}, y={self.y})"


# ---------------------------------------------------------------------------
# Mock obspython module
# ---------------------------------------------------------------------------

def _make_mock_obs(*, scene_source_width=1920, scene_source_height=1080,
                   source_width=80, scene_find_returns=None):
    """Build a ``MagicMock`` that quacks like ``obspython``.

    Parameters
    ----------
    scene_source_width, scene_source_height:
        Values returned by ``obs_source_get_width/height`` for the *scene*
        source (the one returned by ``obs_frontend_get_current_scene``).
    source_width:
        Value returned by ``obs_source_get_width`` for scene-item sources.
    scene_find_returns:
        If ``None``, ``obs_scene_find_source`` returns ``None`` (create path).
        Otherwise, the value it should return (update path).
    """
    mock = MagicMock(name="obspython")

    # Constants
    mock.LOG_INFO = 0
    mock.LOG_ERROR = 1
    mock.OBS_PATH_FILE = 0

    # vec2 factory
    mock.vec2 = Vec2

    # Scene source returned by obs_frontend_get_current_scene
    _scene_source = MagicMock(name="scene_source")

    # Track which source we're querying width for
    _scene_item_source = MagicMock(name="scene_item_source")

    def _get_width(source):
        if source is _scene_source:
            return scene_source_width
        return source_width

    def _get_height(source):
        if source is _scene_source:
            return scene_source_height
        return 0  # not used for non-scene sources

    mock.obs_frontend_get_current_scene.return_value = _scene_source
    mock.obs_source_get_width.side_effect = _get_width
    mock.obs_source_get_height.side_effect = _get_height

    # Scene graph
    _scene = MagicMock(name="scene")
    mock.obs_scene_from_source.return_value = _scene

    _scene_item = MagicMock(name="scene_item") if scene_find_returns is not None else None
    mock.obs_scene_find_source.return_value = _scene_item

    # Created sources / items
    _created_source = MagicMock(name="created_source")
    _created_item = MagicMock(name="created_scene_item")
    _created_settings = MagicMock(name="created_settings")

    mock.obs_source_create.return_value = _created_source
    mock.obs_scene_add.return_value = _created_item
    mock.obs_data_create.return_value = _created_settings

    # For the update path, scene_item_get_source returns the item source
    mock.obs_sceneitem_get_source.return_value = _scene_item_source

    # obs_source_get_settings returns a settings mock
    _existing_settings = MagicMock(name="existing_settings")
    mock.obs_source_get_settings.return_value = _existing_settings

    # obs_get_source_by_name — used in _cleanup_sources
    _global_source = MagicMock(name="global_source")
    mock.obs_get_source_by_name.return_value = _global_source

    # Stash internal refs for assertions
    mock._scene_source = _scene_source
    mock._scene = _scene
    mock._scene_item = _scene_item
    mock._scene_item_source = _scene_item_source
    mock._created_source = _created_source
    mock._created_item = _created_item
    mock._created_settings = _created_settings
    mock._existing_settings = _existing_settings
    mock._global_source = _global_source

    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_obs(request):
    """Install a mock ``obspython`` into ``sys.modules`` for the test duration.

    Accepts keyword overrides via ``@pytest.mark.parametrize`` or direct
    ``request.param``.  Returns the mock object for assertions.
    """
    kwargs = getattr(request, "param", {}) or {}
    mock = _make_mock_obs(**kwargs)

    old = sys.modules.get("obspython")
    sys.modules["obspython"] = mock
    yield mock
    if old is None:
        sys.modules.pop("obspython", None)
    else:
        sys.modules["obspython"] = old


@pytest.fixture()
def obs_script(mock_obs):
    """Import ``obs_click_pop`` with a fresh module-level state.

    The module is forcibly reloaded so each test starts with clean globals.
    Returns the module object.
    """
    # Remove cached module so reload picks up the mock
    sys.modules.pop("obs_click_pop", None)
    import obs_click_pop
    importlib.reload(obs_click_pop)  # ensure clean state

    # Reset mutable globals
    obs_click_pop._listener = None
    obs_click_pop._click_queue.clear()
    obs_click_pop._timer_active = False
    obs_click_pop._active_clicks.clear()

    yield obs_click_pop

    # Cleanup
    sys.modules.pop("obs_click_pop", None)
