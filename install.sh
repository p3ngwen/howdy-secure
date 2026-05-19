#!/usr/bin/env bash
# Full install wizard: IR emitter → Howdy face auth → Howdy Secure keyring unlock
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_USER="${SUDO_USER:-$USER}"
HOWDY_CONFIG="/lib/security/howdy/config.ini"
HOWDY_MODELS="/lib/security/howdy/models"

step()   { echo -e "\n${BLUE}${BOLD}[$1/$2]${NC} ${BOLD}$3${NC}"; }
ok()     { echo -e "  ${GREEN}✓${NC} $1"; }
info()   { echo -e "  ${YELLOW}→${NC} $1"; }
die()    { echo -e "\n${RED}Error:${NC} $1" >&2; exit 1; }

confirm() {
    local prompt="$1" default="${2:-y}"
    local hint=$([[ "$default" == "y" ]] && echo "Y/n" || echo "y/N")
    read -rp "  $prompt [$hint]: " ans
    ans="${ans:-$default}"
    [[ "${ans,,}" == y* ]]
}

[[ $EUID -eq 0 ]] || die "Run with sudo:  sudo ./install.sh"
[[ -n "$REAL_USER" && "$REAL_USER" != "root" ]] || die "Run via sudo as your normal user, not directly as root."

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        Howdy Secure  —  Full Install Wizard          ║${NC}"
echo -e "${BOLD}║  IR camera → face auth → seamless keyring unlock     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Installing for user: ${BOLD}${REAL_USER}${NC}"
echo ""
echo "  Steps:"
echo "    1. IR emitter support  (linux-enable-ir-emitter)"
echo "    2. Howdy face auth     (face enrollment)"
echo "    3. Howdy Secure        (TPM seal + PAM config)"
echo ""

# ── Step 1: IR emitter ────────────────────────────────────────────────────────

step 1 3 "IR emitter"

if confirm "Does your device have an IR/infrared camera for face authentication?" y; then

    if command -v linux-enable-ir-emitter &>/dev/null; then
        ver=$(linux-enable-ir-emitter --version 2>&1 | head -1 || echo "unknown version")
        ok "linux-enable-ir-emitter already installed ($ver)"
    else
        echo ""
        echo "  linux-enable-ir-emitter is not installed."
        echo ""
        echo "  Install it with:"
        echo "    bash <(curl -s https://raw.githubusercontent.com/EmixamPP/linux-enable-ir-emitter/master/scripts/install.sh)"
        echo ""
        echo "  Then re-run this script."
        echo ""
        die "Install linux-enable-ir-emitter first, then re-run: sudo ./install.sh"
    fi

    if systemctl is-enabled linux-enable-ir-emitter &>/dev/null; then
        ok "IR emitter service already configured and enabled."
    else
        echo ""
        echo "  The IR emitter needs to be configured for your specific camera."
        echo "  The wizard will capture frames from each video device and ask"
        echo "  whether your IR emitter lit up. Follow the prompts carefully."
        echo ""
        linux-enable-ir-emitter configure
        systemctl enable --now linux-enable-ir-emitter
        ok "IR emitter configured and enabled."
    fi

else
    info "Skipping IR emitter — continuing with standard camera."
fi

# ── Step 2: Howdy ─────────────────────────────────────────────────────────────

step 2 3 "Howdy face authentication"

if ! command -v howdy &>/dev/null; then
    # Pre-install dlib and opencv ourselves so Howdy's postinst doesn't try to
    # compile dlib 19.16 from source (10-30 min, and broken on Ubuntu 24.04+
    # due to pip's externally-managed-environment restriction).
    # dlib 20.x ships pre-built wheels, so this takes seconds instead.
    info "Installing Howdy dependencies (dlib, opencv)..."
    apt-get install -y python3-pip cmake build-essential python3-dev libopenblas-dev

    # --break-system-packages is required on Ubuntu 24.04+; harmless on older
    pip3 install --break-system-packages --quiet dlib opencv-python 2>/dev/null \
        || pip3 install --quiet dlib opencv-python
    ok "dlib and opencv installed."

    info "Installing Howdy (this may take a minute)..."
    add-apt-repository -y ppa:boltgolt/howdy
    apt-get update -q
    # DEBIAN_FRONTEND=noninteractive skips Howdy's interactive camera picker,
    # which causes the postinst to take the early-exit path — bypassing its
    # broken dlib-from-source compilation. We handle camera config below.
    DEBIAN_FRONTEND=noninteractive apt-get install -y howdy
    ok "Howdy installed."
