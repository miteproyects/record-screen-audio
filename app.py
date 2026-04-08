"""
Record Screen + Audio — OpenTF
Streamlit UI for recording macOS screen, system audio, and microphone.
"""

import streamlit as st
import os
import time
import glob
from datetime import datetime
from pathlib import Path

from audio_manager import AudioManager
from recorder import Recorder, RecordingConfig, merge_tracks, get_track_count

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Record Screen + Audio — OpenTF",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* ── Reset & Global ────────────────────────────────────── */
    .block-container {
        padding: 2rem 3rem 3rem 3rem;
        max-width: 1100px;
    }
    h1, h2, h3 { font-weight: 700 !important; }

    /* ── Hero Header ───────────────────────────────────────── */
    .hero {
        text-align: center;
        padding: 1.5rem 0 0.5rem 0;
    }
    .hero h1 {
        font-size: 2.2rem;
        margin-bottom: 0.25rem;
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .hero p {
        color: #666;
        font-size: 1rem;
        margin-top: 0;
    }

    /* ── Source Cards ───────────────────────────────────────── */
    .source-card {
        background: #ffffff;
        border: 2px solid #e8e8e8;
        border-radius: 16px;
        padding: 1.5rem;
        text-align: center;
        transition: all 0.25s ease;
        min-height: 160px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }
    .source-card.active {
        border-color: #4361ee;
        background: linear-gradient(135deg, #f0f4ff 0%, #e8edff 100%);
        box-shadow: 0 4px 20px rgba(67, 97, 238, 0.15);
    }
    .source-card.inactive {
        opacity: 0.5;
        border-color: #ddd;
        background: #fafafa;
    }
    .source-icon {
        font-size: 2.5rem;
        margin-bottom: 0.5rem;
    }
    .source-title {
        font-size: 1rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0.25rem;
    }
    .source-desc {
        font-size: 0.78rem;
        color: #777;
        line-height: 1.3;
    }
    .source-device {
        margin-top: 0.5rem;
        font-size: 0.72rem;
        color: #4361ee;
        font-weight: 600;
        background: rgba(67, 97, 238, 0.08);
        padding: 2px 10px;
        border-radius: 20px;
    }

    /* ── Big Record Button ─────────────────────────────────── */
    .big-btn-wrap {
        text-align: center;
        margin: 1.5rem 0 1rem 0;
    }

    /* ── Recording Banner ──────────────────────────────────── */
    @keyframes pulse-glow {
        0%, 100% { box-shadow: 0 0 20px rgba(244, 67, 54, 0.3); }
        50% { box-shadow: 0 0 40px rgba(244, 67, 54, 0.6); }
    }
    @keyframes pulse-dot {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.5; transform: scale(0.8); }
    }
    .rec-banner {
        background: linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%);
        color: white;
        padding: 1.25rem 2rem;
        border-radius: 16px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 1.5rem;
        animation: pulse-glow 2s ease-in-out infinite;
    }
    .rec-left {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .rec-dot {
        width: 16px; height: 16px;
        background: #fff;
        border-radius: 50%;
        animation: pulse-dot 1s ease-in-out infinite;
    }
    .rec-label {
        font-size: 1.1rem;
        font-weight: 700;
        letter-spacing: 0.05em;
    }
    .rec-timer {
        font-family: 'SF Mono', 'Fira Code', 'Courier New', monospace;
        font-size: 2rem;
        font-weight: 800;
        letter-spacing: 0.08em;
    }

    /* ── Summary Row ───────────────────────────────────────── */
    .summary-row {
        display: flex;
        justify-content: center;
        gap: 2rem;
        margin: 0.75rem 0 0.5rem 0;
        flex-wrap: wrap;
    }
    .summary-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #f0f4ff;
        border: 1px solid #d0d9ff;
        border-radius: 20px;
        padding: 6px 16px;
        font-size: 0.82rem;
        font-weight: 600;
        color: #4361ee;
    }
    .summary-chip.off {
        background: #f5f5f5;
        border-color: #e0e0e0;
        color: #aaa;
        text-decoration: line-through;
    }

    /* ── Status Pills ──────────────────────────────────────── */
    .dep-row {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        justify-content: center;
        margin-bottom: 1rem;
    }
    .dep-pill {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .dep-ok {
        background: #e8f5e9;
        color: #2e7d32;
    }
    .dep-err {
        background: #ffebee;
        color: #c62828;
    }

    /* ── File Cards ─────────────────────────────────────────── */
    .file-card {
        background: #fff;
        border: 1px solid #eee;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.5rem;
        transition: border-color 0.2s;
    }
    .file-card:hover {
        border-color: #4361ee;
    }

    /* ── Section Headers ───────────────────────────────────── */
    .section-head {
        display: flex;
        align-items: center;
        gap: 10px;
        margin: 2rem 0 1rem 0;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid #f0f0f0;
    }
    .section-head h3 {
        margin: 0 !important;
        padding: 0 !important;
    }

    /* ── Hide default Streamlit stuff ──────────────────────── */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; }

    /* ── Buttons ────────────────────────────────────────────── */
    .stButton > button {
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.2s;
    }
    div[data-testid="stHorizontalBlock"] .stButton > button[kind="primary"] {
        font-size: 1.05rem;
        padding: 0.6rem 2rem;
    }
