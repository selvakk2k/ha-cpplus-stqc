"""Camera platform for CP PLUS STQC integration."""

from __future__ import annotations

import logging
from typing import Any
import urllib.parse

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_PORT_RTSP, DOMAIN, SUBENTRY_TYPE_CHANNEL, TYPE_NVR
from .coordinator import CPPlusDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS cameras from a config entry."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    if coordinator.client.device_type == TYPE_NVR and coordinator.channels:
        subentries = {
            s.data.get("channel"): s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
        }
        for ch in coordinator.channels:
            ch_num = ch["channel"]
            subentry = subentries.get(ch_num)
            ch_name = (subentry.title if subentry and subentry.title else None) or ch["name"]
            subentry_id = subentry.subentry_id if subentry else None
            subentry_data = dict(subentry.data) if subentry else {}

            entities: list[Camera] = [
                CPPlusCamera(
                    coordinator,
                    channel=ch_num,
                    subtype=0,
                    stream_label="Main",
                    channel_name=ch_name,
                    subentry_data=subentry_data,
                ),
                CPPlusCamera(
                    coordinator,
                    channel=ch_num,
                    subtype=1,
                    stream_label="Sub",
                    channel_name=ch_name,
                    subentry_data=subentry_data,
                ),
            ]
            async_add_entities(entities, config_subentry_id=subentry_id)
    else:
        entities = [
            CPPlusCamera(coordinator, channel=1, subtype=0, stream_label="Main"),
            CPPlusCamera(coordinator, channel=1, subtype=1, stream_label="Sub"),
        ]
        async_add_entities(entities)


class CPPlusCamera(CoordinatorEntity[CPPlusDataUpdateCoordinator], Camera):
    """Representation of a CP PLUS Camera stream."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: CPPlusDataUpdateCoordinator,
        channel: int = 1,
        subtype: int = 0,
        stream_label: str = "Main",
        channel_name: str | None = None,
        subentry_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the CP PLUS camera entity."""
        super().__init__(coordinator)
        Camera.__init__(self)
        self._channel = channel
        self._subtype = subtype
        self._stream_label = stream_label
        self._channel_name = channel_name
        self._subentry_data = subentry_data or {}

        serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )

        if self.coordinator.client.device_type == TYPE_NVR and channel_name:
            self._attr_unique_id = f"{serial}_ch{channel}_{stream_label.lower()}"
            self._attr_name = f"{stream_label}"
            self._attr_device_info = self.coordinator.get_channel_device_info(channel, channel_name)
        else:
            self._attr_unique_id = f"{serial}_{stream_label.lower()}"
            self._attr_name = stream_label
            self._attr_device_info = self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return true if entity is available."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        attrs: dict[str, Any] = {
            "channel": self._channel,
            "subtype": self._subtype,
            "stream_profile": self._stream_label,
        }
        if self._subentry_data and self._subentry_data.get("direct_connection"):
            attrs["connection_mode"] = "direct"
            attrs["direct_host"] = self._subentry_data.get("direct_host")
        else:
            attrs["connection_mode"] = "nvr_proxy"
        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Capture a snapshot image from the CP PLUS camera."""
        return await self.coordinator.client.async_get_snapshot(self._channel)

    async def stream_source(self) -> str | None:
        """Return the RTSP stream URL."""
        if (
            self._subentry_data
            and self._subentry_data.get("direct_connection")
            and self._subentry_data.get("direct_host")
        ):
            direct_host = self._subentry_data["direct_host"]
            direct_rtsp_port = self._subentry_data.get("direct_rtsp_port", DEFAULT_PORT_RTSP)
            username = urllib.parse.quote(
                self._subentry_data.get("direct_username") or self.coordinator.client.username,
                safe="",
            )
            password = urllib.parse.quote(
                self._subentry_data.get("direct_password") or self.coordinator.client.password,
                safe="",
            )
            return (
                f"rtsp://{username}:{password}@{direct_host}:{direct_rtsp_port}"
                f"/cam/realmonitor?channel=1&subtype={self._subtype}"
            )

        return self.coordinator.client.get_stream_url(self._channel, self._subtype)

