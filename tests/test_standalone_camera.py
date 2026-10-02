"""Tests for CP PLUS STQC standalone camera support."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant

from custom_components.cpplus.client import CPPlusClient
from custom_components.cpplus.const import (
    TYPE_CAMERA,
    STREAM_PROFILE_DAHUA_CH1,
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

    # RTSP stream URL falls back to realmonitor when ONVIF URIs have not yet been queried
    url = client.get_stream_url(channel=1, subtype=0)
    assert url == "rtsp://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"

    # Sub-stream mirrors main stream when not yet discovered to protect from 404 stream worker errors
    sub_fallback = client.get_stream_url(channel=1, subtype=1)
    assert sub_fallback == "rtsp://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"

    # When ONVIF stream URIs are discovered, get_stream_url returns the discovered URIs
    client._onvif_stream_uris = {
        0: "rtsp://admin:cam_password@10.0.29.212:554/onvif1",
        1: "rtsp://admin:cam_password@10.0.29.212:554/onvif2",
    }
    url_onvif = client.get_stream_url(channel=1, subtype=0)
    assert url_onvif == "rtsp://admin:cam_password@10.0.29.212:554/onvif1"

    sub_url_onvif = client.get_stream_url(channel=1, subtype=1)
    assert sub_url_onvif == "rtsp://admin:cam_password@10.0.29.212:554/onvif2"

    # When only main stream is in ONVIF, sub-stream mirrors main stream
    client._onvif_stream_uris = {0: "rtsp://admin:cam_password@10.0.29.212:554/onvif1"}
    assert client.get_stream_url(channel=1, subtype=1) == "rtsp://admin:cam_password@10.0.29.212:554/onvif1"

    # When direct stream paths are discovered via RTSP probe, get_stream_url returns direct paths
    client._direct_stream_paths = {
        0: "/cam/realmonitor?channel=0&subtype=0",
        1: "/cam/realmonitor?channel=0&subtype=1",
    }
    url_direct = client.get_stream_url(channel=1, subtype=0)
    assert url_direct == "rtsp://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=0&subtype=0"
    sub_direct = client.get_stream_url(channel=1, subtype=1)
    assert sub_direct == "rtsp://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=0&subtype=1"
    client._direct_stream_paths = {}

    # RTSPS encryption when use_tls is True or rtsp_over_tls is True
    client._onvif_stream_uris = {}
    rtsps_url = client.get_stream_url(channel=1, subtype=0, use_tls=True)
    assert rtsps_url == "rtsps://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"

    # Safe quoting: RFC 3986 sub-delimiters remain unencoded for PyAV / FFmpeg Digest Auth
    client.password = "P@ssw0rd!#$&*"
    quoted_url = client.get_stream_url(channel=1, subtype=0)
    assert "P%40ssw0rd!%23$&*" in quoted_url
    assert "/cam/realmonitor" in quoted_url

    # Profile override to STREAM_PROFILE_DAHUA_CH1
    client.password = "cam_password"
    dahua_url = client.get_stream_url(channel=1, subtype=0, stream_profile=STREAM_PROFILE_DAHUA_CH1)
    assert dahua_url == "rtsp://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"

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


@pytest.mark.asyncio
async def test_standalone_camera_serial_cascade_system_device_info():
    """Test serial extraction when magicBox.getSerialNo is missing and system.getDeviceInfo provides it."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=80,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )
    client._logged_in = True

    async def mock_rpc(method: str, params: dict | None = None) -> dict:
        if method == "magicBox.getDeviceType":
            return {"result": False}
        if method == "magicBox.getSerialNo":
            return {"result": False}
        if method == "magicBox.getSystemInfo":
            return {"result": False}
        if method == "system.getDeviceInfo":
            return {
                "result": True,
                "params": {
                    "deviceInfo": {
                        "serialNumber": "CASCADE_SERIAL_789",
                        "deviceType": "CP-UNC-DA21L3C-Q",
                    }
                },
            }
        return {"result": False}

    with patch.object(client, "async_call_rpc", side_effect=mock_rpc):
        info = await client.async_get_device_info()

    assert info["hardware"] == "CP-UNC-DA21L3C-Q"
    assert info["serial"] == "CASCADE_SERIAL_789"
    await client.async_close()


@pytest.mark.asyncio
async def test_standalone_camera_serial_cascade_fallback_to_host():
    """Test safe host fallback when camera provides no serial number via any method."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=80,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )
    client._logged_in = True

    with patch.object(client, "async_call_rpc", return_value={"result": False}), \
         patch.object(client, "async_nvr_request", side_effect=Exception("No CGI")):
        info = await client.async_get_device_info()

    assert info["serial"] == "CP_10_0_29_212"
    await client.async_close()


@pytest.mark.asyncio
async def test_async_call_rpc_uses_http_scheme():
    """Verify async_call_rpc uses http:// when use_ssl is False (port 80)."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=80,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )
    client._logged_in = True
    client._session_id = "test_session_id"

    mock_session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"result": True, "params": {}})
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.post.return_value = mock_cm

    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
        await client.async_call_rpc("magicBox.getDeviceType")

    called_url = mock_session.post.call_args[0][0]
    assert called_url == "http://10.0.29.212:80/cpapi2"
    assert not called_url.startswith("https://")
    await client.async_close()


