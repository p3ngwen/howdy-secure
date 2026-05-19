"""
PAM configuration management — install and remove the Howdy Secure PAM module.

We edit /etc/pam.d/gdm-password (and common-auth as a fallback) to add our
module after the Howdy auth line so it runs on successful face auth.
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

PAM_MODULE_SO  = "/usr/lib/security/pam_howdy_secure.so"
PAM_FILES      = [
    Path("/etc/pam.d/gdm-password"),
    Path("/etc/pam.d/login"),
]
PAM_INSERT_LINE = "auth    optional    pam_howdy_secure.so\n"
PAM_HOWDY_PATTERN = re.compile(r"^auth.*pam_python.*howdy", re.IGNORECASE)
PAM_MARKER = "# howdy-secure-managed\n"

def pam_module_installed() -> bool:
    for pam_file in PAM_FILES:
        if pam_file.exists() and "pam_howdy_secure" in pam_file.read_text():
            return True
    return False

def _backup(path: Path):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(f".bak-{ts}")
    shutil.copy2(path, backup)
    return backup

def install_pam_module() -> tuple[bool, str]:
    installed_any = False
    for pam_file in PAM_FILES:
        if not pam_file.exists():
            continue

        lines = pam_file.read_text().splitlines(keepends=True)

        if any("pam_howdy_secure" in l for l in lines):
            continue  # already installed in this file

        backup = _backup(pam_file)
        new_lines = []
        inserted = False
        for line in lines:
            new_lines.append(line)
            if PAM_HOWDY_PATTERN.match(line) and not inserted:
                new_lines.append(PAM_MARKER)
                new_lines.append(PAM_INSERT_LINE)
                inserted = True

        if not inserted:
            # Howdy line not found — append at end of auth block
            new_lines.append(PAM_MARKER)
            new_lines.append(PAM_INSERT_LINE)

        pam_file.write_text("".join(new_lines))
        installed_any = True

    if not installed_any:
        return False, "No supported PAM files found or module already installed everywhere."
    return True, "PAM module installed in gdm-password and/or login."

def remove_pam_module() -> tuple[bool, str]:
    removed_any = False
    for pam_file in PAM_FILES:
        if not pam_file.exists():
            continue
        text = pam_file.read_text()
        if "pam_howdy_secure" not in text and PAM_MARKER not in text:
            continue
        _backup(pam_file)
        lines = pam_file.read_text().splitlines(keepends=True)
        filtered = [
            l for l in lines
            if "pam_howdy_secure" not in l and l != PAM_MARKER
        ]
        pam_file.write_text("".join(filtered))
        removed_any = True

    if not removed_any:
        return False, "PAM module was not installed — nothing to remove."
    return True, "PAM module removed. Original files backed up with .bak-* suffix."
