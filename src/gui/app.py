#!/usr/bin/env python3
"""
Howdy Secure — Management app + system tray indicator.
Run as a normal user: howdy-secure-app
Privileged operations escalate via pkexec automatically.
"""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, GLib, AyatanaAppIndicator3

try:
    gi.require_version('Vte', '2.91')
    from gi.repository import Vte
    HAS_VTE = True
except (ValueError, ImportError):
    HAS_VTE = False

import os
import re
import pwd
import subprocess
import threading
import shutil
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

APP_ID       = 'com.goodguytek.howdy-secure'
VERSION      = '1.0.0'
REAL_USER    = os.environ.get('SUDO_USER') or os.environ.get('USER', '')
HOWDY_CONF   = Path('/lib/security/howdy/config.ini')
HOWDY_MODELS = Path('/lib/security/howdy/models')
SEALED_BLOB  = Path('/etc/howdy-secure/sealed.blob')
RECOVERY_HASH = Path('/etc/howdy-secure/recovery.hash')

ICON_OK      = 'security-high-symbolic'
ICON_WARN    = 'security-low-symbolic'

# ── Status checking ───────────────────────────────────────────────────────────

def _path_exists(p):
    """Check file existence without raising PermissionError."""
    try:
        return Path(p).exists()
    except PermissionError:
        # Directory is root-only; treat as exists if parent dir is there
        # (sealed.blob is only created after a successful setup)
        return Path(p).parent.exists() and \
               subprocess.run(['sudo', '-n', 'test', '-f', str(p)],
                              capture_output=True).returncode == 0
    except Exception:
        return False

def get_status():
    """Return a dict describing the health of each component."""
    pam_active = False
    for f in ['/etc/pam.d/gdm-password', '/etc/pam.d/gdm-fingerprint',
              '/etc/pam.d/common-auth']:
        try:
            if 'pam_howdy_secure' in Path(f).read_text():
                pam_active = True
                break
        except Exception:
            pass

    # linux-enable-ir-emitter is a oneshot service — check enabled, not active
    ir_enabled = subprocess.run(
        ['systemctl', 'is-enabled', '--quiet', 'linux-enable-ir-emitter']
    ).returncode == 0

    models = _list_models()

    # Fall back to file existence if howdy list returned nothing (e.g. no SUDO_USER)
    if not models and (HOWDY_MODELS / f'{REAL_USER}.dat').exists():
        import time
        mtime = (HOWDY_MODELS / f'{REAL_USER}.dat').stat().st_mtime
        date  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))
        models = [(0, date, REAL_USER.capitalize())]

    return {
        'howdy_installed': bool(shutil.which('howdy')),
        'tpm_sealed':      _path_exists(SEALED_BLOB),
        'recovery_set':    _path_exists(RECOVERY_HASH),
        'pam_active':      pam_active,
        'face_count':      len(models),
        'models':          models,
        'ir_emitter':      ir_enabled,
        'ir_installed':    bool(shutil.which('linux-enable-ir-emitter')),
    }

def overall_ok(status):
    return (status['tpm_sealed'] and status['pam_active']
            and status['face_count'] > 0 and status['howdy_installed'])

def _list_models():
    """Return list of (id, date, label) tuples from `howdy list`."""
    result = subprocess.run(['howdy', 'list'], capture_output=True, text=True)
    models = []
    # Lines look like:  \t0   2026-05-18 00:23:15  Jeremy
    for line in result.stdout.splitlines():
        m = re.match(r'\s+(\d+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)', line)
        if m:
            models.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    return models

def _read_howdy_conf():
    try:
        text = HOWDY_CONF.read_text()
        conf = {}
        for line in text.splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, _, v = line.partition('=')
                conf[k.strip()] = v.strip()
        return conf
    except Exception:
        return {}

# ── Helpers ───────────────────────────────────────────────────────────────────

def run_pkexec(args):
    """Run a privileged command via pkexec in a background thread, return Popen."""
    return subprocess.Popen(['pkexec'] + args)

def spawn_vte(terminal, argv):
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

def make_terminal(height=200):
    term = Vte.Terminal()
    term.set_size_request(-1, height)
    term.set_scroll_on_output(True)
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.add(term)
    return term, sw

