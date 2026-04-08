#!/bin/bash
###############################################################################
#  Setup  —  Record Screen + Audio (OpenTF)
#  One-time setup: installs dependencies and creates the Multi-Output Device.
###############################################################################

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; MAGENTA='\033[0;35m'
BOLD='\033[1m'; NC='\033[0m'
CHECK="${GREEN}✓${NC}"; CROSS="${RED}✗${NC}"; ARROW="${CYAN}➜${NC}"

echo -e "${BOLD}${MAGENTA}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     Setup — Record Screen + Audio (OpenTF)    ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: Homebrew ─────────────────────────────────────────────────────────
echo -e "${BOLD}Step 1: Homebrew${NC}"
if command -v brew &>/dev/null; then
    echo -e "  ${CHECK} Homebrew is installed"
else
    echo -e "  ${CROSS} Homebrew not found"
    echo -e "  ${ARROW} Install it: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

# ── Step 2: ffmpeg ───────────────────────────────────────────────────────────
echo -e "\n${BOLD}Step 2: ffmpeg${NC}"
if command -v ffmpeg &>/dev/null; then
    echo -e "  ${CHECK} ffmpeg is installed ($(ffmpeg -version 2>&1 | head -1 | awk '{print $3}'))"
else
    echo -e "  ${ARROW} Installing ffmpeg..."
    brew install ffmpeg
    echo -e "  ${CHECK} ffmpeg installed"
fi

# ── Step 3: SwitchAudioSource ────────────────────────────────────────────────
echo -e "\n${BOLD}Step 3: SwitchAudioSource${NC}"
if command -v SwitchAudioSource &>/dev/null; then
    echo -e "  ${CHECK} SwitchAudioSource is installed"
else
    echo -e "  ${ARROW} Installing switchaudio-osx..."
    brew install switchaudio-osx
    echo -e "  ${CHECK} SwitchAudioSource installed"
fi

# ── Step 4: BlackHole ────────────────────────────────────────────────────────
echo -e "\n${BOLD}Step 4: BlackHole${NC}"
BH_FOUND=false
BH_LIST=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 || true)
if echo "$BH_LIST" | grep -qi "blackhole"; then
    BH_FOUND=true
    BH_NAME=$(echo "$BH_LIST" | grep -i "blackhole" | head -1 | sed 's/.*\] //')
    echo -e "  ${CHECK} BlackHole detected: ${BH_NAME}"
else
    echo -e "  ${CROSS} BlackHole not detected"
    echo -e "  ${ARROW} Download from: ${CYAN}https://existential.audio/blackhole/${NC}"
    echo -e "  ${ARROW} Install the 2ch version (recommended for this use case)"
    echo ""
    read -rp "  Press Enter after installing BlackHole to continue..."

    # Re-check
    BH_LIST=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 || true)
    if echo "$BH_LIST" | grep -qi "blackhole"; then
        echo -e "  ${CHECK} BlackHole now detected!"
        BH_FOUND=true
    else
        echo -e "  ${CROSS} Still not detected. You may need to restart your Mac."
        echo -e "  ${YELLOW}Continuing setup anyway...${NC}"
    fi
fi

# ── Step 5: Multi-Output Device ──────────────────────────────────────────────
echo -e "\n${BOLD}Step 5: Multi-Output Device${NC}"
echo -e "  This device lets you hear audio while BlackHole captures it."
echo ""

# Check if it already exists
MO_EXISTS=false
if command -v SwitchAudioSource &>/dev/null; then
    if SwitchAudioSource -a -t output 2>/dev/null | grep -qi "multi-output"; then
        MO_EXISTS=true
    fi
fi

if [[ "$MO_EXISTS" == true ]]; then
    echo -e "  ${CHECK} Multi-Output Device already exists!"
else
    echo -e "  ${YELLOW}Multi-Output Device not found. Let's create it.${NC}"
    echo ""
    echo -e "  ${BOLD}I'll open Audio MIDI Setup for you.${NC}"
    echo -e "  Follow these steps:"
    echo ""
    echo -e "  ${CYAN}1.${NC} Click the ${BOLD}+${NC} button at the bottom-left"
    echo -e "  ${CYAN}2.${NC} Select ${BOLD}\"Create Multi-Output Device\"${NC}"
    echo -e "  ${CYAN}3.${NC} Check these boxes in the device list:"
    echo -e "       ${CHECK} BlackHole 2ch"
    echo -e "       ${CHECK} MacBook Pro Speakers (or your headphones/speakers)"
    echo -e "  ${CYAN}4.${NC} Make sure ${BOLD}\"BlackHole 2ch\"${NC} is checked under ${BOLD}\"Drift Correction\"${NC}"
    echo -e "  ${CYAN}5.${NC} Close Audio MIDI Setup when done"
    echo ""

    read -rp "  Press Enter to open Audio MIDI Setup..."
    open "/Applications/Utilities/Audio MIDI Setup.app"

    echo ""
    read -rp "  Press Enter after creating the Multi-Output Device..."

    # Verify
    if command -v SwitchAudioSource &>/dev/null; then
        if SwitchAudioSource -a -t output 2>/dev/null | grep -qi "multi-output"; then
            echo -e "  ${CHECK} Multi-Output Device created successfully!"
        else
            echo -e "  ${YELLOW}Could not verify Multi-Output Device. It may still work.${NC}"
        fi
    fi
fi

# ── Step 6: Permissions ──────────────────────────────────────────────────────
echo -e "\n${BOLD}Step 6: macOS Permissions${NC}"
echo -e "  When you first run ${BOLD}record.sh${NC}, macOS will ask for:"
echo -e "    ${ARROW} Screen Recording permission"
echo -e "    ${ARROW} Microphone access"
echo -e "  ${BOLD}Grant both${NC} in System Settings → Privacy & Security."
echo -e "  You may need to add ${BOLD}Terminal${NC} (or iTerm) to the allowed apps."

# ── Step 7: Make scripts executable ──────────────────────────────────────────
echo -e "\n${BOLD}Step 7: Making scripts executable${NC}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$SCRIPT_DIR/record.sh" 2>/dev/null && echo -e "  ${CHECK} record.sh" || true
chmod +x "$SCRIPT_DIR/merge.sh"  2>/dev/null && echo -e "  ${CHECK} merge.sh"  || true
chmod +x "$SCRIPT_DIR/setup.sh"  2>/dev/null && echo -e "  ${CHECK} setup.sh"  || true

# ── Summary ──────────────────────────────────────────────────────────────────
echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  To start recording:"
echo -e "    ${CYAN}cd \"$SCRIPT_DIR\"${NC}"
echo -e "    ${CYAN}./record.sh${NC}"
echo ""
echo -e "  Recordings will be saved to:"
echo -e "    ${CYAN}$SCRIPT_DIR/Recordings/${NC}"
echo ""