else
    ok "Howdy already installed."
fi

# Camera device
current_device=$(grep -Po '(?<=device_path = )\S+' "$HOWDY_CONFIG" 2>/dev/null || true)

if [[ -z "$current_device" || "$current_device" == "None" || ! -e "$current_device" ]]; then
    echo ""
    echo "  Available video devices:"
    for dev in /dev/video*; do
        product=$(udevadm info --query=property --name="$dev" 2>/dev/null \
            | grep "ID_V4L_PRODUCT" | cut -d= -f2 || echo "unknown")
        echo "    $dev  ($product)"
    done
    echo ""
    echo "  Tip: The IR camera is often a higher-numbered device (e.g. /dev/video2)."
    echo "       You can test with:  mpv --demuxer-lavf-format=video4linux2 /dev/videoN"
    echo ""
    read -rp "  Enter IR camera device path: " cam_dev
    [[ -e "$cam_dev" ]] || die "Device '$cam_dev' not found."
    sed -i "s|device_path = .*|device_path = $cam_dev|" "$HOWDY_CONFIG"
    ok "Camera set to $cam_dev"
else
    ok "Camera already configured: $current_device"
fi

# Face enrollment
if [[ -f "${HOWDY_MODELS}/${REAL_USER}.dat" ]]; then
    ok "Face model already enrolled for ${REAL_USER}."
    if confirm "Enroll an additional face model (e.g. glasses, different lighting)?" n; then
        howdy add -U "$REAL_USER"
        ok "Additional face model enrolled."
    fi
else
    echo ""
    info "No face model found — starting enrollment for: ${REAL_USER}"
    echo "  Look directly at the IR camera when the capture starts."
    echo ""
    howdy add -U "$REAL_USER"
    ok "Face enrolled."
fi

# Quick auth test
echo ""
if confirm "Test face recognition now? (recommended)" y; then
    echo "  Look at the camera..."
    if howdy test -U "$REAL_USER" 2>/dev/null; then
        ok "Face recognition test passed."
    else
        echo ""
        echo -e "  ${YELLOW}Warning:${NC} Test did not pass. You can continue and troubleshoot later."
        echo "  Try adjusting lighting or re-enrolling with: sudo howdy add"
        confirm "Continue anyway?" y || die "Aborted. Fix face auth then re-run."
    fi
fi

# ── Step 3: Howdy Secure ──────────────────────────────────────────────────────

step 3 3 "Howdy Secure (build + install + setup)"

# Build dependencies
missing_pkgs=()
dpkg -s build-essential &>/dev/null 2>&1 || missing_pkgs+=(build-essential)
dpkg -s libpam0g-dev    &>/dev/null 2>&1 || missing_pkgs+=(libpam0g-dev)
dpkg -s tpm2-tools      &>/dev/null 2>&1 || missing_pkgs+=(tpm2-tools)
dpkg -s python3         &>/dev/null 2>&1 || missing_pkgs+=(python3)

if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
    info "Installing build dependencies: ${missing_pkgs[*]}"
    apt-get install -y "${missing_pkgs[@]}"
    ok "Dependencies installed."
fi

info "Building Howdy Secure..."
make -C "$SCRIPT_DIR" clean all

info "Installing binaries..."
make -C "$SCRIPT_DIR" install
ok "Howdy Secure installed."

echo ""
echo "  Now running the Howdy Secure setup wizard."
echo "  You will need your GNOME Keyring (login) password"
echo "  and will receive a recovery code — write it down."
echo ""
howdy-secure setup

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║                   All done!                         ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Log out and back in — your keyring will unlock automatically"
echo "  when Howdy recognizes your face at the login screen."
echo ""
echo "  Useful commands:"
echo "    howdy-secure status        check everything is healthy"
echo "    sudo howdy-secure enroll   re-seal after a kernel update"
echo "    sudo howdy-secure recover  use recovery code if unsealing fails"
echo "    sudo howdy test            test face recognition"
echo ""