def status_row(icon='content-loading-symbolic', text=''):
    box  = Gtk.Box(spacing=8)
    icon_w = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
    label  = Gtk.Label(label=text)
    label.set_halign(Gtk.Align.START)
    box.pack_start(icon_w, False, False, 0)
    box.pack_start(label,  True,  True,  0)
    return box, icon_w, label

def tick(ok):
    return 'emblem-ok-symbolic' if ok else 'dialog-warning-symbolic'

# ── Overview page ─────────────────────────────────────────────────────────────

class OverviewPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.set_border_width(28)

        headline = Gtk.Label()
        headline.set_markup('<span size="x-large" weight="bold">Status</span>')
        headline.set_halign(Gtk.Align.START)

        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(16)
        self.grid.set_row_spacing(12)
        self.grid.set_column_homogeneous(True)

        self._cards = {}
        items = [
            ('pam_active',      'PAM module',       'Authentication hook'),
            ('tpm_sealed',      'TPM sealed',       'Keyring password secured'),
            ('face_count',      'Face enrolled',    'Biometric identity'),
            ('howdy_installed', 'Howdy',            'Face auth engine'),
            ('ir_emitter',      'IR emitter',       'Infrared camera emitter'),
            ('recovery_set',    'Recovery code',    'Backup unlock method'),
        ]
        for idx, (key, title, subtitle) in enumerate(items):
            card = self._make_card(title, subtitle)
            self._cards[key] = card
            self.grid.attach(card, idx % 2, idx // 2, 1, 1)

        self.hint_label = Gtk.Label()
        self.hint_label.set_markup('')
        self.hint_label.set_halign(Gtk.Align.START)
        self.hint_label.set_line_wrap(True)

        for w in [headline, self.grid, self.hint_label]:
            self.pack_start(w, False, False, 0)

    def _make_card(self, title, subtitle):
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(12)

        icon_title = Gtk.Box(spacing=8)
        icon = Gtk.Image.new_from_icon_name('content-loading-symbolic', Gtk.IconSize.MENU)
        t = Gtk.Label()
        t.set_markup(f'<b>{title}</b>')
        t.set_halign(Gtk.Align.START)
        icon_title.pack_start(icon, False, False, 0)
        icon_title.pack_start(t, True, True, 0)

        sub = Gtk.Label(label=subtitle)
        sub.set_halign(Gtk.Align.START)
        sub.get_style_context().add_class('dim-label')

        status = Gtk.Label(label='Checking…')
        status.set_halign(Gtk.Align.START)

        box.pack_start(icon_title, False, False, 0)
        box.pack_start(sub,        False, False, 0)
        box.pack_start(status,     False, False, 0)
        frame.add(box)

        # store refs for update
        frame._icon   = icon
        frame._status = status
        return frame

    def update(self, st):
        checks = {
            'pam_active':      (st['pam_active'],      'Active' if st['pam_active'] else 'Not in PAM config'),
            'tpm_sealed':      (st['tpm_sealed'],       'Sealed' if st['tpm_sealed'] else 'Not sealed'),
            'face_count':      (st['face_count'] > 0,  f"{st['face_count']} model(s) enrolled" if st['face_count'] else 'No model enrolled'),
            'howdy_installed': (st['howdy_installed'],  'Installed' if st['howdy_installed'] else 'Not installed'),
            'ir_emitter':      (st['ir_installed'],     'Enabled' if st['ir_emitter'] else ('Not enabled' if st['ir_installed'] else 'Not installed')),
            'recovery_set':    (st['recovery_set'],     'Set' if st['recovery_set'] else 'Not configured'),
        }
        for key, (ok, msg) in checks.items():
            card = self._cards[key]
            card._icon.set_from_icon_name(tick(ok), Gtk.IconSize.MENU)
            card._status.set_text(msg)

        if not overall_ok(st):
            hints = []
            if not st['howdy_installed']:
                hints.append('Howdy is not installed — go to Keyring to run setup.')
            if not st['tpm_sealed']:
                hints.append('Keyring is not sealed — go to Keyring to run setup.')
            if st['face_count'] == 0:
                hints.append('No face enrolled — go to Face Models to enroll.')
            if not st['pam_active']:
                hints.append('PAM module is not active — re-run setup.')
            self.hint_label.set_markup(
                '<span foreground="orange">⚠  ' + '  '.join(hints) + '</span>')
        else:
            self.hint_label.set_markup(
                '<span foreground="green">✓  Howdy Secure is active and healthy.</span>')


# ── Face Models page ──────────────────────────────────────────────────────────

class FacesPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_border_width(24)

        headline = Gtk.Label()
        headline.set_markup('<span size="x-large" weight="bold">Face Models</span>')
        headline.set_halign(Gtk.Align.START)

        # Model list
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect('row-selected', self._on_row_selected)
        list_frame = Gtk.Frame()
        list_frame.add(self.listbox)

        # Buttons
        self.add_btn    = Gtk.Button(label='Enroll New Face…')
        self.test_btn   = Gtk.Button(label='Test Recognition')
        self.delete_btn = Gtk.Button(label='Delete Selected')
        self.delete_btn.get_style_context().add_class('destructive-action')
        self.test_btn.set_sensitive(False)
        self.delete_btn.set_sensitive(False)
        self.add_btn.connect('clicked',    self._on_add)
        self.test_btn.connect('clicked',   self._on_test)
        self.delete_btn.connect('clicked', self._on_delete)

        btn_row = Gtk.Box(spacing=8)
        btn_row.pack_start(self.add_btn,    False, False, 0)
        btn_row.pack_start(self.test_btn,   False, False, 0)
        btn_row.pack_end(self.delete_btn,   False, False, 0)

        # Terminal for enroll/test output
        if HAS_VTE:
            self.terminal, tsw = make_terminal(180)
            self.terminal.connect('child-exited', self._on_cmd_done)
            term_frame = Gtk.Frame(label=' Output ')
            term_frame.add(tsw)
            self._term_frame = term_frame
        else:
            self.terminal    = None
            self._term_frame = None

        self.pack_start(headline,   False, False, 0)
        self.pack_start(list_frame, False, False, 0)
        self.pack_start(btn_row,    False, False, 0)
        if self._term_frame:
            self.pack_start(self._term_frame, True, True, 0)

    def update(self, models):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for model_id, date, label in models:
            row = Gtk.ListBoxRow()
            row.model_id = model_id
            box = Gtk.Box(spacing=12)
            box.set_border_width(8)
            face_icon = Gtk.Image.new_from_icon_name('avatar-default-symbolic', Gtk.IconSize.LARGE_TOOLBAR)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            name_lbl = Gtk.Label()
            name_lbl.set_markup(f'<b>{label}</b>')
            name_lbl.set_halign(Gtk.Align.START)
            date_lbl = Gtk.Label(label=f'Enrolled {date}')
            date_lbl.set_halign(Gtk.Align.START)
            date_lbl.get_style_context().add_class('dim-label')
            info.pack_start(name_lbl, False, False, 0)
            info.pack_start(date_lbl, False, False, 0)
            box.pack_start(face_icon, False, False, 0)
            box.pack_start(info,      True,  True,  0)
            row.add(box)
            self.listbox.add(row)
        self.listbox.show_all()
        self.test_btn.set_sensitive(len(models) > 0)

    def _on_row_selected(self, listbox, row):
        self.delete_btn.set_sensitive(row is not None)

    def _on_add(self, btn):
        if self.terminal:
            spawn_vte(self.terminal, ['pkexec', 'howdy', 'add'])
        else:
            subprocess.Popen(['pkexec', 'howdy', 'add'])

    def _on_test(self, btn):
        if self.terminal:
            spawn_vte(self.terminal, ['pkexec', 'howdy', 'test'])
        else:
            subprocess.Popen(['pkexec', 'howdy', 'test'])

    def _on_delete(self, btn):
        row = self.listbox.get_selected_row()
        if not row:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text='Delete this face model?',
        )
        dialog.format_secondary_text(
            'This cannot be undone. You can re-enroll at any time.')
        if dialog.run() == Gtk.ResponseType.OK:
            model_id = row.model_id
            if self.terminal:
                spawn_vte(self.terminal, ['pkexec', 'howdy', 'remove', str(model_id)])
            else:
                subprocess.Popen(['pkexec', 'howdy', 'remove', str(model_id)])
        dialog.destroy()

    def _on_cmd_done(self, _terminal, _status):
        # Signal parent to refresh status
        self.get_toplevel().refresh_status()


