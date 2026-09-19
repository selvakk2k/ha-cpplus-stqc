"""The CP PLUS Home Assistant integration."""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr
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
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    TYPE_CAMERA,
    TYPE_NVR,
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

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_close()
    return unload_ok
