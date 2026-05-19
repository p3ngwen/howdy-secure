"""
Recovery code generation and verification.

A recovery code lets the user re-seal their keyring password into the TPM
after a TPM reset or hardware change, without having to know their original
keyring password (just the recovery code + their new password).

We store a bcrypt hash of the recovery code so we can verify it without
storing the code itself in plaintext.
"""

import os
import sys
import secrets
import hashlib
import getpass
from pathlib import Path

from tpm_utils import seal_secret, check_tpm, TPM_HANDLE, tpm_handle_exists, evict_tpm_handle

CONFIG_DIR = Path("/etc/howdy-secure")
SEALED_BLOB = CONFIG_DIR / "sealed.blob"
RECOVERY_HASH = CONFIG_DIR / "recovery.hash"

def generate_recovery_code() -> str:
    """Generate a human-readable 5-group recovery code (like BitLocker)."""
    groups = [secrets.token_hex(3).upper() for _ in range(5)]
    return "-".join(groups)

def _hash_code(code: str) -> str:
    """SHA-256 hash with a stored salt (salt:hash hex)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.sha256(salt + code.encode()).hexdigest()
    return salt.hex() + ":" + digest

def _verify_code(code: str, stored: str) -> bool:
    parts = stored.strip().split(":")
    if len(parts) != 2:
        return False
    salt = bytes.fromhex(parts[0])
    expected = parts[1]
    digest = hashlib.sha256(salt + code.encode()).hexdigest()
    return secrets.compare_digest(digest, expected)

def save_recovery_hash(code: str, path: Path):
    path.write_text(_hash_code(code))
    path.chmod(0o600)

def run_recovery():
    print("""
╔══════════════════════════════════════════════════════╗
║          Howdy Secure  —  Recovery                  ║
╚══════════════════════════════════════════════════════╝
""")
    if not RECOVERY_HASH.exists():
        print("Error: no recovery hash found. Is Howdy Secure set up?")
        sys.exit(1)

    stored = RECOVERY_HASH.read_text().strip()

    print("Enter your recovery code (format: XXXXXX-XXXXXX-XXXXXX-XXXXXX-XXXXXX):")
    for attempt in range(3):
        code = input("Recovery code: ").strip().upper()
        if _verify_code(code, stored):
            print("  Recovery code accepted.\n")
            break
        print(f"  Incorrect code. {2 - attempt} attempt(s) remaining.")
    else:
        print("Too many failed attempts.")
        sys.exit(1)

    ok, msg = check_tpm()
    if not ok:
        print(f"TPM check failed: {msg}")
        sys.exit(1)

    print("Enter your new keyring password to re-seal into TPM:")
    for _ in range(3):
        password = getpass.getpass("New keyring password: ")
        confirm  = getpass.getpass("Confirm password: ")
        if password == confirm:
            break
        print("Passwords do not match.")
    else:
        print("Too many failed attempts.")
        sys.exit(1)

    if tpm_handle_exists(TPM_HANDLE):
        evict_tpm_handle(TPM_HANDLE)

    ok, err = seal_secret(password.encode(), SEALED_BLOB)
    if not ok:
        print(f"Failed to seal: {err}")
        sys.exit(1)

    # Generate a fresh recovery code for next time
    new_code = generate_recovery_code()
    save_recovery_hash(new_code, RECOVERY_HASH)

    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │       YOUR NEW RECOVERY CODE                │")
    print("  │                                             │")
    print(f"  │   {new_code}   │")
    print("  │                                             │")
    print("  │  Your old code is now invalid. Save this.  │")
    print("  └─────────────────────────────────────────────┘")
    print()
    print("✓ Recovery complete. You can log out and back in to test.\n")