# ── Settings page ─────────────────────────────────────────────────────────────

class SettingsPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_border_width(24)

        headline = Gtk.Label()
        headline.set_markup('<span size="x-large" weight="bold">Settings</span>')
        headline.set_halign(Gtk.Align.START)

        note = Gtk.Label()
        note.set_markup(
            '<span foreground="gray" size="small">'
            'Changes require root — you will be prompted for your password.'
            '</span>'
        )
        note.set_halign(Gtk.Align.START)

        # Camera
        cam_frame = Gtk.Frame(label=' Camera device ')
        cam_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cam_box.set_border_width(12)

        store = Gtk.ListStore(str, str)
        self.cam_combo = Gtk.ComboBox.new_with_model(store)
        r = Gtk.CellRendererText()
        self.cam_combo.pack_start(r, True)
        self.cam_combo.add_attribute(r, 'text', 1)

        self.cam_current = Gtk.Label()
        self.cam_current.set_halign(Gtk.Align.START)
        self.cam_current.get_style_context().add_class('dim-label')

        cam_apply = Gtk.Button(label='Apply camera change')
        cam_apply.connect('clicked', self._on_apply_camera)

        for w in [self.cam_current, self.cam_combo, cam_apply]:
            cam_box.pack_start(w, False, False, 0)
        cam_frame.add(cam_box)

        # Certainty
        cert_frame = Gtk.Frame(label=' Face recognition certainty ')
        cert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cert_box.set_border_width(12)

        cert_desc = Gtk.Label(
            label='Lower = easier to match (less secure). Higher = stricter. Default: 3.5')
        cert_desc.set_halign(Gtk.Align.START)
        cert_desc.get_style_context().add_class('dim-label')

        self.cert_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1.0, 6.0, 0.5)
        self.cert_scale.set_value(3.5)
        self.cert_scale.set_draw_value(True)
        self.cert_scale.add_mark(3.5, Gtk.PositionType.BOTTOM, 'default')

        cert_apply = Gtk.Button(label='Apply certainty change')
        cert_apply.connect('clicked', self._on_apply_certainty)

        for w in [cert_desc, self.cert_scale, cert_apply]:
            cert_box.pack_start(w, False, False, 0)
        cert_frame.add(cert_box)

        for w in [headline, note, cam_frame, cert_frame]:
            self.pack_start(w, False, False, 0)

        self._populate_cameras()
        self._load_conf()

    def _populate_cameras(self):
        store = self.cam_combo.get_model()
        store.clear()
        for d in sorted(Path('/dev').glob('video*')):
            try:
                out = subprocess.run(
                    ['udevadm', 'info', '--query=property', f'--name={d}'],
                    capture_output=True, text=True).stdout
                product = next(
                    (l.split('=',1)[1] for l in out.splitlines() if 'ID_V4L_PRODUCT' in l), '')
                label = f"{d}  ({product})" if product else str(d)
            except Exception:
                label = str(d)
            store.append([str(d), label])

    def _load_conf(self):
        conf = _read_howdy_conf()
        cur = conf.get('device_path', '')
        self.cam_current.set_text(f'Current: {cur}' if cur else 'Current: not set')
        # Select current in combo
        store = self.cam_combo.get_model()
        for i, row in enumerate(store):
            if row[0] == cur:
                self.cam_combo.set_active(i)
                break
        try:
            cert = float(conf.get('certainty', '3.5'))
            self.cert_scale.set_value(cert)
        except ValueError:
            pass

    def _on_apply_camera(self, btn):
        it = self.cam_combo.get_active_iter()
        if not it:
            return
        path = self.cam_combo.get_model()[it][0]
        # Write via pkexec python3 -c (so we don't need a separate helper)
        script = (
            f"import re; t=open('{HOWDY_CONF}').read(); "
            f"t=re.sub(r'device_path = .*', 'device_path = {path}', t); "
            f"open('{HOWDY_CONF}','w').write(t)"
        )
        subprocess.run(['pkexec', 'python3', '-c', script])
        self._load_conf()

    def _on_apply_certainty(self, btn):
        val = self.cert_scale.get_value()
        script = (
            f"import re; t=open('{HOWDY_CONF}').read(); "
            f"t=re.sub(r'certainty = .*', 'certainty = {val}', t); "
            f"open('{HOWDY_CONF}','w').write(t)"
        )
        subprocess.run(['pkexec', 'python3', '-c', script])