@pytest.mark.asyncio
async def test_standalone_camera_snapshot_fallback():
    """Verify async_get_snapshot falls back across endpoint list when first URL fails."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=443,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )

    requested_urls = []

    async def mock_request_bytes(uri: str, method: str = "GET", data=None) -> bytes:
        requested_urls.append(uri)
        if uri == "/cgi-bin/snapshot.cgi?channel=1":
            raise Exception("HTTP 404")
        if uri == "/cgi-bin/snapshot.cgi":
            return b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 150
        raise Exception("Not found")

    with patch.object(client, "async_nvr_request_bytes", side_effect=mock_request_bytes):
        snapshot_bytes = await client.async_get_snapshot(channel=1)

    assert snapshot_bytes is not None
    assert len(snapshot_bytes) > 100
    assert "/cgi-bin/snapshot.cgi?channel=1" in requested_urls
    assert "/cgi-bin/snapshot.cgi" in requested_urls
    await client.async_close()


@pytest.mark.asyncio
async def test_standalone_camera_control_rpc_fallbacks():
    """Verify control methods fall back to JSON-RPC on standalone cameras when CGI is unavailable."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=443,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )

    rpc_calls = []

    async def mock_rpc(method: str, params: dict | None = None) -> dict:
        rpc_calls.append((method, params))
        return {"result": True}

    with (
        patch.object(client, "async_nvr_request", side_effect=Exception("No CGI")),
        patch.object(client, "async_call_rpc", side_effect=mock_rpc),
    ):
        assert await client.async_set_video_in_mode(0, 1) is True
        assert await client.async_set_lighting_mode(0, "Auto") is True
        assert await client.async_set_smd_human(0, True) is True
        assert await client.async_set_smd_vehicle(0, True) is True
        assert await client.async_set_tripwire(0, True) is True
        assert await client.async_set_audio_enable(0, True) is True

    assert len(rpc_calls) == 6
    await client.async_close()


@pytest.mark.asyncio
async def test_standalone_camera_onvif_discovery():
    """Verify ONVIF dynamic profile discovery, stream URI extraction, and snapshot discovery."""
    client = CPPlusClient(
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
        rtsp_over_tls=True,
    )

    # 1. Test WS-Security header formatting
    ws_hdr = client._create_ws_security_header()
    assert "<wsse:Username>admin</wsse:Username>" in ws_hdr
    assert "Password Type=\"http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest\"" in ws_hdr
    assert "<wsse:Nonce" in ws_hdr
    assert "<wsu:Created>" in ws_hdr

    # 2. Test URI formatting with auth and TLS
    raw_rtsp = "rtsp://10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"
    formatted = client._format_rtsp_uri_with_auth(raw_rtsp)
    assert formatted == "rtsps://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"

    # 3. Test dynamic ONVIF discovery with mocked SOAP responses
    profiles_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
        <s:Body>
            <trt:GetProfilesResponse>
                <trt:Profiles token="Profile_1" fixed="true"><tt:Name xmlns:tt="http://www.onvif.org/ver10/schema">MainStream</tt:Name></trt:Profiles>
                <trt:Profiles token="Profile_2" fixed="true"><tt:Name xmlns:tt="http://www.onvif.org/ver10/schema">SubStream</tt:Name></trt:Profiles>
            </trt:GetProfilesResponse>
        </s:Body>
    </s:Envelope>"""

    stream1_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
        <s:Body><trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://10.0.29.212:554/cam/realmonitor?channel=1&amp;subtype=0</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse></s:Body>
    </s:Envelope>"""

    stream2_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
        <s:Body><trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://10.0.29.212:554/cam/realmonitor?channel=1&amp;subtype=1</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse></s:Body>
    </s:Envelope>"""

    snap_xml = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
        <s:Body><trt:GetSnapshotUriResponse><trt:MediaUri><tt:Uri>http://10.0.29.212:80/onvif/snapshot?Profile=Profile_1</tt:Uri></trt:MediaUri></trt:GetSnapshotUriResponse></s:Body>
    </s:Envelope>"""

    async def mock_soap_request(url, path, body, session):
        if "<trt:GetProfiles/>" in body:
            return profiles_xml
        if 'Profile_1</trt:ProfileToken>' in body and "<trt:GetStreamUri>" in body:
            return stream1_xml
        if 'Profile_2</trt:ProfileToken>' in body and "<trt:GetStreamUri>" in body:
            return stream2_xml
        if "<trt:GetSnapshotUri>" in body:
            return snap_xml
        return None

    with patch.object(client, "_async_soap_request", side_effect=mock_soap_request):
        uris = await client.async_get_onvif_stream_uris()

    assert len(uris) == 2
    assert uris[0] == "rtsps://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=0"
    assert uris[1] == "rtsps://admin:cam_password@10.0.29.212:554/cam/realmonitor?channel=1&subtype=1"
    assert client.get_stream_url(1, 0) == uris[0]
    assert client.get_stream_url(1, 1) == uris[1]
    assert 0 in client._onvif_snapshot_uris
    await client.async_close()




