"""
recorder.py — FFmpeg-based screen + audio recorder for macOS.

Uses a PID file to track the ffmpeg process across Streamlit reruns.
This is critical because Streamlit re-executes the entire script on
every UI interaction, so in-memory Popen references are lost.
"""

import subprocess
import signal
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from audio_manager import AudioManager, AudioDevice

# Persistent state file (survives Streamlit reruns)
STATE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(STATE_DIR, ".recording_state.json")
LOG_FILE = os.path.join(STATE_DIR, ".ffmpeg_log.txt")


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


def _save_state(pid: int, output_file: str, start_time: float, has_two_audio: bool):
    """Persist recording state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump({
            "pid": pid,
            "output_file": output_file,
            "start_time": start_time,
            "has_two_audio": has_two_audio,
        }, f)


def _load_state() -> Optional[dict]:
    """Load recording state from disk."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _clear_state():
    """Remove the state file."""
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)  # signal 0 = just check, don't kill
        return True
    except (OSError, ProcessLookupError):
        return False


class Recorder:
    def __init__(self, audio_manager: AudioManager, recordings_dir: str):
        self._am = audio_manager
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        """Check if ffmpeg is currently recording (via PID file)."""
        state = _load_state()
        if state and _pid_alive(state["pid"]):
            return True
        # If state file exists but process is dead, clean up
        if state:
            _clear_state()
            # Restore audio if process died unexpectedly
            self._am.restore_settings()
        return False

    @property
    def last_error(self) -> str:
        """Read the last ffmpeg log for debugging."""
        try:
            with open(LOG_FILE, "r") as f:
                return f.read().strip()[-800:]
        except Exception:
            return ""

    @property
    def output_file(self) -> str:
        state = _load_state()
        return state["output_file"] if state else ""

    @property
    def elapsed_seconds(self) -> float:
        state = _load_state()
        if state and state.get("start_time"):
            return time.time() - state["start_time"]
        return 0.0

    @property
    def has_separate_tracks(self) -> bool:
        state = _load_state()
        return state.get("has_two_audio", False) if state else False

    def start(self, config: RecordingConfig) -> tuple[bool, str]:
        """Start recording. Returns (success, message)."""
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
        output_file = os.path.join(
            self._recordings_dir, f"recording_{timestamp}{suffix}.mp4"
        )

        # Build ffmpeg command
        cmd, has_two_audio = self._build_command(config, output_file)
        if not cmd:
            self._am.restore_settings()
            return False, "Could not build ffmpeg command."

        try:
            # Log stderr to file for debugging
            log_fh = open(LOG_FILE, "w")

            # Start ffmpeg as a detached process so it survives Streamlit reruns
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
                start_new_session=True,  # detach from parent
            )

            # Brief pause to check if ffmpeg crashed immediately
            time.sleep(1.5)
            if process.poll() is not None:
                # ffmpeg already exited — read the log for the error
                log_fh.close()
                err_msg = ""
                try:
                    with open(LOG_FILE, "r") as f:
                        err_msg = f.read().strip()[-500:]  # last 500 chars
                except Exception:
                    pass
                self._am.restore_settings()
                return False, f"ffmpeg exited immediately. Error:\n{err_msg}"

            # Save PID + metadata to disk
            _save_state(
                pid=process.pid,
                output_file=output_file,
                start_time=time.time(),
                has_two_audio=has_two_audio,
            )

            return True, f"Recording started → {os.path.basename(output_file)}"

        except Exception as e:
            self._am.restore_settings()
            return False, f"Failed to start ffmpeg: {e}"

    def stop(self) -> tuple[bool, str]:
        """Stop recording gracefully via PID. Returns (success, message)."""
        state = _load_state()
        if not state:
            return False, "No recording in progress."

        pid = state["pid"]
        output_file = state["output_file"]

        if not _pid_alive(pid):
            _clear_state()
            self._am.restore_settings()
            return False, "Recording process already ended."

        try:
            # Send SIGINT (equivalent to Ctrl+C) for graceful ffmpeg shutdown
            os.kill(pid, signal.SIGINT)

            # Wait for ffmpeg to finalize the file
            waited = 0
            while _pid_alive(pid) and waited < 15:
                time.sleep(0.5)
                waited += 0.5

            # Force kill if still running
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
                time.sleep(1)

        except Exception:
            # Try force kill as last resort
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

        _clear_state()

        # Restore audio settings
        self._am.restore_settings()

        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            return True, f"Saved: {os.path.basename(output_file)} ({size_mb:.1f} MB)"
        else:
            return False, "Recording file not found — ffmpeg may have failed."

    def _build_command(self, cfg: RecordingConfig, output_file: str) -> tuple[list[str], bool]:
        """Build the ffmpeg command. Returns (cmd, has_two_audio)."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
        has_two_audio = False

        s_idx = cfg.screen_device.index if cfg.screen_device else 0
        bh_idx = cfg.blackhole_device.index if cfg.blackhole_device else 0
        mic_idx = cfg.mic_device.index if cfg.mic_device else 0

        if cfg.record_screen:
            if cfg.record_system_audio and cfg.record_mic:
                has_two_audio = True
                cmd += [
                    "-f", "avfoundation",
                    "-capture_cursor", "1",
                    "-capture_mouse_clicks", "1",
                    "-pixel_format", "uyvy422",
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
                    "-pixel_format", "uyvy422",
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
                    "-pixel_format", "uyvy422",
                    "-framerate", str(cfg.framerate),
                    "-i", f"{s_idx}:{mic_idx}",
                    "-map", "0:v", "-map", "0:a",
                    "-c:v", "h264_videotoolbox", "-b:v", cfg.video_bitrate,
                    "-realtime", "true",
                    "-c:a", "aac", "-b:a", cfg.audio_bitrate_mic,
                    "-metadata:s:a:0", "title=Microphone",
                ]

            else:
                cmd += [
                    "-f", "avfoundation",
                    "-capture_cursor", "1",
                    "-capture_mouse_clicks", "1",
                    "-pixel_format", "uyvy422",
                    "-framerate", str(cfg.framerate),
                    "-i", f"{s_idx}:none",
                    "-c:v", "h264_videotoolbox", "-b:v", cfg.video_bitrate,
                    "-realtime", "true",
                ]

        else:
            if cfg.record_system_audio and cfg.record_mic:
                has_two_audio = True
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

        cmd.append(output_file)
        return cmd, has_two_audio


def merge_tracks(
    input_file: str,
    system_vol: float = 0.5,
    mic_vol: float = 0.5,
) -> tuple[bool, str]:
    """Merge two audio tracks into one. Returns (success, output_path)."""
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
