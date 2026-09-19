# Project Handover: CP PLUS STQC Home Assistant Integration (`ha-cpplus-stqc`)

This document serves as the primary technical context, architectural briefing, and protocol reference for any developer or AI coding agent working on the `ha-cpplus-stqc` custom Home Assistant integration and its companion hardware tools.

---

## 1. Executive Summary & Hardware Verification

* **Repository Goal**: 100% local, zero-cloud Home Assistant custom integration (`cpplus`) titled **`CP PLUS STQC`** (Manufacturer: **`CP PLUS`**) for an 18-unit residential apartment building's centralized CCTV and access infrastructure.
* **Central NVR**: 32-channel 4K NVR at `192.168.1.100:443` (Hardware Model: **`CP-UNR-4K4322-V4`**, Dahua OEM `DH-NVR4232-HDS3`).
* **Hardware Scope**:
  - **17 Connected Channels** managed via the NVR: 13 floor and terrace cameras, 2 gate perimeter cameras, 1 lobby camera, and 1 apartment intercom/VTO station.
  - **Dual-Mode Operation**: The integration supports both central NVR hub operation (discovering and managing all 17 channels with child devices) and direct standalone camera operation (for isolated STQC cameras using native `cpapi2`).
* **Verification Milestones (Hardware Verified)**:
  - **NVR Hub Communication**: Authenticated HTTP Digest CGI over HTTPS port 443 with self-signed SSL verification bypass.
  - **Real-Time Push Event Stream**: Continuous HTTP chunked listener on `/cgi-bin/eventManager.cgi?action=attach&codes=[All]`, driving instant state updates for AI Human Detection, Vehicle Detection, Tripwire Breaches, and Motion.
  - **Gate Camera Audio (`192.168.1.101` & `192.168.1.102`)**: Direct NetSDK binary packet configuration on port 34567 via [`scripts/configure_gate_audio.py`](file:///home/skk/Documents/antigravity/associationsmart/scripts/configure_gate_audio.py), enabling `AudioEnable` on Main and Sub streams directly in non-volatile EEPROM (`pcm_alaw`).
  - **Live RTSP Streaming**: Validated via `ffprobe` across both Main (`subtype=0`) and Sub (`subtype=1`) streams (`ExitCode=0`, `video:hevc`, `audio:pcm_alaw`).
  - **Snapshot Previews**: Binary JPEG extraction via `async_nvr_request_bytes()` with automatic HTTP 401 Digest challenge-response retry.
  - **Home Assistant 2026 Registry Compliance**: Fixed `RuntimeError` by pre-registering the parent NVR device and using `via_device_id` for all child camera devices.
  - **Hardware Telemetry Resolution**: Extracted exact model numbers, serials, firmware versions, and direct IP web URLs from the NVR's `RemoteDevice` configuration table.
  - **Total Registered Entities**: **88 entities** across 18 devices (34 camera stream entities, 50 binary sensors, 3 telemetry sensors, 1 reboot button).

---

## 2. Hardware Topology & Discovered Models

| Channel | Channel Name | Target IP | Discovered Model | Architecture | Firmware Version | Serial Number | Protocol | Area |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Hub** | **CP PLUS STQC SE NVR** | `192.168.1.100` | `CP-UNR-4K4322-V4` | 32CH 4K NVR | NVR OS | `NVR_SERIAL_EXAMPLE` | Digest CGI | Meeting Room / Rack |
| `01` | **Main Gate** | `192.168.1.101` | `IPC_GK7205V200_85K40T_S38` | NetSDK IP Cam | `V1.00.T01...ONVIF 21.06` | `CAM01_SERIAL_EXAMPLE` | Onvif / NetSDK | Main Gate |
| `02` | **Sub Gate** | `192.168.1.102` | `IPC_GK7205V200_85K40T_S38` | NetSDK IP Cam | `V1.00.T01...ONVIF 21.06` | `CAM02_SERIAL_EXAMPLE` | Onvif / NetSDK | Sub Gate |
| `03` | **Main Gate Front** | `192.168.1.103` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM03_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | Stilt Parking |
| `04` | **Main Gate Back** | `192.168.1.104` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM04_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | Stilt Parking |
| `05` | **Sub Gate Back** | `192.168.1.105` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM05_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | Stilt Parking |
| `06` | **Sub Gate Front** | `192.168.1.106` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM06_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | Stilt Parking |
| `07` | **Lobby** | `192.168.1.107` | `P03H41` | IP Camera | `V1.09.64-20221209` | `CAM07_SERIAL_EXAMPLE` | Onvif | Ground Floor Lobby |
| `08` | **1st Floor Lobby** | `192.168.1.108` | `CP-UNC-DA21L3C-Q` | STQC Dome | `2.860.00AT002.0.R,2025-12-03` | `CAM08_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 1st Floor Lobby |
| `09` | **2nd Floor Lobby** | `192.168.1.109` | `CP-UNC-DA21L3C-Q` | STQC Dome | `2.860.00AT002.0.R,2025-12-03` | `CAM09_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 2nd Floor Lobby |
| `10` | **3rd Floor Lobby** | `192.168.1.110` | `CP-UNC-DA21L3C-Q` | STQC Dome | `2.860.00AT002.0.R,2025-12-03` | `CAM10_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 3rd Floor Lobby |
| `11` | **4th Floor Lobby** | `192.168.1.111` | `CP-UNC-DA21L3C-Q` | STQC Dome | `2.860.00AT002.0.R,2025-12-03` | `CAM11_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 4th Floor Lobby |
| `12` | **5th Floor Lobby** | `192.168.1.112` | `CP-UNC-DA21L3C-Q` | STQC Dome | `2.860.00AT002.0.R,2025-12-03` | `CAM12_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 5th Floor Lobby |
| `13` | **5th Floor Terrace** | `192.168.1.113` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM13_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 5th Floor Terrace |
| `14` | **6th Floor Terrace Left** | `192.168.1.114` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM14_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 6th Floor Terrace |
| `15` | **6th Floor Terrace Right** | `192.168.1.115` | `CP-UNC-TA21L3C-Q` | STQC Bullet | `2.860.00AT002.0.R,2025-12-03` | `CAM15_SERIAL_EXAMPLE` | CPPLUS / cpapi2 | 6th Floor Terrace |
| `16` | **Intercom** | `192.168.1.150` | `VTO6531H` | Dahua VTO | `4.600.0000000.5.R, 2025-07-22` | `CAM16_SERIAL_EXAMPLE` | Private / Onvif | Stilt Parking |
| `17` | **VTO2** | `192.168.1.150` | `VTO6531H` | Dahua VTO | `4.600.0000000.5.R, 2025-07-22` | `CAM16_SERIAL_EXAMPLE` | Private / Onvif | Stilt Parking |

---

## 3. Protocol Mechanics & Authentication

### A. Central NVR Hub (`192.168.1.100:443`)
* **Transport**: HTTPS port 443 with self-signed SSL.
* **Authentication**: HTTP Digest Authentication (`AsyncDigestAuth` in [`client.py`](file:///home/skk/Documents/antigravity/associationsmart/custom_components/cpplus/client.py#L35-L95)).
* **Channel & Configuration Discovery**:
  - `ChannelTitle`: `/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle` (Camera names).
  - `RemoteDevice`: `/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice` (Hardware models, IPs, serial numbers, firmware versions, ports).
  - `SmartMotionDetect`: `/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect` (SMD Plus Human and Vehicle capabilities per channel).
  - `CrossLineDetection`: `/cgi-bin/configManager.cgi?action=getConfig&name=CrossLineDetection` (Tripwire perimeter capabilities).
* **Real-Time Push Event Stream**:
  - Endpoint: `GET /cgi-bin/eventManager.cgi?action=attach&codes=[All]`
  - Format: HTTP multipart/chunked boundary stream.
  - Event Codes Handled:
    - `SmartMotionHuman`, `HumanDetect` -> Sets `Human Detection` binary sensor.
    - `SmartMotionVehicle`, `VehicleDetect` -> Sets `Vehicle Detection` binary sensor.
    - `CrossLineDetection`, `CrossRegionDetection` -> Sets `Tripwire Breach` binary sensor.
    - `VideoMotion` -> Sets standard `Motion` binary sensor.
* **Snapshot Extraction**:
  - Endpoint: `/cgi-bin/snapshot.cgi?channel={channel}`
  - Method: `async_nvr_request_bytes()` handles binary streams with challenge-response Digest retries to extract live JPEG frames (~90KB).

### B. Direct STQC IP Camera Mode (`cpapi2`)
* **Transport**: HTTPS port 443 with JSON-RPC.
* **Authentication**: Two-step uppercase double-MD5 challenge-response:
  1. `POST /cpapi2` with `{"method": "user.login", "params": {"userName": username, "clientType": "Web3.0"}}` to acquire `random` salt and `realm`.
  2. Compute:
     `inner_md5 = md5(username:realm:password).hexdigest().upper()`
     `auth_hash = md5(username:random:inner_md5).hexdigest().upper()`
  3. `POST /cpapi2` with `{"method": "user.signin", "params": {"userName": username, "password": auth_hash}}`.
* **Lockout Policy**: Standard STQC cameras lock login for 300 seconds after 5 failed attempts (`LockLoginTimes: 5`, `LoginFailLockTime: 300`). Never brute force.

### C. Gate Cameras NetSDK Audio Enablement (`192.168.1.101`, `192.168.1.102`)
* **Transport**: Xiongmai NetSDK binary protocol on TCP port `34567`.
* **Mechanics**:
  - The Goke GK7205V200 encoder defaults to video-only RTSP streaming.
  - Script [`scripts/configure_gate_audio.py`](file:///home/skk/Documents/antigravity/associationsmart/scripts/configure_gate_audio.py) logs into the NetSDK daemon, reads `Simplify.Encode`, sets `AudioEnable=true` on Main and Sub streams, and issues a non-volatile EEPROM save (`OPNetManager / EEPROM Commit`).

---

## 4. Home Assistant 2026 Invariants & Architectural Rules

### Invariant 1: `via_device_id` vs `via_device` (Strict Deprecation Rule)
* **Problem**: In Home Assistant 2026.1+, calling `device_registry.async_get_or_create` with the legacy `via_device=(DOMAIN, parent_serial)` tuple raises an unhandled `RuntimeError`.
* **Enforced Solution**:
  1. Pre-register the parent NVR device directly in `__init__.py` using Home Assistant's `dr.async_get(hass).async_get_or_create(...)`.
  2. Store the returned parent device's registry ID string in `coordinator.parent_device_id`.
  3. Link child cameras in `coordinator.get_channel_device_info()` using `info["via_device_id"] = self.parent_device_id`.

### Invariant 2: RTSP Stream URL Credentials Encoding
* **Problem**: Passwords containing special characters (such as `@`, `#`, `:`, `/`) break standard RTSP parsers in Home Assistant and FFmpeg. If `@` is unencoded, FFmpeg interprets the characters following `@` as the hostname, causing DNS resolution failures.
* **Enforced Solution**: Always pass credentials through `urllib.parse.quote(..., safe="")` in `get_stream_url()`:
  ```python
  enc_user = urllib.parse.quote(self.username, safe="")
  enc_pass = urllib.parse.quote(self.password, safe="")
  return f"rtsp://{enc_user}:{enc_pass}@{self.host}:{self.rtsp_port}/cam/realmonitor?channel={channel}&subtype={subtype}"
  ```

### Invariant 3: Non-Blocking SSL Context Initialization
* **Problem**: `ssl.create_default_context()` performs synchronous filesystem operations to load CA certificates, which triggers Home Assistant's event loop block detector (`asyncio` warnings).
* **Enforced Solution**: Construct an explicit client SSL context:
  ```python
  ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
  ctx.check_hostname = False
  ctx.verify_mode = ssl.CERT_NONE
  ```

### Invariant 4: Sibling Platform Parity
* **Rule**: Whenever modifying entity availability, `DeviceInfo`, event listeners, or lifecycle hooks, audit all sibling platform files:
  - `camera.py`: Live RTSP camera streams (Main & Sub).
  - `binary_sensor.py`: AI Human, Vehicle, Tripwire, Motion, and Connectivity sensors.
  - `sensor.py`: Hub diagnostic telemetry (Model, Serial, Firmware).
  - `button.py`: Hardware reboot button.
* Child camera device metadata must remain identical across `camera.py` and `binary_sensor.py` by always routing through `coordinator.get_channel_device_info()`.

### Invariant 5: Integration Identity Standards
* **Integration Title**: Must remain strictly **`CP PLUS STQC`**.
* **Manufacturer**: Must remain strictly **`CP PLUS`**.
* **Zero Plaintext Secrets**: Passwords must never be output into logs, terminal stdout, git commits, or chat text.

---

## 5. Repository File Structure

```text
ha-cpplus-stqc/
├── custom_components/
│   └── cpplus/
│       ├── __init__.py           # Parent device pre-registration, platform dispatch, background event listener
│       ├── manifest.json         # Integration metadata: "CP PLUS STQC", local_polling, requirements
│       ├── const.py              # Domain constants, port defaults (443, 554), event keys
│       ├── client.py             # Async client: Digest CGI, cpapi2 JSON-RPC, non-blocking TLS, snapshots
│       ├── coordinator.py        # DataUpdateCoordinator: channel mapping, device info factory, push event handler
│       ├── camera.py             # Camera platform: Dual-stream RTSP (Main + Sub) and live snapshots
│       ├── binary_sensor.py      # Binary sensor platform: SMD Human, Vehicle, Tripwire, Motion, Online status
│       ├── sensor.py             # Sensor platform: Hub hardware model, serial number, firmware version
│       ├── button.py             # Button platform: Hardware restart / reboot entity
│       ├── config_flow.py        # UI setup workflow with automatic NVR vs Camera detection
│       ├── services.yaml         # Service declarations (cpplus.reboot)
│       ├── strings.json          # UI localization strings
│       └── translations/
│           └── en.json           # English translation schema
├── scripts/
│   └── configure_gate_audio.py   # Standalone NetSDK tool for EEPROM gate camera audio activation
├── HANDOVER.md                   # This comprehensive technical handover briefing
├── README.md                     # GitHub repository documentation
├── hacs.json                     # HACS distribution metadata
└── .gitignore                    # Python, Home Assistant, and IDE exclusions
```

---

## 6. Deployment & Verification Protocol

### Target Environment
* **Target Home Assistant Host**: `192.168.1.50` (`emeraldha`)
* **Component Directory**: `/config/custom_components/cpplus`
* **SSH Access**: `user@192.168.1.50` with sudo privileges

### Fast Deployment Pipeline
Deploy code modifications directly from the local repository:
```bash
# 1. Create tarball locally
tar -C custom_components -czf /tmp/cpplus.tar.gz cpplus

# 2. Transfer and extract via SSH with sudo
ssh user@192.168.1.50 "sudo tar -C /config/custom_components -xzf - < /tmp/cpplus.tar.gz"

# 3. Restart Home Assistant Core
# Call ha_restart(confirm=True) via ha-association MCP tool or ha core restart
```

### Verification Checklist
1. **Python Syntax**: Verify with `python3 -m py_compile custom_components/cpplus/*.py`.
2. **HA System Health**: Verify config validity via `ha_get_system_health(include="config_check")` (`is_valid: true`).
3. **Device Registry**: Confirm all 18 devices display exact hardware models (`CP-UNC-DA21L3C-Q`, `CP-UNC-TA21L3C-Q`, `IPC_GK7205V200_85K40T_S38`, `VTO6531H`, `CP-UNR-4K4322-V4`).
4. **Stream & Snapshot Integrity**: Confirm live stream delivery and snapshot rendering across Main and Sub streams.
