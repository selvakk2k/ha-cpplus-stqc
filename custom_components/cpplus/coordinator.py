"""DataUpdateCoordinator for CP PLUS STQC cameras and NVRs."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo

from .client import CPPlusClient
from .const import (
    DOMAIN,
    MANUFACTURER,
    TYPE_NVR,
    EVENT_HUMAN,
    EVENT_VEHICLE,
    EVENT_TRIPWIRE,
    EVENT_MOTION,
)

_LOGGER = logging.getLogger(__name__)


class CPPlusDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage CP PLUS camera and NVR state updates."""

    def __init__(self, hass: HomeAssistant, client: CPPlusClient, name: str) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"CP PLUS STQC {name}",
            update_interval=timedelta(seconds=30),
        )
        self.client = client
        self.device_name = name
        self.device_info_data: dict[str, Any] = {}
        self.channels: list[dict[str, Any]] = []
        self.channel_events: dict[int, dict[str, bool]] = {}
        self.parent_device_id: str | None = None

    def handle_event(self, channel_idx: int, event_code: str, action: str) -> None:
        """Process push event from NVR event stream."""
        is_active = (action.lower() == "start")
        events = self.channel_events.setdefault(channel_idx, {})

        if event_code in ["SmartMotionHuman", "HumanDetect"]:
            events[EVENT_HUMAN] = is_active
            _LOGGER.debug("Channel %d Human detection: %s", channel_idx, is_active)
        elif event_code in ["SmartMotionVehicle", "VehicleDetect"]:
            events[EVENT_VEHICLE] = is_active
            _LOGGER.debug("Channel %d Vehicle detection: %s", channel_idx, is_active)
        elif event_code in ["CrossLineDetection", "CrossRegionDetection"]:
            events[EVENT_TRIPWIRE] = is_active
            _LOGGER.debug("Channel %d Tripwire alert: %s", channel_idx, is_active)
        elif event_code in ["VideoMotion"]:
            events[EVENT_MOTION] = is_active
            _LOGGER.debug("Channel %d VideoMotion: %s", channel_idx, is_active)

        # Fire native Home Assistant event for automation triggers
        ch = next((c for c in self.channels if c.get("index") == channel_idx or c.get("channel") == channel_idx + 1), {})
        ch_name = ch.get("name") or f"Channel {channel_idx + 1}"
        self.hass.bus.async_fire(
            "cpplus_event",
            {
                "channel": channel_idx + 1,
                "channel_index": channel_idx,
                "channel_name": ch_name,
                "event_type": event_code,
                "action": action,
            },
        )

        # Notify entity listeners immediately without waiting for polling loop
        if self.data:
            new_data = dict(self.data)
            new_data["channel_events"] = dict(self.channel_events)
            self.async_set_updated_data(new_data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest camera or NVR telemetry."""
        try:
            if not self.device_info_data:
                self.device_info_data = await self.client.async_get_device_info()

            if self.client.device_type == TYPE_NVR:
                if not self.channels:
                    self.channels = await self.client.async_get_channels()
                # Lightweight connection ping
                await self.client.async_nvr_request("/cgi-bin/magicBox.cgi?action=getDeviceType")
            else:
                # Periodic ping / keepalive query to test connection on standalone camera
                await self.client.async_call_rpc("magicBox.getDeviceType")

            return {
                "online": True,
                "serial": self.device_info_data.get("serial", self.client.host),
                "hardware": self.device_info_data.get("hardware", "CP PLUS STQC"),
                "firmware": self.device_info_data.get("firmware", "Unknown"),
                "device_type": self.client.device_type,
                "channels": self.channels,
                "channel_events": self.channel_events,
            }
        except Exception as err:
            _LOGGER.warning("Coordinator update error on %s: %s", self.client.host, err)
            raise UpdateFailed(f"Error communicating with CP PLUS device at {self.client.host}: {err}") from err

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant DeviceInfo for the main NVR or standalone camera."""
        serial = self.device_info_data.get("serial") or self.client.host
        model = self.device_info_data.get("hardware") or ("CP-UNR-4K4322-V4" if self.client.device_type == TYPE_NVR else "CP-UNC-TA21L3C-Q")
        firmware = self.device_info_data.get("firmware") or "Unknown"

        return DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"CP PLUS STQC {self.device_name}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=firmware,
            configuration_url=f"https://{self.client.host}:{self.client.port}",
        )

    def get_channel_device_info(self, channel_num: int, channel_name: str) -> DeviceInfo:
        """Return Home Assistant DeviceInfo for a child camera channel connected to the NVR."""
        nvr_serial = self.device_info_data.get("serial") or self.client.host
        channel_unique_id = f"{nvr_serial}_ch{channel_num}"

        ch = next((c for c in self.channels if c.get("channel") == channel_num), {})
        model = ch.get("model") or "Camera"
        manufacturer = ch.get("manufacturer") or ("CP PLUS" if model.startswith("CP-") else ("Dahua" if model.startswith("VTO") else ("Xiongmai" if model.startswith("IPC_GK") else "Generic ONVIF")))
        serial = ch.get("serial")
        firmware = ch.get("firmware")
        addr = ch.get("address")
        https_port = ch.get("https_port")
        http_port = ch.get("http_port")

        if addr:
            if https_port and https_port not in ("0", "443"):
                config_url = f"https://{addr}:{https_port}"
            elif https_port == "443":
                config_url = f"https://{addr}"
            elif http_port and http_port not in ("0", "80"):
                config_url = f"http://{addr}:{http_port}"
            else:
                config_url = f"http://{addr}"
        else:
            config_url = f"https://{self.client.host}:{self.client.port}"

        info = DeviceInfo(
            identifiers={(DOMAIN, channel_unique_id)},
            name=f"{channel_name}",
            manufacturer=manufacturer,
            model=model,
            configuration_url=config_url,
        )
        if firmware:
            info["sw_version"] = firmware
        if serial:
            info["serial_number"] = serial
        if self.parent_device_id:
            info["via_device_id"] = self.parent_device_id
        return info

