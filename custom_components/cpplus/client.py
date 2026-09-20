"""Client communication interface for CP PLUS STQC cameras and NVRs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
import urllib.parse
from datetime import datetime
from typing import Any, Callable
import aiohttp

from .const import TYPE_CAMERA, TYPE_NVR

_LOGGER = logging.getLogger(__name__)


class AsyncDigestAuth:
    """Helper to perform HTTP Digest Authentication with aiohttp."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.realm: str | None = None
        self.nonce: str | None = None
        self.qop: str | None = None
        self.opaque: str | None = None
        self.nc = 0

    def parse_challenge(self, header: str) -> None:
        realm_m = re.search(r'realm="([^"]+)"', header)
        if realm_m:
            self.realm = realm_m.group(1)
        nonce_m = re.search(r'nonce="([^"]+)"', header)
        if nonce_m:
            new_nonce = nonce_m.group(1)
            if new_nonce != self.nonce:
                self.nonce = new_nonce
                self.nc = 0
        qop_m = re.search(r'qop="?([^",\s]+)"?', header)
        self.qop = qop_m.group(1) if qop_m else None
        opaque_m = re.search(r'opaque="([^"]*)"', header)
        self.opaque = opaque_m.group(1) if opaque_m else None

    def build_header(self, method: str, uri: str) -> str:
        self.nc += 1
        nc_str = f"{self.nc:08x}"
        cnonce = os.urandom(8).hex()
        ha1 = hashlib.md5(f"{self.username}:{self.realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        if self.qop:
            resp = hashlib.md5(f"{ha1}:{self.nonce}:{nc_str}:{cnonce}:{self.qop}:{ha2}".encode()).hexdigest()
            header = (
                f'Digest username="{self.username}", realm="{self.realm}", '
                f'nonce="{self.nonce}", uri="{uri}", response="{resp}", '
                f'qop={self.qop}, nc={nc_str}, cnonce="{cnonce}"'
            )
        else:
            resp = hashlib.md5(f"{ha1}:{self.nonce}:{ha2}".encode()).hexdigest()
            header = (
                f'Digest username="{self.username}", realm="{self.realm}", '
                f'nonce="{self.nonce}", uri="{uri}", response="{resp}"'
            )
        if self.opaque:
            header += f', opaque="{self.opaque}"'
        return header


class CPPlusClient:
    """Async client for CP PLUS STQC cameras and NVRs."""

    def __init__(
        self,
        hass=None,
        host: str = "",
        port: int = 443,
        rtsp_port: int = 554,
        username: str = "admin",
        password: str = "",
        device_type: str = TYPE_CAMERA,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the CP PLUS STQC client."""
        self.hass = hass
        self.host = host
        self.port = port
        self.rtsp_port = rtsp_port
        self.username = username
        self.password = password
        self.device_type = device_type
        self._external_session = session is not None
        self._session = session
        self._session_id: str | None = None
        self._logged_in = False
        self._keep_alive_interval = 30
        self._req_id = 1
        self._lock = asyncio.Lock()
        self._device_info: dict[str, Any] = {}
        self._digest_auth: AsyncDigestAuth | None = None
        self._event_auth: AsyncDigestAuth | None = None
        self._event_session: aiohttp.ClientSession | None = None
        self._channels: list[dict[str, Any]] = []
        self._stopped = False

    def _get_ssl_context(self) -> ssl.SSLContext:
        """Return SSL context that accepts self-signed camera certificates."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._get_ssl_context())
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    def _next_id(self) -> int:
        """Return the next RPC request ID."""
        self._req_id += 1
        return self._req_id

    async def async_login(self) -> bool:
        """Perform the challenge-response login over /cpapi2_Login."""
        async with self._lock:
            session = await self._get_session()
            login_url = f"https://{self.host}:{self.port}/cpapi2_Login"

            # Step 1: Request authentication challenge (firstLogin)
            req1 = {
                "method": "user.signin",
                "params": {
                    "userName": self.username,
                    "password": "",
                    "clientType": "Web3.0",
                },
                "id": self._next_id(),
            }
            body1 = json.dumps(req1)
            etag1 = hashlib.sha256(body1.encode()).hexdigest()
            headers1 = {
                "Content-Type": "application/json",
                "ETag": etag1,
                "User-Agent": "Mozilla/5.0",
            }

            try:
                async with session.post(login_url, data=body1, headers=headers1) as resp:
                    if resp.status != 200:
                        raise ConnectionError(f"HTTP error {resp.status} during firstLogin")
                    data1 = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Connection error contacting CP PLUS camera %s: %s", self.host, err)
                raise ConnectionError(f"Cannot connect to {self.host}:{self.port}") from err

            params = data1.get("params", {})
            realm = params.get("realm", "")
            random_val = params.get("random", "")
            challenge_session = data1.get("session", "")
            encryption = params.get("encryption", "Default")

            if not realm or not random_val:
                raise ConnectionError(f"Unexpected challenge response from {self.host}: {data1}")

            # Step 2: Compute uppercase double-MD5 response matching camera specification:
            # inner = MD5(user + ":" + realm + ":" + password).upper()
            # outer = MD5(user + ":" + random + ":" + inner).upper()
            inner_md5 = hashlib.md5(
                f"{self.username}:{realm}:{self.password}".encode()
            ).hexdigest().upper()
            auth_hash = hashlib.md5(
                f"{self.username}:{random_val}:{inner_md5}".encode()
            ).hexdigest().upper()

            # Step 3: Send authenticated credentials (secondLogin)
            req2 = {
                "method": "user.signin",
                "params": {
                    "userName": self.username,
                    "password": auth_hash,
                    "clientType": "Web3.0",
                    "authorityType": encryption,
                    "loginType": "Direct",
                },
                "id": self._next_id(),
                "session": challenge_session,
            }
            body2 = json.dumps(req2)
            etag2 = hashlib.sha256(body2.encode()).hexdigest()
            headers2 = {
                "Content-Type": "application/json",
                "ETag": etag2,
                "User-Agent": "Mozilla/5.0",
            }

            try:
                async with session.post(login_url, data=body2, headers=headers2) as resp:
                    if resp.status != 200:
                        raise ConnectionError(f"HTTP error {resp.status} during secondLogin")
                    data2 = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise ConnectionError(f"Failed to post secondLogin to {self.host}") from err

            if not data2.get("result"):
                error_msg = data2.get("error", {}).get("message", "Invalid credentials")
                _LOGGER.warning("Authentication failed for %s on %s: %s", self.username, self.host, error_msg)
                raise ConnectionError(f"Authentication failed: {error_msg}")

            self._session_id = data2.get("session") or challenge_session
            self._logged_in = True
            self._keep_alive_interval = data2.get("params", {}).get("keepAliveInterval", 30)
            _LOGGER.info("Successfully authenticated with CP PLUS STQC camera at %s", self.host)
            return True

    async def async_call_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch a JSON-RPC command to /cpapi2."""
        if not self._logged_in or not self._session_id:
            await self.async_login()

        session = await self._get_session()
        rpc_url = f"https://{self.host}:{self.port}/cpapi2"

        req = {
            "method": method,
            "params": params,
            "id": self._next_id(),
            "session": self._session_id,
        }
        body = json.dumps(req)
        etag = hashlib.sha256(body.encode()).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "ETag": etag,
            "User-Agent": "Mozilla/5.0",
        }

        try:
            async with session.post(rpc_url, data=body, headers=headers) as resp:
                if resp.status != 200:
                    raise ConnectionError(f"RPC HTTP error {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ConnectionError(f"RPC call {method} to {self.host} failed") from err

        # If session expired or interface error, attempt re-login once
        if not data.get("result") and data.get("error", {}).get("code") in [268632064, 268632065, 268959743]:
            _LOGGER.debug("Session may have expired on %s, attempting re-authentication", self.host)
            self._logged_in = False
            await self.async_login()
            req["session"] = self._session_id
            body = json.dumps(req)
            headers["ETag"] = hashlib.sha256(body.encode()).hexdigest()
            async with session.post(rpc_url, data=body, headers=headers) as retry_resp:
                return await retry_resp.json(content_type=None)

        return data

    async def async_detect_device_type(self) -> str:
        """Detect whether the target host is an NVR or a standalone camera."""
        session = await self._get_session()
        nvr_url = f"https://{self.host}:{self.port}/cgi-bin/magicBox.cgi?action=getDeviceType"
        try:
            async with session.get(nvr_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 401 and "Digest" in resp.headers.get("WWW-Authenticate", ""):
                    self.device_type = TYPE_NVR
                    self._digest_auth = AsyncDigestAuth(self.username, self.password)
                    self._digest_auth.parse_challenge(resp.headers["WWW-Authenticate"])
                    _LOGGER.info("Detected CP PLUS STQC NVR at %s", self.host)
                    return TYPE_NVR
        except Exception as err:
            _LOGGER.debug("NVR probe error on %s: %s", self.host, err)

        self.device_type = TYPE_CAMERA
        _LOGGER.info("Detected CP PLUS STQC standalone camera at %s", self.host)
        return TYPE_CAMERA

    async def async_nvr_request(self, uri: str, method: str = "GET", data: Any = None) -> str:
        """Execute an authenticated HTTP Digest request against NVR CGI."""
        if not self._digest_auth:
            self._digest_auth = AsyncDigestAuth(self.username, self.password)
        session = await self._get_session()
        url = f"https://{self.host}:{self.port}{uri}"

        headers = {"User-Agent": "Mozilla/5.0"}
        if self._digest_auth.realm and self._digest_auth.nonce:
            headers["Authorization"] = self._digest_auth.build_header(method, uri)

        async with session.request(method, url, headers=headers, data=data) as resp:
            if resp.status == 401:
                auth_hdr = resp.headers.get("WWW-Authenticate", "")
                if "Digest" in auth_hdr:
                    self._digest_auth.parse_challenge(auth_hdr)
                    headers["Authorization"] = self._digest_auth.build_header(method, uri)
                    async with session.request(method, url, headers=headers, data=data) as retry_resp:
                        if retry_resp.status == 401:
                            raise ConnectionError("Authentication failed on NVR")
                        return await retry_resp.text()
            elif resp.status == 200:
                return await resp.text()
            raise ConnectionError(f"NVR HTTP error {resp.status} on {uri}")

    async def async_nvr_request_bytes(self, uri: str, method: str = "GET", data: Any = None) -> bytes:
        """Execute an authenticated HTTP Digest request against NVR CGI returning binary bytes."""
        if not self._digest_auth:
            self._digest_auth = AsyncDigestAuth(self.username, self.password)
        session = await self._get_session()
        url = f"https://{self.host}:{self.port}{uri}"

        headers = {"User-Agent": "Mozilla/5.0"}
        if self._digest_auth.realm and self._digest_auth.nonce:
            headers["Authorization"] = self._digest_auth.build_header(method, uri)

        async with session.request(method, url, headers=headers, data=data) as resp:
            if resp.status == 401:
                auth_hdr = resp.headers.get("WWW-Authenticate", "")
                if "Digest" in auth_hdr:
                    self._digest_auth.parse_challenge(auth_hdr)
                    headers["Authorization"] = self._digest_auth.build_header(method, uri)
                    async with session.request(method, url, headers=headers, data=data) as retry_resp:
                        if retry_resp.status == 401:
                            raise ConnectionError("Authentication failed on NVR")
                        return await retry_resp.read()
            elif resp.status == 200:
                return await resp.read()
            raise ConnectionError(f"NVR HTTP error {resp.status} on {uri}")

    async def async_get_channels(self) -> list[dict[str, Any]]:
        """Query NVR for all channels, camera names, and AI detection capabilities."""
        if self.device_type != TYPE_NVR:
            return []

        title_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle")
        title_map: dict[int, str] = {}
        for line in title_res.splitlines():
            m = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name=(.*)", line.strip())
            if m:
                title_map[int(m.group(1))] = m.group(2).strip()

        smd_map: dict[int, dict[str, bool]] = {}
        try:
            smd_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect")
            for line in smd_res.splitlines():
                m_en = re.match(r"table\.SmartMotionDetect\[(\d+)\]\.Enable=(true|false)", line.strip(), re.I)
                if m_en:
                    idx = int(m_en.group(1))
                    smd_map.setdefault(idx, {})["enable"] = m_en.group(2).lower() == "true"
                m_hum = re.match(r"table\.SmartMotionDetect\[(\d+)\]\.ObjectTypes\.Human=(true|false)", line.strip(), re.I)
                if m_hum:
                    idx = int(m_hum.group(1))
                    smd_map.setdefault(idx, {})["human"] = m_hum.group(2).lower() == "true"
                m_veh = re.match(r"table\.SmartMotionDetect\[(\d+)\]\.ObjectTypes\.Vehicle=(true|false)", line.strip(), re.I)
                if m_veh:
                    idx = int(m_veh.group(1))
                    smd_map.setdefault(idx, {})["vehicle"] = m_veh.group(2).lower() == "true"
        except Exception as err:
            _LOGGER.debug("Could not query SmartMotionDetect: %s", err)

        tripwire_map: dict[int, bool] = {}
        try:
            trip_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=CrossLineDetection")
            for line in trip_res.splitlines():
                m_trip = re.match(r"table\.CrossLineDetection\[(\d+)\]\.Enable=(true|false)", line.strip(), re.I)
                if m_trip:
                    tripwire_map[int(m_trip.group(1))] = m_trip.group(2).lower() == "true"
        except Exception as err:
            _LOGGER.debug("Could not query CrossLineDetection: %s", err)

        remote_devices: dict[int, dict[str, str]] = {}
        try:
            rd_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice")
            for line in rd_res.splitlines():
                m_rd = re.match(r"table\.RemoteDevice(?:\.uuid:System_CONFIG_NETCAMERA_INFO_)?(?:\[)?(\d+)(?:\])?\.(.*?)=(.*)", line.strip())
                if m_rd:
                    idx = int(m_rd.group(1))
                    prop = m_rd.group(2)
                    val = m_rd.group(3).strip()
                    remote_devices.setdefault(idx, {})[prop] = val
        except Exception as err:
            _LOGGER.debug("Could not query RemoteDevice: %s", err)

        video_mode_map: dict[int, int] = {}
        try:
            vim_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=VideoInMode")
            for line in vim_res.splitlines():
                m_vim = re.match(r"table\.VideoInMode\[(\d+)\]\.Mode=(\d+)", line.strip())
                if m_vim:
                    video_mode_map[int(m_vim.group(1))] = int(m_vim.group(2))
        except Exception as err:
            _LOGGER.debug("Could not query VideoInMode: %s", err)

        lighting_map: dict[int, str] = {}
        try:
            light_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=Lighting")
            for line in light_res.splitlines():
                m_light = re.match(r"table\.Lighting\[(\d+)\]\[0\]\.Mode=([a-zA-Z0-9]+)", line.strip())
                if m_light:
                    lighting_map[int(m_light.group(1))] = m_light.group(2)
        except Exception as err:
            _LOGGER.debug("Could not query Lighting: %s", err)

        audio_map: dict[int, bool] = {}
        try:
            enc_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")
            for line in enc_res.splitlines():
                m_aud = re.match(
                    r"table\.Encode\[(\d+)\]\.MainFormat\[0\]\.(?:Audio\.Enable|AudioEnable)=(true|false)",
                    line.strip(),
                    re.I,
                )
                if m_aud:
                    audio_map[int(m_aud.group(1))] = m_aud.group(2).lower() == "true"
        except Exception as err:
            _LOGGER.debug("Could not query Encode audio: %s", err)

        channels: list[dict[str, Any]] = []
        for idx, name in sorted(title_map.items()):
            if not name or (name.startswith("Channel") and idx >= 17):
                continue
            smd_info = smd_map.get(idx, {})
            rd_info = remote_devices.get(idx, {})

            raw_model = rd_info.get("DeviceType")
            addr = rd_info.get("Address")
            serial_no = rd_info.get("SerialNo")
            firmware_ver = rd_info.get("Version")
            http_port = rd_info.get("HttpPort")
            https_port = rd_info.get("HttpsPort")
            vendor = rd_info.get("Vendor")

            # Determine human-friendly and accurate model string & manufacturer
            if raw_model and raw_model != "Default":
                model = raw_model
            elif addr and addr.startswith("192.168.1."):
                last_octet = int(addr.split(".")[-1])
                if 217 <= last_octet <= 221:
                    model = "CP-UNC-DA21L3C-Q"
                elif 212 <= last_octet <= 224:
                    model = "CP-UNC-TA21L3C-Q"
                else:
                    model = "Camera"
            else:
                model = "Camera"

            # Brand and Native CP PLUS classification
            if model.startswith("CP-"):
                manufacturer = "CP PLUS"
                is_native_cpplus = True
            elif model.startswith("VTO"):
                manufacturer = "Dahua"
                is_native_cpplus = False
            elif model.startswith("IPC_GK"):
                manufacturer = "Xiongmai"
                is_native_cpplus = False
            elif vendor:
                manufacturer = vendor
                is_native_cpplus = False
            else:
                manufacturer = "Generic ONVIF"
                is_native_cpplus = False

            channels.append({
                "index": idx,
                "channel": idx + 1,
                "name": name,
                "model": model,
                "manufacturer": manufacturer,
                "is_native_cpplus": is_native_cpplus,
                "serial": serial_no,
                "firmware": firmware_ver,
                "address": addr,
                "http_port": http_port,
                "https_port": https_port,
                "vendor": vendor,
                "smd_human": smd_info.get("human", False) and smd_info.get("enable", False),
                "smd_vehicle": smd_info.get("vehicle", False) and smd_info.get("enable", False),
                "tripwire": tripwire_map.get(idx, False),
                "video_in_mode": video_mode_map.get(idx, 0),
                "lighting_mode": lighting_map.get(idx, "Auto"),
                "audio_enable": audio_map.get(idx, True),
            })

        self._channels = channels
        return channels

    async def async_start_event_listener(self, callback: Callable[[int, str, str], None]) -> None:
        """Connect to eventManager.cgi and dispatch real-time events to callback."""
        if self.device_type != TYPE_NVR:
            return

        if self._event_auth is None:
            self._event_auth = AsyncDigestAuth(self.username, self.password)

        if self._event_session is None or self._event_session.closed:
            self._event_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=self._get_ssl_context()),
                timeout=aiohttp.ClientTimeout(total=None, sock_read=90),
            )
        session = self._event_session
        uri = "/cgi-bin/eventManager.cgi?action=attach&codes=[All]"
        url = f"https://{self.host}:{self.port}{uri}"

        while not self._stopped:
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                if self._event_auth.realm and self._event_auth.nonce:
                    headers["Authorization"] = self._event_auth.build_header("GET", uri)

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 401:
                        auth_hdr = resp.headers.get("WWW-Authenticate", "")
                        if "Digest" in auth_hdr:
                            self._event_auth.parse_challenge(auth_hdr)
                            continue

                    if resp.status != 200:
                        _LOGGER.warning("NVR event stream HTTP %s, reconnecting...", resp.status)
                        await asyncio.sleep(5)
                        continue

                    buffer = ""
                    async for chunk in resp.content.iter_chunked(1024):
                        if self._stopped:
                            break
                        buffer += chunk.decode(errors="ignore")
                        while "\n\n" in buffer or "\r\n\r\n" in buffer:
                            parts = re.split(r"\r?\n\r?\n", buffer, maxsplit=1)
                            event_block = parts[0]
                            buffer = parts[1] if len(parts) > 1 else ""

                            code_m = re.search(r"Code=([a-zA-Z0-9]+)", event_block)
                            action_m = re.search(r"action=([a-zA-Z0-9]+)", event_block)
                            index_m = re.search(r"index=(\d+)", event_block)

                            if code_m and action_m and index_m:
                                code = code_m.group(1)
                                action = action_m.group(1)
                                ch_idx = int(index_m.group(1))
                                callback(ch_idx, code, action)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("NVR event stream reconnecting (%s)", err)
                await asyncio.sleep(3)
            except Exception as err:
                _LOGGER.error("Unexpected error in NVR event listener: %s", err)
                await asyncio.sleep(5)

    async def async_get_device_info(self) -> dict[str, Any]:
        """Fetch device model, machine name, and serial number."""
        if not self.device_type or self.device_type == TYPE_CAMERA:
            await self.async_detect_device_type()

        if self.device_type == TYPE_NVR:
            model = "CP PLUS STQC NVR"
            serial = self.host.replace(".", "_")
            firmware = "Unknown"

            try:
                dev_res = await self.async_nvr_request("/cgi-bin/magicBox.cgi?action=getDeviceType")
                for line in dev_res.splitlines():
                    if line.startswith("type="):
                        model = line.split("=", 1)[1].strip()
            except Exception as err:
                _LOGGER.debug("NVR getDeviceType error on %s: %s", self.host, err)

            try:
                sys_res = await self.async_nvr_request("/cgi-bin/magicBox.cgi?action=getSystemInfo")
                for line in sys_res.splitlines():
                    if line.startswith("serialNumber="):
                        serial = line.split("=", 1)[1].strip()
                    elif line.startswith("appVersion="):
                        firmware = line.split("=", 1)[1].strip()
            except Exception as err:
                _LOGGER.debug("NVR getSystemInfo error on %s: %s", self.host, err)

            if firmware == "Unknown":
                try:
                    ver_res = await self.async_nvr_request("/cgi-bin/magicBox.cgi?action=getSoftwareVersion")
                    for line in ver_res.splitlines():
                        if line.startswith("version="):
                            firmware = line.split("=", 1)[1].strip()
                except Exception as err:
                    _LOGGER.debug("NVR getSoftwareVersion error on %s: %s", self.host, err)

            self._device_info = {
                "serial": serial,
                "hardware": model,
                "firmware": firmware,
                "device_type": TYPE_NVR,
            }
            return self._device_info

        # Standalone Camera flow
        if not self._logged_in:
            await self.async_login()

        model = "CP PLUS STQC IPC"
        serial = self.host.replace(".", "_")
        firmware = "Unknown"

        try:
            res = await self.async_call_rpc("magicBox.getDeviceType")
            if res.get("result") and "type" in res.get("params", {}):
                model = res["params"]["type"]
        except Exception as err:
            _LOGGER.debug("Could not get device type on %s: %s", self.host, err)

        try:
            res = await self.async_call_rpc("configManager.getConfig", {"name": "General"})
            if res.get("result"):
                table = res.get("params", {}).get("table", {})
                machine_name = table.get("MachineName")
                if machine_name:
                    serial = machine_name
        except Exception as err:
            _LOGGER.debug("Could not get General config on %s: %s", self.host, err)

        try:
            res = await self.async_call_rpc("magicBox.getSerialNo")
            if res.get("result") and "serial" in res.get("params", {}):
                serial = res["params"]["serial"]
        except Exception:
            pass

        self._device_info = {
            "serial": serial,
            "hardware": model,
            "firmware": firmware,
            "device_type": TYPE_CAMERA,
        }
        return self._device_info

    async def async_reboot(self) -> bool:
        """Send reboot command to CP PLUS camera or NVR."""
        if self.device_type == TYPE_NVR:
            try:
                res = await self.async_nvr_request("/cgi-bin/magicBox.cgi?action=reboot")
                return "ok" in res.lower() or "success" in res.lower()
            except Exception as err:
                _LOGGER.error("Failed to reboot NVR at %s: %s", self.host, err)
                return False

        try:
            res = await self.async_call_rpc("magicBox.reboot")
            return bool(res.get("result"))
        except Exception as err:
            _LOGGER.error("Failed to reboot CP PLUS camera at %s: %s", self.host, err)
            return False

    def get_stream_url(self, channel: int = 1, subtype: int = 0) -> str:
        """Return the RTSP stream URL for the requested channel and stream type."""
        # Subtype 0 = Main Stream (HD), 1 = Sub Stream (SD)
        username = urllib.parse.quote(self.username, safe="")
        password = urllib.parse.quote(self.password, safe="")
        return (
            f"rtsp://{username}:{password}@{self.host}:{self.rtsp_port}"
            f"/cam/realmonitor?channel={channel}&subtype={subtype}"
        )

    async def async_get_snapshot(self, channel: int = 1) -> bytes | None:
        """Fetch a snapshot JPEG image from camera or NVR."""
        if self.device_type == TYPE_NVR:
            try:
                return await self.async_nvr_request_bytes(f"/cgi-bin/snapshot.cgi?channel={channel}")
            except Exception as err:
                _LOGGER.debug("HTTP snapshot failed for NVR channel %d at %s: %s", channel, self.host, err)
                return None

        session = await self._get_session()
        snapshot_url = f"https://{self.host}:{self.port}/cgi-bin/snapshot.cgi?channel={channel}"
        try:
            auth = aiohttp.BasicAuth(self.username, self.password)
            async with session.get(snapshot_url, auth=auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception as err:
            _LOGGER.debug("HTTP snapshot failed for %s: %s", self.host, err)
        return None

    async def async_set_video_in_mode(self, channel_idx: int, mode: int) -> bool:
        """Set VideoInMode (Day/Night) for a channel: 0=Color, 1=Auto, 2=Black & White."""
        if self.device_type != TYPE_NVR:
            return False
        uri = f"/cgi-bin/configManager.cgi?action=setConfig&VideoInMode[{channel_idx}].Mode={mode}"
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("Failed to set VideoInMode on channel %d: %s", channel_idx, err)
            return False

    async def async_set_lighting_mode(self, channel_idx: int, mode: str) -> bool:
        """Set Lighting mode for a channel: Auto, Manual, Off."""
        if self.device_type != TYPE_NVR:
            return False
        uri = f"/cgi-bin/configManager.cgi?action=setConfig&Lighting[{channel_idx}][0].Mode={mode}"
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("Failed to set Lighting mode on channel %d: %s", channel_idx, err)
            return False

    async def async_set_smd_human(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable SmartMotionDetect Human recognition on a channel."""
        if self.device_type != TYPE_NVR:
            return False
        en_str = "true" if enable else "false"
        uri = (
            f"/cgi-bin/configManager.cgi?action=setConfig"
            f"&SmartMotionDetect[{channel_idx}].Enable=true"
            f"&SmartMotionDetect[{channel_idx}].ObjectTypes.Human={en_str}"
        )
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("Failed to set SMD Human on channel %d: %s", channel_idx, err)
            return False

    async def async_set_smd_vehicle(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable SmartMotionDetect Vehicle recognition on a channel."""
        if self.device_type != TYPE_NVR:
            return False
        en_str = "true" if enable else "false"
        uri = (
            f"/cgi-bin/configManager.cgi?action=setConfig"
            f"&SmartMotionDetect[{channel_idx}].Enable=true"
            f"&SmartMotionDetect[{channel_idx}].ObjectTypes.Vehicle={en_str}"
        )
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("Failed to set SMD Vehicle on channel %d: %s", channel_idx, err)
            return False

    async def async_set_tripwire(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable CrossLineDetection (Tripwire) on a channel."""
        if self.device_type != TYPE_NVR:
            return False
        en_str = "true" if enable else "false"
        uri = f"/cgi-bin/configManager.cgi?action=setConfig&CrossLineDetection[{channel_idx}].Enable={en_str}"
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("Failed to set CrossLineDetection on channel %d: %s", channel_idx, err)
            return False

    async def async_set_audio_enable(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable audio transmission on channel RTSP stream."""
        if self.device_type != TYPE_NVR:
            return False
        val = "true" if enable else "false"
        uri = (
            f"/cgi-bin/configManager.cgi?action=setConfig"
            f"&table.Encode[{channel_idx}].MainFormat[0].AudioEnable={val}"
            f"&table.Encode[{channel_idx}].ExtraFormat[0].AudioEnable={val}"
        )
        try:
            res = await self.async_nvr_request(uri)
            if "ok" in res.lower():
                return True
            # Fallback without 'table.' prefix if NVR firmware prefers Encode[x]
            fallback_uri = (
                f"/cgi-bin/configManager.cgi?action=setConfig"
                f"&Encode[{channel_idx}].MainFormat[0].AudioEnable={val}"
                f"&Encode[{channel_idx}].ExtraFormat[0].AudioEnable={val}"
            )
            res2 = await self.async_nvr_request(fallback_uri)
            return "ok" in res2.lower()
        except Exception as err:
            _LOGGER.error("Failed to set audio enable on channel %d: %s", channel_idx, err)
            return False

    async def async_ptz_control(
        self,
        channel: int,
        code: str,
        arg1: int = 0,
        arg2: int = 5,
        arg3: int = 0,
        stop: bool = False,
    ) -> bool:
        """Send PTZ movement or zoom command to camera channel."""
        if self.device_type != TYPE_NVR:
            return False
        action = "stop" if stop else "start"
        uri = (
            f"/cgi-bin/ptz.cgi?action={action}"
            f"&channel={channel}&code={code}&arg1={arg1}&arg2={arg2}&arg3={arg3}"
        )
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("PTZ control failed on channel %d (%s): %s", channel, code, err)
            return False

    async def async_ptz_preset(self, channel: int, preset: int) -> bool:
        """Command PTZ to move to configured preset number."""
        if self.device_type != TYPE_NVR:
            return False
        uri = f"/cgi-bin/ptz.cgi?action=start&channel={channel}&code=GotoPreset&arg1=0&arg2={preset}&arg3=0"
        try:
            res = await self.async_nvr_request(uri)
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.error("PTZ preset %d failed on channel %d: %s", preset, channel, err)
            return False

    async def async_close(self) -> None:
        """Close background connections and session."""
        self._stopped = True
        if self._event_session and not self._event_session.closed:
            await self._event_session.close()
        if not self._external_session and self._session and not self._session.closed:
            await self._session.close()

    def get_playback_url(self, channel: int, start_time: str, end_time: str) -> str:
        """Return the RTSP playback URL for the requested channel and time range.

        Timestamps format: 'YYYY_MM_DD_HH_MM_SS' (e.g. 2026_09_20_14_00_00) or 'YYYY-MM-DD HH:MM:SS'.
        """
        username = urllib.parse.quote(self.username, safe="")
        password = urllib.parse.quote(self.password, safe="")
        start_fmt = start_time.replace("-", "_").replace(" ", "_").replace(":", "_")
        end_fmt = end_time.replace("-", "_").replace(" ", "_").replace(":", "_")
        return (
            f"rtsp://{username}:{password}@{self.host}:{self.rtsp_port}"
            f"/cam/playback?channel={channel}&starttime={start_fmt}&endtime={end_fmt}"
        )

    async def async_create_find_session(self) -> str | None:
        """Create a file find session on the NVR."""
        if self.device_type != TYPE_NVR:
            return None
        try:
            res = await self.async_nvr_request("/cgi-bin/mediaFileFind.cgi?action=factory.create")
            for line in res.splitlines():
                if line.startswith("result="):
                    return line.split("=", 1)[1].strip()
        except Exception as err:
            _LOGGER.error("Failed to create mediaFileFind session on %s: %s", self.host, err)
        return None

    async def async_close_find_session(self, session_id: str) -> bool:
        """Destroy a file find session on the NVR."""
        if self.device_type != TYPE_NVR:
            return False
        try:
            res = await self.async_nvr_request(f"/cgi-bin/mediaFileFind.cgi?action=destroy&object={session_id}")
            return "ok" in res.lower()
        except Exception as err:
            _LOGGER.debug("Failed to close mediaFileFind session %s: %s", session_id, err)
            return False

    async def async_find_recordings(
        self,
        channel: int,
        start_time: str,
        end_time: str,
        count: int = 50,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search NVR for recorded clips within a time range (YYYY-MM-DD HH:MM:SS)."""
        if self.device_type != TYPE_NVR:
            return []

        session_id = await self.async_create_find_session()
        if not session_id:
            return []

        try:
            start_encoded = urllib.parse.quote(start_time)
            end_encoded = urllib.parse.quote(end_time)
            find_uri = (
                f"/cgi-bin/mediaFileFind.cgi?action=findFile&object={session_id}"
                f"&condition.Channel={channel}&condition.StartTime={start_encoded}"
                f"&condition.EndTime={end_encoded}&condition.Types[0]=dav"
            )
            if event_types:
                for idx, et in enumerate(event_types):
                    find_uri += f"&condition.Events[{idx}]={et}"

            find_res = await self.async_nvr_request(find_uri)
            if "ok" not in find_res.lower() and "true" not in find_res.lower():
                _LOGGER.warning("NVR findFile returned unexpected response: %s", find_res)
                return []

            next_uri = f"/cgi-bin/mediaFileFind.cgi?action=findNextFile&object={session_id}&count={count}"
            next_res = await self.async_nvr_request(next_uri)

            items: dict[int, dict[str, Any]] = {}
            for line in next_res.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"items\[(\d+)\]\.([a-zA-Z0-9_\[\]]+)=(.*)", line)
                if m:
                    idx = int(m.group(1))
                    key = m.group(2)
                    val = m.group(3)
                    if idx not in items:
                        items[idx] = {}
                    if key == "FilePath":
                        items[idx]["path"] = val
                    elif key == "StartTime":
                        items[idx]["start_time"] = val
                    elif key == "EndTime":
                        items[idx]["end_time"] = val
                    elif key == "Length":
                        try:
                            items[idx]["length"] = int(val)
                            items[idx]["size_mb"] = round(int(val) / (1024 * 1024), 1)
                        except ValueError:
                            items[idx]["length"] = 0
                            items[idx]["size_mb"] = 0.0
                    elif key == "Type":
                        items[idx]["type"] = val
                    elif key.startswith("Flags"):
                        items[idx].setdefault("flags", []).append(val)

            results: list[dict[str, Any]] = []
            for idx in sorted(items.keys()):
                item = items[idx]
                if "path" in item:
                    flags = item.get("flags", [])
                    path_str = item.get("path", "")
                    # Extract Dahua/CP PLUS filename tag [M]=Motion, [R]=Regular, [A]=Alarm, [H]=Human, [V]=Vehicle
                    m_tag = re.search(r"\[([a-zA-Z0-9]+)\]\[\d+@\d+\]", path_str)
                    tag = m_tag.group(1).upper() if m_tag else ""

                    if "HUMAN" in tag or any("Human" in f for f in flags):
                        event_type = "Human"
                    elif "VEHICLE" in tag or any("Vehicle" in f for f in flags):
                        event_type = "Vehicle"
                    elif tag == "M" or "[M]" in path_str or any("Motion" in f for f in flags):
                        event_type = "Motion"
                    elif tag == "A" or any("Alarm" in f for f in flags):
                        event_type = "Alarm"
                    elif tag == "R" or any("Regular" in f for f in flags):
                        event_type = "Continuous"
                    elif any("Event" in f for f in flags):
                        event_type = "AI Event"
                    else:
                        event_type = "Continuous"
                    item["event_type"] = event_type

                    duration = 0
                    if "start_time" in item and "end_time" in item:
                        try:
                            t_start = datetime.strptime(item["start_time"], "%Y-%m-%d %H:%M:%S")
                            t_end = datetime.strptime(item["end_time"], "%Y-%m-%d %H:%M:%S")
                            duration = int((t_end - t_start).total_seconds())
                        except Exception:
                            duration = 0
                    item["duration"] = duration
                    item["channel"] = channel
                    results.append(item)

            return results
        finally:
            await self.async_close_find_session(session_id)



