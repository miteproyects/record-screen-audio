#!/bin/bash
###############################################################################
#  Record Screen + Audio  —  OpenTF
#  Records macOS screen, system audio (via BlackHole), and microphone.
#  Separate audio tracks with easy merge option.
#
#  Usage:  ./record.sh
#  Stop:   Press Ctrl+C  (file is finalized properly & audio settings restored)
###############################################################################

set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RECORDINGS_DIR="$SCRIPT_DIR/Recordings"
mkdir -p "$RECORDINGS_DIR"

# ── Colors & Symbols ─────────────────────────────────────────────────────────
RED='\033[0;31m';  GREEN='\033[0;32m';  YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m';   MAGENTA='\033[0;35m'
BOLD='\033[1m';    DIM='\033[2m';       NC='\033[0m'
CHECK="${GREEN}✓${NC}"; CROSS="${RED}✗${NC}"; ARROW="${CYAN}➜${NC}"

# ── State ────────────────────────────────────────────────────────────────────
RECORDING_PID=""
ORIGINAL_OUTPUT_DEVICE=""
ORIGINAL_INPUT_DEVICE=""
ORIGINAL_OUTPUT_VOLUME=""
MULTI_OUTPUT_SET=false

# Toggle defaults
OPT_SCREEN=true
OPT_SYSTEM_AUDIO=true
OPT_MIC=true

# Device selections (indices for ffmpeg avfoundation)
SCREEN_IDX=""
BLACKHOLE_IDX=""
MIC_IDX=""
BLACKHOLE_NAME=""
MIC_NAME=""

# ── Cleanup (runs on EXIT / Ctrl+C / TERM) ──────────────────────────────────
cleanup() {
    echo ""
    echo -e "${YELLOW}━━━ Shutting down ━━━${NC}"

    # Stop ffmpeg gracefully
    if [[ -n "$RECORDING_PID" ]] && kill -0 "$RECORDING_PID" 2>/dev/null; then
        echo -e "${ARROW} Stopping ffmpeg (finalizing file)..."
        kill -INT "$RECORDING_PID" 2>/dev/null || true
        # Wait up to 15 seconds for ffmpeg to finalize
        local waited=0
        while kill -0 "$RECORDING_PID" 2>/dev/null && (( waited < 15 )); do
            sleep 1
            (( waited++ ))
        done
        if kill -0 "$RECORDING_PID" 2>/dev/null; then
            kill -9 "$RECORDING_PID" 2>/dev/null || true
        fi
    fi

    restore_audio
    echo -e "${GREEN}━━━ All done ━━━${NC}"
}
trap cleanup EXIT

# ── Audio helpers ────────────────────────────────────────────────────────────
save_audio() {
    if command -v SwitchAudioSource &>/dev/null; then
        ORIGINAL_OUTPUT_DEVICE="$(SwitchAudioSource -c -t output 2>/dev/null || echo "")"
        ORIGINAL_INPUT_DEVICE="$(SwitchAudioSource -c -t input 2>/dev/null || echo "")"
        # Save volume (0-100)
        ORIGINAL_OUTPUT_VOLUME="$(osascript -e 'output volume of (get volume settings)' 2>/dev/null || echo "")"
        echo -e "${BLUE}Saved current audio settings:${NC}"
        echo -e "  Output : ${BOLD}${ORIGINAL_OUTPUT_DEVICE:-unknown}${NC}"
        echo -e "  Input  : ${BOLD}${ORIGINAL_INPUT_DEVICE:-unknown}${NC}"
        echo -e "  Volume : ${BOLD}${ORIGINAL_OUTPUT_VOLUME:-unknown}%${NC}"
    else
        echo -e "${YELLOW}SwitchAudioSource not found — cannot save/restore audio device.${NC}"
    fi
}

restore_audio() {
    echo -e "${ARROW} Restoring audio settings..."
    if command -v SwitchAudioSource &>/dev/null; then
        if [[ -n "$ORIGINAL_OUTPUT_DEVICE" ]]; then
            SwitchAudioSource -s "$ORIGINAL_OUTPUT_DEVICE" -t output 2>/dev/null || true
            echo -e "  Output restored → ${GREEN}${ORIGINAL_OUTPUT_DEVICE}${NC}"
        fi
        if [[ -n "$ORIGINAL_INPUT_DEVICE" ]]; then
            SwitchAudioSource -s "$ORIGINAL_INPUT_DEVICE" -t input 2>/dev/null || true
            echo -e "  Input restored  → ${GREEN}${ORIGINAL_INPUT_DEVICE}${NC}"
        fi
        if [[ -n "$ORIGINAL_OUTPUT_VOLUME" ]]; then
            osascript -e "set volume output volume ${ORIGINAL_OUTPUT_VOLUME}" 2>/dev/null || true
            echo -e "  Volume restored → ${GREEN}${ORIGINAL_OUTPUT_VOLUME}%${NC}"
        fi
    fi
    echo -e "${CHECK} Audio settings restored to original state."
}

# ── Dependency check ─────────────────────────────────────────────────────────
check_deps() {
    echo -e "\n${BOLD}Checking dependencies...${NC}"
    local ok=true

    # ffmpeg
    if command -v ffmpeg &>/dev/null; then
        echo -e "  ${CHECK} ffmpeg"
    else
        echo -e "  ${CROSS} ffmpeg  →  brew install ffmpeg"
        ok=false
    fi

    # SwitchAudioSource
    if command -v SwitchAudioSource &>/dev/null; then
        echo -e "  ${CHECK} SwitchAudioSource"
    else
        echo -e "  ${CROSS} SwitchAudioSource  →  brew install switchaudio-osx"
        ok=false
    fi

    # BlackHole (ffmpeg -list_devices always exits non-zero, so capture output first)
    local bh_found=false
    local device_list
    device_list=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 || true)
    if echo "$device_list" | grep -qi "blackhole"; then
        bh_found=true
        echo -e "  ${CHECK} BlackHole audio driver"
    else
        echo -e "  ${CROSS} BlackHole not detected  →  https://existential.audio/blackhole/"
        ok=false
    fi

    if [[ "$ok" == false ]]; then
        echo -e "\n${RED}Install missing dependencies first, or run ./setup.sh${NC}"
        exit 1
    fi
    echo -e "${GREEN}All dependencies OK.${NC}"
}

