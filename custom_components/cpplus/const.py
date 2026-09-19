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
]
