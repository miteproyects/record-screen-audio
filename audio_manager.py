"""
audio_manager.py — Manages macOS audio devices for recording.

Handles:
  - Discovering audio/video devices via ffmpeg avfoundation
  - Saving and restoring system audio settings (output, input, volume)
  - Switching to Multi-Output Device for recording
"""

import subprocess
import re
import shutil
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AudioDevice:
    index: int
    name: str
    kind: str  # "video" or "audio"


@dataclass
class AudioSnapshot:
    """Snapshot of system audio settings before recording."""
    output_device: str = ""
    input_device: str = ""
    output_volume: str = ""


class AudioManager:
    def __init__(self):
        self._snapshot: Optional[AudioSnapshot] = None
        self._has_switch = shutil.which("SwitchAudioSource") is not None
        self._has_ffmpeg = shutil.which("ffmpeg") is not None

    # ── Device Discovery ─────────────────────────────────────────────────

    def discover_devices(self) -> tuple[list[AudioDevice], list[AudioDevice]]:
        """Return (video_devices, audio_devices) from ffmpeg avfoundation."""
        if not self._has_ffmpeg:
            return [], []

        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=10,
            )
            raw = result.stderr  # ffmpeg writes device list to stderr
        except Exception:
            return [], []

        video_devices: list[AudioDevice] = []
        audio_devices: list[AudioDevice] = []
        section = ""

        for line in raw.splitlines():
            if "AVFoundation video devices" in line:
                section = "video"
                continue
            elif "AVFoundation audio devices" in line:
                section = "audio"
                continue

            match = re.search(r"\[(\d+)\]\s+(.*)", line)
            if match and section:
                idx = int(match.group(1))
                name = match.group(2).strip()
                dev = AudioDevice(index=idx, name=name, kind=section)
                if section == "video":
                    video_devices.append(dev)
                else:
                    audio_devices.append(dev)

        return video_devices, audio_devices

    def find_blackhole(self, audio_devices: list[AudioDevice]) -> Optional[AudioDevice]:
        """Find BlackHole device in audio device list."""
        for dev in audio_devices:
            if "blackhole" in dev.name.lower():
                return dev
        return None

    def find_default_mic(self, audio_devices: list[AudioDevice]) -> Optional[AudioDevice]:
        """Find first non-BlackHole, non-Multi-Output audio device (likely the mic)."""
        skip = {"blackhole", "multi-output"}
        for dev in audio_devices:
            if not any(s in dev.name.lower() for s in skip):
                return dev
        return None

    def find_screen(self, video_devices: list[AudioDevice]) -> Optional[AudioDevice]:
        """Find first screen capture device."""
        for dev in video_devices:
            if "screen" in dev.name.lower() or "capture" in dev.name.lower():
                return dev
        return video_devices[-1] if video_devices else None

    # ── Audio Settings Save/Restore ──────────────────────────────────────

    def save_settings(self) -> AudioSnapshot:
        """Save current audio output, input, and volume."""
        snap = AudioSnapshot()

        if self._has_switch:
            try:
                snap.output_device = subprocess.run(
                    ["SwitchAudioSource", "-c", "-t", "output"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                pass

            try:
                snap.input_device = subprocess.run(
                    ["SwitchAudioSource", "-c", "-t", "input"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                pass

        try:
            result = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                capture_output=True, text=True, timeout=5,
            )
            snap.output_volume = result.stdout.strip()
        except Exception:
            pass

        self._snapshot = snap
        return snap

    def restore_settings(self) -> bool:
        """Restore audio settings from the saved snapshot."""
        if not self._snapshot:
            return False

        snap = self._snapshot
        restored = False

        if self._has_switch:
            if snap.output_device:
                try:
                    subprocess.run(
                        ["SwitchAudioSource", "-s", snap.output_device, "-t", "output"],
                        capture_output=True, timeout=5,
                    )
                    restored = True
                except Exception:
                    pass

            if snap.input_device:
                try:
                    subprocess.run(
                        ["SwitchAudioSource", "-s", snap.input_device, "-t", "input"],
                        capture_output=True, timeout=5,
                    )
                    restored = True
                except Exception:
                    pass

        if snap.output_volume:
            try:
                subprocess.run(
                    ["osascript", "-e", f"set volume output volume {snap.output_volume}"],
                    capture_output=True, timeout=5,
                )
                restored = True
            except Exception:
                pass

        self._snapshot = None
        return restored

    @property
    def snapshot(self) -> Optional[AudioSnapshot]:
        return self._snapshot

    # ── Multi-Output Device ──────────────────────────────────────────────

    def has_multi_output(self) -> bool:
        """Check if a Multi-Output Device exists."""
        if not self._has_switch:
            return False
        try:
            result = subprocess.run(
                ["SwitchAudioSource", "-a", "-t", "output"],
                capture_output=True, text=True, timeout=5,
            )
            return "multi-output" in result.stdout.lower()
        except Exception:
            return False

    def get_multi_output_name(self) -> str:
        """Get the exact name of the Multi-Output Device."""
        if not self._has_switch:
            return ""
        try:
            result = subprocess.run(
                ["SwitchAudioSource", "-a", "-t", "output"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "multi-output" in line.lower():
                    return line.strip()
        except Exception:
            pass
        return ""

    def activate_multi_output(self) -> bool:
        """Switch system output to Multi-Output Device."""
        name = self.get_multi_output_name()
        if not name:
            return False
        try:
            subprocess.run(
                ["SwitchAudioSource", "-s", name, "-t", "output"],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    # ── Dependency Checks ────────────────────────────────────────────────

    def check_dependencies(self) -> dict[str, bool]:
        """Check all required dependencies."""
        checks = {
            "ffmpeg": self._has_ffmpeg,
            "SwitchAudioSource": self._has_switch,
            "BlackHole": False,
        }

        if self._has_ffmpeg:
            _, audio = self.discover_devices()
            checks["BlackHole"] = self.find_blackhole(audio) is not None

        return checks