# ── Keyring page ──────────────────────────────────────────────────────────────

class KeyringPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_border_width(24)

        headline = Gtk.Label()
        headline.set_markup('<span size="x-large" weight="bold">Keyring &amp; TPM</span>')
        headline.set_halign(Gtk.Align.START)

        # Status
        status_frame = Gtk.Frame(label=' Status ')
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        status_box.set_border_width(12)
        tpm_row, self.tpm_icon, self.tpm_label = status_row(text='Checking…')
        rec_row, self.rec_icon, self.rec_label = status_row(text='Checking…')
        pam_row, self.pam_icon, self.pam_label = status_row(text='Checking…')
        for row in [tpm_row, rec_row, pam_row]:
            status_box.pack_start(row, False, False, 0)
        status_frame.add(status_box)

        # Actions
        actions_frame = Gtk.Frame(label=' Actions ')
        actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        actions_box.set_border_width(12)

        reseal_btn = Gtk.Button(label='Re-seal keyring password…')
        reseal_btn.set_tooltip_text(
            'Use this after a kernel update if the keyring popup reappears.')
        reseal_btn.connect('clicked', self._on_reseal)

        setup_btn = Gtk.Button(label='Run full setup wizard…')
        setup_btn.set_tooltip_text(
            'First-time setup or complete re-configuration.')
        setup_btn.connect('clicked', self._on_setup)

        recover_btn = Gtk.Button(label='Recover using recovery code…')
        recover_btn.connect('clicked', self._on_recover)

        for btn in [reseal_btn, setup_btn, recover_btn]:
            actions_box.pack_start(btn, False, False, 0)
        actions_frame.add(actions_box)

        # Terminal
        if HAS_VTE:
            self.terminal, tsw = make_terminal(220)
            term_frame = Gtk.Frame(label=' Output ')
            term_frame.add(tsw)
            self._term_frame = term_frame
        else:
            self.terminal    = None
            self._term_frame = None

        for w in [headline, status_frame, actions_frame]:
            self.pack_start(w, False, False, 0)
        if self._term_frame:
            self.pack_start(self._term_frame, True, True, 0)

    def update(self, st):
        self.tpm_icon.set_from_icon_name(tick(st['tpm_sealed']),  Gtk.IconSize.MENU)
        self.tpm_label.set_text('TPM sealed' if st['tpm_sealed'] else 'TPM not sealed — run setup')
        self.rec_icon.set_from_icon_name(tick(st['recovery_set']), Gtk.IconSize.MENU)
        self.rec_label.set_text('Recovery hash saved' if st['recovery_set'] else 'Recovery code not configured')
        self.pam_icon.set_from_icon_name(tick(st['pam_active']),  Gtk.IconSize.MENU)
        self.pam_label.set_text('PAM module active' if st['pam_active'] else 'PAM module not in config')

    def _run(self, cmd):
        if self.terminal:
            spawn_vte(self.terminal, cmd)
        else:
            subprocess.Popen(cmd)

    def _on_reseal(self, btn):
        self._run(['pkexec', 'howdy-secure', 'enroll'])

    def _on_setup(self, btn):
        self._run(['pkexec', 'howdy-secure', 'setup'])

    def _on_recover(self, btn):
        self._run(['pkexec', 'howdy-secure', 'recover'])


