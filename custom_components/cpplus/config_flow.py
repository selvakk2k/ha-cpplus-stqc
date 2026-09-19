"""Config flow for CP PLUS integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .client import CPPlusClient
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_RTSP_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_DEVICE_TYPE,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    TYPE_NVR,
    TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)


class CPPlusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CP PLUS cameras and NVRs."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT_HTTPS)
            rtsp_port = user_input.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP)
            username = user_input[CONF_USERNAME].strip()
            password = user_input.get(CONF_PASSWORD, "")
            name = user_input.get(CONF_NAME, "").strip() or host

            client = CPPlusClient(
                hass=self.hass,
                host=host,
                port=port,
                rtsp_port=rtsp_port,
                username=username,
                password=password,
            )

            try:
                device_info = await client.async_get_device_info()
                serial = device_info.get("serial", host)
                model = device_info.get("hardware", "STQC")
                device_type = device_info.get("device_type", TYPE_CAMERA)

                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                await client.async_close()

                if device_type == TYPE_NVR:
                    title = f"CP PLUS STQC NVR ({model})"
                else:
                    title = f"CP PLUS STQC {name} ({model})"

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_RTSP_PORT: rtsp_port,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_NAME: name,
                        CONF_DEVICE_TYPE: device_type,
                    },
                )
            except ConnectionError as err:
                _LOGGER.warning("Connection error configuring CP PLUS %s: %s", host, err)
                errors["base"] = "invalid_auth" if "Authentication failed" in str(err) else "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected exception in CP PLUS config flow: %s", err)
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME, default="admin"): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(CONF_NAME, default=""): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT_HTTPS): int,
                vol.Optional(CONF_RTSP_PORT, default=DEFAULT_PORT_RTSP): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
