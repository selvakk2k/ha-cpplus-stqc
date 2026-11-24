"""Unit tests for CP PLUS diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.cpplus.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.cpplus.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_redacts_credentials():
    """Test diagnostics redacts host, username, password, and RTSP stream URLs."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "cpplus_entry_100"
    entry.version = 1
    entry.domain = DOMAIN
    entry.title = "Front Yard Camera"
    entry.pref_disable_new_entities = False
    entry.pref_disable_polling = False
    entry.data = {
        CONF_HOST: "192.168.1.105",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "super_secret_camera_password",
        "port": 443,
        "rtsp_port": 554,
        "device_type": "camera",
    }
    entry.options = {
        "stream_profile": "auto",
    }

    subentry = MagicMock()
    subentry.subentry_id = "sub_channel_1"
    subentry.subentry_type = "channel"
    subentry.title = "Front Yard Main Stream"
    subentry.data = {
        "channel_number": 1,
        "rtsp_url": "rtsp://admin:super_secret_camera_password@192.168.1.105:554/cam/realmonitor",
        "snapshot_url": "https://192.168.1.105/cgi-bin/snapshot.cgi",
    }
    entry.subentries = {"sub_channel_1": subentry}

    mock_client = MagicMock()
    mock_client.is_connected = True
    mock_client.device_type = "camera"
    mock_client.model = "CP-UNC-TA21PL3"
    mock_client.sw_version = "2.800.0000000.1.R"
    mock_client.channels = {1: MagicMock()}

    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.client = mock_client
    mock_coordinator.data = {"motion": True, "human": False}

    entry.runtime_data = mock_coordinator
    hass.data = {DOMAIN: {entry.entry_id: mock_coordinator}}

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Verify entry redactions
    entry_data = diag["entry"]["data"]
    assert entry_data[CONF_HOST] == "**REDACTED**"
    assert entry_data[CONF_USERNAME] == "**REDACTED**"
    assert entry_data[CONF_PASSWORD] == "**REDACTED**"
    assert entry_data["port"] == 443

    # Verify subentry redactions
    sub_data = diag["entry"]["subentries"][0]["data"]
    assert sub_data["rtsp_url"] == "**REDACTED**"
    assert sub_data["snapshot_url"] == "**REDACTED**"
    assert sub_data["channel_number"] == 1

    # Verify coordinator snapshot
    coord = diag["coordinator"]
    assert coord["last_update_success"] is True
    assert coord["connected"] is True
    assert coord["device_type"] == "camera"
    assert coord["model"] == "CP-UNC-TA21PL3"
    assert coord["channels_count"] == 1
    assert coord["data"]["motion"] is True


@pytest.mark.asyncio
async def test_diagnostics_without_coordinator():
    """Test diagnostics works gracefully before coordinator is initialized."""
    hass = MagicMock()
    hass.data = {}
    entry = MagicMock()
    entry.entry_id = "cpplus_unloaded"
    entry.version = 1
    entry.domain = DOMAIN
    entry.title = "Unloaded NVR"
    entry.pref_disable_new_entities = False
    entry.pref_disable_polling = False
    entry.data = {
        CONF_HOST: "192.168.1.200",
        CONF_USERNAME: "nvr_admin",
        CONF_PASSWORD: "secret_nvr_pw",
    }
    entry.options = {}
    entry.subentries = {}
    entry.runtime_data = None

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["entry"]["title"] == "Unloaded NVR"
    assert diag["entry"]["data"][CONF_HOST] == "**REDACTED**"
    assert diag["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["coordinator"] == {}

