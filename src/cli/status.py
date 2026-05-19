"""
Show current installation status.
"""

import shutil
from pathlib import Path

from tpm_utils import check_tpm, tpm_handle_exists, unseal_secret, TPM_HANDLE
from pam_config import pam_module_installed

CONFIG_DIR   = Path("/etc/howdy-secure")
SEALED_BLOB  = CONFIG_DIR / "sealed.blob"
RECOVERY_HASH = CONFIG_DIR / "recovery.hash"

def tick(ok: bool) -> str:
    return "✓" if ok else "✗"

def run_status():
    print("\nhowdy-secure status\n" + "─" * 40)

    tpm_ok, tpm_msg = check_tpm()
    print(f"  {tick(tpm_ok)} TPM2: {tpm_msg}")

    blob_ok = SEALED_BLOB.exists()
    print(f"  {tick(blob_ok)} Sealed blob: {'found' if blob_ok else 'not found — run setup'}")

    recovery_ok = RECOVERY_HASH.exists()
    print(f"  {tick(recovery_ok)} Recovery hash: {'found' if recovery_ok else 'not found'}")

    pam_ok = pam_module_installed()
    print(f"  {tick(pam_ok)} PAM module: {'installed' if pam_ok else 'not installed'}")

    howdy_ok = bool(shutil.which("howdy"))
    print(f"  {tick(howdy_ok)} Howdy: {'found' if howdy_ok else 'not found'}")

    if blob_ok and tpm_ok:
        secret, err = unseal_secret(SEALED_BLOB)
        unseal_ok = secret is not None
        print(f"  {tick(unseal_ok)} TPM unseal test: {'passed' if unseal_ok else f'FAILED ({err})'}")

    all_ok = all([tpm_ok, blob_ok, recovery_ok, pam_ok, howdy_ok])
    print()
    if all_ok:
        print("  Everything looks good. Face auth should unlock the keyring on login.")
    else:
        print("  Some checks failed. Run 'sudo howdy-secure setup' to fix.")
    print()