</style>
""", unsafe_allow_html=True)


# ── Session State ────────────────────────────────────────────────────────────

def init_state():
    defaults = {
        "audio_manager": None,
        "recorder": None,
        "video_devices": [],
        "audio_devices": [],
        "recording": False,
        "last_message": "",
        "last_message_type": "info",
        "opt_screen": True,
        "opt_system_audio": True,
        "opt_mic": True,
        "sel_screen": None,
        "sel_blackhole": None,
        "sel_mic": None,
        "merge_file": None,
        "show_devices": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "Recordings")


def get_am() -> AudioManager:
    if st.session_state.audio_manager is None:
        st.session_state.audio_manager = AudioManager()
    return st.session_state.audio_manager


def get_rec() -> Recorder:
    if st.session_state.recorder is None:
        st.session_state.recorder = Recorder(get_am(), RECORDINGS_DIR)
    return st.session_state.recorder


def refresh_devices():
    am = get_am()
    v, a = am.discover_devices()
    st.session_state.video_devices = v
    st.session_state.audio_devices = a
    bh = am.find_blackhole(a)
    mic = am.find_default_mic(a)
    scr = am.find_screen(v)
    if bh:
        st.session_state.sel_blackhole = bh.index
    if mic:
        st.session_state.sel_mic = mic.index
    if scr:
        st.session_state.sel_screen = scr.index


if not st.session_state.video_devices and not st.session_state.audio_devices:
    refresh_devices()


def fmt_time(sec: float) -> str:
    h, m, s = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(b: int) -> str:
    if b < 1024**2:
        return f"{b/1024:.0f} KB"
    if b < 1024**3:
        return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"


def get_device_name(devices, idx):
    for d in devices:
        if d.index == idx:
            return d.name
    return "Auto"


# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="hero">
    <h1>Record Screen + Audio</h1>
    <p>Capture screen, system audio, and microphone — all at once</p>
</div>
""", unsafe_allow_html=True)

recorder = get_rec()

# ── Dependency pills ─────────────────────────────────────────────────────────
deps = get_am().check_dependencies()
has_mo = get_am().has_multi_output()

pills_html = '<div class="dep-row">'
for name, ok in deps.items():
    cls = "dep-ok" if ok else "dep-err"
    icon = "✓" if ok else "✗"
    pills_html += f'<span class="dep-pill {cls}">{icon} {name}</span>'

mo_cls = "dep-ok" if has_mo else "dep-err"
mo_icon = "✓" if has_mo else "!"
pills_html += f'<span class="dep-pill {mo_cls}">{mo_icon} Multi-Output</span>'
pills_html += '</div>'
st.markdown(pills_html, unsafe_allow_html=True)

