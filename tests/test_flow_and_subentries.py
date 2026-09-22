"""Tests for CP PLUS config flow, subentries, and migrations."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from types import MappingProxyType

sys.modules.setdefault("turbojpeg", MagicMock())

from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cpplus.const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_RTSP_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_DEVICE_TYPE,
    SUBENTRY_TYPE_CHANNEL,
    SUBENTRY_TYPE_HUB,
    TYPE_NVR,
    TYPE_CAMERA,
)
from custom_components.cpplus.config_flow import (
    CameraChannelSubentryFlowHandler,
    NVRHubSubentryFlowHandler,
    CPPlusConfigFlow,
)
from custom_components.cpplus import async_migrate_entry, async_setup_entry


@pytest.mark.asyncio
async def test_step_user_menu(hass: HomeAssistant) -> None:
    """Test user step presents menu with nvr and standalone options."""
    flow = CPPlusConfigFlow()
    flow.hass = hass

    result = await flow.async_step_user()
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result["menu_options"] == ["nvr", "standalone"]


@pytest.mark.asyncio
async def test_step_nvr_success(hass: HomeAssistant) -> None:
    """Test successful NVR configuration flow."""
    flow = CPPlusConfigFlow()
    flow.hass = hass
    flow.context = {}

    # Show form
    result = await flow.async_step_nvr()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "nvr"

    with (
        patch("custom_components.cpplus.config_flow.CPPlusClient") as mock_client_cls,
    ):
        mock_client = mock_client_cls.return_value
        mock_client.async_get_device_info = AsyncMock(
            return_value={
                "serial": "NVR_SERIAL_123",
                "hardware": "CP-UNR-4K4322-V4",
                "device_type": TYPE_NVR,
                "firmware": "2.400.0000.0",
            }
        )
        mock_client.async_close = AsyncMock()

        result = await flow.async_step_nvr(
            {
                CONF_HOST: "192.168.1.100",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret_password",
                CONF_NAME: "",
            }
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "CP PLUS NVR CP-UNR-4K4322-V4"
    assert result["data"][CONF_HOST] == "192.168.1.100"
    assert result["data"][CONF_DEVICE_TYPE] == TYPE_NVR
    assert flow.unique_id == "NVR_SERIAL_123"
    assert result["version"] == 2


@pytest.mark.asyncio
async def test_step_standalone_success(hass: HomeAssistant) -> None:
    """Test successful Standalone camera configuration flow."""
    flow = CPPlusConfigFlow()
    flow.hass = hass
    flow.context = {}

    # Show form
    result = await flow.async_step_standalone()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "standalone"

    with (
        patch("custom_components.cpplus.config_flow.CPPlusClient") as mock_client_cls,
    ):
        mock_client = mock_client_cls.return_value
        mock_client.async_get_device_info = AsyncMock(
            return_value={
                "serial": "CAM_SERIAL_456",
                "hardware": "CP-UNC-TA21L3C-Q",
                "device_type": TYPE_CAMERA,
                "firmware": "2.800.0000.0",
            }
        )
        mock_client.async_close = AsyncMock()

        result = await flow.async_step_standalone(
            {
                CONF_HOST: "192.168.1.50",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret_password",
                CONF_NAME: "Living Room",
            }
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "CP PLUS Camera Living Room"
    assert result["data"][CONF_HOST] == "192.168.1.50"
    assert result["data"][CONF_DEVICE_TYPE] == TYPE_CAMERA
    assert flow.unique_id == "CAM_SERIAL_456"
    assert result["version"] == 2


@pytest.mark.asyncio
async def test_supported_subentry_types() -> None:
    """Test supported subentry types returns channel handler only for NVR entries."""
    nvr_entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE_TYPE: TYPE_NVR})
    standalone_entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE_TYPE: TYPE_CAMERA})

    assert CPPlusConfigFlow.async_get_supported_subentry_types(nvr_entry) == {
        SUBENTRY_TYPE_CHANNEL: CameraChannelSubentryFlowHandler,
    }
    assert CPPlusConfigFlow.async_get_supported_subentry_types(standalone_entry) == {}


@pytest.mark.asyncio
async def test_subentry_flow_add_and_reconfigure(hass: HomeAssistant) -> None:
    """Test adding and reconfiguring a channel subentry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_DEVICE_TYPE: TYPE_NVR,
            CONF_USERNAME: "admin",
        },
        unique_id="NVR_SERIAL_TEST",
    )
    entry.add_to_hass(hass)

    # Initialize subentry flow to add channel 1
    flow = CameraChannelSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_CHANNEL)
    flow.context = {"source": "user"}

    result = await flow.async_step_user({"channel": "1", "name": "Balcony Cam"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Balcony Cam"
    assert result["data"]["channel"] == 1

    # Create subentry on entry to test reconfigure and duplicate prevention
    subentry = ConfigSubentry(
        data=MappingProxyType(result["data"]),
        subentry_type=SUBENTRY_TYPE_CHANNEL,
        title="Balcony Cam",
        unique_id=f"{entry.unique_id}_ch1",
    )
    hass.config_entries.async_add_subentry(entry, subentry)

    # Attempt adding duplicate channel 1
    dup_flow = CameraChannelSubentryFlowHandler()
    dup_flow.hass = hass
    dup_flow.handler = (entry.entry_id, SUBENTRY_TYPE_CHANNEL)
    dup_flow.context = {"source": "user"}

    dup_result = await dup_flow.async_step_user({"channel": "1", "name": "Duplicate Balcony"})
    assert dup_result["type"] == FlowResultType.FORM
    assert dup_result["errors"] == {"channel": "channel_exists"}

    # Reconfigure subentry title
    reconf_flow = CameraChannelSubentryFlowHandler()
    reconf_flow.hass = hass
    reconf_flow.handler = (entry.entry_id, SUBENTRY_TYPE_CHANNEL)
    reconf_flow.context = {"source": "reconfigure", "subentry_id": subentry.subentry_id}

    reconf_result = await reconf_flow.async_step_reconfigure({"name": "Front Porch Cam"})
    assert reconf_result["type"] == FlowResultType.ABORT
    assert reconf_result["reason"] == "reconfigure_successful"
    assert subentry.title == "Front Porch Cam"
    assert subentry.data["name"] == "Front Porch Cam"


@pytest.mark.asyncio
async def test_nvr_hub_subentry_reconfigure(hass: HomeAssistant) -> None:
    """Test reconfiguring the NVR hub subentry title."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_DEVICE_TYPE: TYPE_NVR,
            CONF_USERNAME: "admin",
        },
        unique_id="NVR_HUB_TEST",
    )
    entry.add_to_hass(hass)

    hub_subentry = ConfigSubentry(
        data=MappingProxyType({"name": "CP PLUS NVR Main"}),
        subentry_type=SUBENTRY_TYPE_HUB,
        title="CP PLUS NVR Main",
        unique_id="NVR_HUB_TEST_hub",
    )
    hass.config_entries.async_add_subentry(entry, hub_subentry)

    reconf_flow = NVRHubSubentryFlowHandler()
    reconf_flow.hass = hass
    reconf_flow.handler = (entry.entry_id, SUBENTRY_TYPE_HUB)
    reconf_flow.context = {"source": "reconfigure", "subentry_id": hub_subentry.subentry_id}

    result = await reconf_flow.async_step_reconfigure({"name": "Security Office NVR"})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert hub_subentry.title == "Security Office NVR"
    assert hub_subentry.data["name"] == "Security Office NVR"

    # User step on NVR hub handler aborts because hub is already configured
    user_flow = NVRHubSubentryFlowHandler()
    user_flow.hass = hass
    user_flow.handler = (entry.entry_id, SUBENTRY_TYPE_HUB)
    user_flow.context = {"source": "user"}
    user_result = await user_flow.async_step_user()
    assert user_result["type"] == FlowResultType.ABORT
    assert user_result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_migration_v1_to_v2(hass: HomeAssistant) -> None:
    """Test migrating v1 config entry to v2."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_DEVICE_TYPE: TYPE_NVR,
            CONF_USERNAME: "admin",
        },
        unique_id="NVR_MIGRATE_TEST",
    )
    entry.add_to_hass(hass)

    assert entry.version == 1
    migration_success = await async_migrate_entry(hass, entry)
    assert migration_success is True
    assert entry.version == 2


