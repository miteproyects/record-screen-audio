"""
Record Screen + Audio — OpenTF
Streamlit UI for recording macOS screen, system audio, and microphone.
"""

import streamlit as st
import streamlit.components.v1 as components
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

    /* ── Source Cards (clickable buttons) ──────────────────── */
    @keyframes card-glow {
        0%, 100% { box-shadow: 0 0 15px rgba(67, 97, 238, 0.25), 0 4px 15px rgba(67, 97, 238, 0.1); }
        50% { box-shadow: 0 0 25px rgba(67, 97, 238, 0.4), 0 4px 20px rgba(67, 97, 238, 0.2); }
    }
    .source-card-btn > button {
        background: #ffffff !important;
        border: 2px solid #e8e8e8 !important;
        border-radius: 16px !important;
        padding: 1.5rem 1rem !important;
        text-align: center !important;
        transition: all 0.3s ease !important;
        min-height: 170px !important;
        width: 100% !important;
        cursor: pointer !important;
        color: #1a1a2e !important;
    }
    .source-card-btn > button:hover {
        transform: translateY(-2px) !important;
    }
    /* Active = glowing blue border + soft blue background */
    .source-card-btn.active > button {
        border-color: #4361ee !important;
        background: linear-gradient(135deg, #f0f4ff 0%, #e8edff 100%) !important;
        animation: card-glow 2.5s ease-in-out infinite !important;
    }
    .source-card-btn.active > button:hover {
        animation: none !important;
        box-shadow: 0 0 30px rgba(67, 97, 238, 0.5), 0 6px 25px rgba(67, 97, 238, 0.25) !important;
    }
    /* Inactive = dimmed, inset shadow, greyed out */
    .source-card-btn.inactive > button {
        opacity: 0.55 !important;
        border-color: #d0d0d0 !important;
        background: #f5f5f5 !important;
        box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.08) !important;
        filter: grayscale(40%) !important;
    }
    .source-card-btn.inactive > button:hover {
        opacity: 0.75 !important;
        border-color: #bbb !important;
        box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.08), 0 2px 10px rgba(0,0,0,0.08) !important;
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
is_rec = recorder.is_recording

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

# ── Multi-Output Device Setup ────────────────────────────────────────────────
if not has_mo:
    with st.container():
        st.markdown("""
        <div style="background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
                    border: 1px solid #ffb74d; border-radius: 12px;
                    padding: 1.25rem 1.5rem; margin-bottom: 1rem;">
            <div style="font-weight: 700; font-size: 1rem; margin-bottom: 0.5rem;">
                ⚠️ Multi-Output Device Required
            </div>
            <div style="font-size: 0.88rem; color: #444; line-height: 1.6;">
                Without it, BlackHole captures system audio but you <b>won't hear it</b>
                (Teams, Zoom, YouTube will be silent during recording).<br>
                The Multi-Output Device sends audio to <b>both</b> your speakers AND BlackHole.
            </div>
        </div>
        """, unsafe_allow_html=True)

        setup_col1, setup_col2 = st.columns(2)

        with setup_col1:
            if st.button("🔧 Auto-Create Multi-Output Device", type="primary", use_container_width=True):
                ok, msg = get_am().create_multi_output_via_script()
                if ok:
                    st.success(msg)
                else:
                    st.warning(msg)
                    st.markdown("""
                    **Manual steps in Audio MIDI Setup:**
                    1. Click **+** at bottom-left
                    2. Select **"Create Multi-Output Device"**
                    3. Check **BlackHole 2ch** and **MacBook Pro Speakers**
                    4. Enable **Drift Correction** for BlackHole
                    """)

        with setup_col2:
            if st.button("📖 Open Audio MIDI Setup", use_container_width=True):
                get_am().open_midi_setup()
                st.info("Audio MIDI Setup opened. Follow the steps above to create the device.")

        if st.button("🔄 Check Again", use_container_width=True):
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
#  RECORDING BANNER with JS timer (no Python rerun needed)
# ══════════════════════════════════════════════════════════════════════════════

if is_rec:
    start_ts = recorder.elapsed_seconds
    # Use components.html so JavaScript actually runs (st.markdown strips <script>)
    components.html(f"""
    <style>
        @keyframes pulse-glow {{
            0%, 100% {{ box-shadow: 0 0 20px rgba(244, 67, 54, 0.3); }}
            50% {{ box-shadow: 0 0 40px rgba(244, 67, 54, 0.6); }}
        }}
        @keyframes pulse-dot {{
            0%, 100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.5; transform: scale(0.8); }}
        }}
        .rec-banner {{
            background: linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%);
            color: white;
            padding: 1.25rem 2rem;
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            animation: pulse-glow 2s ease-in-out infinite;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }}
        .rec-left {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .rec-dot {{
            width: 16px; height: 16px;
            background: #fff;
            border-radius: 50%;
            animation: pulse-dot 1s ease-in-out infinite;
        }}
        .rec-label {{
            font-size: 1.1rem;
            font-weight: 700;
            letter-spacing: 0.05em;
        }}
        .rec-timer {{
            font-family: 'SF Mono', 'Fira Code', 'Courier New', monospace;
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: 0.08em;
        }}
    </style>
    <div class="rec-banner">
        <div class="rec-left">
            <div class="rec-dot"></div>
            <span class="rec-label">RECORDING</span>
        </div>
        <span class="rec-timer" id="rec-timer">00:00</span>
    </div>
    <script>
        (function() {{
            var seconds = {int(start_ts)};
            var el = document.getElementById('rec-timer');
            function pad(n) {{ return n < 10 ? '0' + n : '' + n; }}
            function tick() {{
                var h = Math.floor(seconds / 3600);
                var m = Math.floor((seconds % 3600) / 60);
                var s = seconds % 60;
                el.textContent = h > 0
                    ? pad(h) + ':' + pad(m) + ':' + pad(s)
                    : pad(m) + ':' + pad(s);
                seconds++;
            }}
            tick();
            setInterval(tick, 1000);
        }})();
    </script>
    """, height=80)

