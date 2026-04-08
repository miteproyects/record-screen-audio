"""
recorder.py — FFmpeg-based screen + audio recorder for macOS.

Builds and manages ffmpeg subprocesses for recording screen, system audio
(via BlackHole), and microphone — individually or in any combination.
"""

import subprocess
import signal
import os
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from audio_manager import AudioManager, AudioDevice


@dataclass
class RecordingConfig:
    record_screen: bool = True
    record_system_audio: bool = True
    record_mic: bool = True
    screen_device: Optional[AudioDevice] = None
    blackhole_device: Optional[AudioDevice] = None
    mic_device: Optional[AudioDevice] = None
    output_dir: str = ""
    framerate: int = 30
    video_bitrate: str = "8000k"
    audio_bitrate_system: str = "192k"
    audio_bitrate_mic: str = "128k"


class Recorder:
    def __init__(self, audio_manager: AudioManager, recordings_dir: str):
        self._am = audio_manager
        self._recordings_dir = recordings_dir
        self._process: Optional[subprocess.Popen] = None
        self._output_file: str = ""
        self._start_time: Optional[float] = None
        self._has_two_audio: bool = False

        os.makedirs(recordings_dir, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def output_file(self) -> str:
        return self._output_file

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    @property
    def has_separate_tracks(self) -> bool:
        return self._has_two_audio

    def start(self, config: RecordingConfig) -> tuple[bool, str]:
        """Start recording with the given config. Returns (success, message)."""
        if self.is_recording:
            return False, "Already recording."

        if not config.record_screen and not config.record_system_audio and not config.record_mic:
            return False, "Nothing selected to record."

        # Save audio settings
        self._am.save_settings()

        # Activate Multi-Output Device if recording system audio
        if config.record_system_audio:
            if self._am.has_multi_output():
                self._am.activate_multi_output()

        # Build output filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = "" if config.record_screen else "_audio"
        self._output_file = os.path.join(
            self._recordings_dir, f"recording_{timestamp}{suffix}.mp4"
        )

        # Build ffmpeg command
        cmd = self._build_command(config)
        if not cmd:
            self._am.restore_settings()
            return False, "Could not build ffmpeg command."

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._start_time = time.time()
            return True, f"Recording started → {os.path.basename(self._output_file)}"
        except Exception as e:
            self._am.restore_settings()
            return False, f"Failed to start ffmpeg: {e}"

    def stop(self) -> tuple[bool, str]:
        """Stop recording gracefully. Returns (success, message)."""
        if not self.is_recording:
            return False, "Not recording."

        try:
            # Send 'q' to ffmpeg stdin for graceful stop
            if self._process and self._process.stdin:
                self._process.stdin.write(b"q")
                self._process.stdin.flush()

            # Wait up to 15 seconds for finalization
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.send_signal(signal.SIGINT)
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()

        except Exception:
            if self._process:
                try:
                    self._process.kill()
                except Exception:
                    pass

        self._process = None
        self._start_time = None

        # Restore audio settings
        self._am.restore_settings()

        if os.path.exists(self._output_file):
            size_mb = os.path.getsize(self._output_file) / (1024 * 1024)
            return True, f"Saved: {os.path.basename(self._output_file)} ({size_mb:.1f} MB)"
        else:
            return False, "Recording file not found — ffmpeg may have failed."

    def _build_command(self, cfg: RecordingConfig) -> list[str]:
        """Build the ffmpeg command based on configuration."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
        self._has_two_audio = False

        s_idx = cfg.screen_device.index if cfg.screen_device else 0
        bh_idx = cfg.blackhole_device.index if cfg.blackhole_device else 0
        mic_idx = cfg.mic_device.index if cfg.mic_device else 0

        if cfg.record_screen:
            if cfg.record_system_audio and cfg.record_mic:
                # Screen + System Audio + Mic (two audio tracks)
                self._has_two_audio = True
                cmd += [
                    "-f", "avfoundation",
                    "-capture_cursor", "1",
                    "-capture_mouse_clicks", "1",
                    "-framerate", str(cfg.framerate),
                    "-i", f"{s_idx}:{bh_idx}",
                    "-f", "avfoundation",
                    "-i", f":{mic_idx}",
                    "-map", "0:v", "-map", "0:a", "-map", "1:a",
                    "-c:v", "h264_videotoolbox", "-b:v", cfg.video_bitrate,
                    "-realtime", "true",
                    "-c:a:0", "aac", "-b:a:0", cfg.audio_bitrate_system,
                    "-c:a:1", "aac", "-b:a:1", cfg.audio_bitrate_mic,
                    "-metadata:s:a:0", "title=System Audio (BlackHole)",
                    "-metadata:s:a:1", "title=Microphone",
                ]

            elif cfg.record_system_audio:
                cmd += [
                    "-f", "avfoundation",
                    "-capture_cursor", "1",
                    "-capture_mouse_clicks", "1",
                    "-framerate", str(cfg.framerate),
                    "-i", f"{s_idx}:{bh_idx}",
                    "-map", "0:v", "-map", "0:a",
                    "-c:v", "h264_videotoolbox", "-b:v", cfg.video_bitrate,
                    "-realtime", "true",
                    "-c:a", "aac", "-b:a", cfg.audio_bitrate_system,
                    "-metadata:s:a:0", "title=System Audio (BlackHole)",
                ]

            elif cfg.record_mic:
                cmd += [
                    "-f", "avfoundation",
                    "-capture_cursor", "1",
                    "-capture_mouse_clicks", "1",
                    "-framerate", str(cfg.framerate),
                    "-i", f"{s_idx}:{mic_idx}",
                    "-map", "0:v", "-map", "0:a",
                    "-c:v", "h264_videotoolbox", "-b:v", cfg.video_bitrate,
                    "-realtime", "true",
                    "-c:a", "aac", "-b:a", cfg.audio_bitrate_mic,
                    "-metadata:s:a:0", "title=Microphone",
                ]

            else:
                # Screen only
                cmd += [
                    "-f", "avfoundation",
                    "-capture_cursor", "1",
                    "-capture_mouse_clicks", "1",
                    "-framerate", str(cfg.framerate),
                    "-i", f"{s_idx}:none",
                    "-c:v", "h264_videotoolbox", "-b:v", cfg.video_bitrate,
                    "-realtime", "true",
                ]

        else:
            # Audio only (no screen)
            if cfg.record_system_audio and cfg.record_mic:
                self._has_two_audio = True
                cmd += [
                    "-f", "avfoundation", "-i", f":{bh_idx}",
                    "-f", "avfoundation", "-i", f":{mic_idx}",
                    "-map", "0:a", "-map", "1:a",
                    "-c:a:0", "aac", "-b:a:0", cfg.audio_bitrate_system,
                    "-c:a:1", "aac", "-b:a:1", cfg.audio_bitrate_mic,
                    "-metadata:s:a:0", "title=System Audio (BlackHole)",
                    "-metadata:s:a:1", "title=Microphone",
                ]

            elif cfg.record_system_audio:
                cmd += [
                    "-f", "avfoundation", "-i", f":{bh_idx}",
                    "-c:a", "aac", "-b:a", cfg.audio_bitrate_system,
                ]

            elif cfg.record_mic:
                cmd += [
                    "-f", "avfoundation", "-i", f":{mic_idx}",
                    "-c:a", "aac", "-b:a", cfg.audio_bitrate_mic,
                ]

        cmd.append(self._output_file)
        return cmd


def merge_tracks(
    input_file: str,
    system_vol: float = 0.5,
    mic_vol: float = 0.5,
) -> tuple[bool, str]:
    """Merge two audio tracks in a recording into one. Returns (success, output_path)."""
    output_file = input_file.replace(".mp4", "_merged.mp4")

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", input_file,
        "-filter_complex",
        f"[0:a:0]volume={system_vol}[sys];"
        f"[0:a:1]volume={mic_vol}[mic];"
        f"[sys][mic]amix=inputs=2:duration=longest[aout]",
        "-map", "0:v?", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
        output_file,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(output_file):
            return True, output_file
        return False, result.stderr
    except Exception as e:
        return False, str(e)


def get_track_count(filepath: str) -> int:
    """Get number of audio tracks in a file."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                filepath,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return len([l for l in result.stdout.strip().splitlines() if l.strip()])
    except Exception:
        return 0
