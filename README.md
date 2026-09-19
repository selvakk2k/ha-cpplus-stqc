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
- [Technical Handover Reference](#technical-handover-reference)
- [My Integrations & Lovelace Cards](#my-integrations--lovelace-cards)
- [Credits & License](#credits--license)

---

## Features

- **Dual-Mode Operation**:
  - **NVR Hub Mode**: Connects to the central NVR, auto-discovers all active channels, creates child devices for each camera, and streams real-time AI push events.
  - **Direct Camera Mode**: Connects directly to standalone STQC IP cameras via native `cpapi2` HTTPS JSON-RPC.
- **Dual-Stream RTSP Video**: Provides dedicated entities for Main (high resolution) and Sub (low bandwidth) video feeds with credentials encoding.
- **Live Snapshot Previews**: Captures instant JPEG snapshots via authenticated Digest requests with automated challenge-response retries.
- **Real-Time Push Event Dispatch**: Continuous HTTP chunked listener on `eventManager.cgi` providing instant binary sensor triggers:
  - Smart Motion Detection (SMD Plus) **Human Detection**
  - Smart Motion Detection (SMD Plus) **Vehicle Detection**
  - Perimeter **Tripwire Breach** (`CrossLineDetection`)
  - Standard **Motion Detection** (`VideoMotion`)
- **Native Event Bus Dispatch**: Automatically dispatches `cpplus_event` on the Home Assistant event bus for easy automation triggers without polling.
- **Day/Night & Illuminator Controls**: Dedicated select entities per camera channel for Day/Night mode (`Color`, `Auto`, `Black & White`) and camera illuminator control (`Auto`, `Manual`, `Off`).
- **AI Detection Arming Controls**: Configurable switch entities per channel to toggle Human Detection, Vehicle Detection, and Tripwire algorithms directly from Home Assistant.
- **PTZ Movement & Preset Services**: Native services (`cpplus.ptz_move`, `cpplus.ptz_stop`, `cpplus.ptz_preset`) supporting pan, tilt, optical zoom, focus adjustment, and preset recall on PTZ-capable channels.
- **Rich Hardware Telemetry**: Automatically queries the NVR remote device table to report each channel's exact hardware model number, serial number, firmware version, and direct camera web URL.
- **Remote Reboot Control**: Dedicated button entity to safely restart the hardware.
- **Home Assistant 2026 Registry Compliance**: Built using modern `via_device_id` linkage, non-blocking TLS initialization, and cross-platform parity across camera, binary sensor, sensor, button, select, and switch platforms.

---

## Supported Hardware Models

| Hardware Model | Device Type | Protocol Family | Hardware Verified |
| :--- | :--- | :--- | :---: |
| **`CP-UNR-4K4322-V4`** | 32-Channel 4K Network Video Recorder | NVR Digest CGI | ✅ |
| **`CP-UNC-DA21L3C-Q`** | STQC 2MP Fixed Lens Dome Camera | CPPLUS `cpapi2` / ONVIF | ✅ |
| **`CP-UNC-TA21L3C-Q`** | STQC 2MP Fixed Lens Bullet Camera | CPPLUS `cpapi2` / ONVIF | ✅ |
| **`IPC_GK7205V200_85K40T_S38`** | NetSDK IP Camera (Goke GK7205V200 SoC) | Xiongmai NetSDK / ONVIF | ✅ |
| **`VTO6531H`** | Dahua Apartment Video Door Phone / Intercom | Dahua Private / ONVIF | ✅ |
| **`P03H41`** | Fixed Dome IP Camera | ONVIF | ✅ |

> [!NOTE]
> Other CP PLUS, Dahua OEM, and ONVIF-compliant IP cameras connected to supported CP PLUS or Dahua NVRs will function normally with automated channel discovery.

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
│     Central 32CH 4K NVR      │ │     Standalone Camera       │
│      CP-UNR-4K4322-V4        │ │      CP-UNC-TA21L3C-Q       │
├──────────────────────────────┤ └─────────────────────────────┘
│ • ChannelTitle & RemoteDevice│
│ • eventManager.cgi (Push AI) │
│ • snapshot.cgi & RTSP 554    │
└──────────────┬───────────────┘
               │ Channels 1-17
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Connected Hardware:                                         │
│ • CP-UNC-DA21L3C-Q (Lobbies 1st - 5th Floor)                │
│ • CP-UNC-TA21L3C-Q (Terrace & Perimeter Bullet Cameras)     │
│ • IPC_GK7205V200 (Gate Cameras with EEPROM NetSDK Audio)    │
│ • VTO6531H (Apartment Video Door Station)                   │
└─────────────────────────────────────────────────────────────┘
```

1. **Central NVR Hub**: Communicates over HTTPS port 443 using HTTP Digest Authentication. Real-time events stream continuously from `/cgi-bin/eventManager.cgi?action=attach&codes=[All]`.
2. **Direct STQC Camera**: Authenticates over HTTPS port 443 using the `cpapi2` JSON-RPC protocol with two-step uppercase double-MD5 salted challenges.
3. **Gate Camera Audio Tool**: [`scripts/configure_gate_audio.py`](scripts/configure_gate_audio.py) provides a direct NetSDK binary connection over port 34567 to enable `AudioEnable` on Main and Sub streams in non-volatile EEPROM.

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
3. Fill in the connection parameters:
   - **Host**: IP address of your NVR (`192.168.1.100`) or camera (`192.168.1.103`).
   - **HTTPS Port**: `443` (default).
   - **RTSP Port**: `554` (default).
   - **Username**: Device administrative username (e.g. `admin`).
   - **Password**: Device password.
4. Click **Submit**. The integration automatically probes the host, identifies whether it is an NVR hub or standalone camera, and registers all devices and entities.

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

## Technical Handover Reference

For in-depth protocol frame specifications, NetSDK EEPROM commands, and internal Home Assistant 2026 registry migration details, refer to the [HANDOVER.md](HANDOVER.md) file included in this repository.

---

## My Integrations & Lovelace Cards

| Integration / Card | Category | Description | Status |
| :--- | :--- | :--- | :--- |
| [CP PLUS STQC](https://github.com/selvakk2k/ha-cpplus-stqc) | Integration | Local NVR & STQC IP Camera integration with real-time AI push events | `Stable` |
| [Panasonic AC India](https://github.com/selvakk2k/ha-miraie-ac-in) | Integration | Local IR & Cloud MQTT control for Panasonic MirAIe Air Conditioners | `Stable` |
| [Panasonic AC India Card](https://github.com/selvakk2k/miraie-ac-card-in) | Lovelace Card | Modern Lovelace card for Panasonic ACs | `Stable` |
| [Indian BLDC Fan IR](https://github.com/selvakk2k/superfan_ir) | Integration | Native Home Assistant integration for Indian BLDC ceiling fans (Superfan, Atomberg) | `Stable` |
| [Indian BLDC Fan Card](https://github.com/selvakk2k/superfan-card) | Lovelace Card | Interactive Lovelace card with speed dial & mode toggles for BLDC fans | `Stable` |
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
