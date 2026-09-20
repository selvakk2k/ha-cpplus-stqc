"""HTTP views for CP PLUS STQC surveillance media and playback streaming."""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .client import AsyncDigestAuth

_LOGGER = logging.getLogger(__name__)


class CPPlusPlaybackMediaView(HomeAssistantView):
    """View to proxy playback video file streaming from the NVR."""

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
        start_time = request.query.get("start")
        end_time = request.query.get("end")
        file_path = request.query.get("file")

        # If start and end timestamps are present, stream live fMP4 from the NVR RTSP playback server
        if start_time and end_time:
            return await self._stream_rtsp_fmp4(request, client, int(channel), start_time, end_time)

        # Fallback to direct raw file proxy with DigestAuth
        if file_path:
            return await self._stream_file(request, client, file_path)

        return web.Response(status=400, text="Missing start/end or file query parameter")

    async def _stream_rtsp_fmp4(
        self,
        request: web.Request,
        client: Any,
        channel: int,
        start_time: str,
        end_time: str,
    ) -> web.StreamResponse:
        """Stream playback RTSP converted to fragmented MP4 (fMP4) via ffmpeg."""
        rtsp_url = client.get_playback_url(channel, start_time, end_time)
        _LOGGER.debug("Starting fMP4 playback stream from: %s", rtsp_url)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-reset_timestamps", "1",
            "-",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as err:
            _LOGGER.error("Failed to spawn ffmpeg for playback on %s: %s", client.host, err)
            return web.Response(status=500, text=f"ffmpeg spawn error: {err}")

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "video/mp4",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
        await response.prepare(request)

        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                await response.write(chunk)
        except (asyncio.CancelledError, ConnectionResetError):
            _LOGGER.debug("Client disconnected from playback stream")
        finally:
            try:
                if proc.returncode is None:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            await response.write_eof()

        return response

    async def _stream_file(
        self, request: web.Request, client: Any, file_path: str
    ) -> web.StreamResponse:
        """Stream raw file from NVR with robust Digest authentication."""
        if not client._digest_auth:
            client._digest_auth = AsyncDigestAuth(client.username, client.password)

        load_uri = f"/cgi-bin/RPC_Loadfile/{urllib.parse.quote(file_path, safe='/')}"

        try:
            session = await client._get_session()
            url = f"https://{client.host}:{client.port}{load_uri}"
            headers = {"User-Agent": "Mozilla/5.0"}
            if client._digest_auth.realm and client._digest_auth.nonce:
                headers["Authorization"] = client._digest_auth.build_header("GET", load_uri)

            range_hdr = request.headers.get("Range")
            if range_hdr:
                headers["Range"] = range_hdr

            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    auth_hdr = resp.headers.get("WWW-Authenticate", "")
                    if "Digest" in auth_hdr:
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
