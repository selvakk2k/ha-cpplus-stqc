import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("turbojpeg", MagicMock())

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cpplus.client import CPPlusClient
from custom_components.cpplus.const import (
    CONF_DEVICE_TYPE,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RTSP_OVER_TLS,
    CONF_RTSP_PORT,
    CONF_STREAM_PROFILE,
    CONF_USERNAME,
    DOMAIN,
    STREAM_PROFILE_DAHUA_CH0,
    STREAM_PROFILE_DAHUA_CH1,
    STREAM_PROFILE_VIDEO_LIVE,
    SUBENTRY_TYPE_CHANNEL,
    TYPE_CAMERA,
    TYPE_NVR,
)


@pytest.mark.asyncio
async def test_stream_url_encoding_with_special_characters():
    """Verify that RTSP URLs strictly quote passwords with safe='' for go2rtc/Cloudflare."""
    client = CPPlusClient(
        host="10.0.29.200",
        port=443,
        rtsp_port=554,
        username="admin",
        password="p@ss!word#123$",
        device_type=TYPE_NVR,
    )
    url = client.get_stream_url(channel=16, subtype=0)
    # Password special characters must be fully percent-encoded
    assert "@" not in url.split("@", 1)[0].split(":", 2)[2]
    assert url == "rtsp://admin:p%40ss%21word%23123%24@10.0.29.200:554/cam/realmonitor?channel=16&subtype=0"

    # Channel 17
    url_vto2 = client.get_stream_url(channel=17, subtype=0)
    assert url_vto2 == "rtsp://admin:p%40ss%21word%23123%24@10.0.29.200:554/cam/realmonitor?channel=17&subtype=0"

    # Standalone camera profile
    client_cam = CPPlusClient(
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="cam_password",
        device_type=TYPE_CAMERA,
    )
    cam_url = client_cam.get_stream_url(channel=1, subtype=0)
    assert cam_url == "rtsp://admin:cam_password@10.0.29.212:554/video/live?channel=1&subtype=0"


@pytest.mark.asyncio
async def test_config_flow_nvr_setup(hass: HomeAssistant, enable_custom_integrations):
    """Test standard NVR user setup flow via menu selection."""
    with patch("custom_components.cpplus.config_flow.CPPlusClient") as mock_client_cls, \
         patch("custom_components.cpplus.async_setup_entry", return_value=True):
        mock_instance = AsyncMock()
        mock_instance.async_get_device_info.return_value = {
            "serial": "NVR_SERIAL_TEST_123",
            "hardware": "CP-UNR-4K4322-V4",
            "firmware": "1.00.14.00.R",
            "device_type": TYPE_NVR,
        }
        mock_client_cls.return_value = mock_instance

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "user"

        result_nvr = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": "nvr"},
        )
        assert result_nvr["type"] is FlowResultType.FORM
        assert result_nvr["step_id"] == "nvr"

        result2 = await hass.config_entries.flow.async_configure(
            result_nvr["flow_id"],
            {
                CONF_HOST: "10.0.29.200",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret_password",
                CONF_NAME: "Main NVR",
                CONF_PORT: 443,
                CONF_RTSP_PORT: 554,
            },
        )
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["title"] == "CP PLUS NVR Main NVR"
        assert result2["data"][CONF_HOST] == "10.0.29.200"
        assert result2["data"]["device_type"] == TYPE_NVR


@pytest.mark.asyncio
async def test_config_flow_standalone_camera_setup(hass: HomeAssistant, enable_custom_integrations):
    """Test standalone camera user setup flow via menu selection."""
    with patch("custom_components.cpplus.config_flow.CPPlusClient") as mock_client_cls, \
         patch("custom_components.cpplus.async_setup_entry", return_value=True):
        mock_instance = AsyncMock()
        mock_instance.async_get_device_info.return_value = {
            "serial": "CAM_SERIAL_TEST_456",
            "hardware": "CP-UNC-TA21L3C-Q",
            "firmware": "2.860.00AT002.0.R",
            "device_type": TYPE_CAMERA,
        }
        mock_client_cls.return_value = mock_instance

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "user"

        result_cam = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": "camera"},
        )
        assert result_cam["type"] is FlowResultType.FORM
        assert result_cam["step_id"] == "camera"

        result2 = await hass.config_entries.flow.async_configure(
            result_cam["flow_id"],
            {
                CONF_HOST: "10.0.29.212",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret_password",
                CONF_NAME: "Living Room Camera",
                CONF_PORT: 443,
                CONF_RTSP_PORT: 554,
            },
        )
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["title"] == "CP PLUS Camera Living Room Camera"
        assert result2["data"][CONF_HOST] == "10.0.29.212"
        assert result2["data"]["device_type"] == TYPE_CAMERA



