"""
recorder.py — Hybrid screen + audio recorder for macOS.

Architecture (3 separate processes, individually toggleable):
  1. screencapture -v    → video only (.mov)
  2. ffmpeg avfoundation → system audio via BlackHole (.m4a)
  3. ffmpeg avfoundation → microphone (.m4a)
  4. On stop: concatenate segments per source, then merge into MP4

Each source can be toggled on/off mid-recording. Every toggle-on creates
a NEW segment file. All segments are tracked in lists and concatenated
on stop, so no data is lost when toggling.

PID state + segment lists are persisted to disk (survives Streamlit reruns).
A 'session_active' flag keeps the session alive even when all PIDs are 0.
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
    """Merge kwargs into existing state."""
    existing = _load_state() or {}
    existing.update(kwargs)
    with open(STATE_FILE, "w") as f:
        json.dump(existing, f)


def _overwrite_state(data: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)


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


def _kill_gracefully(pid: int, timeout: float = 3):
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGINT)
        waited = 0.0
        while _pid_alive(pid) and waited < timeout:
            time.sleep(0.3)
            waited += 0.3
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.3)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def _log(msg: str):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def _concat_segments(segments: list[str], output_path: str, file_type: str = "audio") -> bool:
    """Concatenate multiple segment files into one using ffmpeg concat demuxer.
    Returns True if output exists and has data."""
    # Filter to segments that actually exist and have data
    valid = [s for s in segments if s and os.path.exists(s) and os.path.getsize(s) > 100]

    if not valid:
        return False

    if len(valid) == 1:
        # Single segment — just rename
        os.rename(valid[0], output_path)
        return os.path.exists(output_path)

    # Write concat list file
    list_file = output_path + ".list.txt"
    with open(list_file, "w") as f:
        for seg in valid:
            # Escape single quotes in path
            safe = seg.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_path,
    ]

    try:
        _log(f"Concat cmd: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            _log(f"Concat error: {result.stderr}")
            # Fallback: use first valid segment
            os.rename(valid[0], output_path)
        return os.path.exists(output_path)
    except Exception as e:
        _log(f"Concat exception: {e}")
        os.rename(valid[0], output_path)
        return os.path.exists(output_path)
    finally:
        try:
            os.remove(list_file)
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
        return state.get("session_active", False)

    @property
    def active_sources(self) -> dict:
        state = _load_state()
        if not state:
            return {"screen": False, "sysaudio": False, "mic": False}
        return {
            "screen": _pid_alive(state.get("screen_pid", 0)),
            "sysaudio": _pid_alive(state.get("sysaudio_pid", 0)),
            "mic": _pid_alive(state.get("mic_pid", 0)),
        }

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
        return state.get("output_file", "") if state else ""

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
        return bool(state.get("sysaudio_segments")) and bool(state.get("mic_segments"))

    # ── Start ────────────────────────────────────────────────────────────

    def start(self, config: RecordingConfig) -> tuple[bool, str]:
        if self.is_recording:
            return False, "Already recording."

        if not config.record_screen and not config.record_system_audio and not config.record_mic:
            return False, "Nothing selected to record."

        self._am.save_settings()

        if config.record_system_audio and self._am.has_multi_output():
            self._am.activate_multi_output()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = os.path.join(
            self._recordings_dir, f"recording_{timestamp}.mp4"
        )

        screen_pid = 0
        sysaudio_pid = 0
        mic_pid = 0
        screen_segments = []
        sysaudio_segments = []
        mic_segments = []

        blackhole_idx = config.blackhole_device.index if config.blackhole_device else 0
        mic_idx = config.mic_device.index if config.mic_device else 2

        with open(LOG_FILE, "w") as f:
            f.write(f"Recording started at {timestamp}\n")

        seg_counter = 0

        # ── 1. Screen Video ─────────────────────────────────────────
        if config.record_screen:
            seg_counter += 1
            screen_tmp = os.path.join(
                self._recordings_dir, f".tmp_screen_{timestamp}_{seg_counter}.mov"
            )
            try:
                proc = subprocess.Popen(
                    ["screencapture", "-v", "-C", screen_tmp],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                screen_pid = proc.pid
                screen_segments.append(screen_tmp)
            except Exception as e:
                self._am.restore_settings()
                return False, f"Failed to start screen capture: {e}"

        # ── 2. System Audio via BlackHole ───────────────────────────
        if config.record_system_audio and config.blackhole_device:
            seg_counter += 1
            sysaudio_tmp = os.path.join(
                self._recordings_dir, f".tmp_sysaudio_{timestamp}_{seg_counter}.m4a"
            )
            sysaudio_pid = self._start_audio_process(
                blackhole_idx, config.audio_bitrate_system, sysaudio_tmp, "System audio"
            )
            if sysaudio_pid == -1:
                if screen_pid:
                    _kill_gracefully(screen_pid)
                self._am.restore_settings()
                return False, "Failed to start system audio capture"
            sysaudio_segments.append(sysaudio_tmp)

        # ── 3. Microphone ───────────────────────────────────────────
        if config.record_mic and config.mic_device:
            seg_counter += 1
            mic_tmp = os.path.join(
                self._recordings_dir, f".tmp_mic_{timestamp}_{seg_counter}.m4a"
            )
            mic_pid = self._start_audio_process(
                mic_idx, config.audio_bitrate_mic, mic_tmp, "Mic"
            )
            if mic_pid == -1:
                if screen_pid:
                    _kill_gracefully(screen_pid)
                if sysaudio_pid > 0:
                    _kill_gracefully(sysaudio_pid)
                self._am.restore_settings()
                return False, "Failed to start mic capture"
            mic_segments.append(mic_tmp)

        # Brief check
        time.sleep(1.5)

        errors = []
        if screen_pid and not _pid_alive(screen_pid):
            errors.append("Screen capture exited immediately.")
        if sysaudio_pid and not _pid_alive(sysaudio_pid):
            errors.append("System audio capture failed.")
        if mic_pid and not _pid_alive(mic_pid):
            errors.append("Mic capture failed.")

        if errors:
            for pid in [screen_pid, sysaudio_pid, mic_pid]:
                if pid and pid > 0:
                    _kill_gracefully(pid)
            self._am.restore_settings()
            return False, " ".join(errors) + f"\n{self.last_error}"

        _overwrite_state({
            "session_active": True,
            "output_file": output_file,
            "start_time": time.time(),
            "timestamp": timestamp,
            "seg_counter": seg_counter,
            "screen_pid": screen_pid,
            "sysaudio_pid": sysaudio_pid,
            "mic_pid": mic_pid,
            "screen_segments": screen_segments,
            "sysaudio_segments": sysaudio_segments,
            "mic_segments": mic_segments,
            "blackhole_idx": blackhole_idx,
            "mic_idx": mic_idx,
            "audio_bitrate_system": config.audio_bitrate_system,
            "audio_bitrate_mic": config.audio_bitrate_mic,
        })

        sources = []
        if config.record_screen:
            sources.append("screen")
        if config.record_system_audio:
            sources.append("system audio")
        if config.record_mic:
            sources.append("mic")

        return True, f"Recording {' + '.join(sources)}"

    # ── Toggle individual sources ────────────────────────────────────

    def toggle_screen(self, enable: bool) -> tuple[bool, str]:
        state = _load_state()
        if not state or not state.get("session_active"):
            return False, "No recording in progress."

        current_pid = state.get("screen_pid", 0)
        is_on = _pid_alive(current_pid)

        if enable and not is_on:
            timestamp = state.get("timestamp", "0")
            seg_counter = state.get("seg_counter", 0) + 1
            screen_tmp = os.path.join(
                self._recordings_dir, f".tmp_screen_{timestamp}_{seg_counter}.mov"
            )
            try:
                proc = subprocess.Popen(
                    ["screencapture", "-v", "-C", screen_tmp],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.5)
                if _pid_alive(proc.pid):
                    segments = state.get("screen_segments", [])
                    segments.append(screen_tmp)
                    _save_state(
                        screen_pid=proc.pid,
                        screen_segments=segments,
                        seg_counter=seg_counter,
                    )
                    _log(f"Screen started (segment {len(segments)}), pid={proc.pid}")
                    return True, "Screen capture started"
                else:
                    return False, "Screen capture exited immediately"
            except Exception as e:
                return False, f"Failed: {e}"

        elif not enable and is_on:
            _kill_gracefully(current_pid, timeout=3)
            _save_state(screen_pid=0)
            _log("Screen stopped")
            return True, "Screen capture stopped"

        return True, "No change"

    def toggle_sysaudio(self, enable: bool) -> tuple[bool, str]:
        state = _load_state()
        if not state or not state.get("session_active"):
            return False, "No recording in progress."

        current_pid = state.get("sysaudio_pid", 0)
        is_on = _pid_alive(current_pid)

        if enable and not is_on:
            if self._am.has_multi_output():
                self._am.activate_multi_output()

            timestamp = state.get("timestamp", "0")
            seg_counter = state.get("seg_counter", 0) + 1
            blackhole_idx = state.get("blackhole_idx", 0)
            bitrate = state.get("audio_bitrate_system", "192k")
            sysaudio_tmp = os.path.join(
                self._recordings_dir, f".tmp_sysaudio_{timestamp}_{seg_counter}.m4a"
            )
            pid = self._start_audio_process(blackhole_idx, bitrate, sysaudio_tmp, "System audio")
            if pid > 0:
                time.sleep(0.5)
                if _pid_alive(pid):
                    segments = state.get("sysaudio_segments", [])
                    segments.append(sysaudio_tmp)
                    _save_state(
                        sysaudio_pid=pid,
                        sysaudio_segments=segments,
                        seg_counter=seg_counter,
                    )
                    _log(f"System audio started (segment {len(segments)})")
                    return True, "System audio started"
                else:
                    return False, "System audio exited immediately"
            return False, "Failed to start system audio"

        elif not enable and is_on:
            _kill_gracefully(current_pid, timeout=3)
            _save_state(sysaudio_pid=0)
            _log("System audio stopped")
            return True, "System audio stopped"

        return True, "No change"

    def toggle_mic(self, enable: bool) -> tuple[bool, str]:
        state = _load_state()
        if not state or not state.get("session_active"):
            return False, "No recording in progress."

        current_pid = state.get("mic_pid", 0)
        is_on = _pid_alive(current_pid)

        if enable and not is_on:
            timestamp = state.get("timestamp", "0")
            seg_counter = state.get("seg_counter", 0) + 1
            mic_idx = state.get("mic_idx", 2)
            bitrate = state.get("audio_bitrate_mic", "128k")
            mic_tmp = os.path.join(
                self._recordings_dir, f".tmp_mic_{timestamp}_{seg_counter}.m4a"
            )
            pid = self._start_audio_process(mic_idx, bitrate, mic_tmp, "Mic")
            if pid > 0:
                time.sleep(0.5)
                if _pid_alive(pid):
                    segments = state.get("mic_segments", [])
                    segments.append(mic_tmp)
                    _save_state(
                        mic_pid=pid,
                        mic_segments=segments,
                        seg_counter=seg_counter,
                    )
                    _log(f"Mic started (segment {len(segments)})")
                    return True, "Microphone started"
                else:
                    return False, "Mic exited immediately"
            return False, "Failed to start microphone"

        elif not enable and is_on:
            _kill_gracefully(current_pid, timeout=3)
            _save_state(mic_pid=0)
            _log("Mic stopped")
            return True, "Microphone stopped"

        return True, "No change"

    # ── Helper ───────────────────────────────────────────────────────

    def _start_audio_process(self, device_idx: int, bitrate: str, output_path: str, label: str) -> int:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-f", "avfoundation",
            "-i", f":{device_idx}",
            "-c:a", "aac", "-b:a", bitrate,
            output_path,
        ]
        try:
            log_fh = open(LOG_FILE, "a")
            log_fh.write(f"{label} cmd: {' '.join(cmd)}\n")
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
            )
            return proc.pid
        except Exception as e:
            _log(f"{label} failed: {e}")
            return -1

    # ── Stop ─────────────────────────────────────────────────────────

    def stop(self) -> tuple[bool, str]:
        state = _load_state()
        if not state:
            return False, "No recording in progress."

        screen_pid = state.get("screen_pid", 0)
        sysaudio_pid = state.get("sysaudio_pid", 0)
        mic_pid = state.get("mic_pid", 0)
        output_file = state.get("output_file", "")
        screen_segments = state.get("screen_segments", [])
        sysaudio_segments = state.get("sysaudio_segments", [])
        mic_segments = state.get("mic_segments", [])

        # Stop all live processes
        for pid in [mic_pid, sysaudio_pid, screen_pid]:
            if pid and _pid_alive(pid):
                _kill_gracefully(pid, timeout=5)

        time.sleep(2)

        _clear_state()
        self._am.restore_settings()

        if not output_file:
            return False, "No output file configured."

        # ── Concatenate segments per source ──────────────────────────
        timestamp = os.path.basename(output_file).replace("recording_", "").replace(".mp4", "")

        screen_combined = os.path.join(self._recordings_dir, f".combined_screen_{timestamp}.mov")
        sysaudio_combined = os.path.join(self._recordings_dir, f".combined_sysaudio_{timestamp}.m4a")
        mic_combined = os.path.join(self._recordings_dir, f".combined_mic_{timestamp}.m4a")

        has_screen = _concat_segments(screen_segments, screen_combined, "video")
        has_sysaudio = _concat_segments(sysaudio_segments, sysaudio_combined, "audio")
        has_mic = _concat_segments(mic_segments, mic_combined, "audio")

        _log(f"--- Stop ---")
        _log(f"screen: {has_screen} ({len(screen_segments)} segments)")
        _log(f"sysaudio: {has_sysaudio} ({len(sysaudio_segments)} segments)")
        _log(f"mic: {has_mic} ({len(mic_segments)} segments)")

        # ── Merge based on what's available ──────────────────────────

        if has_screen and has_sysaudio and has_mic:
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_combined,
                "-i", sysaudio_combined,
                "-i", mic_combined,
                "-map", "0:v", "-map", "1:a", "-map", "2:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-metadata:s:a:0", "title=System Audio",
                "-metadata:s:a:1", "title=Microphone",
                "-shortest",
                output_file,
            ]
            self._run_merge(merge_cmd, output_file, screen_combined)

        elif has_screen and has_sysaudio:
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_combined, "-i", sysaudio_combined,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-metadata:s:a:0", "title=System Audio",
                output_file,
            ]
            self._run_merge(merge_cmd, output_file, screen_combined)

        elif has_screen and has_mic:
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", screen_combined, "-i", mic_combined,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                "-metadata:s:a:0", "title=Microphone",
                output_file,
            ]
            self._run_merge(merge_cmd, output_file, screen_combined)

        elif has_screen:
            os.rename(screen_combined, output_file)

        elif has_sysaudio or has_mic:
            src = sysaudio_combined if has_sysaudio else mic_combined
            output_file = output_file.replace(".mp4", "_audio.m4a")
            os.rename(src, output_file)

        else:
            return False, "No recording files found — capture may have failed."

        # Clean up ALL temp/combined files
        all_temps = screen_segments + sysaudio_segments + mic_segments + [
            screen_combined, sysaudio_combined, mic_combined
        ]
        for tmp in all_temps:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            parts = []
            if has_screen:
                parts.append(f"video ({len(screen_segments)} seg)")
            if has_sysaudio:
                parts.append(f"system audio ({len(sysaudio_segments)} seg)")
            if has_mic:
                parts.append(f"mic ({len(mic_segments)} seg)")
            return True, f"Saved: {os.path.basename(output_file)} ({size_mb:.1f} MB) — {' + '.join(parts)}"
        else:
            return False, "Recording file not found after merge."

    def _run_merge(self, cmd, output_file, fallback_file):
        try:
            _log(f"Merge cmd: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                _log(f"Merge error:\n{result.stderr}")
                if fallback_file and os.path.exists(fallback_file):
                    os.rename(fallback_file, output_file)
                return False
            return True
        except Exception as e:
            _log(f"Merge exception: {e}")
            if fallback_file and os.path.exists(fallback_file):
                os.rename(fallback_file, output_file)
            return False


# ── Merge utility (user-facing) ──────────────────────────────────────────────

def merge_tracks(input_file: str, system_vol: float = 0.5, mic_vol: float = 0.5) -> tuple[bool, str]:
    output_file = input_file.replace(".mp4", "_merged.mp4")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", input_file,
        "-filter_complex",
        f"[0:a:0]volume={system_vol}[sys];[0:a:1]volume={mic_vol}[mic];"
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
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", filepath],
            capture_output=True, text=True, timeout=10,
        )
        return len([l for l in result.stdout.strip().splitlines() if l.strip()])
    except Exception:
        return 0
