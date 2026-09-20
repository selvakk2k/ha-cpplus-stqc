"""The CP PLUS Home Assistant integration."""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .views import CPPlusPlaybackMediaView

from .client import CPPlusClient
from .coordinator import CPPlusDataUpdateCoordinator
from .const import (
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    CONF_HOST,
    CONF_PORT,
    CONF_RTSP_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_DEVICE_TYPE,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    TYPE_CAMERA,
    TYPE_NVR,
    PTZ_COMMANDS,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CP PLUS camera or NVR from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT_HTTPS)
    rtsp_port = entry.data.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP)
    username = entry.data[CONF_USERNAME]
    password = entry.data.get(CONF_PASSWORD, "")
    name = entry.data.get(CONF_NAME, host)
    device_type = entry.data.get(CONF_DEVICE_TYPE, TYPE_CAMERA)

    client = CPPlusClient(
        hass=hass,
        host=host,
        port=port,
        rtsp_port=rtsp_port,
        username=username,
        password=password,
        device_type=device_type,
    )

    coordinator = CPPlusDataUpdateCoordinator(hass, client, name)
    await coordinator.async_config_entry_first_refresh()

    # Pre-register the parent device in the device registry to obtain its device ID
    device_registry = dr.async_get(hass)
    serial = coordinator.data.get("serial", client.host) if coordinator.data else client.host
    model = coordinator.device_info_data.get("hardware") or (
        "CP-UNR-4K4322-V4" if client.device_type == TYPE_NVR else "CP-UNC-TA21L3C-Q"
    )
    parent_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, serial)},
        name=f"CP PLUS STQC {coordinator.device_name}",
        manufacturer=MANUFACTURER,
        model=model,
        sw_version=coordinator.device_info_data.get("firmware", "Unknown"),
        configuration_url=f"https://{client.host}:{client.port}",
    )
    coordinator.parent_device_id = parent_device.id

    hass.data[DOMAIN][entry.entry_id] = coordinator

    if client.device_type == TYPE_NVR:
        _LOGGER.info("Starting real-time NVR event listener for %s", host)
        entry.async_create_background_task(
            hass,
            client.async_start_event_listener(coordinator.handle_event),
            f"cpplus_event_listener_{host}",
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_reboot(call: ServiceCall) -> None:
        """Handle reboot service call."""
        _LOGGER.info("CP PLUS reboot requested for %s", host)
        await client.async_reboot()

    hass.services.async_register(
        DOMAIN,
        "reboot",
        handle_reboot,
        schema=vol.Schema({}),
    )

    async def handle_ptz_move(call: ServiceCall) -> None:
        """Handle PTZ movement service call."""
        channel = call.data.get("channel", 1)
        cmd_name = call.data.get("command", "up").lower()
        speed = call.data.get("speed", 5)
        ptz_cmd = PTZ_COMMANDS.get(cmd_name, "Up")
        await client.async_ptz_control(channel=channel, code=ptz_cmd, arg2=speed, stop=False)

    async def handle_ptz_stop(call: ServiceCall) -> None:
        """Handle PTZ stop service call."""
        channel = call.data.get("channel", 1)
        cmd_name = call.data.get("command", "up").lower()
        ptz_cmd = PTZ_COMMANDS.get(cmd_name, "Up")
        await client.async_ptz_control(channel=channel, code=ptz_cmd, stop=True)

    async def handle_ptz_preset(call: ServiceCall) -> None:
        """Handle PTZ preset jump service call."""
        channel = call.data.get("channel", 1)
        preset = call.data.get("preset", 1)
        await client.async_ptz_preset(channel=channel, preset=preset)

    if not hass.services.has_service(DOMAIN, "ptz_move"):
        hass.services.async_register(
            DOMAIN,
            "ptz_move",
            handle_ptz_move,
            schema=vol.Schema(
                {
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Required("command"): cv.string,
                    vol.Optional("speed", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, "ptz_stop"):
        hass.services.async_register(
            DOMAIN,
            "ptz_stop",
            handle_ptz_stop,
            schema=vol.Schema(
                {
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Optional("command", default="up"): cv.string,
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, "ptz_preset"):
        hass.services.async_register(
            DOMAIN,
            "ptz_preset",
            handle_ptz_preset,
            schema=vol.Schema(
                {
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Required("preset"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
                }
            ),
        )

    # Register HTTP media streaming view for playback
    if "playback_view" not in hass.data[DOMAIN]:
        hass.http.register_view(CPPlusPlaybackMediaView(hass))
        hass.data[DOMAIN]["playback_view"] = True

    async def handle_play_recording(call: ServiceCall) -> None:
        """Handle historical recording playback service call."""
        channel = call.data.get("channel", 1)
        start_time = call.data["start_time"]
        end_time = call.data["end_time"]
        media_player = call.data.get("media_player")

        url = client.get_playback_url(channel, start_time, end_time)
        _LOGGER.info("Playing recording for channel %d (%s - %s) on %s", channel, start_time, end_time, media_player)

        if media_player:
            await hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": media_player,
                    "media_content_id": url,
                    "media_content_type": "video",
                },
                blocking=True,
            )

    if not hass.services.has_service(DOMAIN, "play_recording"):
        hass.services.async_register(
            DOMAIN,
            "play_recording",
            handle_play_recording,
            schema=vol.Schema(
                {
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Required("start_time"): cv.string,
                    vol.Required("end_time"): cv.string,
                    vol.Optional("media_player"): cv.entity_id,
                },
                extra=vol.ALLOW_EXTRA,
            ),
        )

    async def handle_search_recordings(call: ServiceCall) -> ServiceResponse:
        """Search NVR for recorded clips within a time range."""
        channel = call.data.get("channel", 1)
        start_time = call.data["start_time"]
        end_time = call.data["end_time"]
        count = call.data.get("count", 50)
        clips = await client.async_find_recordings(
            channel=channel, start_time=start_time, end_time=end_time, count=count
        )
        return {"channel": channel, "count": len(clips), "clips": clips}

    if not hass.services.has_service(DOMAIN, "search_recordings"):
        hass.services.async_register(
            DOMAIN,
            "search_recordings",
            handle_search_recordings,
            schema=vol.Schema(
                {
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Required("start_time"): cv.string,
                    vol.Required("end_time"): cv.string,
                    vol.Optional("count", default=50): cv.positive_int,
                },
                extra=vol.ALLOW_EXTRA,
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_close()
    return unload_ok