@pytest.mark.asyncio
async def test_subentry_channel_reconfigure(hass: HomeAssistant, enable_custom_integrations):
    """Test channel subentry renaming flow."""
    from custom_components.cpplus.config_flow import CameraChannelSubentryFlowHandler
    from types import MappingProxyType
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="CP PLUS NVR",
        data={
            CONF_HOST: "10.0.29.200",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            "device_type": TYPE_NVR,
        },
        unique_id="NVR_SERIAL_TEST_123",
    )
    entry.add_to_hass(hass)

    subentry = config_entries.ConfigSubentry(
        data=MappingProxyType({
            "channel": 16,
            "channel_index": 15,
            "name": "Network Room Intercom",
        }),
        subentry_type=SUBENTRY_TYPE_CHANNEL,
        title="Network Room Intercom",
        unique_id="NVR_SERIAL_TEST_123_ch16",
    )
    hass.config_entries.async_add_subentry(entry, subentry)

    # Show reconfigure form
    res = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CHANNEL),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert res["type"] is FlowResultType.FORM
    assert res["step_id"] == "reconfigure"

    # Submit updated name
    res2 = await hass.config_entries.subentries.async_configure(
        res["flow_id"],
        {"channel_name": "Main Gate Intercom"},
    )
    assert res2["type"] is FlowResultType.ABORT
    assert res2["reason"] == "reconfigure_successful"
    assert subentry.title == "Main Gate Intercom"


@pytest.mark.asyncio
async def test_options_flow(hass: HomeAssistant, enable_custom_integrations):
    """Test options flow to configure stream profile and RTSP port."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="CP PLUS NVR",
        data={
            CONF_HOST: "10.0.29.200",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            "device_type": TYPE_NVR,
        },
        unique_id="NVR_SERIAL_TEST_123",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_RTSP_PORT: 554,
            CONF_STREAM_PROFILE: STREAM_PROFILE_DAHUA_CH1,
            CONF_RTSP_OVER_TLS: False,
        },
    )
    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STREAM_PROFILE] == STREAM_PROFILE_DAHUA_CH1
    assert entry.options[CONF_RTSP_OVER_TLS] is False


@pytest.mark.asyncio
async def test_subentry_add_channel_and_direct_camera(hass: HomeAssistant, enable_custom_integrations):
    """Test adding a channel subentry with direct camera connection."""
    from custom_components.cpplus.config_flow import CameraChannelSubentryFlowHandler
    from custom_components.cpplus.camera import CPPlusCamera
    from custom_components.cpplus.coordinator import CPPlusDataUpdateCoordinator
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="CP PLUS NVR",
        data={
            CONF_HOST: "10.0.29.200",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            "device_type": TYPE_NVR,
        },
        unique_id="NVR_SERIAL_TEST_123",
    )
    entry.add_to_hass(hass)

    # Add channel subentry via user step
    res = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CHANNEL),
        context={"source": config_entries.SOURCE_USER},
    )
    assert res["type"] is FlowResultType.FORM
    assert res["step_id"] == "user"

    # Submit with direct connection parameters in single unified form
    res2 = await hass.config_entries.subentries.async_configure(
        res["flow_id"],
        {
            "channel": 18,
            "channel_name": "Gate Intercom",
            "direct_host": "10.0.29.215",
            "direct_rtsp_port": 554,
        },
    )
    assert res2["type"] is FlowResultType.CREATE_ENTRY
    assert res2["title"] == "Gate Intercom"
    assert res2["data"]["direct_connection"] is True
    assert res2["data"]["direct_host"] == "10.0.29.215"

    # Verify direct camera stream source
    client = CPPlusClient(
        hass=hass,
        host="10.0.29.200",
        port=443,
        rtsp_port=554,
        username="admin",
        password="secret_password",
        device_type=TYPE_NVR,
    )
    coord = CPPlusDataUpdateCoordinator(hass, client, "CP PLUS NVR")
    coord.data = {"serial": "NVR_SERIAL_TEST_123", "online": True}
    cam = CPPlusCamera(
        coord,
        channel=18,
        subtype=0,
        stream_label="Main",
        channel_name="Gate Intercom",
        subentry_data=res2["data"],
    )
    url = await cam.stream_source()
    assert url == "rtsp://admin:secret_password@10.0.29.215:554/cam/realmonitor?channel=1&subtype=0"
    assert cam.extra_state_attributes["connection_mode"] == "direct"
    assert cam.extra_state_attributes["direct_host"] == "10.0.29.215"


@pytest.mark.asyncio
async def test_subentry_add_standard_channel(hass: HomeAssistant, enable_custom_integrations):
    """Test adding a standard channel subentry without direct host."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="CP PLUS NVR",
        data={
            CONF_HOST: "10.0.29.200",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            "device_type": TYPE_NVR,
        },
        unique_id="NVR_SERIAL_TEST_123",
    )
    entry.add_to_hass(hass)

    res = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CHANNEL),
        context={"source": config_entries.SOURCE_USER},
    )
    assert res["type"] is FlowResultType.FORM
    assert res["step_id"] == "user"

    res2 = await hass.config_entries.subentries.async_configure(
        res["flow_id"],
        {
            "channel": 5,
            "channel_name": "Driveway Camera",
        },
    )
    assert res2["type"] is FlowResultType.CREATE_ENTRY
    assert res2["title"] == "Driveway Camera"
    assert res2["data"]["direct_connection"] is False
    assert res2["data"]["channel"] == 5