# ── Device discovery ─────────────────────────────────────────────────────────
# Parses ffmpeg -list_devices output into arrays
declare -a VIDEO_DEVICES_IDX VIDEO_DEVICES_NAME
declare -a AUDIO_DEVICES_IDX AUDIO_DEVICES_NAME

discover_devices() {
    local raw
    raw=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 || true)

    local section=""
    while IFS= read -r line; do
        if echo "$line" | grep -q "AVFoundation video devices"; then
            section="video"
            continue
        elif echo "$line" | grep -q "AVFoundation audio devices"; then
            section="audio"
            continue
        fi

        # Match lines like: [AVFoundation ...] [0] Device Name
        if [[ "$line" =~ \[([0-9]+)\]\ (.*) ]]; then
            local idx="${BASH_REMATCH[1]}"
            local name="${BASH_REMATCH[2]}"
            if [[ "$section" == "video" ]]; then
                VIDEO_DEVICES_IDX+=("$idx")
                VIDEO_DEVICES_NAME+=("$name")
            elif [[ "$section" == "audio" ]]; then
                AUDIO_DEVICES_IDX+=("$idx")
                AUDIO_DEVICES_NAME+=("$name")
            fi
        fi
    done <<< "$raw"
}

print_devices() {
    echo -e "\n${BOLD}${CYAN}Video Devices:${NC}"
    for i in "${!VIDEO_DEVICES_IDX[@]}"; do
        echo -e "  [${VIDEO_DEVICES_IDX[$i]}] ${VIDEO_DEVICES_NAME[$i]}"
    done
    echo -e "\n${BOLD}${CYAN}Audio Devices:${NC}"
    for i in "${!AUDIO_DEVICES_IDX[@]}"; do
        echo -e "  [${AUDIO_DEVICES_IDX[$i]}] ${AUDIO_DEVICES_NAME[$i]}"
    done
}

