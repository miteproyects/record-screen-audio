"""
recorder.py — Hybrid screen + audio recorder for macOS.

Architecture (3 separate processes, no conflicts):
  1. screencapture -v    → video only (.mov)
  2. ffmpeg avfoundation → system audio via BlackHole (.m4a)
  3. ffmpeg avfoundation → microphone (.m4a)
  4. On stop: merge all into one MP4 with 2 audio tracks

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
            for k in ["screen_pid", "sysaudio_pid", "mic_pid"]
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
        if not state:
            return False
        return state.get("has_sysaudio", False) and state.get("has_mic", False)

    # ── Start ────────────────────────────────────────────────────────────

    def start(self, config: RecordingConfig) -> tuple[bool, str]:
        if self.is_recording:
            return False, "Already recording."

        if not config.record_screen and not config.record_system_audio and not config.record_mic:
            return False, "Nothing selected to record."

        # Save audio settings before changing anything
        self._am.save_settings()

        # Activate Multi-Output Device so user can still hear audio
        # while BlackHole captures it
        if config.record_system_audio and self._am.has_multi_output():
            self._am.activate_multi_output()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = os.path.join(
            self._recordings_dir, f"recording_{timestamp}.mp4"
        )

        screen_pid = 0
        sysaudio_pid = 0
        mic_pid = 0
        screen_tmp = ""
        sysaudio_tmp = ""
        mic_tmp = ""

        blackhole_idx = config.blackhole_device.index if config.blackhole_device else 0
        mic_idx = config.mic_device.index if config.mic_device else 2

        # Clear log
        with open(LOG_FILE, "w") as f:
            f.write(f"Recording started at {timestamp}\n")

        # ── 1. Screen Video (macOS native screencapture) ────────────
        #    screencapture -v captures video only (no audio from CLI)
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

        # ── 2. System Audio via BlackHole (separate ffmpeg) ─────────
        #    Single avfoundation audio input from BlackHole device
        if config.record_system_audio and config.blackhole_device:
            sysaudio_tmp = os.path.join(
                self._recordings_dir, f".tmp_sysaudio_{timestamp}.m4a"
            )
            sysaudio_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "avfoundation",
                "-i", f":{blackhole_idx}",
                "-c:a", "aac", "-b:a", config.audio_bitrate_system,
                sysaudio_tmp,
            ]

            try:
                log_fh = open(LOG_FILE, "a")
                log_fh.write(f"System audio cmd: {' '.join(sysaudio_cmd)}\n")
                sysaudio_proc = subprocess.Popen(
                    sysaudio_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fh,
                )
                sysaudio_pid = sysaudio_proc.pid
            except Exception as e:
                if screen_pid:
                    _kill_gracefully(screen_pid)
                self._am.restore_settings()
                return False, f"Failed to start system audio capture: {e}"

        # ── 3. Microphone (separate ffmpeg, different device) ───────
        #    Single avfoundation audio input from mic device
        if config.record_mic and config.mic_device:
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
                log_fh = open(LOG_FILE, "a")
                log_fh.write(f"Mic cmd: {' '.join(mic_cmd)}\n")
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
                if sysaudio_pid:
                    _kill_gracefully(sysaudio_pid)
                self._am.restore_settings()
                return False, f"Failed to start mic capture: {e}"

        # Brief check that processes are alive
        time.sleep(1.5)

        errors = []
        if screen_pid and not _pid_alive(screen_pid):
            errors.append("Screen capture exited immediately.")
        if sysaudio_pid and not _pid_alive(sysaudio_pid):
            errors.append("System audio capture failed.")
        if mic_pid and not _pid_alive(mic_pid):
            errors.append("Mic capture failed.")

        if errors:
            # Kill all surviving processes
            for pid in [screen_pid, sysaudio_pid, mic_pid]:
                if pid:
                    _kill_gracefully(pid)
            self._am.restore_settings()
            err_detail = self.last_error
            return False, " ".join(errors) + (f"\n{err_detail}" if err_detail else "")

        # Save state
        _save_state(
            output_file=output_file,
            start_time=time.time(),
            has_sysaudio=(sysaudio_pid > 0),
            has_mic=(mic_pid > 0),
            screen_pid=screen_pid,
            sysaudio_pid=sysaudio_pid,
            mic_pid=mic_pid,
            screen_tmp=screen_tmp,
            sysaudio_tmp=sysaudio_tmp,
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
        sysaudio_pid = state.get("sysaudio_pid", 0)
        mic_pid = state.get("mic_pid", 0)
        output_file = state["output_file"]
        screen_tmp = state.get("screen_tmp", "")
        sysaudio_tmp = state.get("sysaudio_tmp", "")
        mic_tmp = state.get("mic_tmp", "")

        # Stop all processes gracefully (audio first, then screen)
        if mic_pid:
            _kill_gracefully(mic_pid, timeout=10)
        if sysaudio_pid:
            _kill_gracefully(sysaudio_pid, timeout=10)
        if screen_pid:
            _kill_gracefully(screen_pid, timeout=10)

        # Wait for files to finalize
        time.sleep(2)

        _clear_state()
        self._am.restore_settings()

        # ── Check what files we have ─────────────────────────────────
        has_screen = screen_tmp and os.path.exists(screen_tmp) and os.path.getsize(screen_tmp) > 1000
        has_sysaudio = sysaudio_tmp and os.path.exists(sysaudio_tmp) and os.path.getsize(sysaudio_tmp) > 100
        has_mic_file = mic_tmp and os.path.exists(mic_tmp) and os.path.getsize(mic_tmp) > 100

        # Log what we found
        with open(LOG_FILE, "a") as f:
            f.write(f"\n--- Stop ---\n")
            f.write(f"screen: {has_screen} ({screen_tmp})\n")
            f.write(f"sysaudio: {has_sysaudio} ({sysaudio_tmp})\n")
            f.write(f"mic: {has_mic_file} ({mic_tmp})\n")

        # ── Merge based on what's available ──────────────────────────

        if has_screen and has_sysaudio and has_mic_file:
            # Full merge: video + system audio + mic → MP4 with 2 audio tracks
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_tmp,       # input 0: video
                "-i", sysaudio_tmp,     # input 1: system audio
                "-i", mic_tmp,          # input 2: mic audio
                "-map", "0:v",          # video from screen
                "-map", "1:a",          # system audio from BlackHole
                "-map", "2:a",          # mic audio
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-metadata:s:a:0", "title=System Audio",
                "-metadata:s:a:1", "title=Microphone",
                "-shortest",
                output_file,
            ]
            ok = self._run_merge(merge_cmd, output_file, screen_tmp)

        elif has_screen and has_sysaudio:
            # Video + system audio only
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_tmp,
                "-i", sysaudio_tmp,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-metadata:s:a:0", "title=System Audio",
                output_file,
            ]
            ok = self._run_merge(merge_cmd, output_file, screen_tmp)

        elif has_screen and has_mic_file:
            # Video + mic only
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_tmp,
                "-i", mic_tmp,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-metadata:s:a:0", "title=Microphone",
                output_file,
            ]
            ok = self._run_merge(merge_cmd, output_file, screen_tmp)

        elif has_screen:
            # Video only (no audio)
            os.rename(screen_tmp, output_file)

        elif has_sysaudio or has_mic_file:
            # Audio only
            audio_file = sysaudio_tmp if has_sysaudio else mic_tmp
            output_file = output_file.replace(".mp4", "_audio.m4a")
            os.rename(audio_file, output_file)

        else:
            return False, "No recording files found — capture may have failed."

        # Clean up temp files
        for tmp in [screen_tmp, sysaudio_tmp, mic_tmp]:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            parts = []
            if has_screen:
                parts.append("video")
            if has_sysaudio:
                parts.append("system audio")
            if has_mic_file:
                parts.append("mic")
            return True, f"Saved: {os.path.basename(output_file)} ({size_mb:.1f} MB) — {' + '.join(parts)}"
        else:
            return False, "Recording file not found after merge."

    def _run_merge(self, cmd, output_file, fallback_file):
        """Run ffmpeg merge command with fallback."""
        try:
            with open(LOG_FILE, "a") as f:
                f.write(f"Merge cmd: {' '.join(cmd)}\n")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                with open(LOG_FILE, "a") as f:
                    f.write(f"Merge error:\n{result.stderr}\n")
                # Fallback: just use video
                if fallback_file and os.path.exists(fallback_file):
                    os.rename(fallback_file, output_file)
                return False
            return True
        except Exception as e:
            with open(LOG_FILE, "a") as f:
                f.write(f"Merge exception: {e}\n")
            if fallback_file and os.path.exists(fallback_file):
                os.rename(fallback_file, output_file)
            return False


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
