"""Tests for CP PLUS STQC standalone camera support."""

import pytest
from unittest.mock import AsyncMock, patch
from homeassistant.core import HomeAssistant

from custom_components.cpplus.client import CPPlusClient
from custom_components.cpplus.const import (
    TYPE_CAMERA,
    STREAM_PROFILE_VIDEO_LIVE,
)


@pytest.mark.asyncio
async def test_standalone_camera_channel_discovery():
    """Test channel discovery and stream URL for a standalone camera."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )
    client._device_info = {
        "serial": "IPC_SERIAL_12345",
        "hardware": "CP-UNC-TA21L3C-Q",
        "firmware": "1.00.00.R",
        "device_type": TYPE_CAMERA,
    }

    with patch.object(client, "async_nvr_request", side_effect=Exception("No CGI")):
        channels = await client.async_get_channels()

    assert len(channels) == 1
    ch = channels[0]
    assert ch["channel"] == 1
    assert ch["index"] == 0
    assert ch["name"] == "10.0.29.212"
    assert ch["model"] == "CP-UNC-TA21L3C-Q"
    assert ch["manufacturer"] == "CP PLUS"
    assert ch["is_native_cpplus"] is True

    # RTSP stream URL
    url = client.get_stream_url(channel=1, subtype=0)
    assert url == "rtsp://admin:cam_password@10.0.29.212:554/video/live?channel=1&subtype=0"

    sub_url = client.get_stream_url(channel=1, subtype=1)
    assert sub_url == "rtsp://admin:cam_password@10.0.29.212:554/video/live?channel=1&subtype=1"
    await client.async_close()


@pytest.mark.asyncio
async def test_standalone_camera_ping():
    """Test pinging standalone camera via JSON-RPC."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )

    with patch.object(client, "async_call_rpc", new_callable=AsyncMock) as mock_rpc:
        mock_rpc.return_value = {"result": True}
        assert await client.async_ping() is True
        mock_rpc.assert_called_once_with("magicBox.getDeviceType")
    await client.async_close()


@pytest.mark.asyncio
async def test_standalone_camera_firmware_extraction():
    """Test standalone camera firmware parsing from getSystemInfo."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )
    client._logged_in = True

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
                    "Version": "2.860.00AT002.0.R",
                    "BuildDate": "2025-12-03",
                },
            }
        return {"result": False}

    with patch.object(client, "async_call_rpc", side_effect=mock_rpc):
        info = await client.async_get_device_info()

    assert info["hardware"] == "CP-UNC-TA21L3C-Q"
    assert info["serial"] == "O55DOBEK0WN5AC6L"
    assert info["firmware"] == "2.860.00AT002.0.R (Build: 2025-12-03)"
    await client.async_close()