# Find device index by partial name match (case-insensitive)
find_audio_device() {
    local search="$1"
    for i in "${!AUDIO_DEVICES_NAME[@]}"; do
        if echo "${AUDIO_DEVICES_NAME[$i]}" | grep -qi "$search"; then
            echo "${AUDIO_DEVICES_IDX[$i]}"
            return 0
        fi
    done
    return 1
}

find_audio_device_name() {
    local search="$1"
    for i in "${!AUDIO_DEVICES_NAME[@]}"; do
        if echo "${AUDIO_DEVICES_NAME[$i]}" | grep -qi "$search"; then
            echo "${AUDIO_DEVICES_NAME[$i]}"
            return 0
        fi
    done
    return 1
}

# Find first "Capture screen" device
find_screen_device() {
    for i in "${!VIDEO_DEVICES_NAME[@]}"; do
        if echo "${VIDEO_DEVICES_NAME[$i]}" | grep -qi "capture screen\|screen"; then
            echo "${VIDEO_DEVICES_IDX[$i]}"
            return 0
        fi
    done
    # Fallback: last video device (usually the screen)
    echo "${VIDEO_DEVICES_IDX[-1]}"
}

# ── User picks a device from a list ─────────────────────────────────────────
pick_device() {
    local type="$1"  # "video" or "audio"
    local purpose="$2"
    local default_idx="$3"

    if [[ "$type" == "video" ]]; then
        local -n idxs=VIDEO_DEVICES_IDX
        local -n names=VIDEO_DEVICES_NAME
    else
        local -n idxs=AUDIO_DEVICES_IDX
        local -n names=AUDIO_DEVICES_NAME
    fi

    echo -e "\n${BOLD}Select ${purpose}:${NC}"
    for i in "${!idxs[@]}"; do
        local marker=""
        if [[ "${idxs[$i]}" == "$default_idx" ]]; then
            marker=" ${GREEN}(default)${NC}"
        fi
        echo -e "  [${idxs[$i]}] ${names[$i]}${marker}"
    done

    read -rp "Enter device number [${default_idx}]: " choice
    choice="${choice:-$default_idx}"

    # Validate
    for idx in "${idxs[@]}"; do
        if [[ "$idx" == "$choice" ]]; then
            echo "$choice"
            return
        fi
    done

    echo -e "${YELLOW}Invalid choice, using default: ${default_idx}${NC}"
    echo "$default_idx"
}

# ── Menu ─────────────────────────────────────────────────────────────────────
show_menu() {
    clear
    echo -e "${BOLD}${MAGENTA}"
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║       Record Screen + Audio  —  OpenTF       ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo -e "  ${BOLD}Recording Options:${NC}"
    echo ""

    local s_icon; [[ "$OPT_SCREEN" == true ]]       && s_icon="${CHECK}" || s_icon="${CROSS}"
    local a_icon; [[ "$OPT_SYSTEM_AUDIO" == true ]]  && a_icon="${CHECK}" || a_icon="${CROSS}"
    local m_icon; [[ "$OPT_MIC" == true ]]            && m_icon="${CHECK}" || m_icon="${CROSS}"

    echo -e "    [1] ${s_icon}  Screen Recording"
    echo -e "    [2] ${a_icon}  System Audio (Teams/Zoom/YouTube via BlackHole)"
    echo -e "    [3] ${m_icon}  Microphone (your voice / room noise)"
    echo ""
    echo -e "    [4] ${CYAN}Select specific devices${NC}"
    echo -e "    [5] ${GREEN}${BOLD}START RECORDING${NC}"
    echo -e "    [q] Quit"
    echo ""
}

