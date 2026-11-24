"""Diagnostics support for CP PLUS."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

if TYPE_CHECKING:
    from .coordinator import CPPlusDataUpdateCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_HOST,
    "password",
    "username",
    "host",
    "rtsp_url",
    "snapshot_url",
    "serial_number",
    "mac_address",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a CP PLUS config entry."""
    coordinator: CPPlusDataUpdateCoordinator | None = (
        getattr(entry, "runtime_data", None)
        or hass.data.get(DOMAIN, {}).get(entry.entry_id)
    )

    coordinator_info: dict[str, Any] = {}
    if coordinator:
        client = coordinator.client
        coordinator_info = {
            "last_update_success": coordinator.last_update_success,
            "connected": getattr(client, "is_connected", None),
            "device_type": getattr(client, "device_type", None),
            "model": getattr(client, "model", None),
            "sw_version": getattr(client, "sw_version", None),
            "channels_count": len(getattr(client, "channels", {})),
            "data": (
                async_redact_data(dict(coordinator.data), TO_REDACT)
                if coordinator.data and isinstance(coordinator.data, dict)
                else {}
            ),
        }

    # Format subentries if present
    subentries_info: list[dict[str, Any]] = []
    if hasattr(entry, "subentries") and entry.subentries:
        for subentry in entry.subentries.values():
            subentries_info.append(
                {
                    "subentry_id": getattr(subentry, "subentry_id", None),
                    "subentry_type": getattr(subentry, "subentry_type", None),
                    "title": getattr(subentry, "title", None),
                    "data": async_redact_data(
                        dict(getattr(subentry, "data", {})), TO_REDACT
                    ),
                }
            )

    return {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "pref_disable_new_entities": entry.pref_disable_new_entities,
            "pref_disable_polling": entry.pref_disable_polling,
            "subentries": subentries_info,
        },
        "coordinator": coordinator_info,
    }

