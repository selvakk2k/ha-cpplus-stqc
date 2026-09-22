"""Constants for the CP PLUS integration."""

from homeassistant.const import Platform

DOMAIN = "cpplus"
MANUFACTURER = "CP PLUS"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_RTSP_PORT = "rtsp_port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_NAME = "name"
CONF_USE_SSL = "use_ssl"

CONF_DEVICE_TYPE = "device_type"
CONF_CHANNELS = "channels"

DEFAULT_PORT_HTTPS = 443
DEFAULT_PORT_RTSP = 554

TYPE_CAMERA = "camera"
TYPE_NVR = "nvr"

EVENT_HUMAN = "human"
EVENT_VEHICLE = "vehicle"
EVENT_TRIPWIRE = "tripwire"
EVENT_MOTION = "motion"

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
]

DAY_NIGHT_MODES: dict[int, str] = {
    0: "Color",
    1: "Auto",
    2: "Black & White",
}
DAY_NIGHT_NAME_TO_INT: dict[str, int] = {v: k for k, v in DAY_NIGHT_MODES.items()}

LIGHTING_MODES: list[str] = [
    "Auto",
    "Manual",
    "Off",
]

PTZ_COMMANDS: dict[str, str] = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "zoom_in": "ZoomIn",
    "zoom_out": "ZoomOut",
    "focus_near": "FocusNear",
    "focus_far": "FocusFar",
}

SUBENTRY_TYPE_HUB = "hub"
SUBENTRY_TYPE_CHANNEL = "channel"

CONF_STREAM_PROFILE = "stream_profile"
CONF_RTSP_OVER_TLS = "rtsp_over_tls"

STREAM_PROFILE_DAHUA_CH1 = "dahua_ch1"
STREAM_PROFILE_DAHUA_CH0 = "dahua_ch0"
STREAM_PROFILE_VIDEO_LIVE = "video_live"
STREAM_PROFILE_ONVIF = "onvif"
STREAM_PROFILE_LIVE = "live"