# ── Set Multi-Output Device as system output ─────────────────────────────────
activate_multi_output() {
    if ! command -v SwitchAudioSource &>/dev/null; then
        echo -e "${YELLOW}Cannot auto-switch audio output (SwitchAudioSource not found).${NC}"
        echo -e "${YELLOW}Manually set your output to Multi-Output Device in System Settings.${NC}"
        return
    fi

    # Check if a Multi-Output Device exists
    local mo_name
    mo_name=$(SwitchAudioSource -a -t output 2>/dev/null | grep -i "multi-output" | head -1 || echo "")

    if [[ -z "$mo_name" ]]; then
        echo -e "${YELLOW}No Multi-Output Device found.${NC}"
        echo -e "${YELLOW}Run ./setup.sh to create one, or create it manually in Audio MIDI Setup.${NC}"
        echo -e "${YELLOW}Without it, you won't hear audio while BlackHole captures it.${NC}"
        read -rp "Continue anyway? [Y/n]: " yn
        [[ "${yn,,}" == "n" ]] && exit 0
        return
    fi

    echo -e "${ARROW} Switching system output to: ${BOLD}${mo_name}${NC}"
    SwitchAudioSource -s "$mo_name" -t output 2>/dev/null || true
    MULTI_OUTPUT_SET=true
    echo -e "${CHECK} System audio now routes through BlackHole AND your speakers."
}

