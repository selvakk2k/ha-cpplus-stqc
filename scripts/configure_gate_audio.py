#!/usr/bin/env python3
"""
Configure audio on CP PLUS STQC gate cameras (192.168.1.101 & 192.168.1.102)
Uses a secure Wayland desktop dialog (zenity) to prompt for credentials.
"""

import sys
import os
import subprocess
import json
import time

try:
    from dvrip import DVRIPCam
except ImportError:
    print("dvrip not found, please run with 'uv run --with python-dvr'")
    sys.exit(1)


def get_password_gui(username="admin"):
    """Prompt user for password using zenity GUI dialog."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        import getpass
        return getpass.getpass(f"Password for CP PLUS camera ({username}): ")

    cmd = [
        "zenity",
        "--password",
        f"--title=CP PLUS Camera Authentication ({username})"
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return proc.stdout.strip()
    except subprocess.CalledProcessError:
        print("Password prompt cancelled.")
        sys.exit(0)


def configure_camera_audio(ip, user, password, port=34567):
    print(f"\n[{ip}] Connecting to CP PLUS Camera on port {port}...")
    cam = DVRIPCam(ip, user=user, password=password, port=port)
    
    logged_in = False
    try:
        logged_in = cam.login()
    except Exception as e:
        print(f"[{ip}] Login error: {e}")
        return False
        
    if not logged_in:
        print(f"[{ip}] Login failed with sofia hash. Testing alternative authentication hash...")
        try:
            import hashlib
            md5_hash = hashlib.md5(password.encode()).hexdigest()
            cam.hash_pass = md5_hash
            logged_in = cam.login()
        except Exception:
            pass

    if not logged_in:
        print(f"[{ip}] Authentication failed! Please verify the password.")
        cam.close()
        return False

    print(f"[{ip}] Successfully authenticated with camera!")
    
    try:
        sys_info = cam.get_system_info()
        print(f"[{ip}] Device Model: {sys_info.get('Hardware', 'Unknown')}, Build Date: {sys_info.get('BuildTime', 'Unknown')}")
    except Exception:
        pass

    print(f"[{ip}] Fetching current encoder configuration (Simplify.Encode)...")
    try:
        encode_info = cam.get_encode_info()
    except Exception as e:
        print(f"[{ip}] Failed to get encode info: {e}")
        cam.close()
        return False

    if not encode_info:
        print(f"[{ip}] Empty encode configuration received.")
        cam.close()
        return False

    print(f"[{ip}] Current channel count: {len(encode_info)}")
    modified = False

    for ch_idx, ch in enumerate(encode_info):
        main_fmt = ch.get("MainFormat", {})
        extra_fmt = ch.get("ExtraFormat", {})
        
        main_audio = main_fmt.get("AudioEnable", False)
        extra_audio = extra_fmt.get("AudioEnable", False)
        
        print(f"[{ip}] Channel {ch_idx}: MainStream Audio={main_audio}, SubStream Audio={extra_audio}")
        
        if not main_audio:
            main_fmt["AudioEnable"] = True
            modified = True
        if not extra_audio:
            extra_fmt["AudioEnable"] = True
            modified = True

    if modified:
        print(f"[{ip}] Updating camera encoder settings to enable audio...")
        try:
            res = cam.set_info("Simplify.Encode", encode_info)
            print(f"[{ip}] Save response: {res}")
        except Exception as e:
            print(f"[{ip}] Failed to set encoder info: {e}")
            cam.close()
            return False

        time.sleep(1)
        # Verify
        new_info = cam.get_encode_info()
        main_now = new_info[0].get("MainFormat", {}).get("AudioEnable")
        extra_now = new_info[0].get("ExtraFormat", {}).get("AudioEnable")
        print(f"[{ip}] Verification after update: Main Audio={main_now}, Sub Audio={extra_now}")
        if main_now and extra_now:
            print(f"[{ip}] Audio successfully enabled in camera EEPROM!")
        else:
            print(f"[{ip}] Warning: Audio status not confirmed.")
    else:
        print(f"[{ip}] Audio is already enabled on all streams.")

    cam.close()
    return True


def verify_rtsp_audio(ip, user, password):
    print(f"\n[{ip}] Verifying audio stream presence over RTSP (port 554)...")
    rtsp_url = f"rtsp://{user}:{password}@{ip}:554/user={user}&password={password}&channel=1&stream=0.sdp"
    cmd = [
        "ffprobe",
        "-v", "error",
        "-rtsp_transport", "tcp",
        "-timeout", "5000000",
        "-show_entries", "stream=codec_type,codec_name,sample_rate,channels",
        "-of", "json",
        rtsp_url
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        codecs = [f"{s.get('codec_type')}:{s.get('codec_name')}" for s in streams]
        print(f"[{ip}] RTSP Detected Streams: {', '.join(codecs)}")
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        if has_audio:
            print(f"[{ip}] SUCCESS: Live audio track confirmed on RTSP!")
            return True
        else:
            print(f"[{ip}] Notice: No audio track detected yet (may require camera reboot).")
            return False
    except subprocess.TimeoutExpired:
        print(f"[{ip}] RTSP probe timed out.")
        return False
    except Exception as e:
        print(f"[{ip}] RTSP probe error: {e}")
        return False


def main():
    username = os.environ.get("CPPLUS_USER", "admin")
    password = os.environ.get("CPPLUS_PASSWORD")
    
    if not password:
        password = get_password_gui(username)

    if not password:
        print("No password provided, exiting.")
        sys.exit(1)

    gate_ips = ["192.168.1.101", "192.168.1.102"]
    
    for ip in gate_ips:
        success = configure_camera_audio(ip, username, password)
        if success:
            verify_rtsp_audio(ip, username, password)

    print("\nGate camera audio configuration completed.")


if __name__ == "__main__":
    main()
