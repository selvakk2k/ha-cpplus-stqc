"""Media Source implementation for CP PLUS STQC cameras and NVRs."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any
import urllib.parse

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up CP PLUS media source."""
    return CPPlusMediaSource(hass)


class CPPlusMediaSource(MediaSource):
    """Provide CP PLUS STQC surveillance recordings as a media source."""

    name: str = "CP PLUS STQC Surveillance"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize CP PLUS media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_browse_media(
        self,
        item: MediaSourceItem,
    ) -> BrowseMediaSource:
        entries = {
            k: v for k, v in self.hass.data.get(DOMAIN, {}).items()
            if hasattr(v, "device_name")
        }
        if not entries:
            raise Unresolvable("No CP PLUS STQC integration entries active")

        # Root level: List of configured NVRs
        if not item.identifier:
            return self._build_root(entries)

        parts = item.identifier.split("/")
        entry_id = parts[0]
        coordinator = entries.get(entry_id)
        if not coordinator:
            raise Unresolvable(f"Integration entry {entry_id} not found")

        # Level 1: List of Camera Channels on this NVR
        if len(parts) == 1:
            return self._build_channels(coordinator, entry_id)

        try:
            channel = int(parts[1])
        except ValueError:
            raise Unresolvable(f"Invalid channel identifier: {parts[1]}") from None

        # Level 2: List of recent calendar dates for selected channel
        if len(parts) == 2:
            return self._build_dates(coordinator, entry_id, channel)

        # Level 3: Event Category folders for selected Date
        date_str = parts[2]
        if len(parts) == 3:
            return await self._build_event_categories(coordinator, entry_id, channel, date_str)

        # Level 4: Clips within selected Event Category
        category = parts[3]
        if len(parts) == 4:
            return await self._build_clips(coordinator, entry_id, channel, date_str, category)

        raise Unresolvable(f"Unknown media identifier: {item.identifier}")

    def _build_root(self, entries: dict[str, Any]) -> BrowseMediaSource:
        """Build the root directory listing all active NVR hubs."""
        children = []
        for entry_id, coordinator in entries.items():
            name = f"CP PLUS STQC {coordinator.device_name}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=entry_id,
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=name,
                    can_play=False,
                    can_expand=True,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title="CP PLUS STQC Surveillance",
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _build_channels(self, coordinator: Any, entry_id: str) -> BrowseMediaSource:
        """Build list of camera channels."""
        children = []
        for ch in coordinator.channels:
            ch_num = ch.get("channel", 1)
            ch_name = ch.get("name") or f"Channel {ch_num}"
            model = ch.get("model") or "Camera"
            mfr = ch.get("manufacturer") or "CP PLUS"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{ch_num}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"Ch {ch_num}: {ch_name} ({mfr} {model})",
                    can_play=False,
                    can_expand=True,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"CP PLUS STQC {coordinator.device_name} Cameras",
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _build_dates(self, coordinator: Any, entry_id: str, channel: int) -> BrowseMediaSource:
        """Build list of recent calendar dates for browsing."""
        ch = next((c for c in coordinator.channels if c.get("channel") == channel), {})
        ch_name = ch.get("name") or f"Channel {channel}"

        children = []
        now = datetime.now()
        for offset in range(7):
            dt = now - timedelta(days=offset)
            date_str = dt.strftime("%Y-%m-%d")
            label = "Today" if offset == 0 else ("Yesterday" if offset == 1 else dt.strftime("%A, %b %d"))
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{channel}/{date_str}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"{label} ({date_str})",
                    can_play=False,
                    can_expand=True,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{channel}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{ch_name} - Select Date",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _build_event_categories(
        self, coordinator: Any, entry_id: str, channel: int, date_str: str
    ) -> BrowseMediaSource:
        """Build event category subfolders (All, Motion, Human, Vehicle, Continuous) for a date."""
        ch = next((c for c in coordinator.channels if c.get("channel") == channel), {})
        ch_name = ch.get("name") or f"Channel {channel}"

        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59"

        recordings = await coordinator.client.async_find_recordings(
            channel=channel, start_time=start_time, end_time=end_time, count=100
        )

        counts = {
            "all": len(recordings),
            "human": sum(1 for r in recordings if r.get("event_type") == "Human"),
            "vehicle": sum(1 for r in recordings if r.get("event_type") == "Vehicle"),
            "motion": sum(1 for r in recordings if r.get("event_type") == "Motion"),
            "continuous": sum(1 for r in recordings if r.get("event_type") == "Continuous"),
        }

        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{channel}/{date_str}/all",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=f"All Recordings ({counts['all']} clips)",
                can_play=False,
                can_expand=True,
            )
        ]

        if counts["human"] > 0:
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{channel}/{date_str}/human",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"Human Detection ({counts['human']} clips)",
                    can_play=False,
                    can_expand=True,
                )
            )

        if counts["vehicle"] > 0:
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{channel}/{date_str}/vehicle",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"Vehicle Detection ({counts['vehicle']} clips)",
                    can_play=False,
                    can_expand=True,
                )
            )

        if counts["motion"] > 0:
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{channel}/{date_str}/motion",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"Motion Events ({counts['motion']} clips)",
                    can_play=False,
                    can_expand=True,
                )
            )

        if counts["continuous"] > 0:
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{channel}/{date_str}/continuous",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=f"Continuous Footage ({counts['continuous']} clips)",
                    can_play=False,
                    can_expand=True,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{channel}/{date_str}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{ch_name} on {date_str} ({counts['all']} clips)",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _build_clips(
        self, coordinator: Any, entry_id: str, channel: int, date_str: str, category: str = "all"
    ) -> BrowseMediaSource:
        """Query NVR for clips on selected date and build media list filtered by category."""
        start_time = f"{date_str} 00:00:00"
        end_time = f"{date_str} 23:59:59"

        recordings = await coordinator.client.async_find_recordings(
            channel=channel, start_time=start_time, end_time=end_time, count=100
        )

        ch = next((c for c in coordinator.channels if c.get("channel") == channel), {})
        ch_name = ch.get("name") or f"Channel {channel}"

        # Filter by category
        if category == "human":
            recordings = [r for r in recordings if r.get("event_type") == "Human"]
        elif category == "vehicle":
            recordings = [r for r in recordings if r.get("event_type") == "Vehicle"]
        elif category == "motion":
            recordings = [r for r in recordings if r.get("event_type") == "Motion"]
        elif category == "continuous":
            recordings = [r for r in recordings if r.get("event_type") == "Continuous"]

        children = []
        for rec in recordings:
            path = rec.get("path", "")
            rec_start = rec.get("start_time", "").split(" ")[-1]
            rec_end = rec.get("end_time", "").split(" ")[-1]
            event_type = rec.get("event_type", "Continuous")
            size_mb = rec.get("size_mb", 0)
            duration_sec = rec.get("duration", 0)
            mins = duration_sec // 60
            secs = duration_sec % 60

            start_full = rec.get("start_time", "")
            end_full = rec.get("end_time", "")
            path_encoded = urllib.parse.quote(path, safe="")
            start_encoded = urllib.parse.quote(start_full, safe="")
            end_encoded = urllib.parse.quote(end_full, safe="")
            clip_id = f"{entry_id}/{channel}/{date_str}/{category}/{start_encoded}/{end_encoded}/{path_encoded}"

            title = f"{rec_start} - {rec_end} [{event_type}] ({mins}m {secs:02d}s, {size_mb} MB)"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=clip_id,
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=True,
                    can_expand=False,
                )
            )

        cat_label = {
            "all": "All",
            "human": "Human Detection",
            "vehicle": "Vehicle Detection",
            "motion": "Motion Events",
            "continuous": "Continuous",
        }.get(category, category.title())

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{channel}/{date_str}/{category}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=f"{ch_name} {cat_label} on {date_str} ({len(recordings)} clips)",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media clip to a playable stream URL."""
        parts = item.identifier.split("/")
        if len(parts) < 4:
            raise Unresolvable(f"Invalid clip identifier: {item.identifier}")

        entry_id = parts[0]
        channel = int(parts[1])

        # Supported identifier formats:
        # 1) entry_id/channel/date/category/start/end/path (7 parts)
        # 2) entry_id/channel/date/start/end/path          (6 parts)
        # 3) entry_id/channel/date/path                    (4 parts)
        if len(parts) >= 7:
            start_time = urllib.parse.unquote(parts[4])
            end_time = urllib.parse.unquote(parts[5])
            file_path = urllib.parse.unquote(parts[6])
            stream_url = (
                f"/api/cpplus/playback/{entry_id}/{channel}?"
                f"start={urllib.parse.quote(start_time)}&end={urllib.parse.quote(end_time)}&file={urllib.parse.quote(file_path)}"
            )
        elif len(parts) == 6:
            start_time = urllib.parse.unquote(parts[3])
            end_time = urllib.parse.unquote(parts[4])
            file_path = urllib.parse.unquote(parts[5])
            stream_url = (
                f"/api/cpplus/playback/{entry_id}/{channel}?"
                f"start={urllib.parse.quote(start_time)}&end={urllib.parse.quote(end_time)}&file={urllib.parse.quote(file_path)}"
            )
        else:
            path_encoded = parts[3]
            file_path = urllib.parse.unquote(path_encoded)
            stream_url = f"/api/cpplus/playback/{entry_id}/{channel}?file={urllib.parse.quote(file_path)}"

        return PlayMedia(stream_url, "video/mp4")
