"""
Record Screen + Audio — OpenTF
Streamlit UI for recording macOS screen, system audio, and microphone.
"""

import streamlit as st
import os
import time
import glob
from datetime import datetime, timedelta
from pathlib import Path

from audio_manager import AudioManager
from recorder import Recorder, RecordingConfig, merge_tracks, get_track_count

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Record Screen + Audio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* ── Global ─────────────────────────────────────────────── */
    .block-container { padding-top: 2rem; }

    /* ── Status Cards ───────────────────────────────────────── */
    .status-card {
        padding: 1rem 1.25rem;
        border-radius: 12px;
        margin-bottom: 0.75rem;
        border: 1px solid rgba(128,128,128,0.15);
    }
    .status-ok   { background: linear-gradient(135deg, #e8f5e9 0%, #f1f8e9 100%); border-left: 4px solid #4caf50; }
    .status-warn { background: linear-gradient(135deg, #fff8e1 0%, #fff3e0 100%); border-left: 4px solid #ff9800; }
    .status-err  { background: linear-gradient(135deg, #ffebee 0%, #fce4ec 100%); border-left: 4px solid #f44336; }

    .status-card .label { font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
    .status-card .value { font-size: 1rem; font-weight: 600; color: #222; margin-top: 2px; }

    /* ── Recording Indicator ────────────────────────────────── */
    @keyframes pulse-red {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
    }
    .rec-dot {
        display: inline-block;
        width: 14px; height: 14px;
        background: #f44336;
        border-radius: 50%;
        animation: pulse-red 1.2s ease-in-out infinite;
        margin-right: 8px;
        vertical-align: middle;
    }
    .rec-banner {
        background: linear-gradient(135deg, #ff1744 0%, #d50000 100%);
        color: white;
        padding: 1rem 1.5rem;
        border-radius: 12px;
        font-size: 1.1rem;
        font-weight: 600;
        margin-bottom: 1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .rec-timer {
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 1.4rem;
        font-weight: 700;
    }

    /* ── Device Cards ───────────────────────────────────────── */
    .device-header {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #888;
        margin-bottom: 4px;
    }

    /* ── Sidebar ────────────────────────────────────────────── */
    section[data-testid="stSidebar"] .block-container { padding-top: 1rem; }
    section[data-testid="stSidebar"] hr { margin: 0.75rem 0; }

    /* ── File List ──────────────────────────────────────────── */
    .file-row {
        padding: 0.6rem 1rem;
        border-radius: 8px;
        background: #f8f9fa;
        margin-bottom: 0.5rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border: 1px solid #eee;
    }
    .file-name { font-weight: 600; font-size: 0.9rem; }
    .file-meta { font-size: 0.8rem; color: #888; }

    /* ── Toggle Buttons ─────────────────────────────────────── */
    .toggle-row {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 0.8rem 1rem;
        border-radius: 10px;
        margin-bottom: 0.5rem;
        transition: background 0.2s;
    }
    .toggle-row:hover { background: #f5f5f5; }
    .toggle-icon { font-size: 1.5rem; }
    .toggle-label { font-weight: 600; }
    .toggle-desc { font-size: 0.8rem; color: #777; }

    /* ── Buttons ─────────────────────────────────────────────── */
    .stButton > button {
        border-radius: 10px;
        font-weight: 600;
        padding: 0.5rem 1.5rem;
        transition: all 0.2s;
    }
</style>
""", unsafe_allow_html=True)


# ── Session State Initialization ─────────────────────────────────────────────

def init_state():
    """Initialize all session state variables."""
    defaults = {
        "audio_manager": None,
        "recorder": None,
        "video_devices": [],
        "audio_devices": [],
        "recording": False,
        "start_time": None,
        "last_message": "",
        "last_message_type": "info",  # info, success, error, warning
        # Toggle states
        "opt_screen": True,
        "opt_system_audio": True,
        "opt_mic": True,
        # Device selections (index in the device list)
        "sel_screen": None,
        "sel_blackhole": None,
        "sel_mic": None,
        # Merge
        "merge_file": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ── Lazy Init ────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "Recordings")


def get_audio_manager() -> AudioManager:
    if st.session_state.audio_manager is None:
        st.session_state.audio_manager = AudioManager()
    return st.session_state.audio_manager


def get_recorder() -> Recorder:
    if st.session_state.recorder is None:
        st.session_state.recorder = Recorder(get_audio_manager(), RECORDINGS_DIR)
    return st.session_state.recorder


def refresh_devices():
    am = get_audio_manager()
    v, a = am.discover_devices()
    st.session_state.video_devices = v
    st.session_state.audio_devices = a
    # Auto-select defaults
    bh = am.find_blackhole(a)
    mic = am.find_default_mic(a)
    scr = am.find_screen(v)
    if bh and st.session_state.sel_blackhole is None:
        st.session_state.sel_blackhole = bh.index
    if mic and st.session_state.sel_mic is None:
        st.session_state.sel_mic = mic.index
    if scr and st.session_state.sel_screen is None:
        st.session_state.sel_screen = scr.index


# Initial device discovery
if not st.session_state.video_devices and not st.session_state.audio_devices:
    refresh_devices()


# ── Helper: format seconds ───────────────────────────────────────────────────

def fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def fmt_size(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    elif bytes_ < 1024**2:
        return f"{bytes_/1024:.1f} KB"
    elif bytes_ < 1024**3:
        return f"{bytes_/1024**2:.1f} MB"
    else:
        return f"{bytes_/1024**3:.2f} GB"


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## ⚙️ Settings")

    # ── Dependency Status ────────────────────────────────────────────────
    st.markdown("#### System Status")
    deps = get_audio_manager().check_dependencies()

    for name, ok in deps.items():
        icon = "✅" if ok else "❌"
        st.markdown(f"{icon} **{name}**")

    if not all(deps.values()):
        st.warning("Run `./setup.sh` or install missing dependencies with Homebrew.")

    has_mo = get_audio_manager().has_multi_output()
    icon_mo = "✅" if has_mo else "⚠️"
    st.markdown(f"{icon_mo} **Multi-Output Device**")
    if not has_mo:
        st.caption("Create in Audio MIDI Setup so you can hear audio while recording.")

    st.divider()

    # ── Saved Audio Settings ─────────────────────────────────────────────
    snap = get_audio_manager().snapshot
    if snap:
        st.markdown("#### 💾 Saved Audio State")
        st.caption("Will be restored when recording stops.")
        st.code(
            f"Output : {snap.output_device}\n"
            f"Input  : {snap.input_device}\n"
            f"Volume : {snap.output_volume}%",
            language=None,
        )
        st.divider()

    # ── Device Selection ─────────────────────────────────────────────────
    st.markdown("#### 🎛️ Devices")

    if st.button("🔄 Refresh Devices", use_container_width=True):
        refresh_devices()
        st.rerun()

    vdevs = st.session_state.video_devices
    adevs = st.session_state.audio_devices

    if vdevs:
        screen_options = {d.index: d.name for d in vdevs}
        default_scr = st.session_state.sel_screen if st.session_state.sel_screen in screen_options else list(screen_options.keys())[0]
        st.session_state.sel_screen = st.selectbox(
            "Screen",
            options=list(screen_options.keys()),
            format_func=lambda x: screen_options[x],
            index=list(screen_options.keys()).index(default_scr),
        )

    if adevs:
        audio_options = {d.index: d.name for d in adevs}

        # BlackHole selector
        bh_default = st.session_state.sel_blackhole if st.session_state.sel_blackhole in audio_options else list(audio_options.keys())[0]
        st.session_state.sel_blackhole = st.selectbox(
            "System Audio (BlackHole)",
            options=list(audio_options.keys()),
            format_func=lambda x: audio_options[x],
            index=list(audio_options.keys()).index(bh_default),
        )

        # Mic selector
        mic_default = st.session_state.sel_mic if st.session_state.sel_mic in audio_options else list(audio_options.keys())[0]
        st.session_state.sel_mic = st.selectbox(
            "Microphone",
            options=list(audio_options.keys()),
            format_func=lambda x: audio_options[x],
            index=list(audio_options.keys()).index(mic_default),
        )

    st.divider()

    # ── Advanced ─────────────────────────────────────────────────────────
    with st.expander("Advanced Settings"):
        framerate = st.select_slider("Frame Rate", options=[15, 24, 30, 60], value=30)
        video_br = st.select_slider("Video Bitrate", options=["4000k", "6000k", "8000k", "12000k", "16000k"], value="8000k")
        audio_br_sys = st.select_slider("System Audio Bitrate", options=["96k", "128k", "192k", "256k", "320k"], value="192k")
        audio_br_mic = st.select_slider("Mic Bitrate", options=["64k", "96k", "128k", "192k"], value="128k")

    st.divider()
    st.caption("Record Screen + Audio — OpenTF")
    st.caption(f"Recordings saved to: `Recordings/`")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════

# ── Header ───────────────────────────────────────────────────────────────────
st.markdown("# 🎬 Record Screen + Audio")
st.caption("Record your Mac screen, system audio (Teams/Zoom/YouTube), and microphone — all in one.")

# ── Recording Banner ─────────────────────────────────────────────────────────
recorder = get_recorder()

if recorder.is_recording:
    elapsed = recorder.elapsed_seconds
    st.markdown(
        f"""<div class="rec-banner">
            <span><span class="rec-dot"></span> RECORDING</span>
            <span class="rec-timer">{fmt_duration(elapsed)}</span>
        </div>""",
        unsafe_allow_html=True,
    )

# ── Status Message ───────────────────────────────────────────────────────────
if st.session_state.last_message:
    msg_type = st.session_state.last_message_type
    if msg_type == "success":
        st.success(st.session_state.last_message)
    elif msg_type == "error":
        st.error(st.session_state.last_message)
    elif msg_type == "warning":
        st.warning(st.session_state.last_message)
    else:
        st.info(st.session_state.last_message)

# ── Recording Controls ───────────────────────────────────────────────────────
st.markdown("### What to Record")

col1, col2, col3 = st.columns(3)

with col1:
    st.session_state.opt_screen = st.toggle(
        "🖥️ Screen",
        value=st.session_state.opt_screen,
        help="Record your Mac screen (full display)",
        disabled=recorder.is_recording,
    )
    if st.session_state.opt_screen:
        scr_name = ""
        for d in st.session_state.video_devices:
            if d.index == st.session_state.sel_screen:
                scr_name = d.name
                break
        st.caption(f"→ {scr_name or 'Auto-detect'}")

with col2:
    st.session_state.opt_system_audio = st.toggle(
        "🔊 System Audio",
        value=st.session_state.opt_system_audio,
        help="Capture Teams/Zoom/YouTube audio via BlackHole",
        disabled=recorder.is_recording,
    )
    if st.session_state.opt_system_audio:
        bh_name = ""
        for d in st.session_state.audio_devices:
            if d.index == st.session_state.sel_blackhole:
                bh_name = d.name
                break
        st.caption(f"→ {bh_name or 'Auto-detect'}")

with col3:
    st.session_state.opt_mic = st.toggle(
        "🎙️ Microphone",
        value=st.session_state.opt_mic,
        help="Record your voice / room noise",
        disabled=recorder.is_recording,
    )
    if st.session_state.opt_mic:
        mic_name = ""
        for d in st.session_state.audio_devices:
            if d.index == st.session_state.sel_mic:
                mic_name = d.name
                break
        st.caption(f"→ {mic_name or 'Auto-detect'}")

st.markdown("")

# ── Start / Stop Buttons ────────────────────────────────────────────────────
btn_col1, btn_col2, _ = st.columns([1, 1, 2])

with btn_col1:
    if not recorder.is_recording:
        if st.button("⏺️ Start Recording", type="primary", use_container_width=True):
            # Build config
            cfg = RecordingConfig(
                record_screen=st.session_state.opt_screen,
                record_system_audio=st.session_state.opt_system_audio,
                record_mic=st.session_state.opt_mic,
                framerate=framerate if "framerate" in dir() else 30,
                video_bitrate=video_br if "video_br" in dir() else "8000k",
                audio_bitrate_system=audio_br_sys if "audio_br_sys" in dir() else "192k",
                audio_bitrate_mic=audio_br_mic if "audio_br_mic" in dir() else "128k",
            )

            # Set device objects
            for d in st.session_state.video_devices:
                if d.index == st.session_state.sel_screen:
                    cfg.screen_device = d
                    break
            for d in st.session_state.audio_devices:
                if d.index == st.session_state.sel_blackhole:
                    cfg.blackhole_device = d
                if d.index == st.session_state.sel_mic:
                    cfg.mic_device = d

            ok, msg = recorder.start(cfg)
            st.session_state.recording = ok
            st.session_state.last_message = msg
            st.session_state.last_message_type = "success" if ok else "error"
            st.rerun()

with btn_col2:
    if recorder.is_recording:
        if st.button("⏹️ Stop Recording", type="secondary", use_container_width=True):
            ok, msg = recorder.stop()
            st.session_state.recording = False
            st.session_state.last_message = msg
            st.session_state.last_message_type = "success" if ok else "error"
            st.rerun()

# Auto-refresh while recording to update the timer
if recorder.is_recording:
    time.sleep(1)
    st.rerun()


# ── Recordings List ──────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📁 Recordings")

recordings = sorted(
    glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4")),
    key=os.path.getmtime,
    reverse=True,
)

if not recordings:
    st.info("No recordings yet. Hit **Start Recording** to begin!")
else:
    for filepath in recordings:
        fname = os.path.basename(filepath)
        fsize = os.path.getsize(filepath)
        fdate = datetime.fromtimestamp(os.path.getmtime(filepath))
        tracks = get_track_count(filepath)
        is_merged = "_merged" in fname

        with st.container():
            rc1, rc2, rc3, rc4 = st.columns([3, 1, 1, 1.5])

            with rc1:
                icon = "🎞️" if not is_merged else "🔗"
                st.markdown(f"**{icon} {fname}**")
                st.caption(f"{fdate.strftime('%b %d, %Y %I:%M %p')}  ·  {fmt_size(fsize)}  ·  {tracks} audio track(s)")

            with rc2:
                # Download button
                with open(filepath, "rb") as f:
                    st.download_button(
                        "⬇️ Download",
                        data=f,
                        file_name=fname,
                        mime="video/mp4",
                        key=f"dl_{fname}",
                        use_container_width=True,
                    )

            with rc3:
                if tracks >= 2 and not is_merged:
                    if st.button("🔗 Merge", key=f"merge_{fname}", use_container_width=True):
                        st.session_state.merge_file = filepath
                        st.rerun()

            with rc4:
                # Open in Finder
                if st.button("📂 Reveal", key=f"reveal_{fname}", use_container_width=True):
                    os.system(f'open -R "{filepath}"')


# ── Merge Dialog ─────────────────────────────────────────────────────────────
if st.session_state.merge_file and os.path.exists(st.session_state.merge_file):
    st.markdown("---")
    st.markdown("### 🔗 Merge Audio Tracks")
    st.caption(f"File: `{os.path.basename(st.session_state.merge_file)}`")

    mcol1, mcol2 = st.columns(2)
    with mcol1:
        sys_vol = st.slider("System Audio Volume", 0.0, 1.0, 0.5, 0.05, key="merge_sys_vol")
    with mcol2:
        mic_vol = st.slider("Microphone Volume", 0.0, 1.0, 0.5, 0.05, key="merge_mic_vol")

    preset_col1, preset_col2, preset_col3 = st.columns(3)
    with preset_col1:
        if st.button("Equal Mix (50/50)", use_container_width=True):
            st.session_state.merge_sys_vol = 0.5
            st.session_state.merge_mic_vol = 0.5
            st.rerun()
    with preset_col2:
        if st.button("System Louder (70/30)", use_container_width=True):
            st.session_state.merge_sys_vol = 0.7
            st.session_state.merge_mic_vol = 0.3
            st.rerun()
    with preset_col3:
        if st.button("Mic Louder (30/70)", use_container_width=True):
            st.session_state.merge_sys_vol = 0.3
            st.session_state.merge_mic_vol = 0.7
            st.rerun()

    merge_btn_col, cancel_col, _ = st.columns([1, 1, 2])
    with merge_btn_col:
        if st.button("✅ Merge Now", type="primary", use_container_width=True):
            with st.spinner("Merging audio tracks..."):
                ok, result = merge_tracks(
                    st.session_state.merge_file,
                    system_vol=sys_vol,
                    mic_vol=mic_vol,
                )
            if ok:
                st.session_state.last_message = f"Merged: {os.path.basename(result)}"
                st.session_state.last_message_type = "success"
            else:
                st.session_state.last_message = f"Merge failed: {result}"
                st.session_state.last_message_type = "error"
            st.session_state.merge_file = None
            st.rerun()

    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.session_state.merge_file = None
            st.rerun()
