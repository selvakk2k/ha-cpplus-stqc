"""The CP PLUS Home Assistant integration."""

from __future__ import annotations

import logging
from types import MappingProxyType
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later
import voluptuous as vol

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
    CONF_STREAM_PROFILE,
    CONF_RTSP_OVER_TLS,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    SUBENTRY_TYPE_CHANNEL,
    SUBENTRY_TYPE_HUB,
    TYPE_CAMERA,
    TYPE_NVR,
    PTZ_COMMANDS,
)

_LOGGER = logging.getLogger(__name__)

import asyncio
from typing import Any

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _resolve_coordinators(hass: HomeAssistant, call: ServiceCall) -> list[CPPlusDataUpdateCoordinator]:
    """Resolve target coordinators from service call device or config entry targets."""
    coordinators_map: dict[str, CPPlusDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators_map:
        return []

    target_devices = call.data.get("device_id")
    if target_devices:
        if isinstance(target_devices, str):
            target_devices = [target_devices]
        dev_reg = dr.async_get(hass)
        matched = []
        for dev_id in target_devices:
            dev = dev_reg.async_get(dev_id)
            if dev:
                for entry_id in dev.config_entries:
                    if entry_id in coordinators_map and coordinators_map[entry_id] not in matched:
                        matched.append(coordinators_map[entry_id])
        if matched:
            return matched
        raise ServiceValidationError(f"Target device '{target_devices}' was not found in CP PLUS integration.")

    # Automatically target the single entry if exactly one exists
    if len(coordinators_map) == 1:
        return list(coordinators_map.values())

    # If multiple entries exist and target was omitted, fail closed
    raise ServiceValidationError(
        "Multiple CP PLUS devices found. Please specify target device_id in the service call."
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up CP PLUS integration level services."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_reboot(call: ServiceCall) -> None:
        """Handle reboot service call."""
        for coord in _resolve_coordinators(hass, call):
            _LOGGER.info("CP PLUS reboot requested for %s (%s)", coord.client.host, coord.device_name)
            await coord.client.async_reboot()

    async def handle_ptz_move(call: ServiceCall) -> None:
        """Handle PTZ movement service call."""
        channel = call.data.get("channel", 1)
        cmd_name = call.data.get("command", "up").lower()
        if cmd_name not in PTZ_COMMANDS:
            raise ServiceValidationError(f"Invalid PTZ command '{cmd_name}'. Valid commands: {list(PTZ_COMMANDS.keys())}")
        speed = call.data.get("speed", 5)
        duration = call.data.get("duration")
        ptz_cmd = PTZ_COMMANDS[cmd_name]

        for coord in _resolve_coordinators(hass, call):
            await coord.client.async_ptz_control(channel=channel, code=ptz_cmd, arg2=speed, stop=False)
            if duration:
                async def _auto_stop_cb(_now: Any, c=coord, ch=channel, cmd=ptz_cmd) -> None:
                    await c.client.async_ptz_control(channel=ch, code=cmd, stop=True)
                async_call_later(hass, duration, _auto_stop_cb)

    async def handle_ptz_stop(call: ServiceCall) -> None:
        """Handle PTZ stop service call."""
        channel = call.data.get("channel", 1)
        cmd_name = call.data.get("command", "up").lower()
        if cmd_name not in PTZ_COMMANDS:
            raise ServiceValidationError(f"Invalid PTZ command '{cmd_name}'. Valid commands: {list(PTZ_COMMANDS.keys())}")
        ptz_cmd = PTZ_COMMANDS[cmd_name]
        for coord in _resolve_coordinators(hass, call):
            await coord.client.async_ptz_control(channel=channel, code=ptz_cmd, stop=True)

    async def handle_ptz_preset(call: ServiceCall) -> None:
        """Handle PTZ preset jump service call."""
        channel = call.data.get("channel", 1)
        preset = call.data.get("preset", 1)
        for coord in _resolve_coordinators(hass, call):
            await coord.client.async_ptz_preset(channel=channel, preset=preset)

    if not hass.services.has_service(DOMAIN, "reboot"):
        hass.services.async_register(
            DOMAIN,
            "reboot",
            handle_reboot,
            schema=vol.Schema({vol.Optional("device_id"): cv.string}),
        )

    if not hass.services.has_service(DOMAIN, "ptz_move"):
        hass.services.async_register(
            DOMAIN,
            "ptz_move",
            handle_ptz_move,
            schema=vol.Schema(
                {
                    vol.Optional("device_id"): cv.string,
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Required("command"): vol.In(list(PTZ_COMMANDS.keys())),
                    vol.Optional("speed", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
                    vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=60.0)),
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
                    vol.Optional("device_id"): cv.string,
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Optional("command", default="up"): vol.In(list(PTZ_COMMANDS.keys())),
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
                    vol.Optional("device_id"): cv.string,
                    vol.Optional("channel", default=1): cv.positive_int,
                    vol.Required("preset"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
                }
            ),
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CP PLUS camera or NVR from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    port = entry.options.get(CONF_PORT, entry.data.get(CONF_PORT, DEFAULT_PORT_HTTPS))
    rtsp_port = entry.options.get(CONF_RTSP_PORT, entry.data.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP))
    stream_profile = entry.options.get(CONF_STREAM_PROFILE, entry.data.get(CONF_STREAM_PROFILE))
    rtsp_over_tls = entry.options.get(CONF_RTSP_OVER_TLS, entry.data.get(CONF_RTSP_OVER_TLS))
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
        stream_profile=stream_profile,
        rtsp_over_tls=rtsp_over_tls,
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    coordinator = CPPlusDataUpdateCoordinator(hass, client, name, entry=entry)
    await coordinator.async_config_entry_first_refresh()

    # Pre-register the parent device in the device registry to obtain its device ID
    device_registry = dr.async_get(hass)
    serial = coordinator.data.get("serial", client.host) if coordinator.data else client.host
    model = coordinator.device_info_data.get("hardware") or (
        "CP-UNR-4K4322-V4" if client.device_type == TYPE_NVR else "CP-UNC-TA21L3C-Q"
    )

    if client.device_type == TYPE_NVR:
        if coordinator.device_name and coordinator.device_name != client.host:
            dev_name = f"CP PLUS NVR {coordinator.device_name}"
        else:
            dev_name = f"CP PLUS NVR {model}" if model else "CP PLUS NVR"

        # Register NVR Hub subentry so the NVR device is not orphaned in the UI
        nvr_subentries = entry.get_subentries_of_type(SUBENTRY_TYPE_HUB)
        if not nvr_subentries:
            nvr_subentry = ConfigSubentry(
                data=MappingProxyType({"name": dev_name}),
                subentry_type=SUBENTRY_TYPE_HUB,
                title=dev_name,
                unique_id=f"{serial}_hub",
            )
            hass.config_entries.async_add_subentry(entry, nvr_subentry)
            nvr_subentry_id = nvr_subentry.subentry_id
        else:
            nvr_subentry = nvr_subentries[0]
            nvr_subentry_id = nvr_subentry.subentry_id
            if nvr_subentry.title:
                dev_name = nvr_subentry.title
    else:
        nvr_subentry_id = None
        if coordinator.device_name and coordinator.device_name != client.host:
            dev_name = f"CP PLUS Camera {coordinator.device_name}"
        else:
            dev_name = f"CP PLUS Camera {model}" if model else "CP PLUS Camera"

    coordinator.hub_subentry_id = nvr_subentry_id

    parent_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=nvr_subentry_id,
        identifiers={(DOMAIN, serial)},
        name=dev_name,
        manufacturer=MANUFACTURER,
        model=model,
        sw_version=coordinator.device_info_data.get("firmware", "Unknown"),
        configuration_url=f"https://{client.host}:{client.port}",
    )
    coordinator.parent_device_id = parent_device.id

    hass.data[DOMAIN][entry.entry_id] = coordinator

    _LOGGER.info("Starting real-time event listener for %s (%s)", host, client.device_type)
    entry.async_create_background_task(
        hass,
        client.async_start_event_listener(
            coordinator.handle_event,
            on_auth_failed=lambda: entry.async_start_reauth(hass),
            on_disconnect=coordinator.clear_events,
        ),
        f"cpplus_event_listener_{host}",
    )

    # Register camera channel subentries for NVR entries
    if client.device_type == TYPE_NVR and coordinator.channels:
        existing_subentries = {
            s.unique_id: s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
        }
        for ch in coordinator.channels:
            ch_uid = f"{serial}_ch{ch['channel']}"
            if ch_uid not in existing_subentries:
                subentry = ConfigSubentry(
                    data=MappingProxyType({
                        "channel": ch["channel"],
                        "channel_index": ch["index"],
                        "name": ch["name"],
                        "is_native_cpplus": ch.get("is_native_cpplus", False),
                        "has_smd": ch.get("has_smd", False),
                        "has_tripwire": ch.get("has_tripwire", False),
                        "model": ch.get("model", "Camera"),
                        "address": ch.get("address"),
                    }),
                    subentry_type=SUBENTRY_TYPE_CHANNEL,
                    title=ch["name"],
                    unique_id=ch_uid,
                )
                hass.config_entries.async_add_subentry(entry, subentry)
            else:
                subentry = existing_subentries[ch_uid]
                if subentry.title:
                    ch["name"] = subentry.title

    # Ensure any manually created channel subentry has a synthesized channel in coordinator.channels
    if client.device_type == TYPE_NVR:
        configured_channel_nums = {ch["channel"] for ch in coordinator.channels}
        for subentry in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL):
            ch_num = subentry.data.get("channel")
            if ch_num and ch_num not in configured_channel_nums:
                coordinator.channels.append({
                    "channel": ch_num,
                    "index": ch_num - 1,
                    "name": subentry.title or subentry.data.get("name") or f"Channel {ch_num}",
                    "model": subentry.data.get("model", "Camera"),
                    "manufacturer": "Generic ONVIF",
                    "is_native_cpplus": subentry.data.get("is_native_cpplus", False),
                    "has_smd": subentry.data.get("has_smd", False),
                    "has_tripwire": subentry.data.get("has_tripwire", False),
                    "serial": None,
                    "firmware": None,
                    "address": None,
                })
                configured_channel_nums.add(ch_num)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Purge orphaned switch and select entities for non-native camera channels
    if client.device_type == TYPE_NVR and coordinator.channels:
        ent_reg = er.async_get(hass)
        non_native_channels = {
            ch["channel"] for ch in coordinator.channels if not ch.get("is_native_cpplus", False)
        }
        for entity_entry in list(ent_reg.entities.values()):
            ent_domain = getattr(entity_entry, "domain", entity_entry.entity_id.split(".", 1)[0])
            if entity_entry.config_entry_id == entry.entry_id and ent_domain in ("switch", "select"):
                for ch_num in non_native_channels:
                    prefix = f"{serial}_ch{ch_num}_"
                    if entity_entry.unique_id.startswith(prefix):
                        _LOGGER.warning(
                            "Purging orphaned %s entity %s (%s) for non-native channel %d",
                            ent_domain,
                            entity_entry.entity_id,
                            entity_entry.unique_id,
                            ch_num,
                        )
                        ent_reg.async_remove(entity_entry.entity_id)
                        break

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry to new version."""
    _LOGGER.debug("Migrating CP PLUS config entry from version %s", entry.version)

    if entry.version == 1:
        # Migration from v1 to v2:
        # Subentries are created dynamically during setup
        hass.config_entries.async_update_entry(entry, version=2)
        _LOGGER.info("Successfully migrated CP PLUS config entry to version 2")

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_close()
    return unload_ok
