"""The CP PLUS Home Assistant integration."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any
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
    SUBENTRY_TYPE_CHANNEL,
    LEGACY_SUBENTRY_TYPE_CHANNEL,
    SUBENTRY_TYPE_HUB,
    LEGACY_SUBENTRY_TYPE_HUB,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    TYPE_CAMERA,
    TYPE_NVR,
    STREAM_PROFILE_DAHUA_CH1,
    STREAM_PROFILE_AUTO,
    PTZ_COMMANDS,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _async_get_device_by_identifier(
    dev_reg: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str
) -> dr.DeviceEntry | None:
    """Get device by identifier with backward-compatible fallback."""
    if hasattr(dev_reg, "async_get_device_by_identifier"):
        return dev_reg.async_get_device_by_identifier(identifier, config_entry_id)
    return dev_reg.async_get_device(identifiers={identifier})


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
    device_type = entry.data.get(CONF_DEVICE_TYPE, TYPE_CAMERA)
    default_profile = STREAM_PROFILE_AUTO if device_type == TYPE_CAMERA else STREAM_PROFILE_DAHUA_CH1
    stream_profile = entry.options.get(CONF_STREAM_PROFILE, entry.data.get(CONF_STREAM_PROFILE, default_profile))
    rtsp_over_tls = entry.options.get(CONF_RTSP_OVER_TLS, entry.data.get(CONF_RTSP_OVER_TLS))
    if rtsp_over_tls is None:
        rtsp_over_tls = await CPPlusClient.async_probe_rtsp_tls(host, rtsp_port)
    username = entry.data[CONF_USERNAME]
    password = entry.data.get(CONF_PASSWORD, "")
    name = entry.data.get(CONF_NAME, host)

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

    coordinator = CPPlusDataUpdateCoordinator(hass, client, name)
    await coordinator.async_config_entry_first_refresh()

    if client.device_type == TYPE_CAMERA:
        try:
            await client.async_probe_rtsp_stream_paths()
        except Exception as err:
            _LOGGER.warning("Direct RTSP stream probe failed on %s: %s", client.host, err)
        try:
            await client.async_get_onvif_stream_uris()
        except Exception as err:
            _LOGGER.warning("ONVIF stream URI discovery failed on %s: %s", client.host, err)

    # Pre-register the parent device in the device registry to obtain its device ID
    device_registry = dr.async_get(hass)
    serial = coordinator.data.get("serial", client.host) if coordinator.data else client.host
    model = coordinator.device_info_data.get("hardware") or (
        "CP PLUS NVR" if client.device_type == TYPE_NVR else "CP PLUS IP Camera"
    )

    if client.device_type == TYPE_NVR:
        if coordinator.device_name and coordinator.device_name != client.host:
            dev_name = f"CP PLUS NVR {coordinator.device_name}"
        else:
            dev_name = f"CP PLUS NVR {model}" if model else "CP PLUS NVR"
    else:
        if coordinator.device_name and coordinator.device_name != client.host:
            dev_name = f"CP PLUS Camera {coordinator.device_name}"
        else:
            dev_name = f"CP PLUS Camera {model}" if model else "CP PLUS Camera"

    raw_fw = coordinator.device_info_data.get("firmware", "Unknown")
    if isinstance(raw_fw, dict):
        v = raw_fw.get("Version") or raw_fw.get("softwareVersion") or "Unknown"
        b = raw_fw.get("BuildDate") or raw_fw.get("build")
        sw_version_str = f"{v} (Build {b})" if b else str(v)
    else:
        sw_version_str = str(raw_fw)

    parent_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, serial)},
        name=dev_name,
        manufacturer=MANUFACTURER,
        model=model,
        sw_version=sw_version_str,
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

    if client.device_type == TYPE_NVR:
        # Register NVR Hub subentry for the parent NVR device
        hub_uid = f"{serial}_hub"
        existing_hub_subentries = {
            s.unique_id: s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_HUB)
        }
        for s in entry.get_subentries_of_type(LEGACY_SUBENTRY_TYPE_HUB):
            existing_hub_subentries[s.unique_id] = s

        if hub_uid not in existing_hub_subentries:
            hub_subentry = ConfigSubentry(
                data=MappingProxyType({
                    "host": client.host,
                    "port": client.port,
                    "model": model,
                    "serial": serial,
                }),
                subentry_type=SUBENTRY_TYPE_HUB,
                title=dev_name,
                unique_id=hub_uid,
            )
            hass.config_entries.async_add_subentry(entry, hub_subentry)
        else:
            hub_subentry = existing_hub_subentries[hub_uid]

        coordinator.hub_subentry_id = hub_subentry.subentry_id

        # Associate root NVR device with the hub subentry so HA UI groups it cleanly
        if parent_device.config_subentry_id != hub_subentry.subentry_id:
            device_registry.async_update_device(
                parent_device.id,
                new_config_subentry_id=hub_subentry.subentry_id,
            )

        # Register camera channel subentries for NVR entries
        if coordinator.channels:
            existing_subentries = {
                s.unique_id: s
                for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
            }
            for s in entry.get_subentries_of_type(LEGACY_SUBENTRY_TYPE_CHANNEL):
                existing_subentries[s.unique_id] = s
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
                    existing_subentries[ch_uid] = subentry
                else:
                    subentry = existing_subentries[ch_uid]
                    if subentry.title:
                        ch["name"] = subentry.title

                # Associate existing device in registry with subentry so HA UI groups them cleanly
                dev = _async_get_device_by_identifier(device_registry, (DOMAIN, ch_uid), entry.entry_id)
                if dev and dev.config_subentry_id != subentry.subentry_id:
                    _LOGGER.info(
                        "Associating device %s (%s) with subentry %s",
                        dev.name,
                        ch_uid,
                        subentry.subentry_id,
                    )
                    device_registry.async_update_device(
                        dev.id,
                        new_config_subentry_id=subentry.subentry_id,
                    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Ensure all channel devices in the registry are linked to their corresponding subentry
    if client.device_type == TYPE_NVR and coordinator.channels:
        subentries_by_uid = {
            s.unique_id: s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
        }
        for s in entry.get_subentries_of_type(LEGACY_SUBENTRY_TYPE_CHANNEL):
            subentries_by_uid[s.unique_id] = s
        for ch in coordinator.channels:
            ch_uid = f"{serial}_ch{ch['channel']}"
            subentry = subentries_by_uid.get(ch_uid)
            if subentry:
                dev = _async_get_device_by_identifier(device_registry, (DOMAIN, ch_uid), entry.entry_id)
                if dev and dev.config_subentry_id != subentry.subentry_id:
                    device_registry.async_update_device(
                        dev.id,
                        new_config_subentry_id=subentry.subentry_id,
                    )

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