# ── About page ────────────────────────────────────────────────────────────────

class AboutPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_border_width(40)
        self.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name('security-high-symbolic', Gtk.IconSize.DIALOG)

        title = Gtk.Label()
        title.set_markup('<span size="xx-large" weight="bold">Howdy Secure</span>')

        version = Gtk.Label()
        version.set_markup(f'<span foreground="gray">Version {VERSION}</span>')

        desc = Gtk.Label(
            label='Seamless GNOME Keyring unlock using face authentication and TPM.')
        desc.set_line_wrap(True)

        link = Gtk.LinkButton(
            uri='https://github.com/p3ngwen/howdy-secure',
            label='github.com/p3ngwen/howdy-secure',
        )

        sep = Gtk.Separator()

        user_lbl = Gtk.Label()
        user_lbl.set_markup(
            f'<span foreground="gray" size="small">Configured for user: <b>{REAL_USER}</b></span>')

        for w in [icon, title, version, desc, link, sep, user_lbl]:
            self.pack_start(w, False, False, 0)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Howdy Secure')
        self.set_default_size(860, 560)
        self.set_icon_name(ICON_OK)
        self.connect('delete-event', self._on_close)

        # Sidebar + stack
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        stack.set_transition_duration(150)

        self.overview_page  = OverviewPage()
        self.faces_page     = FacesPage()
        self.settings_page  = SettingsPage()
        self.keyring_page   = KeyringPage()
        self.about_page     = AboutPage()

        stack.add_titled(self.overview_page,  'overview',  'Overview')
        stack.add_titled(self.faces_page,     'faces',     'Face Models')
        stack.add_titled(self.settings_page,  'settings',  'Settings')
        stack.add_titled(self.keyring_page,   'keyring',   'Keyring & TPM')
        stack.add_titled(self.about_page,     'about',     'About')

        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(stack)
        sidebar.set_size_request(170, -1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        hbox.pack_start(sidebar, False, False, 0)
        hbox.pack_start(sep,     False, False, 0)
        hbox.pack_start(stack,   True,  True,  0)
        self.add(hbox)

        self._status = {}
        self.refresh_status()
        # Poll every 30 seconds
        GLib.timeout_add_seconds(30, self._poll)

    def refresh_status(self):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        st = get_status()
        GLib.idle_add(self._apply_status, st)

    def _apply_status(self, st):
        self._status = st
        self.overview_page.update(st)
        self.faces_page.update(st['models'])
        self.keyring_page.update(st)
        # Notify tray
        app = self.get_application()
        if hasattr(app, 'tray'):
            app.tray.set_status(overall_ok(st))

    def _poll(self):
        self.refresh_status()
        return True  # keep repeating

    def _on_close(self, win, event):
        # Hide instead of destroy so the tray can reopen it
        self.hide()
        return True  # prevent destruction


# ── Tray indicator ────────────────────────────────────────────────────────────

class TrayIndicator:
    def __init__(self, app):
        self.app = app
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            APP_ID,
            ICON_OK,
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_attention_icon_full(ICON_WARN, 'Issue detected')
        self.indicator.set_menu(self._build_menu())

    def _build_menu(self):
        menu = Gtk.Menu()

        header = Gtk.MenuItem(label='Howdy Secure')
        header.set_sensitive(False)
        menu.append(header)
        menu.append(Gtk.SeparatorMenuItem())

        open_item = Gtk.MenuItem(label='Open Settings')
        open_item.connect('activate', lambda _: self.app.show_window())
        menu.append(open_item)

        test_item = Gtk.MenuItem(label='Test Face Recognition')
        test_item.connect('activate', lambda _: subprocess.Popen(['pkexec', 'howdy', 'test']))
        menu.append(test_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label='Quit')
        quit_item.connect('activate', lambda _: self.app.quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def set_status(self, ok):
        if ok:
            self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        else:
            self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ATTENTION)


# ── Application ───────────────────────────────────────────────────────────────

class HowdySecureApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window = None
        self.tray   = None

    def do_activate(self):
        if self.window is None:
            self.tray   = TrayIndicator(self)
            self.window = MainWindow(self)
            self.window.show_all()
        else:
            self.show_window()

    def show_window(self):
        if self.window:
            self.window.present()
            self.window.show_all()


def main():
    app = HowdySecureApp()
    app.run(None)


if __name__ == '__main__':
    main()
