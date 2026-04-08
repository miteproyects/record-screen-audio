# 🎬 Record Screen + Audio — OpenTF

A macOS screen & audio recorder with a Streamlit web UI. Record your screen, system audio (Teams/Zoom/YouTube via BlackHole), and microphone — all at once, with separate audio tracks you can merge later.

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-1.30+-red)
![macOS](https://img.shields.io/badge/macOS-12+-black)

## Features

- **Screen recording** — Full display capture at configurable frame rates
- **System audio** — Captures Teams/Zoom/YouTube via BlackHole virtual audio driver
- **Microphone** — Records your voice or room audio
- **Separate tracks** — System audio and mic on independent tracks for post-editing
- **One-click merge** — Combine tracks with adjustable volume ratios
- **Auto-restore** — Your audio settings (output device, input device, volume) are saved before recording and restored after, even on crash
- **Device detection** — Auto-discovers all audio/video devices
- **Clean web UI** — Streamlit-based interface with live recording indicator

## Requirements

| Dependency | Install |
|---|---|
| [BlackHole 2ch](https://existential.audio/blackhole/) | Download from website |
| [Homebrew](https://brew.sh) | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| ffmpeg | `brew install ffmpeg` |
| SwitchAudioSource | `brew install switchaudio-osx` |
| Python 3.9+ | `brew install python` |
| Streamlit | `pip3 install streamlit` |

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USER/record-screen-audio.git
cd record-screen-audio

# 2. Install Python deps
pip3 install -r requirements.txt

# 3. Launch the app
streamlit run app.py
```

Or double-click **`Launch App.command`** in Finder.

## One-Time Setup: Multi-Output Device

To hear audio while BlackHole captures it, create a Multi-Output Device:

1. Open **Audio MIDI Setup** (`/Applications/Utilities/Audio MIDI Setup.app`)
2. Click **+** at bottom-left → **Create Multi-Output Device**
3. Check **BlackHole 2ch** and **your speakers/headphones**
4. Enable **Drift Correction** for BlackHole

The app will automatically switch to this device when recording system audio, and switch back when you stop.

## Project Structure

```
streamlit-app/
├── app.py              # Streamlit UI
├── recorder.py         # FFmpeg recording engine
├── audio_manager.py    # macOS audio device management
├── requirements.txt    # Python dependencies
└── .streamlit/
    └── config.toml     # Streamlit theme config
```

## How It Works

1. **Before recording**: Saves your current output device, input device, and volume
2. **During recording**: Switches system output to Multi-Output Device (BlackHole + speakers), runs ffmpeg with avfoundation to capture screen + audio inputs as separate tracks
3. **After recording**: Restores all audio settings to their original state
4. **Merge (optional)**: Uses ffmpeg to mix separate audio tracks with adjustable volumes

## License

MIT
