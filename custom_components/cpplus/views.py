"""HTTP views for CP PLUS STQC surveillance media and playback streaming."""

from __future__ import annotations

import logging
import urllib.parse
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class CPPlusPlaybackMediaView(HomeAssistantView):
    """View to proxy playback video file downloads or streaming from the NVR."""

    url = "/api/cpplus/playback/{entry_id}/{channel}"
    name = "api:cpplus:playback"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    async def get(self, request: web.Request, entry_id: str, channel: str) -> web.StreamResponse:
        """Handle streaming request for a recorded clip."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id)
        if not coordinator:
            return web.Response(status=404, text="Integration entry not found")

        client = coordinator.client
        file_path = request.query.get("file")
        if not file_path:
            return web.Response(status=400, text="Missing file query parameter")

        # In CP PLUS / Dahua, recorded files are retrieved via RPC_Loadfile
        load_uri = f"/cgi-bin/RPC_Loadfile/{urllib.parse.quote(file_path, safe='/')}"

        try:
            session = await client._get_session()
            url = f"https://{client.host}:{client.port}{load_uri}"
            headers = {"User-Agent": "Mozilla/5.0"}
            if client._digest_auth and client._digest_auth.realm and client._digest_auth.nonce:
                headers["Authorization"] = client._digest_auth.build_header("GET", load_uri)

            range_hdr = request.headers.get("Range")
            if range_hdr:
                headers["Range"] = range_hdr

            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    auth_hdr = resp.headers.get("WWW-Authenticate", "")
                    if "Digest" in auth_hdr and client._digest_auth:
                        client._digest_auth.parse_challenge(auth_hdr)
                        headers["Authorization"] = client._digest_auth.build_header("GET", load_uri)
                        async with session.get(url, headers=headers) as retry_resp:
                            return await self._pipe_response(request, retry_resp)

                return await self._pipe_response(request, resp)
        except Exception as err:
            _LOGGER.error("Playback proxy streaming error on %s: %s", client.host, err)
            return web.Response(status=500, text=f"Streaming error: {err}")

    async def _pipe_response(
        self, request: web.Request, nvr_resp: web.ClientResponse
    ) -> web.StreamResponse:
        """Pipe NVR HTTP response back to the client with appropriate headers."""
        status = nvr_resp.status
        content_type = nvr_resp.headers.get("Content-Type", "video/mp4")
        if "application/octet-stream" in content_type:
            content_type = "video/mp4"

        response = web.StreamResponse(
            status=status,
            headers={
                "Content-Type": content_type,
                "Accept-Ranges": "bytes",
                "Access-Control-Allow-Origin": "*",
            },
        )
        if "Content-Length" in nvr_resp.headers:
            response.headers["Content-Length"] = nvr_resp.headers["Content-Length"]
        if "Content-Range" in nvr_resp.headers:
            response.headers["Content-Range"] = nvr_resp.headers["Content-Range"]

        await response.prepare(request)
        async for chunk in nvr_resp.content.iter_chunked(65536):
            await response.write(chunk)
        await response.write_eof()
        return response
