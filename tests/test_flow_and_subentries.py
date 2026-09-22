"""Tests for CP PLUS STQC config flow, subentries, and stream URLs."""

from unittest.mock import AsyncMock, patch
import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cpplus.client import CPPlusClient
from custom_components.cpplus.const import (
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
    """Test standard NVR user setup flow."""
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
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
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