if not all(deps.values()):
    st.error("Missing dependencies. Run `brew install ffmpeg switchaudio-osx` and install BlackHole from existential.audio/blackhole")

# ══════════════════════════════════════════════════════════════════════════════
#  RECORDING BANNER (when active)
# ══════════════════════════════════════════════════════════════════════════════

if recorder.is_recording:
    elapsed = recorder.elapsed_seconds
    st.markdown(f"""
    <div class="rec-banner">
        <div class="rec-left">
            <div class="rec-dot"></div>
            <span class="rec-label">RECORDING</span>
        </div>
        <span class="rec-timer">{fmt_time(elapsed)}</span>
    </div>
    """, unsafe_allow_html=True)

# ── Status message ───────────────────────────────────────────────────────────
if st.session_state.last_message:
    t = st.session_state.last_message_type
    {"success": st.success, "error": st.error, "warning": st.warning}.get(t, st.info)(
        st.session_state.last_message
    )

# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE CARDS
# ══════════════════════════════════════════════════════════════════════════════

scr_name = get_device_name(st.session_state.video_devices, st.session_state.sel_screen)
bh_name = get_device_name(st.session_state.audio_devices, st.session_state.sel_blackhole)
mic_name = get_device_name(st.session_state.audio_devices, st.session_state.sel_mic)

col1, col2, col3 = st.columns(3, gap="medium")

with col1:
    cls = "active" if st.session_state.opt_screen else "inactive"
    dev_tag = f'<div class="source-device">{scr_name}</div>' if st.session_state.opt_screen else ""
    st.markdown(f"""
    <div class="source-card {cls}">
        <div class="source-icon">🖥️</div>
        <div class="source-title">Screen</div>
        <div class="source-desc">Full display capture with cursor</div>
        {dev_tag}
    </div>
    """, unsafe_allow_html=True)
    st.session_state.opt_screen = st.checkbox(
        "Enable Screen", value=st.session_state.opt_screen,
        disabled=recorder.is_recording, key="chk_screen", label_visibility="collapsed"
    )

with col2:
    cls = "active" if st.session_state.opt_system_audio else "inactive"
    dev_tag = f'<div class="source-device">{bh_name}</div>' if st.session_state.opt_system_audio else ""
    st.markdown(f"""
    <div class="source-card {cls}">
        <div class="source-icon">🔊</div>
        <div class="source-title">System Audio</div>
        <div class="source-desc">Teams · Zoom · YouTube via BlackHole</div>
        {dev_tag}
    </div>
    """, unsafe_allow_html=True)
    st.session_state.opt_system_audio = st.checkbox(
        "Enable System Audio", value=st.session_state.opt_system_audio,
        disabled=recorder.is_recording, key="chk_sysaudio", label_visibility="collapsed"
    )

with col3:
    cls = "active" if st.session_state.opt_mic else "inactive"
    dev_tag = f'<div class="source-device">{mic_name}</div>' if st.session_state.opt_mic else ""
    st.markdown(f"""
    <div class="source-card {cls}">
        <div class="source-icon">🎙️</div>
        <div class="source-title">Microphone</div>
        <div class="source-desc">Your voice and room audio</div>
        {dev_tag}
    </div>
    """, unsafe_allow_html=True)
    st.session_state.opt_mic = st.checkbox(
        "Enable Mic", value=st.session_state.opt_mic,
        disabled=recorder.is_recording, key="chk_mic", label_visibility="collapsed"
    )

# ── Summary chips ────────────────────────────────────────────────────────────
active_sources = []
if st.session_state.opt_screen:
    active_sources.append("🖥️ Screen")
if st.session_state.opt_system_audio:
    active_sources.append("🔊 System Audio")
if st.session_state.opt_mic:
    active_sources.append("🎙️ Microphone")