# ── Build & run ffmpeg command ───────────────────────────────────────────────
start_recording() {
    local timestamp
    timestamp=$(date +"%Y-%m-%d_%H-%M-%S")

    # Validate at least one option is selected
    if [[ "$OPT_SCREEN" == false && "$OPT_SYSTEM_AUDIO" == false && "$OPT_MIC" == false ]]; then
        echo -e "${RED}Nothing selected to record! Enable at least one option.${NC}"
        return 1
    fi

    echo -e "\n${BOLD}━━━ Preparing to record ━━━${NC}\n"

    # Save audio settings
    save_audio

    # If recording system audio, activate Multi-Output Device
    if [[ "$OPT_SYSTEM_AUDIO" == true ]]; then
        activate_multi_output
    fi

    # Auto-detect devices if not manually selected
    if [[ "$OPT_SCREEN" == true && -z "$SCREEN_IDX" ]]; then
        SCREEN_IDX=$(find_screen_device)
    fi
    if [[ "$OPT_SYSTEM_AUDIO" == true && -z "$BLACKHOLE_IDX" ]]; then
        BLACKHOLE_IDX=$(find_audio_device "blackhole" || echo "")
        BLACKHOLE_NAME=$(find_audio_device_name "blackhole" || echo "BlackHole")
        if [[ -z "$BLACKHOLE_IDX" ]]; then
            echo -e "${RED}Could not find BlackHole audio device!${NC}"
            return 1
        fi
    fi
    if [[ "$OPT_MIC" == true && -z "$MIC_IDX" ]]; then
        # Try to find built-in mic or external mic (skip BlackHole and Multi-Output)
        for i in "${!AUDIO_DEVICES_NAME[@]}"; do
            local n="${AUDIO_DEVICES_NAME[$i]}"
            if echo "$n" | grep -qiv "blackhole\|multi-output"; then
                MIC_IDX="${AUDIO_DEVICES_IDX[$i]}"
                MIC_NAME="$n"
                break
            fi
        done
        if [[ -z "$MIC_IDX" ]]; then
            echo -e "${RED}Could not find a microphone device!${NC}"
            return 1
        fi
    fi

    # Show what we're recording
    echo -e "\n${BOLD}Recording configuration:${NC}"
    [[ "$OPT_SCREEN" == true ]]       && echo -e "  ${CHECK} Screen        → device [$SCREEN_IDX]"
    [[ "$OPT_SYSTEM_AUDIO" == true ]] && echo -e "  ${CHECK} System Audio  → device [$BLACKHOLE_IDX] ($BLACKHOLE_NAME)"
    [[ "$OPT_MIC" == true ]]          && echo -e "  ${CHECK} Microphone    → device [$MIC_IDX] ($MIC_NAME)"

    # ── Build ffmpeg command ────────────────────────────────────────────────
    local cmd=()
    cmd+=(ffmpeg -y -hide_banner -loglevel warning -stats)

    local has_two_audio=false

    if [[ "$OPT_SCREEN" == true ]]; then
        # --- CASE: Screen + both audios ---
        if [[ "$OPT_SYSTEM_AUDIO" == true && "$OPT_MIC" == true ]]; then
            has_two_audio=true
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}.mp4"

            # Input 0: screen + system audio
            cmd+=(-f avfoundation -capture_cursor 1 -capture_mouse_clicks 1 -framerate 30)
            cmd+=(-i "${SCREEN_IDX}:${BLACKHOLE_IDX}")
            # Input 1: mic only
            cmd+=(-f avfoundation -i ":${MIC_IDX}")
            # Maps
            cmd+=(-map 0:v -map 0:a -map 1:a)
            # Video codec
            cmd+=(-c:v h264_videotoolbox -b:v 8000k -realtime true)
            # Audio codecs (two tracks)
            cmd+=(-c:a:0 aac -b:a:0 192k)
            cmd+=(-c:a:1 aac -b:a:1 128k)
            # Metadata
            cmd+=(-metadata:s:a:0 title="System Audio (BlackHole)")
            cmd+=(-metadata:s:a:1 title="Microphone")
            cmd+=("$outfile")

        # --- CASE: Screen + system audio only ---
        elif [[ "$OPT_SYSTEM_AUDIO" == true ]]; then
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}.mp4"
            cmd+=(-f avfoundation -capture_cursor 1 -capture_mouse_clicks 1 -framerate 30)
            cmd+=(-i "${SCREEN_IDX}:${BLACKHOLE_IDX}")
            cmd+=(-map 0:v -map 0:a)
            cmd+=(-c:v h264_videotoolbox -b:v 8000k -realtime true)
            cmd+=(-c:a aac -b:a 192k)
            cmd+=(-metadata:s:a:0 title="System Audio (BlackHole)")
            cmd+=("$outfile")

        # --- CASE: Screen + mic only ---
        elif [[ "$OPT_MIC" == true ]]; then
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}.mp4"
            cmd+=(-f avfoundation -capture_cursor 1 -capture_mouse_clicks 1 -framerate 30)
            cmd+=(-i "${SCREEN_IDX}:${MIC_IDX}")
            cmd+=(-map 0:v -map 0:a)
            cmd+=(-c:v h264_videotoolbox -b:v 8000k -realtime true)
            cmd+=(-c:a aac -b:a 128k)
            cmd+=(-metadata:s:a:0 title="Microphone")
            cmd+=("$outfile")

        # --- CASE: Screen only (no audio) ---
        else
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}.mp4"
            cmd+=(-f avfoundation -capture_cursor 1 -capture_mouse_clicks 1 -framerate 30)
            cmd+=(-i "${SCREEN_IDX}:none")
            cmd+=(-c:v h264_videotoolbox -b:v 8000k -realtime true)
            cmd+=("$outfile")
        fi

    else
        # --- No screen, audio only ---
        if [[ "$OPT_SYSTEM_AUDIO" == true && "$OPT_MIC" == true ]]; then
            has_two_audio=true
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}_audio.mp4"
            cmd+=(-f avfoundation -i ":${BLACKHOLE_IDX}")
            cmd+=(-f avfoundation -i ":${MIC_IDX}")
            cmd+=(-map 0:a -map 1:a)
            cmd+=(-c:a:0 aac -b:a:0 192k)
            cmd+=(-c:a:1 aac -b:a:1 128k)
            cmd+=(-metadata:s:a:0 title="System Audio (BlackHole)")
            cmd+=(-metadata:s:a:1 title="Microphone")
            cmd+=("$outfile")

        elif [[ "$OPT_SYSTEM_AUDIO" == true ]]; then
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}_audio.mp4"
            cmd+=(-f avfoundation -i ":${BLACKHOLE_IDX}")
            cmd+=(-c:a aac -b:a 192k)
            cmd+=("$outfile")

        elif [[ "$OPT_MIC" == true ]]; then
            local outfile="${RECORDINGS_DIR}/recording_${timestamp}_audio.mp4"
            cmd+=(-f avfoundation -i ":${MIC_IDX}")
            cmd+=(-c:a aac -b:a 128k)
            cmd+=("$outfile")
        fi
    fi

    echo -e "\n${BOLD}Output: ${NC}${outfile}"
    echo -e "${DIM}(ffmpeg command: ${cmd[*]})${NC}\n"

    echo -e "${RED}${BOLD}  ● REC ${NC} ${BOLD}Recording started — press Ctrl+C to stop${NC}\n"

    # Run ffmpeg in background so trap works
    "${cmd[@]}" &
    RECORDING_PID=$!

    # Wait for ffmpeg to finish (or be killed by trap)
    wait "$RECORDING_PID" 2>/dev/null || true
    RECORDING_PID=""

    echo -e "\n${CHECK} Recording saved: ${BOLD}${outfile}${NC}"

    # Offer merge if we had two audio tracks
    if [[ "$has_two_audio" == true ]]; then
        echo ""
        read -rp "$(echo -e "${CYAN}Merge audio tracks into one? [y/N]: ${NC}")" merge_yn
        if [[ "${merge_yn,,}" == "y" ]]; then
            local merged="${outfile%.mp4}_merged.mp4"
            echo -e "${ARROW} Merging tracks..."
            ffmpeg -y -hide_banner -loglevel warning \
                -i "$outfile" \
                -filter_complex "[0:a:0][0:a:1]amerge=inputs=2,pan=stereo|FL<FL+FL|FR<FR+FR[aout]" \
                -map 0:v? -map "[aout]" \
                -c:v copy -c:a aac -b:a 256k \
                "$merged"
            echo -e "${CHECK} Merged file: ${BOLD}${merged}${NC}"
        fi
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

