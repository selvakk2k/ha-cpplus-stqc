"""Tests for standalone CP PLUS IP camera configuration, entities, and snapshot fallback."""

from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cpplus import async_setup_entry
from custom_components.cpplus.client import CPPlusClient, CPPlusError
from custom_components.cpplus.const import (
    CONF_DEVICE_TYPE,
    DOMAIN,
    TYPE_CAMERA,
)


@pytest.mark.asyncio
async def test_standalone_camera_setup_entities(hass: HomeAssistant) -> None:
    """Test async_setup_entry correctly sets up all entities for a standalone IP camera."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_HOST: "10.0.29.212",
            CONF_DEVICE_TYPE: TYPE_CAMERA,
            CONF_USERNAME: "admin",
            CONF_NAME: "Gate Camera",
        },
        unique_id="CAM_SETUP_TEST_SERIAL",
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

    mock_channel = [
        {
            "channel": 1,
            "index": 0,
            "name": "Gate Camera",
            "model": "CP-UNC-TA21L3C-Q",
            "is_native_cpplus": True,
            "has_smd": True,
            "has_tripwire": True,
            "smd_human": True,
            "smd_vehicle": True,
            "tripwire_enabled": False,
            "video_in_mode": "Auto",
            "lighting_mode": "Auto",
            "audio_enable": True,
        }
    ]

    with (
        patch("custom_components.cpplus.CPPlusClient") as mock_client_cls,
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            return_value=True,
        ),
    ):
        mock_client = mock_client_cls.return_value
        mock_client.device_type = TYPE_CAMERA
        mock_client.host = "10.0.29.212"
        mock_client.port = 80
        mock_client.async_get_device_info = AsyncMock(
            return_value={
                "serial": "CAM_SETUP_TEST_SERIAL",
                "hardware": "CP-UNC-TA21L3C-Q",
                "firmware": "2.800.0000000.8.R",
                "device_type": TYPE_CAMERA,
            }
        )
        mock_client.async_get_channels = AsyncMock(return_value=mock_channel)
        mock_client.async_nvr_request = AsyncMock(return_value="OK")
        mock_client.async_cgi_request = AsyncMock(return_value="OK")
        mock_client.async_ping = AsyncMock(return_value=True)
        mock_client.async_start_event_listener = AsyncMock()
        mock_client.async_close = AsyncMock()

        setup_ok = await async_setup_entry(hass, entry)
        assert setup_ok is True
        mock_client.async_start_event_listener.assert_called_once()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.device_info_data["firmware"] == "2.800.0000000.8.R"
        assert coordinator.device_info_data["hardware"] == "CP-UNC-TA21L3C-Q"
        assert len(coordinator.channels) == 1
        assert coordinator.channels[0]["channel"] == 1


@pytest.mark.asyncio
async def test_snapshot_fallback() -> None:
    """Test that async_get_snapshot falls back to /cgi-bin/snapshot.cgi when channel param returns 404."""
    client = CPPlusClient("10.0.29.212", 80, "admin", "secret", device_type=TYPE_CAMERA)

    call_urls = []

    async def mock_cgi_bytes(endpoint: str) -> bytes:
        call_urls.append(endpoint)
        if "channel=1" in endpoint:
            raise CPPlusError("NVR HTTP error 404 on /cgi-bin/snapshot.cgi?channel=1")
        if endpoint == "/cgi-bin/snapshot.cgi":
            return b"\xff\xd8\xff\xe0" + b"0" * 200
        raise CPPlusError("Not found")

    client.async_cgi_request_bytes = AsyncMock(side_effect=mock_cgi_bytes)

    data = await client.async_get_snapshot(channel=1)
    assert data == b"\xff\xd8\xff\xe0" + b"0" * 200
    assert "/cgi-bin/snapshot.cgi?channel=1" in call_urls
    assert "/cgi-bin/snapshot.cgi" in call_urls


@pytest.mark.asyncio
async def test_standalone_camera_get_channels() -> None:
    """Test that async_get_channels for standalone camera queries index 0 and synthesizes channel 1."""
    client = CPPlusClient("10.0.29.212", 80, "admin", "secret", device_type=TYPE_CAMERA)

    client.async_get_device_info = AsyncMock(
        return_value={
            "serial": "O55DOBEK0WN5AC6L",
            "hardware": "CP-UNC-TA21L3C-Q",
            "firmware": "2.800.0000000.8.R",
            "device_type": TYPE_CAMERA,
        }
    )

    async def mock_cgi(endpoint: str) -> str:
        if "SmartMotionDetect" in endpoint:
            return "table.SmartMotionDetect[0].Enable=true\ntable.SmartMotionDetect[0].HumanDetection=true\ntable.SmartMotionDetect[0].VehicleDetection=true\n"
        if "CrossLineDetection" in endpoint:
            return "table.CrossLineDetection[0].Enable=true\n"
        if "VideoInMode" in endpoint:
            return "table.VideoInMode[0].Config[0]=0\n"
        if "Lighting" in endpoint:
            return "table.Lighting[0][0].Mode=Auto\n"
        if "Encode" in endpoint:
            return "table.Encode[0].MainFormat[0].AudioEnable=true\n"
        return ""

    client.async_cgi_request = AsyncMock(side_effect=mock_cgi)

    channels = await client.async_get_channels()
    assert len(channels) == 1
    ch = channels[0]
    assert ch["channel"] == 1
    assert ch["index"] == 0
    assert ch["has_smd"] is True
    assert ch["smd_human"] is True
    assert ch["smd_vehicle"] is True
    assert ch["has_tripwire"] is True
    assert ch["is_native_cpplus"] is True
    assert ch["audio_enable"] is True
    await client.async_close()


@pytest.mark.asyncio
async def test_stqc_rpc_fallback_when_cgi_fails() -> None:
    """Test that async_get_channels and async_get_device_info fallback to JSON-RPC on STQC camera."""
    client = CPPlusClient("10.0.29.212", 443, "admin", "secret", device_type=TYPE_CAMERA)

    # CGI queries return 404
    client.async_cgi_request = AsyncMock(side_effect=CPPlusError("HTTP error 404"))

    async def mock_rpc(method: str, params: dict | None = None) -> dict:
        if method == "magicBox.getDeviceType":
            return {"result": True, "params": {"type": "CP-UNC-TA21L3C-Q"}}
        if method == "magicBox.getSerialNo":
            return {"result": True, "params": {"serial": "O55DOBEK0WN5AC6L"}}
        if method == "magicBox.getSoftwareVersion":
            return {"result": True, "params": {"version": "2.800.0000000.8.R", "buildDate": "2023-08-15"}}
        if method == "configManager.getConfig":
            name = params.get("name") if params else None
            if name == "SmartMotionDetect":
                return {"result": True, "params": {"table": [{"Enable": True, "ObjectTypes": {"Human": True, "Vehicle": True}}]}}
            if name == "CrossLineDetection":
                return {"result": True, "params": {"table": [{"Enable": True}]}}
            if name == "VideoInMode":
                return {"result": True, "params": {"table": [{"Mode": 1}]}}
            if name == "Lighting":
                return {"result": True, "params": {"table": [[{"Mode": "Auto"}]]}}
            if name == "Encode":
                return {"result": True, "params": {"table": [{"MainFormat": [{"AudioEnable": True}]}]}}
        return {"result": False}

    client.async_call_rpc = AsyncMock(side_effect=mock_rpc)
    client._logged_in = True

    info = await client.async_get_device_info()
    assert info["hardware"] == "CP-UNC-TA21L3C-Q"
    assert info["serial"] == "O55DOBEK0WN5AC6L"
    assert "2.800.0000000.8.R" in info["firmware"]

    channels = await client.async_get_channels()
    assert len(channels) == 1
    ch = channels[0]
    assert ch["has_smd"] is True
    assert ch["smd_human"] is True
    assert ch["smd_vehicle"] is True
    assert ch["has_tripwire"] is True
    assert ch["video_in_mode"] == 1
    assert ch["lighting_mode"] == "Auto"
    assert ch["audio_enable"] is True

    await client.async_close()


@pytest.mark.asyncio
async def test_stqc_firmware_detection_from_system_info_or_config_manager() -> None:
    """Test that firmware version is resolved from magicBox.getSystemInfo case-insensitively or configManager."""
    client = CPPlusClient("10.0.29.212", 443, "admin", "secret", device_type=TYPE_CAMERA)
    client.async_cgi_request = AsyncMock(side_effect=CPPlusError("HTTP error 404"))

    async def mock_rpc(method: str, params: dict | None = None) -> dict:
        if method == "magicBox.getDeviceType":
            return {"result": True, "params": {"type": "CP-UNC-TA21L3C-Q"}}
        if method == "magicBox.getSerialNo":
            return {"result": True, "params": {"serial": "O55DOBEK0WN5AC6L"}}
        if method == "magicBox.getSoftwareVersion":
            return {"result": False}
        if method == "magicBox.getSystemInfo":
            return {
                "result": True,
                "params": {
                    "info": {
                        "deviceType": "CP-UNC-TA21L3C-Q",
                        "serialNumber": "O55DOBEK0WN5AC6L",
                        "softwareVersion": "3.100.0000000.1.R",
                        "buildDate": "2024-01-15",
                    }
                },
            }
        return {"result": False}

    client.async_call_rpc = AsyncMock(side_effect=mock_rpc)
    client._logged_in = True

    info = await client.async_get_device_info()
    assert info["hardware"] == "CP-UNC-TA21L3C-Q"
    assert info["serial"] == "O55DOBEK0WN5AC6L"
    assert info["firmware"] == "3.100.0000000.1.R,build:2024-01-15"
    await client.async_close()