n = len(active_sources)
if n == 3:
    summary_text = "Recording everything — screen, system audio, and microphone simultaneously"
elif n == 0:
    summary_text = "Select at least one source to record"
else:
    summary_text = f"Recording: {' + '.join(active_sources)}"

chips = ""
for src in ["🖥️ Screen", "🔊 System Audio", "🎙️ Microphone"]:
    on = src in active_sources
    cls = "" if on else "off"
    chips += f'<span class="summary-chip {cls}">{src}</span>'

st.markdown(f'<div class="summary-row">{chips}</div>', unsafe_allow_html=True)
st.caption(f"<center>{summary_text}</center>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
#  BIG RECORD / STOP BUTTON
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("")
_, btn_center, _ = st.columns([1, 2, 1])

with btn_center:
    if not recorder.is_recording:
        if st.button(
            "⏺  Start Recording" + (f"  —  {n} source{'s' if n != 1 else ''}" if n else ""),
            type="primary",
            use_container_width=True,
            disabled=(n == 0),
        ):
            cfg = RecordingConfig(
                record_screen=st.session_state.opt_screen,
                record_system_audio=st.session_state.opt_system_audio,
                record_mic=st.session_state.opt_mic,
            )
            for d in st.session_state.video_devices:
                if d.index == st.session_state.sel_screen:
                    cfg.screen_device = d
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
    else:
        if st.button("⏹  Stop Recording", type="primary", use_container_width=True):
            ok, msg = recorder.stop()
            st.session_state.recording = False
            st.session_state.last_message = msg
            st.session_state.last_message_type = "success" if ok else "error"
            st.rerun()

# Auto-refresh timer
if recorder.is_recording:
    time.sleep(1)
    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
#  DEVICE SELECTOR (expandable)
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("")
with st.expander("🎛️  Device Selection & Advanced Settings", expanded=False):
    dc1, dc2, dc3 = st.columns(3)

    vdevs = st.session_state.video_devices
    adevs = st.session_state.audio_devices

    with dc1:
        if vdevs:
            opts = {d.index: d.name for d in vdevs}
            cur = st.session_state.sel_screen if st.session_state.sel_screen in opts else list(opts.keys())[0]
            st.session_state.sel_screen = st.selectbox(
                "Screen Device", list(opts.keys()),
                format_func=lambda x: opts[x],
                index=list(opts.keys()).index(cur),
            )

    with dc2:
        if adevs:
            opts = {d.index: d.name for d in adevs}
            cur = st.session_state.sel_blackhole if st.session_state.sel_blackhole in opts else list(opts.keys())[0]
            st.session_state.sel_blackhole = st.selectbox(
                "System Audio Device (BlackHole)", list(opts.keys()),
                format_func=lambda x: opts[x],
                index=list(opts.keys()).index(cur),
            )

    with dc3:
        if adevs:
            opts = {d.index: d.name for d in adevs}
            cur = st.session_state.sel_mic if st.session_state.sel_mic in opts else list(opts.keys())[0]
            st.session_state.sel_mic = st.selectbox(
                "Microphone Device", list(opts.keys()),
                format_func=lambda x: opts[x],
                index=list(opts.keys()).index(cur),
            )

    if st.button("🔄 Refresh Devices"):
        refresh_devices()
        st.rerun()

    st.markdown("**Advanced**")
    ac1, ac2, ac3, ac4 = st.columns(4)
    with ac1:
        st.select_slider("Frame Rate", [15, 24, 30, 60], 30, key="adv_fps")
    with ac2:
        st.select_slider("Video Bitrate", ["4000k", "6000k", "8000k", "12000k"], "8000k", key="adv_vbr")
    with ac3:
        st.select_slider("Sys Audio Bitrate", ["96k", "128k", "192k", "256k"], "192k", key="adv_abr_sys")
    with ac4:
        st.select_slider("Mic Bitrate", ["64k", "96k", "128k", "192k"], "128k", key="adv_abr_mic")


# ══════════════════════════════════════════════════════════════════════════════
#  RECORDINGS
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-head"><h3>📁 Recordings</h3></div>', unsafe_allow_html=True)

recordings = sorted(
    glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4")),
    key=os.path.getmtime, reverse=True,
)

if not recordings:
    st.markdown("""
    <div style="text-align:center; padding:3rem 0; color:#aaa;">
        <div style="font-size:3rem; margin-bottom:0.5rem;">🎬</div>
        <div>No recordings yet. Hit <b>Start Recording</b> above!</div>
    </div>
    """, unsafe_allow_html=True)
else:
    for fp in recordings:
        fname = os.path.basename(fp)
        fsize = os.path.getsize(fp)
        fdate = datetime.fromtimestamp(os.path.getmtime(fp))
        tracks = get_track_count(fp)
        is_merged = "_merged" in fname
        icon = "🔗" if is_merged else "🎞️"

        with st.container():
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])

            with c1:
                st.markdown(f"**{icon} {fname}**")
                st.caption(
                    f"{fdate.strftime('%b %d, %Y %I:%M %p')}  ·  "
                    f"{fmt_size(fsize)}  ·  {tracks} audio track{'s' if tracks != 1 else ''}"
                )

            with c2:
                with open(fp, "rb") as f:
                    st.download_button(
                        "⬇️ Download", f, fname, "video/mp4",
                        key=f"dl_{fname}", use_container_width=True,
                    )

            with c3:
                if tracks >= 2 and not is_merged:
                    if st.button("🔗 Merge", key=f"mg_{fname}", use_container_width=True):
                        st.session_state.merge_file = fp
                        st.rerun()

            with c4:
                if st.button("📂 Reveal", key=f"rv_{fname}", use_container_width=True):
                    os.system(f'open -R "{fp}"')


