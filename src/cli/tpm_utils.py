"""
TPM2 operations — seal/unseal a secret using tpm2-tools.

We use a simple sealing policy: the primary key is created under the owner
hierarchy with no PCR policy by default. PCR binding is opt-in (future feature)
to avoid lockout on kernel updates.
"""

import os
import subprocess
import tempfile
from pathlib import Path

TPM_HANDLE = "0x81000100"   # persistent handle for the sealed object
PRIMARY_CTX = "/run/howdy-secure-primary.ctx"

def _run(cmd: list[str], input_data: bytes = None) -> tuple[bool, str, bytes]:
    """Run a tpm2-tools command. Returns (success, stderr_msg, stdout_bytes)."""
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            return False, result.stderr.decode(errors="replace").strip(), b""
        return True, "", result.stdout
    except FileNotFoundError:
        return False, f"command not found: {cmd[0]}", b""
    except subprocess.TimeoutExpired:
        return False, f"tpm2 command timed out: {' '.join(cmd)}", b""

def check_tpm() -> tuple[bool, str]:
    ok, err, _ = _run(["tpm2_getcap", "properties-fixed"])
    if not ok:
        return False, f"TPM not accessible: {err}"
    return True, "TPM2 is accessible and responding."

def _create_primary(ctx_path: str) -> tuple[bool, str]:
    ok, err, _ = _run([
        "tpm2_createprimary",
        "--hierarchy", "o",
        "--key-algorithm", "rsa2048:null:aes128cfb",
        "--key-context", ctx_path,
    ])
    return ok, err

def seal_secret(secret: bytes, blob_path: Path) -> tuple[bool, str]:
    """Seal `secret` into the TPM, writing the sealed blob to `blob_path`."""
    with tempfile.TemporaryDirectory(prefix="howdy-tpm-") as tmp:
        primary_ctx = os.path.join(tmp, "primary.ctx")
        pub_file    = os.path.join(tmp, "sealed.pub")
        priv_file   = os.path.join(tmp, "sealed.priv")

        ok, err = _create_primary(primary_ctx)
        if not ok:
            return False, f"createprimary failed: {err}"

        ok, err, _ = _run([
            "tpm2_create",
            "--parent-context", primary_ctx,
            "--sealing-input", "-",        # read secret from stdin
            "--public", pub_file,
            "--private", priv_file,
            "--attributes", "fixedtpm|fixedparent|noda|userwithauth",
        ], input_data=secret)
        if not ok:
            return False, f"tpm2_create failed: {err}"

        # Pack pub + priv into a single blob file we can ship to unseal later
        import struct
        pub_data  = Path(pub_file).read_bytes()
        priv_data = Path(priv_file).read_bytes()
        with open(blob_path, "wb") as f:
            # simple format: [4-byte pub_len][pub][priv]
            f.write(struct.pack(">I", len(pub_data)))
            f.write(pub_data)
            f.write(priv_data)
        blob_path.chmod(0o600)

    return True, "sealed"

def unseal_secret(blob_path: Path) -> tuple[bytes | None, str]:
    """Unseal and return the secret bytes, or (None, error_msg) on failure."""
    import struct

    if not blob_path.exists():
        return None, f"sealed blob not found at {blob_path}"

    raw = blob_path.read_bytes()
    if len(raw) < 4:
        return None, "sealed blob is corrupt"

    pub_len = struct.unpack(">I", raw[:4])[0]
    pub_data  = raw[4 : 4 + pub_len]
    priv_data = raw[4 + pub_len:]

    with tempfile.TemporaryDirectory(prefix="howdy-tpm-") as tmp:
        primary_ctx = os.path.join(tmp, "primary.ctx")
        pub_file    = os.path.join(tmp, "sealed.pub")
        priv_file   = os.path.join(tmp, "sealed.priv")
        obj_ctx     = os.path.join(tmp, "sealed.ctx")

        Path(pub_file).write_bytes(pub_data)
        Path(priv_file).write_bytes(priv_data)

        ok, err = _create_primary(primary_ctx)
        if not ok:
            return None, f"createprimary failed: {err}"

        ok, err, _ = _run([
            "tpm2_load",
            "--parent-context", primary_ctx,
            "--public", pub_file,
            "--private", priv_file,
            "--key-context", obj_ctx,
        ])
        if not ok:
            return None, f"tpm2_load failed: {err}"

        ok, err, stdout = _run([
            "tpm2_unseal",
            "--object-context", obj_ctx,
        ])
        if not ok:
            return None, f"tpm2_unseal failed: {err}"

    return stdout, "ok"

def tpm_handle_exists(handle: str) -> bool:
    ok, _, stdout = _run(["tpm2_getcap", "handles-persistent"])
    if not ok:
        return False
    return handle.lower() in stdout.decode(errors="replace").lower()

def evict_tpm_handle(handle: str):
    _run(["tpm2_evictcontrol", "--hierarchy", "o", "--object", handle])
