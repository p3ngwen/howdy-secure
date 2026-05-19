"""
Setup wizard — walks the user through initial install, re-enrollment, and removal.
"""

import os
import sys
import shutil
import getpass
import subprocess
from pathlib import Path

from tpm_utils import (
    check_tpm,
    seal_secret,
    unseal_secret,
    tpm_handle_exists,
    evict_tpm_handle,
    TPM_HANDLE,
)
from recovery import generate_recovery_code, save_recovery_hash
from pam_config import install_pam_module, remove_pam_module, pam_module_installed

CONFIG_DIR = Path("/etc/howdy-secure")
SEALED_BLOB = CONFIG_DIR / "sealed.blob"
RECOVERY_HASH = CONFIG_DIR / "recovery.hash"

PREREQS = {
    "tpm2_createprimary": "tpm2-tools",
    "howdy":              "howdy",
    "dbus-send":          "dbus-daemon",
}

BANNER = """
╔══════════════════════════════════════════════════════╗
║           Howdy Secure  —  Setup Wizard              ║
║  Seamless GNOME Keyring unlock via face auth + TPM   ║
╚══════════════════════════════════════════════════════╝
"""

def print_step(n, total, msg):
    print(f"\n[{n}/{total}] {msg}")

def abort(msg):
    print(f"\nAborted: {msg}")
    sys.exit(1)

def confirm(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")

def check_prerequisites():
    print_step(1, 6, "Checking prerequisites...")
    missing = []
    for binary, pkg in PREREQS.items():
        if not shutil.which(binary):
            missing.append(pkg)
    if missing:
        print("  Missing packages:", ", ".join(missing))
        print("  Install with:  sudo apt install", " ".join(missing))
        abort("prerequisites not satisfied")
    print("  All prerequisites found.")

def check_tpm_available():
    print_step(2, 6, "Checking TPM availability...")
    ok, msg = check_tpm()
    if not ok:
        abort(f"TPM check failed: {msg}")
    print(f"  {msg}")

def get_keyring_password():
    print_step(3, 6, "Keyring password")
    print("  Enter your GNOME Keyring (Login keyring) password.")
    print("  This is usually your login password.")
    for _ in range(3):
        password = getpass.getpass("  Keyring password: ")
        confirm_pw = getpass.getpass("  Confirm password: ")
        if password == confirm_pw:
            return password
        print("  Passwords do not match, try again.")
    abort("too many failed attempts")

def seal_into_tpm(password: str):
    print_step(4, 6, "Sealing password into TPM...")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)

    if tpm_handle_exists(TPM_HANDLE):
        evict_tpm_handle(TPM_HANDLE)

    ok, msg = seal_secret(password.encode(), SEALED_BLOB)
    if not ok:
        abort(f"TPM sealing failed: {msg}")
    print(f"  Password sealed successfully.")

def setup_recovery():
    print_step(5, 6, "Generating recovery code...")
    code = generate_recovery_code()
    save_recovery_hash(code, RECOVERY_HASH)
    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │          YOUR RECOVERY CODE                 │")
    print("  │                                             │")
    print(f"  │   {code}   │")
    print("  │                                             │")
    print("  │  Write this down and store it somewhere     │")
    print("  │  safe. You will need it if your TPM is      │")
    print("  │  reset or your kernel is updated.           │")
    print("  └─────────────────────────────────────────────┘")
    print()
    if not confirm("  Have you saved your recovery code?", default=False):
        abort("Please save your recovery code before continuing.")

def install_pam():
    print_step(6, 6, "Installing PAM module...")
    if pam_module_installed():
        print("  PAM module already installed.")
        return
    ok, msg = install_pam_module()
    if not ok:
        abort(f"PAM installation failed: {msg}")
    print(f"  {msg}")

def run_smoke_test():
    print("\nRunning smoke test — attempting to unseal from TPM...")
    secret, msg = unseal_secret(SEALED_BLOB)
    if secret is None:
        print(f"  Smoke test FAILED: {msg}")
        print("  The setup completed but unsealing did not work as expected.")
        print("  Run 'howdy-secure status' to diagnose.")
        return
    print("  Smoke test passed — TPM unseal successful.")

def run_setup():
    print(BANNER)
    print("This wizard will set up seamless GNOME Keyring unlock using")
    print("Howdy face authentication and your system TPM.\n")

    if pam_module_installed():
        print("Howdy Secure appears to already be installed.")
        if not confirm("Re-run setup (this will re-seal your password)?", default=False):
            abort("setup cancelled")

    check_prerequisites()
    check_tpm_available()
    password = get_keyring_password()
    seal_into_tpm(password)
    setup_recovery()
    install_pam()
    run_smoke_test()

    print("\n✓ Setup complete! Your next login should unlock the keyring automatically.")
    print("  If you run into issues, use 'howdy-secure recover' with your recovery code.\n")

def run_enroll():
    """Re-seal after a kernel update changed TPM PCR values."""
    print(BANNER.replace("Setup Wizard", "Re-Enrollment"))
    print("Re-sealing your keyring password into the TPM.")
    print("Use this after a kernel update causes keyring unlock to fail.\n")

    if not CONFIG_DIR.exists():
        abort("Howdy Secure is not set up. Run 'howdy-secure setup' first.")

    check_tpm_available()
    password = get_keyring_password()
    seal_into_tpm(password)
    run_smoke_test()
    print("\n✓ Re-enrollment complete.\n")

def run_remove():
    print(BANNER.replace("Setup Wizard", "Uninstall"))
    print("This will remove Howdy Secure and restore your original PAM config.\n")

    if not confirm("Are you sure you want to uninstall Howdy Secure?", default=False):
        abort("uninstall cancelled")

    print("\nRemoving PAM module...")
    ok, msg = remove_pam_module()
    print(f"  {msg}")

    if tpm_handle_exists(TPM_HANDLE):
        print("Evicting TPM handle...")
        evict_tpm_handle(TPM_HANDLE)
        print("  TPM handle evicted.")

    if CONFIG_DIR.exists():
        shutil.rmtree(CONFIG_DIR)
        print(f"  Removed {CONFIG_DIR}")

    print("\n✓ Howdy Secure has been removed.\n")
