# CP PLUS STQC Integration (`ha-cpplus-stqc`)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/github/v/release/selvakk2k/ha-cpplus-stqc?style=flat-square)](https://github.com/selvakk2k/ha-cpplus-stqc/releases)
[![AI-Assisted](https://img.shields.io/badge/AI%20Assisted-Antigravity%20%7C%20Claude-blueviolet?style=flat-square&logo=google)](https://github.com/selvakk2k)
[![AI Attribution](https://img.shields.io/badge/AI%20Attribution-AIA%20PAI%20Nc%20Hin-orange?style=flat-square)](https://aiattribution.github.io/interpret-attribution)

Zero-cloud, 100% local Home Assistant integration for CP PLUS STQC IP cameras, NVRs, and connected residential building security hardware. Supports dual-stream RTSP video, snapshot extraction, real-time push event handling for AI human/vehicle detection and perimeter tripwires, and comprehensive hardware telemetry.

---

## Table of Contents

- [Features](#features)
- [Supported Hardware Models](#supported-hardware-models)
- [Architecture & Protocols](#architecture--protocols)
- [Installation](#installation)
- [Configuration](#configuration)
- [Troubleshooting & Logs](#troubleshooting--logs)
- [My Integrations & Lovelace Cards](#my-integrations--lovelace-cards)
- [Credits & License](#credits--license)

---

## Features

- **Dual-Mode Operation**:
  - **NVR Hub Mode**: Connects to the central NVR, auto-discovers all active channels, creates subentries for each camera, and streams real-time AI push events.
  - **Direct Camera Mode**: Connects directly to standalone STQC IP cameras via native `cpapi2` HTTPS JSON-RPC.
- **Home Assistant Config Subentries**: Uses modern subentry architecture (`nvr_hub` and `camera_channel`). Central NVR hardware and all camera channels are cleanly grouped under their parent device in the Home Assistant UI, with individual channel reconfigure flows for direct camera connections.
- **Dual-Stream RTSP Video & RTSPS Encryption**: Provides dedicated entities for Main (high resolution) and Sub (low bandwidth) video feeds with credentials encoding and optional RTSP over TLS (`rtsps://`) encryption.
- **Dynamic RTSP Stream Probing**: Automatically probes and detects working RTSP stream paths (`/cam/realmonitor`, `/live`, or custom channels) directly on camera hardware, eliminating stream worker `404 Not Found` loops.
- **ONVIF Media & Profile Discovery**: Automated ONVIF profile and stream URI extraction with HTTP Digest authentication for camera snapshots and media endpoints.
- **Universal Remote Streaming Compatibility**: Full native compatibility with Home Assistant's HLS streaming engine, ensuring reliable remote video streaming through Cloudflare Tunnels (`cloudflared`), Tailscale, and Nabu Casa without requiring custom UDP port forwards.
- **Live Snapshot Previews**: Captures instant JPEG snapshots via authenticated Digest requests with automated challenge-response retries and FFmpeg fallback.
- **Real-Time Push Event Dispatch**: Continuous HTTP chunked listener on `eventManager.cgi` providing instant binary sensor triggers:
  - Smart Motion Detection (SMD Plus) **Human Detection**
  - Smart Motion Detection (SMD Plus) **Vehicle Detection**
  - Perimeter **Tripwire Breach** (`CrossLineDetection`)
  - Standard **Motion Detection** (`VideoMotion`)
- **Native Event Bus Dispatch**: Automatically dispatches `cpplus_event` on the Home Assistant event bus for easy automation triggers without polling.
- **Day/Night & Illuminator Controls**: Dedicated select entities per camera channel for Day/Night mode (`Color`, `Auto`, `Black & White`) and camera illuminator control (`Auto`, `Manual`, `Off`).
- **AI Detection Arming Controls**: Configurable switch entities per channel to toggle Human Detection, Vehicle Detection, and Tripwire algorithms directly from Home Assistant.
- **PTZ Movement & Preset Services**: Native services (`cpplus.ptz_move`, `cpplus.ptz_stop`, `cpplus.ptz_preset`) supporting pan, tilt, optical zoom, focus adjustment, and preset recall on PTZ-capable channels with duration auto-stop.
- **Rich Hardware Telemetry**: Automatically queries the NVR remote device table to report each channel's exact hardware model number, serial number, firmware version, and direct camera web URL.
- **Remote Reboot Control**: Dedicated button entity to safely restart the hardware.
- **Home Assistant 2026 Registry Compliance**: Built using modern `via_device_id` linkage, non-blocking TLS initialization, and cross-platform parity across camera, binary sensor, sensor, button, select, and switch platforms.

> [!NOTE]
> Advanced camera controls (AI detection arming, tripwire arming, day/night mode, and illuminator select entities) are capability-gated to native CP PLUS channels. Third-party ONVIF cameras connected to a CP PLUS NVR provide dual-stream RTSP video and snapshot extraction.

---

## Supported Hardware Models

| Hardware Model | Device Type | Protocol Family | Hardware Verified |
| :--- | :--- | :--- | :---: |
| **`CP-UNR-4K4322-V4`** | 32-Channel 4K Network Video Recorder | NVR Digest CGI | ✅ |
| **`CP-UNC-DA21L3C-Q`** | STQC 2MP Fixed Lens Dome Camera | CPPLUS `cpapi2` / ONVIF | ✅ |
| **`CP-UNC-TA21L3C-Q`** | STQC 2MP Fixed Lens Bullet Camera | CPPLUS `cpapi2` / ONVIF | ✅ |

> [!NOTE]
> While third-party ONVIF cameras connected to an NVR work via generic channel discovery, they are best-effort interoperability and not officially supported.

---

## Architecture & Protocols

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant Core                      │
│                  Integration: CP PLUS STQC                  │
└──────────────┬───────────────────────────────┬──────────────┘
               │ HTTPS Port 443                │ HTTPS Port 443
               │ HTTP Digest Auth              │ cpapi2 JSON-RPC
               ▼                               ▼
┌──────────────────────────────┐ ┌─────────────────────────────┐
│     Network Video Recorder   │ │     Standalone Camera       │
│           (NVR Hub)          │ │       (STQC Camera)         │
├──────────────────────────────┤ └─────────────────────────────┘
│ • Channel & Subentry Registry│
│ • Push Event Stream (AI/SMD) │
│ • Dual RTSP & JPEG Snapshots │
└──────────────┬───────────────┘
               │ Channels 1..N
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Discovered Camera Channels:                                 │
│ • Main Stream (High Resolution RTSP / RTSPS)                │
│ • Sub Stream (Low Bandwidth RTSP / RTSPS)                   │
│ • Real-time AI Event Binary Sensors (Human / Vehicle / SMD) │
└─────────────────────────────────────────────────────────────┘
```

1. **Central NVR Hub**: Communicates over HTTPS port 443 using HTTP Digest Authentication. Real-time events stream continuously from `/cgi-bin/eventManager.cgi?action=attach&codes=[All]`.
2. **Direct STQC Camera**: Authenticates over HTTPS port 443 using the `cpapi2` JSON-RPC protocol with two-step uppercase double-MD5 salted challenges.

---

## Installation

### Method 1: Via HACS (Recommended)

1. Open **HACS** in your Home Assistant sidebar.
2. Select **Integrations** > Three dots menu in top-right > **Custom repositories**.
3. Enter `https://github.com/selvakk2k/ha-cpplus-stqc` with category **Integration**.
4. Click **Download**, then restart Home Assistant.

### Method 2: Manual Installation

1. Clone or download this repository.
2. Copy the `custom_components/cpplus` directory into your Home Assistant `/config/custom_components/` directory:
   ```bash
   cp -r custom_components/cpplus /config/custom_components/
   ```
3. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for **`CP PLUS STQC`**.
3. Select your connection mode:
   - **Network Video Recorder (NVR)**: Connect through a central NVR to discover and manage all camera channels automatically. Recommended for multi-camera installations; works reliably even when cameras are on isolated PoE subnets or separate surveillance VLANs.
   - **Standalone IP Camera**: Connect directly to a single IP camera on your network. Enables direct on-camera features, requiring the camera to be directly reachable by Home Assistant on your local network.
4. Fill in the connection parameters:
   - **Host**: IP address or hostname of your NVR (`192.168.1.100`) or camera (`192.168.1.103`).
   - **HTTPS Port**: `443` (default; use `80` for plain HTTP camera setups).
   - **RTSP Port**: `554` (default).
   - **Username**: Device administrative username (e.g. `admin`).
   - **Password**: Device password.
5. Click **Submit**. The integration automatically authenticates, registers the parent device and subentries, and adds all camera entities.

### Options Flow

From **Settings** > **Devices & Services** > **CP PLUS STQC** > **Configure**:
- **RTSP Port**: Override RTSP streaming port (default: `554`).
- **RTSP over TLS**: Enable RTSPS encryption when supported by your hardware or streaming proxy.

---

## Troubleshooting & Logs

To enable detailed debug logging for troubleshooting stream negotiation or push events, add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.cpplus: debug
```

---

## My Integrations & Lovelace Cards

| Integration / Card | Category | Description | Status |
| :--- | :--- | :--- | :--- |
| [CP PLUS STQC](https://github.com/selvakk2k/ha-cpplus-stqc) | Integration | Local NVR & STQC IP Camera integration with real-time AI push events | `Beta` |
| [Panasonic AC India](https://github.com/selvakk2k/ha-panasonic-ac-in) | Integration | Local IR & Cloud MQTT control for Panasonic MirAIe Air Conditioners | `Stable` |
| [Panasonic AC India Card](https://github.com/selvakk2k/panasonic-ac-card-in) | Lovelace Card | Modern Lovelace card for Panasonic ACs | `Stable` |
| [Indian BLDC Fan IR](https://github.com/selvakk2k/ha-bldc-fan-ir) | Integration | Native Home Assistant integration for Indian BLDC ceiling fans (Atomberg, Superfan) | `Stable` |
| [Indian BLDC Fan Card](https://github.com/selvakk2k/bldc-fan-card) | Lovelace Card | Interactive Lovelace card with speed dial & mode toggles for BLDC fans | `Stable` |
| [IFB Washer Local](https://github.com/selvakk2k/ifb-washer-local) | Integration | Local Wi-Fi integration for IFB Front Load Washing Machines & Washer Dryers | `Beta` |
| [IFB Washer Card](https://github.com/selvakk2k/ifb-washer-card) | Lovelace Card | Dedicated Lovelace card for IFB washers & dryers with cycle controls | `Beta` |
| [Tinxy Local Python](https://github.com/selvakk2k/ha-tinxylocal) | Integration | Pure-Python local control for Tinxy smart switches and modules | `Stable` |

---

## Credits & License

### Project Contributors & AI Attribution
* **Lead Architecture & Hardware Validation**: [@selvakk2k](https://github.com/selvakk2k) — physical testing on hardware, architectural design, and domain requirements.
* **Code Implementation & Engineering**: **Antigravity** (Google DeepMind) — core algorithm development, Home Assistant platform migrations, async concurrency architecture, and automated test suites.
* **Pre-Release Code Review & Auditing**: **Claude** (Anthropic) — independent architectural review, edge-case analysis, and verification of upstream compatibility.

Licensed under the **Apache License 2.0**. See the [LICENSE](LICENSE) file for details.