# ── Merge Dialog ─────────────────────────────────────────────────────────────

if st.session_state.merge_file and os.path.exists(st.session_state.merge_file):
    st.markdown("---")
    st.markdown(f"### 🔗 Merge Audio Tracks")
    st.caption(f"`{os.path.basename(st.session_state.merge_file)}`")

    mc1, mc2 = st.columns(2)
    with mc1:
        sys_vol = st.slider("System Audio Volume", 0.0, 1.0, 0.5, 0.05, key="mg_sys")
    with mc2:
        mic_vol = st.slider("Microphone Volume", 0.0, 1.0, 0.5, 0.05, key="mg_mic")

    p1, p2, p3 = st.columns(3)
    with p1:
        if st.button("Equal (50/50)", use_container_width=True):
            st.session_state.mg_sys = 0.5
            st.session_state.mg_mic = 0.5
            st.rerun()
    with p2:
        if st.button("System Louder (70/30)", use_container_width=True):
            st.session_state.mg_sys = 0.7
            st.session_state.mg_mic = 0.3
            st.rerun()
    with p3:
        if st.button("Mic Louder (30/70)", use_container_width=True):
            st.session_state.mg_sys = 0.3
            st.session_state.mg_mic = 0.7
            st.rerun()

    b1, b2, _ = st.columns([1, 1, 2])
    with b1:
        if st.button("✅ Merge Now", type="primary", use_container_width=True):
            with st.spinner("Merging..."):
                ok, result = merge_tracks(st.session_state.merge_file, sys_vol, mic_vol)
            st.session_state.last_message = f"Merged: {os.path.basename(result)}" if ok else f"Failed: {result}"
            st.session_state.last_message_type = "success" if ok else "error"
            st.session_state.merge_file = None
            st.rerun()
    with b2:
        if st.button("Cancel", use_container_width=True):
            st.session_state.merge_file = None
            st.rerun()


# ── Footer ───────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "<center>Record Screen + Audio — OpenTF · "
    "Audio settings are automatically saved before recording and restored after · "
    f"Recordings: <code>{RECORDINGS_DIR}</code></center>",
    unsafe_allow_html=True,
)
