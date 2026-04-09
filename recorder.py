"""
recorder.py — Hybrid screen + audio recorder for macOS.

Architecture:
  - Screen + system audio: macOS native `screencapture -v` (reliable, captures
    both video and system audio from the current output device)
  - Microphone: separate `ffmpeg` process recording from a single avfoundation
    audio input (avoids dual-input conflicts)
  - On stop: merges screen recording + mic track into a single MP4

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

def _save_state(**kwargs):
    with open(STATE_FILE, "w") as f:
        json.dump(kwargs, f)


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
        alive = any(
            _pid_alive(state.get(k, 0))
            for k in ["screen_pid", "mic_pid"]
        )
        if alive:
            return True
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
        return state.get("has_mic_track", False) if state else False

    # ── Start ────────────────────────────────────────────────────────────

    def start(self, config: RecordingConfig) -> tuple[bool, str]:
        if self.is_recording:
            return False, "Already recording."

        if not config.record_screen and not config.record_system_audio and not config.record_mic:
            return False, "Nothing selected to record."

        # Save audio settings before changing anything
        self._am.save_settings()

        # Activate Multi-Output Device so screencapture gets system audio
        # AND the user can still hear it through speakers
        if config.record_system_audio and self._am.has_multi_output():
            self._am.activate_multi_output()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = os.path.join(
            self._recordings_dir, f"recording_{timestamp}.mp4"
        )

        screen_pid = 0
        mic_pid = 0
        screen_tmp = ""
        mic_tmp = ""

        mic_idx = config.mic_device.index if config.mic_device else 2

        # ── 1. Screen + System Audio (macOS native screencapture) ────
        #    screencapture -v captures both video AND audio from the
        #    current system output device (Multi-Output = speakers + BlackHole)
        if config.record_screen or config.record_system_audio:
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

        # ── 2. Microphone (single ffmpeg avfoundation input) ─────────
        #    Separate process, single audio input — no dual-input conflicts
        if config.record_mic:
            mic_tmp = os.path.join(
                self._recordings_dir, f".tmp_mic_{timestamp}.m4a"
            )
            mic_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "avfoundation",
                "-i", f":{mic_idx}",
                "-c:a", "aac", "-b:a", config.audio_bitrate_mic,
                mic_tmp,
            ]

            try:
                log_fh = open(LOG_FILE, "w")
                mic_proc = subprocess.Popen(
                    mic_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fh,
                )
                mic_pid = mic_proc.pid
            except Exception as e:
                if screen_pid:
                    _kill_gracefully(screen_pid)
                self._am.restore_settings()
                return False, f"Failed to start mic capture: {e}"

        # Brief check that processes are alive
        time.sleep(1.0)
        if screen_pid and not _pid_alive(screen_pid):
            if mic_pid:
                _kill_gracefully(mic_pid)
            self._am.restore_settings()
            return False, "Screen capture exited immediately. Check screen recording permissions."

        if mic_pid and not _pid_alive(mic_pid):
            if screen_pid:
                _kill_gracefully(screen_pid)
            self._am.restore_settings()
            err = self.last_error
            return False, f"Mic capture failed: {err}" if err else "Mic capture exited immediately."

        # Save state
        _save_state(
            output_file=output_file,
            start_time=time.time(),
            has_mic_track=(mic_pid > 0),
            screen_pid=screen_pid,
            mic_pid=mic_pid,
            screen_tmp=screen_tmp,
            mic_tmp=mic_tmp,
        )

        sources = []
        if config.record_screen:
            sources.append("screen")
        if config.record_system_audio:
            sources.append("system audio")
        if config.record_mic:
            sources.append("mic")

        return True, f"Recording {' + '.join(sources)}"

    # ── Stop ─────────────────────────────────────────────────────────

    def stop(self) -> tuple[bool, str]:
        state = _load_state()
        if not state:
            return False, "No recording in progress."

        screen_pid = state.get("screen_pid", 0)
        mic_pid = state.get("mic_pid", 0)
        output_file = state["output_file"]
        screen_tmp = state.get("screen_tmp", "")
        mic_tmp = state.get("mic_tmp", "")
        has_mic = state.get("has_mic_track", False)

        # Stop both processes gracefully
        if mic_pid:
            _kill_gracefully(mic_pid, timeout=10)
        if screen_pid:
            _kill_gracefully(screen_pid, timeout=10)

        # Wait a bit for files to finalize
        time.sleep(1)

        _clear_state()
        self._am.restore_settings()

        # ── Merge ────────────────────────────────────────────────────
        has_screen = screen_tmp and os.path.exists(screen_tmp) and os.path.getsize(screen_tmp) > 1000
        has_mic_file = mic_tmp and os.path.exists(mic_tmp) and os.path.getsize(mic_tmp) > 100

        if has_screen and has_mic_file:
            # Merge: screen.mov (video + system audio) + mic.m4a (mic)
            # Result: video + 2 audio tracks (system audio, mic)
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_tmp,    # input 0: video + system audio
                "-i", mic_tmp,       # input 1: mic audio
                "-map", "0:v",       # video from screen
                "-map", "0:a",       # system audio from screen
                "-map", "1:a",       # mic from ffmpeg
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-metadata:s:a:0", "title=System Audio",
                "-metadata:s:a:1", "title=Microphone",
                "-shortest",
                output_file,
            ]

            try:
                result = subprocess.run(
                    merge_cmd, capture_output=True, text=True, timeout=120
                )
                if result.returncode != 0:
                    with open(LOG_FILE, "w") as f:
                        f.write(f"Merge error:\n{result.stderr}")
                    # Fallback: just use screen recording (has video + system audio)
                    os.rename(screen_tmp, output_file)
            except Exception:
                os.rename(screen_tmp, output_file)

            # Clean up temp files
            for tmp in [screen_tmp, mic_tmp]:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass

        elif has_screen:
            # Screen only (video + system audio, no mic)
            os.rename(screen_tmp, output_file)

        elif has_mic_file:
            # Mic only
            output_file = output_file.replace(".mp4", "_audio.m4a")
            os.rename(mic_tmp, output_file)

        else:
            return False, "No recording files found — capture may have failed."

        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            return True, f"Saved: {os.path.basename(output_file)} ({size_mb:.1f} MB)"
        else:
            return False, "Recording file not found after merge."


# ── Merge utility (user-facing, for post-recording) ─────────────────────────

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
