#!/usr/bin/env bash
# Build a .deb package for Howdy Secure.
# Requires: fakeroot, dpkg-deb, gcc, libpam0g-dev
# Usage: ./build-deb.sh [version]
set -euo pipefail

VERSION="${1:-1.0.0}"
ARCH=$(dpkg --print-architecture)
PKG_NAME="howdy-secure_${VERSION}_${ARCH}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="${SCRIPT_DIR}/dist"
STAGE="${DIST}/.stage/${PKG_NAME}"

# ── Preflight ──────────────────────────────────────────────────────────────────

for tool in fakeroot dpkg-deb gcc; do
    command -v "$tool" &>/dev/null || {
        echo "Missing: $tool"
        echo "Install with: sudo apt install fakeroot build-essential"
        exit 1
    }
done

dpkg -s libpam0g-dev &>/dev/null 2>&1 || {
    echo "Missing: libpam0g-dev"
    echo "Install with: sudo apt install libpam0g-dev"
    exit 1
}

# ── Compile ────────────────────────────────────────────────────────────────────

echo "Compiling..."
make -C "${SCRIPT_DIR}" clean all

# ── Stage ─────────────────────────────────────────────────────────────────────

echo "Staging package tree..."
rm -rf "${DIST}/.stage"
mkdir -p \
    "${STAGE}/DEBIAN" \
    "${STAGE}/usr/lib/security" \
    "${STAGE}/usr/local/bin" \
    "${STAGE}/usr/local/lib/howdy-secure/gui" \
    "${STAGE}/usr/share/applications" \
    "${STAGE}/etc/xdg/autostart"

# PAM module
install -m 644 "${SCRIPT_DIR}/pam_howdy_secure.so" \
    "${STAGE}/usr/lib/security/"

# Unseal helper (setuid 4755 applied by fakeroot + postinst chown/chmod)
install -m 755 "${SCRIPT_DIR}/howdy-secure-unseal" \
    "${STAGE}/usr/local/bin/"

# CLI Python library
install -m 644 "${SCRIPT_DIR}/src/cli/"*.py \
    "${STAGE}/usr/local/lib/howdy-secure/"

# CLI entry point
install -m 755 "${SCRIPT_DIR}/src/cli/howdy-secure" \
    "${STAGE}/usr/local/bin/"

# GUI
install -m 644 "${SCRIPT_DIR}/src/gui/installer.py" \
    "${STAGE}/usr/local/lib/howdy-secure/gui/"
install -m 644 "${SCRIPT_DIR}/src/gui/app.py" \
    "${STAGE}/usr/local/lib/howdy-secure/gui/"

# Launcher scripts
printf '#!/bin/sh\nexec python3 /usr/local/lib/howdy-secure/gui/installer.py "$@"\n' \
    > "${STAGE}/usr/local/bin/howdy-secure-installer"
chmod 755 "${STAGE}/usr/local/bin/howdy-secure-installer"

printf '#!/bin/sh\nexec python3 /usr/local/lib/howdy-secure/gui/app.py "$@"\n' \
    > "${STAGE}/usr/local/bin/howdy-secure-app"
chmod 755 "${STAGE}/usr/local/bin/howdy-secure-app"

# Desktop files
install -m 644 "${SCRIPT_DIR}/debian/howdy-secure.desktop" \
    "${STAGE}/usr/share/applications/"
install -m 644 "${SCRIPT_DIR}/debian/howdy-secure-tray.desktop" \
    "${STAGE}/etc/xdg/autostart/"

# DEBIAN control files
install -m 644 "${SCRIPT_DIR}/debian/control" "${STAGE}/DEBIAN/control"
# Update version in control file
sed -i "s/^Version: .*/Version: ${VERSION}/" "${STAGE}/DEBIAN/control"

install -m 755 "${SCRIPT_DIR}/debian/postinst" "${STAGE}/DEBIAN/postinst"
install -m 755 "${SCRIPT_DIR}/debian/prerm"    "${STAGE}/DEBIAN/prerm"

# Compute and inject installed size (kB)
INSTALLED_SIZE=$(du -sk "${STAGE}" | cut -f1)
sed -i "/^Description:/i Installed-Size: ${INSTALLED_SIZE}" "${STAGE}/DEBIAN/control"

# ── Build ──────────────────────────────────────────────────────────────────────

echo "Building ${PKG_NAME}.deb..."

fakeroot bash -c "
    chown -R root:root '${STAGE}'
    chmod 4755 '${STAGE}/usr/local/bin/howdy-secure-unseal'
    dpkg-deb --build '${STAGE}' '${DIST}/${PKG_NAME}.deb'
"

# Clean staging tree
rm -rf "${DIST}/.stage"

echo ""
echo "Done: dist/${PKG_NAME}.deb"
echo ""
echo "Install:   sudo dpkg -i dist/${PKG_NAME}.deb"
echo "Then run:  howdy-secure-installer"
