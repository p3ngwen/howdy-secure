# Howdy Secure

Seamless GNOME Keyring unlock using Howdy face authentication and TPM2.

After a successful face-auth login, your keyring unlocks automatically — no password popup.

---

## How it works

```
Login screen
    └─ Howdy authenticates your face  ──► PAM_SUCCESS
           └─ pam_howdy_secure.so runs
                  └─ Calls howdy-secure-unseal (setuid helper)
                         └─ TPM2 unseals your keyring password
                                └─ Password passed to pam_gnome_keyring
                                       └─ Keyring unlocks ✓
```

Your keyring password is sealed into the TPM at setup time. It can only be unsealed on the same machine, in the same hardware state. If you reset the TPM or wipe the machine, you use your recovery code to re-seal.

---

## Requirements

- Ubuntu 22.04+ / Zorin OS / Debian-based distro
- Howdy (`sudo apt install howdy`) with at least one face model enrolled
- `tpm2-tools` (`sudo apt install tpm2-tools`)
- TPM2 chip (check: `ls /dev/tpm*`)
- GNOME desktop (GDM login manager)

---

## Installation

### Quick install (recommended)

The install script handles everything end-to-end: IR emitter → Howdy → Howdy Secure.

```bash
git clone https://github.com/your-username/howdy-secure
cd howdy-secure
sudo ./install.sh
```

### Manual install

If you already have Howdy set up and just want Howdy Secure:

```bash
# 1. Install build dependencies
sudo apt install build-essential libpam0g-dev tpm2-tools

# 2. Build and install
make
sudo make install

# 3. Run setup wizard
sudo howdy-secure setup
```

### IR camera setup

If your laptop has an IR camera for face auth, you need
[linux-enable-ir-emitter](https://github.com/EmixamPP/linux-enable-ir-emitter)
to activate the IR emitter before Howdy can see your face:

```bash
bash <(curl -s https://raw.githubusercontent.com/EmixamPP/linux-enable-ir-emitter/master/scripts/install.sh)
sudo linux-enable-ir-emitter configure
sudo systemctl enable --now linux-enable-ir-emitter
```

Run `configure` once — it walks you through finding the right UVC control for
your specific camera. The systemd service replays it on every boot.

### Howdy on Ubuntu 24.04+

Howdy's postinst script tries to compile dlib 19.x from source, which breaks
on Ubuntu 24.04+ due to pip's externally-managed-environment restriction and
can take 30+ minutes even when it works. Pre-install dlib yourself first to
avoid this entirely:

```bash
sudo apt install python3-pip cmake build-essential python3-dev libopenblas-dev
pip3 install --break-system-packages dlib opencv-python
DEBIAN_FRONTEND=noninteractive sudo apt install howdy
```

The `DEBIAN_FRONTEND=noninteractive` flag skips the interactive camera picker,
which causes the postinst to exit early without attempting the dlib build.
Configure the camera device manually afterward:

```bash
sudo sed -i 's|device_path = .*|device_path = /dev/videoN|' /lib/security/howdy/config.ini
sudo howdy add
```

---

## Usage

| Command | Description |
|---|---|
| `sudo howdy-secure setup` | First-time setup |
| `sudo howdy-secure enroll` | Re-seal after kernel update |
| `sudo howdy-secure recover` | Recover using recovery code |
| `howdy-secure status` | Check installation health |
| `sudo howdy-secure remove` | Uninstall everything |

---

## Recovery

During setup you receive a **recovery code** that looks like:

```
A1B2C3-D4E5F6-789ABC-DEF012-345678
```

Write it down and store it somewhere safe. If your TPM state changes (kernel update, BIOS update, TPM reset), unsealing will fail and you'll get the keyring popup again. Run `sudo howdy-secure enroll` to re-seal, or `sudo howdy-secure recover` if you need your recovery code.

---

## Security model

- Your keyring password is sealed into the TPM using `tpm2_create` with the owner hierarchy.
- The sealed blob (`/etc/howdy-secure/sealed.blob`) is useless without access to the same TPM.
- The unseal helper is setuid root and validates that the blob path is under `/etc/howdy-secure/`.
- PAM module is marked `optional` so a TPM failure falls back gracefully (keyring popup instead of lockout).
- Recovery codes are never stored plaintext — only a salted SHA-256 hash is kept.

---

## Project structure

```
src/
  pam/
    pam_howdy_secure.c      — PAM module (C)
    howdy_secure_unseal.c   — setuid unseal helper (C)
  cli/
    howdy-secure            — CLI entry point
    setup_wizard.py         — setup / enroll / remove logic
    tpm_utils.py            — TPM2 seal/unseal via tpm2-tools
    recovery.py             — recovery code generation and verification
    pam_config.py           — PAM file editing
    status.py               — installation health check
Makefile
```

---

## Contributing

PRs welcome. Priorities for future versions:

- [ ] KWallet support (KDE)
- [ ] fprintd fingerprint backend (in addition to Howdy)
- [ ] PCR policy binding (optional, for users who want stricter TPM attestation)
- [ ] Fedora / Arch packaging
- [ ] GUI enrollment wizard (GTK)

---

## License

MIT
