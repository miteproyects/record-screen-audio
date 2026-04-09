"""
recorder.py — Hybrid screen + audio recorder for macOS.

Uses macOS native `screencapture -v` for reliable screen capture
(avfoundation's AVCaptureScreenInput is deprecated and produces black
frames on recent macOS), and ffmpeg for audio recording (BlackHole +
microphone). On stop, merges video + audio into a single MP4.

PID state is persisted to disk so recording survives Streamlit reruns.
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

# Persistent files (survive Streamlit reruns)
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


# ── State persistence ────────────────────────────────────────────────────────

def _save_state(
    output_file: str,
    start_time: float,
    has_two_audio: bool,
    screen_pid: int = 0,
    audio_pid: int = 0,
    screen_tmp: str = "",
    audio_tmp: str = "",
):
    with open(STATE_FILE, "w") as f:
        json.dump({
            "screen_pid": screen_pid,
            "audio_pid": audio_pid,
            "output_file": output_file,
            "screen_tmp": screen_tmp,
            "audio_tmp": audio_tmp,
            "start_time": start_time,
            "has_two_audio": has_two_audio,
        }, f)


def _load_state() -> Optional[dict]:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill_gracefully(pid: int, timeout: float = 10):
    """Send SIGINT, wait, then SIGKILL if needed."""
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGINT)
        waited = 0.0
        while _pid_alive(pid) and waited < timeout:
            time.sleep(0.5)
            waited += 0.5
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


# ── Recorder ─────────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self, audio_manager: AudioManager, recordings_dir: str):
        self._am = audio_manager
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        state = _load_state()
        if not state:
            return False
        s_alive = _pid_alive(state.get("screen_pid", 0))
        a_alive = _pid_alive(state.get("audio_pid", 0))
        if s_alive or a_alive:
            return True
        # Both dead — clean up
        _clear_state()
        self._am.restore_settings()
        return False

    @property
    def last_error(self) -> str:
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

    # ── Start ────────────────────────────────────────────────────────────

    def start(self, config: RecordingConfig) -> tuple[bool, str]:
        if self.is_recording:
            return False, "Already recording."

        if not config.record_screen and not config.record_system_audio and not config.record_mic:
            return False, "Nothing selected to record."

        # Save audio settings before changing anything
        self._am.save_settings()

        # Activate Multi-Output Device if recording system audio
        if config.record_system_audio and self._am.has_multi_output():
            self._am.activate_multi_output()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = os.path.join(
            self._recordings_dir, f"recording_{timestamp}.mp4"
        )

        screen_pid = 0
        audio_pid = 0
        screen_tmp = ""
        audio_tmp = ""
        has_two_audio = False

        bh_idx = config.blackhole_device.index if config.blackhole_device else 0
        mic_idx = config.mic_device.index if config.mic_device else 2

        # ── Screen capture (macOS native screencapture) ──────────────
        if config.record_screen:
            screen_tmp = os.path.join(
                self._recordings_dir, f".tmp_screen_{timestamp}.mov"
            )
            try:
                screen_proc = subprocess.Popen(
                    ["screencapture", "-v", "-C", screen_tmp],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                screen_pid = screen_proc.pid
            except Exception as e:
                self._am.restore_settings()
                return False, f"Failed to start screen capture: {e}"

        # ── Audio capture (ffmpeg avfoundation) ──────────────────────
        has_audio = config.record_system_audio or config.record_mic
        if has_audio:
            audio_tmp = os.path.join(
                self._recordings_dir, f".tmp_audio_{timestamp}.m4a"
            )
            audio_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]

            if config.record_system_audio and config.record_mic:
                has_two_audio = True
                audio_cmd += [
                    "-f", "avfoundation", "-i", f":{bh_idx}",
                    "-f", "avfoundation", "-i", f":{mic_idx}",
                    "-map", "0:a", "-map", "1:a",
                    "-c:a:0", "aac", "-b:a:0", config.audio_bitrate_system,
                    "-c:a:1", "aac", "-b:a:1", config.audio_bitrate_mic,
                    "-metadata:s:a:0", "title=System Audio (BlackHole)",
                    "-metadata:s:a:1", "title=Microphone",
                ]
            elif config.record_system_audio:
                audio_cmd += [
                    "-f", "avfoundation", "-i", f":{bh_idx}",
                    "-c:a", "aac", "-b:a", config.audio_bitrate_system,
                    "-metadata:s:a:0", "title=System Audio (BlackHole)",
                ]
            elif config.record_mic:
                audio_cmd += [
                    "-f", "avfoundation", "-i", f":{mic_idx}",
                    "-c:a", "aac", "-b:a", config.audio_bitrate_mic,
                    "-metadata:s:a:0", "title=Microphone",
                ]

            audio_cmd.append(audio_tmp)

            try:
                log_fh = open(LOG_FILE, "w")
                audio_proc = subprocess.Popen(
                    audio_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fh,
                )
                audio_pid = audio_proc.pid
            except Exception as e:
                # Kill screen capture if audio fails
                if screen_pid:
                    _kill_gracefully(screen_pid)
                self._am.restore_settings()
                return False, f"Failed to start audio capture: {e}"

        # Brief check that processes are alive
        time.sleep(1.0)
        if screen_pid and not _pid_alive(screen_pid):
            if audio_pid:
                _kill_gracefully(audio_pid)
            self._am.restore_settings()
            return False, "Screen capture exited immediately. Check screen recording permissions."

        if audio_pid and not _pid_alive(audio_pid):
            if screen_pid:
                _kill_gracefully(screen_pid)
            self._am.restore_settings()
            err = self.last_error
            return False, f"Audio capture failed: {err}" if err else "Audio capture exited immediately."

        # Save state
        _save_state(
            output_file=output_file,
            start_time=time.time(),
            has_two_audio=has_two_audio,
            screen_pid=screen_pid,
            audio_pid=audio_pid,
            screen_tmp=screen_tmp,
            audio_tmp=audio_tmp,
        )

        sources = []
        if config.record_screen:
            sources.append("screen")
        if config.record_system_audio:
            sources.append("system audio")
        if config.record_mic:
            sources.append("mic")

        return True, f"Recording {' + '.join(sources)} → {os.path.basename(output_file)}"

    # ── Stop ─────────────────────────────────────────────────────────

    def stop(self) -> tuple[bool, str]:
        state = _load_state()
        if not state:
            return False, "No recording in progress."

        screen_pid = state.get("screen_pid", 0)
        audio_pid = state.get("audio_pid", 0)
        output_file = state["output_file"]
        screen_tmp = state.get("screen_tmp", "")
        audio_tmp = state.get("audio_tmp", "")
        has_two_audio = state.get("has_two_audio", False)

        # Stop both processes
        if screen_pid:
            # screencapture stops on SIGINT and finalizes the file
            _kill_gracefully(screen_pid, timeout=10)

        if audio_pid:
            _kill_gracefully(audio_pid, timeout=10)

        _clear_state()

        # Restore audio settings
        self._am.restore_settings()

        # ── Merge screen + audio into final MP4 ─────────────────────
        has_screen = screen_tmp and os.path.exists(screen_tmp) and os.path.getsize(screen_tmp) > 0
        has_audio = audio_tmp and os.path.exists(audio_tmp) and os.path.getsize(audio_tmp) > 0

        if has_screen and has_audio:
            # Merge video + audio
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_tmp,
                "-i", audio_tmp,
                "-map", "0:v",
            ]
            if has_two_audio:
                merge_cmd += ["-map", "1:a:0", "-map", "1:a:1"]
            else:
                merge_cmd += ["-map", "1:a"]

            merge_cmd += [
                "-c:v", "copy",
                "-c:a", "copy",
                "-shortest",
                output_file,
            ]

            try:
                result = subprocess.run(
                    merge_cmd, capture_output=True, text=True, timeout=120
                )
                if result.returncode != 0:
                    # Merge failed — log error, keep tmp files
                    with open(LOG_FILE, "a") as f:
                        f.write(f"\nMerge error:\n{result.stderr}")
                    # Fall back: just use the screen recording
                    os.rename(screen_tmp, output_file)
            except Exception as e:
                os.rename(screen_tmp, output_file)

            # Clean up tmp files
            for tmp in [screen_tmp, audio_tmp]:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass

        elif has_screen:
            # Screen only — rename tmp to output
            os.rename(screen_tmp, output_file)

        elif has_audio:
            # Audio only — rename tmp to output
            output_file = output_file.replace(".mp4", "_audio.m4a")
            os.rename(audio_tmp, output_file)

        else:
            return False, "No recording files found — both screen and audio may have failed."

        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            return True, f"Saved: {os.path.basename(output_file)} ({size_mb:.1f} MB)"
        else:
            return False, "Recording file not found after merge."


# ── Merge utility ────────────────────────────────────────────────────────────

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