echo -e "${BOLD}${MAGENTA}Record Screen + Audio — OpenTF${NC}"
echo ""

# Check dependencies
check_deps

# Discover devices
discover_devices
print_devices

# Interactive menu loop
while true; do
    show_menu
    read -rp "  Choose [1-5/q]: " choice

    case "$choice" in
        1)
            OPT_SCREEN=$( [[ "$OPT_SCREEN" == true ]] && echo false || echo true )
            ;;
        2)
            OPT_SYSTEM_AUDIO=$( [[ "$OPT_SYSTEM_AUDIO" == true ]] && echo false || echo true )
            ;;
        3)
            OPT_MIC=$( [[ "$OPT_MIC" == true ]] && echo false || echo true )
            ;;
        4)
            echo ""
            if [[ "$OPT_SCREEN" == true ]]; then
                SCREEN_IDX=$(pick_device "video" "Screen to record" "$(find_screen_device)")
            fi
            if [[ "$OPT_SYSTEM_AUDIO" == true ]]; then
                local_bh=$(find_audio_device "blackhole" || echo "0")
                BLACKHOLE_IDX=$(pick_device "audio" "System Audio device (BlackHole)" "$local_bh")
                BLACKHOLE_NAME="${AUDIO_DEVICES_NAME[$BLACKHOLE_IDX]:-BlackHole}"
            fi
            if [[ "$OPT_MIC" == true ]]; then
                # Default to first non-BlackHole audio device
                local def_mic="0"
                for i in "${!AUDIO_DEVICES_NAME[@]}"; do
                    if echo "${AUDIO_DEVICES_NAME[$i]}" | grep -qiv "blackhole\|multi-output"; then
                        def_mic="${AUDIO_DEVICES_IDX[$i]}"
                        break
                    fi
                done
                MIC_IDX=$(pick_device "audio" "Microphone" "$def_mic")
                MIC_NAME="${AUDIO_DEVICES_NAME[$MIC_IDX]:-Microphone}"
            fi
            read -rp "Press Enter to continue..."
            ;;
        5)
            start_recording
            echo ""
            read -rp "Press Enter to return to menu..."
            ;;
        q|Q)
            echo -e "${GREEN}Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}Invalid option.${NC}"
            sleep 1
            ;;
    esac
done