# ── Status message ───────────────────────────────────────────────────────────
if st.session_state.last_message:
    t = st.session_state.last_message_type
    {"success": st.success, "error": st.error, "warning": st.warning}.get(t, st.info)(
        st.session_state.last_message
    )
    if t == "error":
        log = recorder.last_error
        if log:
            with st.expander("🔍 ffmpeg debug log", expanded=False):
                st.code(log, language=None)

# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE CARDS — toggle on/off by clicking (works during recording too)
# ══════════════════════════════════════════════════════════════════════════════

scr_name = get_device_name(st.session_state.video_devices, st.session_state.sel_screen)
bh_name = get_device_name(st.session_state.audio_devices, st.session_state.sel_blackhole)
mic_name = get_device_name(st.session_state.audio_devices, st.session_state.sel_mic)

col1, col2, col3 = st.columns(3, gap="medium")


def card_label(icon, title, desc, device, is_on):
    dev_html = f"\n\n`{device}`" if is_on else ""
    return f"{icon}\n\n**{title}**\n\n{desc}{dev_html}"


def handle_toggle(source_key, toggle_fn):
    """Handle a card toggle — works both before and during recording."""
    new_val = not st.session_state[source_key]
    if is_rec:
        ok, msg = toggle_fn(new_val)
        if ok:
            st.session_state[source_key] = new_val
            st.session_state.last_message = msg
            st.session_state.last_message_type = "info"
        else:
            st.session_state.last_message = msg
            st.session_state.last_message_type = "error"
    else:
        st.session_state[source_key] = new_val
    st.rerun()


with col1:
    cls = "active" if st.session_state.opt_screen else "inactive"
    st.markdown(f'<div class="source-card-btn {cls}">', unsafe_allow_html=True)
    if st.button(
        card_label("🖥️", "Screen", "Full display capture with cursor", scr_name, st.session_state.opt_screen),
        key="btn_screen", use_container_width=True,
    ):
        handle_toggle("opt_screen", recorder.toggle_screen)
    st.markdown('</div>', unsafe_allow_html=True)

with col2:
    cls = "active" if st.session_state.opt_system_audio else "inactive"
    st.markdown(f'<div class="source-card-btn {cls}">', unsafe_allow_html=True)
    if st.button(
        card_label("🔊", "System Audio", "Teams · Zoom · YouTube via BlackHole", bh_name, st.session_state.opt_system_audio),
        key="btn_sysaudio", use_container_width=True,
    ):
        handle_toggle("opt_system_audio", recorder.toggle_sysaudio)
    st.markdown('</div>', unsafe_allow_html=True)

with col3:
    cls = "active" if st.session_state.opt_mic else "inactive"
    st.markdown(f'<div class="source-card-btn {cls}">', unsafe_allow_html=True)
    if st.button(
        card_label("🎙️", "Microphone", "Your voice and room audio", mic_name, st.session_state.opt_mic),
        key="btn_mic", use_container_width=True,
    ):
        handle_toggle("opt_mic", recorder.toggle_mic)
    st.markdown('</div>', unsafe_allow_html=True)

# Count active sources for the record button
n = sum([st.session_state.opt_screen, st.session_state.opt_system_audio, st.session_state.opt_mic])

# ══════════════════════════════════════════════════════════════════════════════
#  BIG RECORD / STOP BUTTON
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("")
_, btn_center, _ = st.columns([1, 2, 1])

with btn_center:
    if not is_rec:
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

# NO auto-refresh loop here — the JS timer handles the display.
# The page only reruns when the user clicks something.

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
    glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4"))
    + glob.glob(os.path.join(RECORDINGS_DIR, "*.mov")),
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
        # Skip hidden temp files
        if fname.startswith(".tmp_"):
            continue
        fsize = os.path.getsize(fp)
        fdate = datetime.fromtimestamp(os.path.getmtime(fp))
        tracks = get_track_count(fp)
        is_merged = "_merged" in fname
        icon = "🔗" if is_merged else "🎞️"

        with st.container():
            c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])

            with c1:
                st.markdown(f"**{icon} {fname}**")
                st.caption(
                    f"{fdate.strftime('%b %d, %Y %I:%M %p')}  ·  "
                    f"{fmt_size(fsize)}  ·  {tracks} audio track{'s' if tracks != 1 else ''}"
                )

            with c2:
                if st.button("▶️ Play", key=f"pl_{fname}", use_container_width=True):
                    os.system(f'open "{fp}"')

            with c3:
                with open(fp, "rb") as f:
                    mime = "video/mp4" if fp.endswith(".mp4") else "video/quicktime"
                    st.download_button(
                        "⬇️ Download", f, fname, mime,
                        key=f"dl_{fname}", use_container_width=True,
                    )

            with c4:
                if tracks >= 2 and not is_merged:
                    if st.button("🔗 Merge", key=f"mg_{fname}", use_container_width=True):
                        st.session_state.merge_file = fp
                        st.rerun()

            with c5:
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
