"""Pytest configuration and Home Assistant stubs for CP PLUS."""

import sys
from pathlib import Path

# Add project root and custom_components to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set up Home Assistant mock stubs for standalone testing
from tests.ha_stub import setup_ha_stubs  # noqa: E402

setup_ha_stubs()

import homeassistant.components.camera  # noqa: E402
import homeassistant.helpers.config_validation as cv  # noqa: E402


class Camera:
    def __init__(self, *args, **kwargs):
        pass


homeassistant.components.camera.Camera = Camera
cv.config_entry_only_config_schema = lambda domain: {}
