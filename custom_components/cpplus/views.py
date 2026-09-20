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

        # For .cpv files (e.g. Xiongmai gate cameras on port 8899), the NVR does not serve RTSP playback;
        # stream and transcode the raw file directly via ffmpeg stdin pipe into fragmented MP4
        if file_path and file_path.lower().endswith(".cpv"):
            return await self._stream_file_transcode(request, client, file_path, int(channel), entry_id)

        # If start and end timestamps are present, stream live fMP4 from the NVR RTSP playback server (.dav files)
        if start_time and end_time:
            return await self._stream_rtsp_fmp4(request, client, int(channel), start_time, end_time, entry_id)

        # Fallback to file transcode proxy
        if file_path:
            return await self._stream_file_transcode(request, client, file_path, int(channel), entry_id)

        return web.Response(status=400, text="Missing start/end or file query parameter")

    async def _stream_rtsp_fmp4(
        self,
        request: web.Request,
        client: Any,
        channel: int,
        start_time: str,
        end_time: str,
        entry_id: str,
    ) -> web.StreamResponse:
        """Stream playback RTSP converted to fragmented MP4 (fMP4) via ffmpeg."""
        rtsp_url = client.get_playback_url(channel, start_time, end_time)
        _LOGGER.debug("Starting fMP4 playback stream from: %s", rtsp_url)

        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id)
        needs_hevc_transcode = False
        if coordinator and hasattr(coordinator, "channels"):
            ch_info = next((c for c in coordinator.channels if c.get("channel") == channel), {})
            model = str(ch_info.get("model", "")).upper()
            # Xiongmai cameras (IPC_GK*) stream in HEVC/H.265; transcode to H.264 for browsers
            if "IPC_GK" in model:
                needs_hevc_transcode = True

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-map", "0:v:0",
            "-map", "0:a?",
        ]

        if needs_hevc_transcode:
            # Transcode HEVC/H.265 to ultrafast H.264 with standard web-compatible YUV420P pixel format
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",
            ])
        else:
            # Native H.264 streams (CP PLUS CP-*, Onvif P03H41, Dahua VTO*) use direct stream copy
            cmd.extend(["-c:v", "copy"])

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-reset_timestamps", "1",
            "-",
        ])

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
            # Read first chunk to ensure stream initialization
            first_chunk = await proc.stdout.read(65536)
            if not first_chunk:
                stderr_data = await proc.stderr.read()
                _LOGGER.warning(
                    "ffmpeg produced no video stream data for channel %d (model: %s). ffmpeg stderr: %s",
                    channel,
                    model if "model" in locals() else "unknown",
                    stderr_data.decode("utf-8", errors="replace"),
                )
            else:
                await response.write(first_chunk)
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
                elif proc.returncode != 0:
                    stderr_data = await proc.stderr.read()
                    if stderr_data:
                        _LOGGER.warning(
                            "ffmpeg for channel %d exited with code %s: %s",
                            channel,
                            proc.returncode,
                            stderr_data.decode("utf-8", errors="replace"),
                        )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            await response.write_eof()

        return response

    async def _stream_file_transcode(
        self, request: web.Request, client: Any, file_path: str, channel: int, entry_id: str
    ) -> web.StreamResponse:
        """Stream and transcode NVR recorded file (.cpv or .dav) into fragmented MP4 via ffmpeg."""
        if not client._digest_auth:
            client._digest_auth = AsyncDigestAuth(client.username, client.password)

        file_clean = file_path.lstrip("/")
        load_uri = f"/cgi-bin/RPC_Loadfile/{urllib.parse.quote(file_clean, safe='/')}"

        try:
            session = await client._get_session()
            url = f"https://{client.host}:{client.port}{load_uri}"
            headers = {"User-Agent": "Mozilla/5.0"}
            if client._digest_auth.realm and client._digest_auth.nonce:
                headers["Authorization"] = client._digest_auth.build_header("GET", load_uri)

            resp = await session.get(url, headers=headers)
            if resp.status == 401:
                auth_hdr = resp.headers.get("WWW-Authenticate", "")
                if "Digest" in auth_hdr:
                    client._digest_auth.parse_challenge(auth_hdr)
                    headers["Authorization"] = client._digest_auth.build_header("GET", load_uri)
                    resp = await session.get(url, headers=headers)

            if resp.status != 200:
                _LOGGER.warning(
                    "Failed to fetch file from NVR: %s (status: %d)", load_uri, resp.status
                )
                return web.Response(status=resp.status, text=f"NVR file error: {resp.status}")

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "warning",
                "-i", "pipe:0",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-f", "mp4",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-reset_timestamps", "1",
                "-",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def feed_stdin():
                try:
                    async for chunk in resp.content.iter_chunked(65536):
                        if proc.stdin and not proc.stdin.is_closing():
                            proc.stdin.write(chunk)
                            await proc.stdin.drain()
                except Exception as err:
                    _LOGGER.debug("Error feeding ffmpeg stdin: %s", err)
                finally:
                    try:
                        if proc.stdin and not proc.stdin.is_closing():
                            proc.stdin.close()
                    except Exception:
                        pass

            feeder_task = asyncio.create_task(feed_stdin())

            first_chunk = await proc.stdout.read(65536)
            if not first_chunk:
                stderr_data = await proc.stderr.read()
                _LOGGER.warning(
                    "ffmpeg file transcode produced no output for channel %d (file: %s). Exit code: %s. Stderr: %s",
                    channel,
                    file_path,
                    proc.returncode,
                    stderr_data.decode("utf-8", errors="replace"),
                )
                feeder_task.cancel()
                resp.close()
                return web.Response(status=502, text="Transcode failed to produce video")

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
            await response.write(first_chunk)

            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    await response.write(chunk)
            except (asyncio.CancelledError, ConnectionResetError):
                _LOGGER.debug("Client disconnected from file transcode stream")
            finally:
                feeder_task.cancel()
                resp.close()
                try:
                    if proc.returncode is None:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=3.0)
                    elif proc.returncode != 0:
                        stderr_data = await proc.stderr.read()
                        if stderr_data:
                            _LOGGER.warning(
                                "ffmpeg file transcode for channel %d exited with code %s: %s",
                                channel,
                                proc.returncode,
                                stderr_data.decode("utf-8", errors="replace"),
                            )
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                await response.write_eof()

            return response
        except Exception as err:
            _LOGGER.error("File transcode error on %s: %s", client.host, err)
            return web.Response(status=500, text=f"File transcode error: {err}")