@pytest.mark.asyncio
async def test_async_setup_entry_creates_subentries(hass: HomeAssistant) -> None:
    """Test async_setup_entry populates subentries for each discovered NVR channel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_DEVICE_TYPE: TYPE_NVR,
            CONF_USERNAME: "admin",
            CONF_NAME: "Main NVR",
        },
        unique_id="NVR_SETUP_TEST",
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

    mock_channels = [
        {
            "channel": 1,
            "index": 0,
            "name": "Gate Camera",
            "model": "CP-UNC-TA21L3C-Q",
            "is_native_cpplus": True,
            "has_smd": True,
            "has_tripwire": True,
        },
        {
            "channel": 2,
            "index": 1,
            "name": "Lobby ONVIF",
            "model": "IPC_GK",
            "is_native_cpplus": False,
            "has_smd": False,
            "has_tripwire": False,
        },
    ]

    with (
        patch("custom_components.cpplus.CPPlusClient") as mock_client_cls,
        patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True),
    ):
        mock_client = mock_client_cls.return_value
        mock_client.device_type = TYPE_NVR
        mock_client.host = "192.168.1.100"
        mock_client.port = 443
        mock_client.async_get_device_info = AsyncMock(
            return_value={
                "serial": "NVR_SETUP_TEST",
                "hardware": "CP-UNR-4K4322-V4",
                "firmware": "2.400.0000.0",
                "device_type": TYPE_NVR,
            }
        )
        mock_client.async_get_channels = AsyncMock(return_value=mock_channels)
        mock_client.async_nvr_request = AsyncMock(return_value="OK")
        mock_client.async_start_event_listener = AsyncMock()
        mock_client.async_close = AsyncMock()

        setup_ok = await async_setup_entry(hass, entry)
        assert setup_ok is True

        hub_subentries = entry.get_subentries_of_type(SUBENTRY_TYPE_HUB)
        assert len(hub_subentries) == 1
        assert hub_subentries[0].title == "CP PLUS NVR Main NVR"
        assert hub_subentries[0].unique_id == "NVR_SETUP_TEST_hub"

        subentries = entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
        assert len(subentries) == 2
        subentry_ch1 = next(s for s in subentries if s.data["channel"] == 1)
        subentry_ch2 = next(s for s in subentries if s.data["channel"] == 2)

        assert subentry_ch1.title == "Gate Camera"
        assert subentry_ch1.data["is_native_cpplus"] is True
        assert subentry_ch1.unique_id == "NVR_SETUP_TEST_ch1"

        assert subentry_ch2.title == "Lobby ONVIF"
        assert subentry_ch2.data["is_native_cpplus"] is False
        assert subentry_ch2.unique_id == "NVR_SETUP_TEST_ch2"


@pytest.mark.asyncio
async def test_camera_stream_source(hass: HomeAssistant) -> None:
    """Test camera stream source URL with NVR proxy."""
    from custom_components.cpplus.camera import CPPlusCamera
    from custom_components.cpplus.coordinator import CPPlusDataUpdateCoordinator
    from custom_components.cpplus.client import CPPlusClient
    from custom_components.cpplus.const import (
        STREAM_PROFILE_DAHUA_CH0,
        STREAM_PROFILE_DAHUA_CH1,
        STREAM_PROFILE_ONVIF,
        STREAM_PROFILE_LIVE,
    )

    client = CPPlusClient(
        hass,
        host="192.168.1.100",
        port=443,
        rtsp_port=554,
        username="admin",
        password="admin!123",
        device_type=TYPE_NVR,
    )
    coordinator = CPPlusDataUpdateCoordinator(hass, client, name="Test NVR")
    coordinator.data = {"serial": "TEST_NVR", "online": True}

    cam = CPPlusCamera(coordinator, channel=1, subtype=0, stream_label="Main", channel_name="Lobby")
    url = await cam.stream_source()
    assert url == "rtsp://admin:admin!123@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0"

    # Standalone camera defaults to video_live profile and RTSP over TLS (rtsps)
    client_cam = CPPlusClient(
        hass,
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="admin!123",
        device_type=TYPE_CAMERA,
        use_ssl=True,
    )
    coordinator_cam = CPPlusDataUpdateCoordinator(hass, client_cam, name="Test Camera")
    coordinator_cam.data = {"serial": "TEST_CAM", "online": True}
    cam_standalone = CPPlusCamera(coordinator_cam, channel=1, subtype=0, stream_label="Main")
    url_cam = await cam_standalone.stream_source()
    assert url_cam == "rtsps://admin:admin!123@10.0.29.212:554/video/live?channel=1&subtype=0"

    # Plain unencrypted rtsp when TLS is disabled
    assert client_cam.get_stream_url(1, 0, use_tls=False) == "rtsp://admin:admin!123@10.0.29.212:554/video/live?channel=1&subtype=0"

    # ONVIF profile test
    client_onvif = CPPlusClient(
        hass,
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="admin!123",
        device_type=TYPE_CAMERA,
        stream_profile=STREAM_PROFILE_ONVIF,
        rtsp_over_tls=False,
    )
    assert client_onvif.get_stream_url(1, 0) == "rtsp://admin:admin!123@10.0.29.212:554/onvif1"
    assert client_onvif.get_stream_url(1, 1) == "rtsp://admin:admin!123@10.0.29.212:554/onvif2"

    # Live profile test
    client_live = CPPlusClient(
        hass,
        host="10.0.29.212",
        port=443,
        rtsp_port=554,
        username="admin",
        password="admin!123",
        device_type=TYPE_CAMERA,
        stream_profile=STREAM_PROFILE_LIVE,
        rtsp_over_tls=False,
    )
    assert client_live.get_stream_url(1, 0) == "rtsp://admin:admin!123@10.0.29.212:554/live"


@pytest.mark.asyncio
async def test_options_flow(hass: HomeAssistant) -> None:
    """Test options flow for stream profile and ports."""
    from custom_components.cpplus.const import (
        CONF_STREAM_PROFILE,
        CONF_RTSP_OVER_TLS,
        STREAM_PROFILE_ONVIF,
    )
    from custom_components.cpplus.config_flow import CPPlusOptionsFlowHandler

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="OPTIONS_TEST_CAM",
        data={
            CONF_HOST: "10.0.29.212",
            CONF_PORT: 80,
            CONF_RTSP_PORT: 554,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret_password",
            CONF_DEVICE_TYPE: TYPE_CAMERA,
        },
    )
    mock_entry.add_to_hass(hass)
    options_flow = CPPlusOptionsFlowHandler()
    options_flow.hass = hass
    options_flow.handler = mock_entry.entry_id

    result = await options_flow.async_step_init()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await options_flow.async_step_init(
        user_input={
            CONF_STREAM_PROFILE: STREAM_PROFILE_ONVIF,
            CONF_RTSP_OVER_TLS: True,
            CONF_RTSP_PORT: 554,
            CONF_PORT: 80,
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_STREAM_PROFILE] == STREAM_PROFILE_ONVIF
    assert result2["data"][CONF_RTSP_OVER_TLS] is True
    assert result2["data"][CONF_PORT] == 80



