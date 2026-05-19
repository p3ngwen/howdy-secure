#!/usr/bin/env python3
"""
Howdy Secure — GTK3 Installer Wizard
Launched via: sudo python3 src/gui/installer.py
or via the howdy-secure-installer wrapper (uses pkexec).
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

try:
    gi.require_version('Vte', '2.91')
    from gi.repository import Vte
    HAS_VTE = True
except (ValueError, ImportError):
    HAS_VTE = False

import os
import sys
import pwd
import re
import subprocess
import threading
import shutil
from pathlib import Path

# ── Runtime identity ──────────────────────────────────────────────────────────
# Support both sudo (SUDO_USER) and pkexec (PKEXEC_UID).

def _resolve_real_user():
    u = os.environ.get('SUDO_USER')
    if u:
        return u
    uid_str = os.environ.get('PKEXEC_UID')
    if uid_str:
        try:
            pw = pwd.getpwuid(int(uid_str))
            os.environ['SUDO_USER'] = pw.pw_name   # so howdy picks it up
            return pw.pw_name
        except Exception:
            pass
    return os.environ.get('USER', '')

SCRIPT_DIR   = Path(__file__).resolve().parent.parent.parent
REAL_USER    = _resolve_real_user()
HOWDY_CONF   = Path('/lib/security/howdy/config.ini')
HOWDY_MODELS = Path('/lib/security/howdy/models')

# ── Helpers ───────────────────────────────────────────────────────────────────

def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)

def video_devices():
    devs = []
    for d in sorted(Path('/dev').glob('video*')):
        try:
            out = run(['udevadm', 'info', '--query=property', f'--name={d}']).stdout
            product = next(
                (l.split('=', 1)[1] for l in out.splitlines() if 'ID_V4L_PRODUCT' in l), '')
            label = f"{d}  ({product})" if product else str(d)
        except Exception:
            label = str(d)
        devs.append((str(d), label))
    return devs

def current_camera():
    try:
        for line in HOWDY_CONF.read_text().splitlines():
            if line.strip().startswith('device_path'):
                v = line.split('=', 1)[1].strip()
                return v if v and v.lower() != 'none' else None
    except Exception:
        return None

def face_enrolled():
    return (HOWDY_MODELS / f'{REAL_USER}.dat').exists()

def spawn_vte(terminal, argv):
    """Spawn argv in a Vte.Terminal, handling minor API differences."""
    try:
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, None, argv, None,
            GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, None, None,
        )
    except TypeError:
        terminal.spawn_sync(
            Vte.PtyFlags.DEFAULT, None, argv, None,
            GLib.SpawnFlags.SEARCH_PATH, None, None,
        )

def make_terminal(height=180):
    term = Vte.Terminal()
    term.set_size_request(-1, height)
    term.set_scroll_on_output(True)
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.add(term)
    return term, sw

def status_row(icon_name='content-loading-symbolic', text=''):
    box  = Gtk.Box(spacing=8)
    icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
    lbl  = Gtk.Label(label=text)
    lbl.set_halign(Gtk.Align.START)
    box.pack_start(icon, False, False, 0)
    box.pack_start(lbl,  True,  True,  0)
    return box, icon, lbl

# ── Page 0: Welcome ───────────────────────────────────────────────────────────

class WelcomePage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.set_border_width(40)

        title = Gtk.Label()
        title.set_markup('<span size="xx-large" weight="bold">Howdy Secure</span>')
        title.set_halign(Gtk.Align.START)

        tagline = Gtk.Label()
        tagline.set_markup(
            'Seamless GNOME Keyring unlock using face authentication and TPM.\n'
            'After setup, your keyring unlocks automatically at login — no password popup.'
        )
        tagline.set_line_wrap(True)
        tagline.set_halign(Gtk.Align.START)

        sep = Gtk.Separator()

        steps = Gtk.Label()
        steps.set_markup(
            '<b>This wizard will:</b>\n'
            '  1.  Configure the IR emitter  <i>(if your device has one)</i>\n'
            '  2.  Install and set up Howdy face recognition\n'
            '  3.  Seal your keyring password into the TPM\n'
        )
        steps.set_halign(Gtk.Align.START)

        user_note = Gtk.Label()
        user_note.set_markup(
            f'<span foreground="gray" size="small">'
            f'Setting up for user: <b>{REAL_USER}</b></span>'
        )
        user_note.set_halign(Gtk.Align.START)

        for w in [title, tagline, sep, steps, user_note]:
            self.pack_start(w, False, False, 0)


# ── Page 1: IR Emitter ────────────────────────────────────────────────────────

class IREmitterPage(Gtk.Box):
    def __init__(self, assistant):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_border_width(24)
        self.assistant = assistant

        title = Gtk.Label()
        title.set_markup('<span size="large" weight="bold">IR Camera Emitter</span>')
        title.set_halign(Gtk.Align.START)

        desc = Gtk.Label(
            label='Many laptops with IR cameras need the IR emitter manually activated on Linux. '
                  'Skip this step if you use a standard RGB webcam.'
        )
        desc.set_line_wrap(True)
        desc.set_max_width_chars(62)
        desc.set_halign(Gtk.Align.START)

        row, self.s_icon, self.s_label = status_row(text='Checking…')

        # Warning shown when linux-enable-ir-emitter isn't installed
        self.not_installed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        warn = Gtk.Label()
        warn.set_markup(
            '<b>linux-enable-ir-emitter</b> is not installed.\n'
            'Install it first, then re-run this installer:\n\n'
            '<tt>bash &lt;(curl -s https://github.com/EmixamPP/'
            'linux-enable-ir-emitter/releases/latest/download/install.sh)</tt>'
        )
        warn.set_line_wrap(True)
        warn.set_halign(Gtk.Align.START)
        self.not_installed_box.pack_start(warn, False, False, 0)
        self.not_installed_box.set_visible(False)
        self.not_installed_box.set_no_show_all(True)

        self.configure_btn = Gtk.Button(label='Run IR Emitter Setup…')
        self.configure_btn.set_sensitive(False)
        self.configure_btn.connect('clicked', self._on_configure)

        skip_btn = Gtk.Button(label='Skip — no IR camera')
        skip_btn.connect('clicked', lambda _: self._complete())

        btn_row = Gtk.Box(spacing=8)
        btn_row.pack_start(self.configure_btn, False, False, 0)
        btn_row.pack_end(skip_btn, False, False, 0)

        # Terminal for the interactive configure command
        term_frame = Gtk.Frame(label=' IR emitter configuration output ')
        if HAS_VTE:
            self.terminal, sw = make_terminal(200)
            self.terminal.connect('child-exited', self._on_configure_done)
            term_frame.add(sw)
        else:
            self.terminal = None
            term_frame.add(Gtk.Label(label='(A terminal window will open)'))
        term_frame.set_visible(False)
        term_frame.set_no_show_all(True)
        self.term_frame = term_frame

        for w in [title, desc, row, self.not_installed_box, btn_row, term_frame]:
            expand = (w is term_frame)
            self.pack_start(w, expand, expand, 0)

        GLib.idle_add(self._check)

    def _check(self):
        installed = bool(shutil.which('linux-enable-ir-emitter'))
        if not installed:
            self.s_icon.set_from_icon_name('dialog-warning', Gtk.IconSize.MENU)
            self.s_label.set_text('linux-enable-ir-emitter not installed — see instructions below')
            self.not_installed_box.set_visible(True)
            self._complete()  # allow skipping; they can install and re-run
            return

        rc = run(['systemctl', 'is-enabled', 'linux-enable-ir-emitter']).returncode
        if rc == 0:
            self.s_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.s_label.set_text('IR emitter already configured and enabled')
            self._complete()
        else:
            self.s_icon.set_from_icon_name('dialog-information', Gtk.IconSize.MENU)
            self.s_label.set_text('IR emitter installed — click Configure to set it up')
            self.configure_btn.set_sensitive(True)

    def _on_configure(self, btn):
        btn.set_sensitive(False)
        self.term_frame.set_visible(True)
        self.term_frame.show_all()
        if self.terminal:
            spawn_vte(self.terminal, ['linux-enable-ir-emitter', 'configure'])
        else:
            p = subprocess.Popen(
                ['x-terminal-emulator', '-e',
                 'linux-enable-ir-emitter configure; read -p "Press enter to close"'])
            def _wait():
                p.wait()
                run(['systemctl', 'enable', '--now', 'linux-enable-ir-emitter'])
                GLib.idle_add(self._mark_configured)
            threading.Thread(target=_wait, daemon=True).start()

    def _on_configure_done(self, _terminal, _status):
        run(['systemctl', 'enable', '--now', 'linux-enable-ir-emitter'])
        GLib.idle_add(self._mark_configured)

    def _mark_configured(self):
        self.s_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
        self.s_label.set_text('IR emitter configured and enabled')
        self._complete()

    def _complete(self):
        self.assistant.set_page_complete(self, True)


# ── Page 2: Howdy ─────────────────────────────────────────────────────────────

class HowdyPage(Gtk.Box):
    def __init__(self, assistant):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_border_width(24)
        self.assistant = assistant

        title = Gtk.Label()
        title.set_markup('<span size="large" weight="bold">Howdy Face Authentication</span>')
        title.set_halign(Gtk.Align.START)

        # ── Install ──
        install_frame = Gtk.Frame(label=' Installation ')
        ib = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ib.set_border_width(12)
        inst_row, self.inst_icon, self.inst_label = status_row(text='Checking…')
        self.install_btn  = Gtk.Button(label='Install Howdy')
        self.install_btn.set_no_show_all(True)
        self.install_btn.connect('clicked', self._on_install)
        self.install_prog = Gtk.ProgressBar()
        self.install_prog.set_no_show_all(True)
        for w in [inst_row, self.install_btn, self.install_prog]:
            ib.pack_start(w, False, False, 0)
        install_frame.add(ib)

        # ── Camera ──
        cam_frame = Gtk.Frame(label=' Camera device ')
        cb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cb.set_border_width(12)
        cam_row, self.cam_icon, self.cam_label = status_row(text='Detecting…')
        store = Gtk.ListStore(str, str)
        self.cam_combo = Gtk.ComboBox.new_with_model(store)
        r = Gtk.CellRendererText()
        self.cam_combo.pack_start(r, True)
        self.cam_combo.add_attribute(r, 'text', 1)
        self.cam_combo.connect('changed', self._on_cam_changed)
        tip = Gtk.Label()
        tip.set_markup(
            '<span size="small" foreground="gray">'
            'IR cameras are often a higher-numbered /dev/video device.\n'
            'Test with: <tt>mpv --demuxer-lavf-format=video4linux2 /dev/videoN</tt>'
            '</span>'
        )
        tip.set_halign(Gtk.Align.START)
        for w in [cam_row, self.cam_combo, tip]:
            cb.pack_start(w, False, False, 0)
        cam_frame.add(cb)

        # ── Enrollment ──
        enroll_frame = Gtk.Frame(label=' Face enrollment ')
        eb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        eb.set_border_width(12)
        enroll_row, self.enroll_icon, self.enroll_label = status_row(text='Checking…')
        self.enroll_btn = Gtk.Button(label='Enroll Face…')
        self.enroll_btn.set_sensitive(False)
        self.enroll_btn.connect('clicked', self._on_enroll)
        eb.pack_start(enroll_row, False, False, 0)
        eb.pack_start(self.enroll_btn, False, False, 0)
        if HAS_VTE:
            self.enroll_term, esw = make_terminal(150)
            self.enroll_term.connect('child-exited', self._on_enroll_done)
            eb.pack_start(esw, True, True, 0)
        else:
            self.enroll_term = None
        enroll_frame.add(eb)

        for w in [title, install_frame, cam_frame, enroll_frame]:
            self.pack_start(w, False, False, 0)

        GLib.idle_add(self._check_all)

    # ── checks ────────────────────────────────────────────────────────────────

    def _check_all(self):
        self._check_install()
        self._check_camera()
        self._check_enrollment()
        self._refresh_complete()

    def _check_install(self):
        if shutil.which('howdy'):
            self.inst_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.inst_label.set_text('Howdy is installed')
        else:
            self.inst_icon.set_from_icon_name('dialog-warning', Gtk.IconSize.MENU)
            self.inst_label.set_text('Howdy is not installed')
            self.install_btn.set_visible(True)
            self.install_btn.set_sensitive(True)

    def _check_camera(self):
        store = self.cam_combo.get_model()
        store.clear()
        devices = video_devices()
        cur = current_camera()
        active_idx = 0
        for i, (path, label) in enumerate(devices):
            store.append([path, label])
            if cur and path == cur:
                active_idx = i
        if devices:
            self.cam_combo.set_active(active_idx)
        if cur and Path(cur).exists():
            self.cam_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.cam_label.set_text(f'Current camera: {cur}')
        else:
            self.cam_icon.set_from_icon_name('dialog-information', Gtk.IconSize.MENU)
            self.cam_label.set_text('Select your IR camera from the list')

    def _check_enrollment(self):
        if face_enrolled():
            self.enroll_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.enroll_label.set_text(f'Face enrolled for {REAL_USER}')
            self.enroll_btn.set_label('Add another face model…')
            self.enroll_btn.set_sensitive(True)
        else:
            self.enroll_icon.set_from_icon_name('dialog-warning', Gtk.IconSize.MENU)
            self.enroll_label.set_text('No face model found — click Enroll to add one')
            self.enroll_btn.set_sensitive(bool(shutil.which('howdy')))

    # ── actions ───────────────────────────────────────────────────────────────

    def _on_cam_changed(self, combo):
        it = combo.get_active_iter()
        if not it:
            return
        path = combo.get_model()[it][0]
        try:
            text = HOWDY_CONF.read_text()
            text = re.sub(r'device_path = .*', f'device_path = {path}', text)
            HOWDY_CONF.write_text(text)
            self.cam_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.cam_label.set_text(f'Camera set to {path}')
        except Exception as e:
            self.cam_label.set_text(f'Could not write config: {e}')
        self._refresh_complete()

    def _on_install(self, btn):
        btn.set_sensitive(False)
        self.install_prog.set_visible(True)
        self.inst_label.set_text('Installing — this may take a minute…')
        threading.Thread(target=self._do_install, daemon=True).start()

    def _do_install(self):
        pulse_id = GLib.timeout_add(200, lambda: (self.install_prog.pulse(), True)[1])
        env = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}
        steps = [
            ['apt-get', 'install', '-y',
             'python3-pip', 'cmake', 'build-essential', 'python3-dev', 'libopenblas-dev'],
            ['pip3', 'install', '--break-system-packages', '--quiet', 'dlib', 'opencv-python'],
            ['add-apt-repository', '-y', 'ppa:boltgolt/howdy'],
            ['apt-get', 'update', '-q'],
            ['apt-get', 'install', '-y', 'howdy'],
        ]
        ok = all(subprocess.run(s, capture_output=True, env=env).returncode == 0 for s in steps)
        GLib.source_remove(pulse_id)
        GLib.idle_add(self._install_done, ok)

    def _install_done(self, ok):
        self.install_prog.set_visible(False)
        if ok:
            self.inst_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.inst_label.set_text('Howdy installed successfully')
            self.install_btn.set_visible(False)
            self.enroll_btn.set_sensitive(True)
        else:
            self.inst_icon.set_from_icon_name('dialog-error', Gtk.IconSize.MENU)
            self.inst_label.set_text('Installation failed — check system logs')
        self._refresh_complete()

    def _on_enroll(self, btn):
        btn.set_sensitive(False)
        if self.enroll_term:
            spawn_vte(self.enroll_term, ['howdy', 'add'])
        else:
            p = subprocess.Popen(
                ['x-terminal-emulator', '-e',
                 f'howdy add; read -p "Done. Press enter to close"'])
            threading.Thread(
                target=lambda: (p.wait(), GLib.idle_add(self._on_enroll_done, None, 0)),
                daemon=True).start()

    def _on_enroll_done(self, _term, _status):
        self.enroll_btn.set_sensitive(True)
        self._check_enrollment()
        GLib.idle_add(self._refresh_complete)

    def _refresh_complete(self):
        howdy_ok  = bool(shutil.which('howdy'))
        cam_ok    = self.cam_combo.get_active() >= 0
        enrolled  = face_enrolled()
        self.assistant.set_page_complete(self, howdy_ok and cam_ok and enrolled)


# ── Page 3: Howdy Secure ──────────────────────────────────────────────────────

class HowdySecurePage(Gtk.Box):
    def __init__(self, assistant):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_border_width(24)
        self.assistant = assistant

        title = Gtk.Label()
        title.set_markup('<span size="large" weight="bold">Howdy Secure Setup</span>')
        title.set_halign(Gtk.Align.START)

        desc = Gtk.Label(
            label='Howdy Secure will be built and installed. '
                  'Then the setup wizard will seal your keyring password into the TPM '
                  'and generate a recovery code — write it down.'
        )
        desc.set_line_wrap(True)
        desc.set_max_width_chars(62)
        desc.set_halign(Gtk.Align.START)

        build_row, self.build_icon, self.build_label = status_row(text='Ready to build')
        self.build_prog = Gtk.ProgressBar()
        self.build_prog.set_no_show_all(True)

        self.start_btn = Gtk.Button(label='Build & Install')
        self.start_btn.connect('clicked', self._on_start)

        setup_frame = Gtk.Frame(label=' Howdy Secure setup wizard ')
        if HAS_VTE:
            self.terminal, sw = make_terminal(280)
            self.terminal.connect('child-exited', self._on_setup_done)
            setup_frame.add(sw)
        else:
            self.terminal = None
            setup_frame.add(Gtk.Label(label='(A terminal window will open)'))
        setup_frame.set_visible(False)
        setup_frame.set_no_show_all(True)
        self.setup_frame = setup_frame

        for w in [title, desc, build_row, self.build_prog, self.start_btn, setup_frame]:
            expand = (w is setup_frame)
            self.pack_start(w, expand, expand, 0)

    def _on_start(self, btn):
        btn.set_sensitive(False)
        self.build_prog.set_visible(True)
        self.build_label.set_text('Building…')
        threading.Thread(target=self._do_build, daemon=True).start()

    def _do_build(self):
        pulse_id = GLib.timeout_add(150, lambda: (self.build_prog.pulse(), True)[1])
        env = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}

        subprocess.run(
            ['apt-get', 'install', '-y',
             'build-essential', 'libpam0g-dev', 'tpm2-tools', 'python3'],
            capture_output=True, env=env)

        rc  = subprocess.run(['make', '-C', str(SCRIPT_DIR), 'clean', 'all'],
                              capture_output=True).returncode
        rc2 = subprocess.run(['make', '-C', str(SCRIPT_DIR), 'install'],
                              capture_output=True).returncode if rc == 0 else 1

        GLib.source_remove(pulse_id)
        GLib.idle_add(self._build_done, rc == 0 and rc2 == 0)

    def _build_done(self, ok):
        self.build_prog.set_visible(False)
        if ok:
            self.build_icon.set_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.MENU)
            self.build_label.set_text('Built and installed — running setup wizard below')
            self._run_setup()
        else:
            self.build_icon.set_from_icon_name('dialog-error', Gtk.IconSize.MENU)
            self.build_label.set_text('Build failed — ensure build-essential and libpam0g-dev are installed')
            self.start_btn.set_label('Retry')
            self.start_btn.set_sensitive(True)

    def _run_setup(self):
        self.setup_frame.set_visible(True)
        self.setup_frame.show_all()
        if self.terminal:
            spawn_vte(self.terminal, ['howdy-secure', 'setup'])
        else:
            p = subprocess.Popen(
                ['x-terminal-emulator', '-e',
                 'howdy-secure setup; read -p "Done. Press enter to close"'])
            threading.Thread(
                target=lambda: (p.wait(), GLib.idle_add(self._on_setup_done, None, 0)),
                daemon=True).start()

    def _on_setup_done(self, _term, _status):
        GLib.idle_add(self.assistant.set_page_complete, self, True)


# ── Page 4: Done ─────────────────────────────────────────────────────────────

class DonePage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.set_border_width(40)

        icon = Gtk.Image.new_from_icon_name('emblem-ok-symbolic', Gtk.IconSize.DIALOG)

        title = Gtk.Label()
        title.set_markup('<span size="xx-large" weight="bold">All done!</span>')

        msg = Gtk.Label()
        msg.set_markup(
            'Log out and back in — your keyring will unlock automatically\n'
            'when Howdy recognizes your face at the login screen.\n\n'
            '<b>Useful commands:</b>\n'
            '  <tt>howdy-secure status</tt>        — check everything is healthy\n'
            '  <tt>sudo howdy-secure enroll</tt>    — re-seal after a kernel update\n'
            '  <tt>sudo howdy-secure recover</tt>   — use recovery code if needed\n'
            '  <tt>sudo howdy test</tt>             — test face recognition\n'
        )
        msg.set_halign(Gtk.Align.START)

        for w in [icon, title, msg]:
            self.pack_start(w, False, False, 0)


# ── Assistant window ──────────────────────────────────────────────────────────

class InstallerAssistant(Gtk.Assistant):
    def __init__(self):
        super().__init__()
        self.set_title('Howdy Secure Installer')
        self.set_default_size(720, 600)
        self.connect('cancel', Gtk.main_quit)
        self.connect('close',  Gtk.main_quit)

        pages = [
            (WelcomePage(),          Gtk.AssistantPageType.INTRO,   'Welcome',      True),
            (IREmitterPage(self),    Gtk.AssistantPageType.CONTENT, 'IR Emitter',   False),
            (HowdyPage(self),        Gtk.AssistantPageType.CONTENT, 'Howdy',        False),
            (HowdySecurePage(self),  Gtk.AssistantPageType.CONTENT, 'Howdy Secure', False),
            (DonePage(),             Gtk.AssistantPageType.SUMMARY, 'Done',         True),
        ]
        for widget, page_type, page_title, complete in pages:
            self.append_page(widget)
            self.set_page_type(widget, page_type)
            self.set_page_title(widget, page_title)
            self.set_page_complete(widget, complete)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if os.geteuid() != 0:
        # Re-exec with pkexec so we get a polkit auth dialog instead of failing
        os.execvp('pkexec', ['pkexec', sys.executable] + sys.argv)

    win = InstallerAssistant()
    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
