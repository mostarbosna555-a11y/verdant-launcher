from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPolygonF, QLinearGradient, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame, QComboBox, QLineEdit, QSpinBox,
    QFormLayout, QDialog, QDialogButtonBox, QMessageBox, QInputDialog, QScrollArea,
    QFileDialog, QProgressBar, QCheckBox, QSizePolicy, QLayout)

from .core import Launcher, FriendsClient, write_json
from .models import Manifest, World

STYLE = """
QWidget { background: #111715; color: #e7eee9; font-family: 'DejaVu Sans'; font-size: 13px; }
QMainWindow { background: #111715; }
QFrame#sidebar { background: #0c110f; border-right: 1px solid #29342e; }
QLabel { background: transparent; }
QLabel#brand { font-size: 25px; font-weight: 800; color: #b9ed95; }
QLabel#title { font-size: 29px; font-weight: 700; }
QLabel#eyebrow { color: #b1db91; font-size: 11px; font-weight: 700; }
QLabel#muted { color: #97a69c; }
QLabel#cardtitle { font-size: 17px; font-weight: 700; }
QPushButton { background: #24332a; border: 1px solid #354b3c; border-radius: 8px; padding: 11px 17px; font-weight: 600; }
QPushButton:hover { background: #324b3a; border-color: #93bc78; }
QPushButton:disabled { color: #657269; background: #202922; border-color: #29342e; }
QPushButton#primary { background: #b9ed95; color: #182313; border: none; }
QPushButton#primary:hover { background: #d2ffb1; }
QPushButton#nav { background: transparent; border: none; text-align: left; padding: 14px; color: #98a89c; }
QPushButton#nav:checked { background: #233127; color: #c6efa9; }
QFrame#card { background: #19221c; border: 1px solid #2c3a30; border-radius: 12px; }
QLineEdit, QComboBox, QSpinBox { background: #0e1510; border: 1px solid #34463a; border-radius: 7px; padding: 10px; selection-background-color: #527448; }
QLineEdit:focus { border-color: #b9ed95; }
QComboBox QAbstractItemView { background: #19221c; selection-background-color: #324b3a; }
QScrollArea { border: none; background: transparent; }
QProgressBar { border: none; background: #29352b; border-radius: 3px; height: 5px; }
QProgressBar::chunk { background: #b9ed95; border-radius: 3px; }
QCheckBox { spacing: 10px; }
QToolTip { background: #24332a; color: #e7eee9; border: 1px solid #527448; }
"""


def label(text, name=None, wrap=False):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text, fn, primary=False):
    w = QPushButton(text)
    if primary:
        w.setObjectName("primary")
    w.clicked.connect(fn)
    return w


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


def card(title, description):
    frame = QFrame()
    frame.setObjectName("card")
    frame.setMinimumHeight(150)
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    box = QVBoxLayout(frame)
    box.setContentsMargins(22, 19, 22, 19)
    box.setSpacing(10)
    box.addWidget(label(title, "cardtitle"))
    box.addWidget(label(description, "muted", True))
    return frame, box


class Landscape(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(230)
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 28, 30, 28)
        v.addWidget(label("YOUR NEXT ADVENTURE", "eyebrow"))
        title = label("Same world.\nSame setup.", "title")
        title.setStyleSheet("font-size: 35px; color: #f0f5e9;")
        v.addWidget(title)
        v.addWidget(label("Bring your friends. We’ll bring the right mods.", "muted"))
        v.addStretch()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#263e2b"))
        gradient.setColorAt(1, QColor("#18291e"))
        p.setBrush(gradient)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 16, 16)
        w, h = self.width(), self.height()
        p.setBrush(QColor("#bfd692"))
        p.drawEllipse(QPointF(w*.83, h*.26), 25, 25)
        for color, points in [
            ("#3b5639", [(.53,.9),(.61,.48),(.66,.48),(.66,.37),(.71,.37),(.79,.75),(.85,.54),(.9,.54),(1,.8),(1,1),(.53,1)]),
            ("#2c452e", [(.47,1),(.64,.72),(.69,.72),(.69,.6),(.75,.6),(.88,.9),(1,.66),(1,1)]),
            ("#1a3022", [(.5,1),(.7,.88),(.8,.97),(.92,.77),(1,.8),(1,1)])]:
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(w*x, h*y) for x,y in points]))
        for x, y, size in [(.77,.79,22),(.93,.72,30),(.88,.89,16),(.62,.9,14)]:
            p.setBrush(QColor("#101f16"))
            p.drawRect(int(w*x), int(h*y), 5, size)
            p.drawPolygon(QPolygonF([QPointF(w*x-12,h*y+5),QPointF(w*x+2,h*y-size),QPointF(w*x+16,h*y+5)]))
        p.end()


class Job(QThread):
    progress = Signal(str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, task):
        super().__init__()
        self.task = task

    def run(self):
        try:
            self.result.emit(self.task(self.progress.emit))
        except Exception as exc:
            # Do not expose OAuth URLs, request headers, or tokens in errors.
            if isinstance(exc, (ValueError, FileNotFoundError)):
                message = str(exc)
            else:
                message = f"{type(exc).__name__}: the operation could not finish. Check connectivity, credentials, and your OS keyring."
            self.failed.emit(message[:1200])


class Window(QMainWindow):
    def __init__(self, launcher=None):
        super().__init__()
        self.launcher = launcher or Launcher()
        self.job = None
        self.setWindowTitle("Verdant — Minecraft, together")
        self.resize(1190, 800)
        self.setMinimumSize(950, 680)
        shell = QWidget()
        self.setCentralWidget(shell)
        row = QHBoxLayout(shell)
        row.setContentsMargins(0,0,0,0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20,30,20,25)
        nav.addWidget(label("◈  verdant", "brand"))
        nav.addWidget(label("MINECRAFT, TOGETHER", "eyebrow"))
        nav.addSpacing(38)
        self.navs = []
        self.stack = QStackedWidget()
        for i, title in enumerate(["◫   My library", "⌕   Discover mods", "♧   Friends", "⚙   Settings"]):
            b = QPushButton(title)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda checked=False, index=i: self.navigate(index))
            nav.addWidget(b)
            self.navs.append(b)
        nav.addStretch()
        self.account_label = label(self.launcher.config.get("account_name", "Microsoft · not signed in"), "muted", True)
        nav.addWidget(self.account_label)
        nav.addWidget(button("Sign in", self.login))
        nav.addSpacing(20)
        nav.addWidget(label("PRIVATE ALPHA  /  0.1", "eyebrow"))
        row.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(30,25,30,20)
        content.addWidget(self.stack)
        self.status = label("Ready when you are.", "muted", True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0,1)
        self.progress.setValue(0)
        self.progress.setFixedHeight(5)
        content.addWidget(self.status)
        content.addWidget(self.progress)
        row.addLayout(content, 1)
        self.make_library()
        self.make_discover()
        self.make_friends()
        self.make_settings()
        self.reload_profiles()
        self.navigate(0)
        self.heartbeat = QTimer(self)
        self.heartbeat.timeout.connect(self.tick)
        self.heartbeat.start(60000)

    def page(self, heading, subtitle):
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0,0,0,0)
        box.setSpacing(18)
        box.addWidget(label(heading, "title"))
        box.addWidget(label(subtitle, "muted", True))
        self.stack.addWidget(widget)
        return box

    def scroll(self, layout):
        area = QScrollArea()
        area.setWidgetResizable(True)
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0,0,6,0)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        box.setSpacing(12)
        area.setWidget(holder)
        layout.addWidget(area,1)
        return box

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i,b in enumerate(self.navs):
            b.setChecked(i == index)

    def run(self, task, done=None, quiet=False):
        if self.job is not None:
            return
        job = Job(task)
        self.job = job
        if not quiet:
            self.centralWidget().setEnabled(False)
            self.status.setText("Working…")
            self.progress.setRange(0,0)
        job.progress.connect(self.status.setText)
        job.result.connect(lambda value: done(value) if done else None)
        job.failed.connect(lambda message: self.status.setText(message) if quiet else self.error(message))
        job.finished.connect(lambda: self.finish_job(quiet))
        job.start()

    def finish_job(self, quiet):
        old = self.job
        self.job = None
        if old:
            old.deleteLater()
        if not quiet:
            self.centralWidget().setEnabled(True)
            self.progress.setRange(0,1)
            self.progress.setValue(1)

    def error(self, message):
        self.status.setText("Could not complete the operation.")
        QMessageBox.warning(self, "Verdant", message)

    def make_library(self):
        box = self.page("Your little corner of Minecraft", "Separate setups. Shared adventures. Everything in its place.")
        box.addWidget(Landscape())
        controls = QHBoxLayout()
        controls.addWidget(label("YOUR PROFILES", "eyebrow"))
        controls.addStretch()
        controls.addWidget(button("Import setup", self.import_manifest))
        controls.addWidget(button("+ New profile", self.new_profile, True))
        box.addLayout(controls)
        self.profile_cards = self.scroll(box)

    def reload_profiles(self, selected=None):
        profiles = self.launcher.profiles()
        clear(self.profile_cards)
        previous = selected or self.profile_choice.currentData()
        self.profile_choice.clear()
        for pid, m in profiles:
            self.profile_choice.addItem(f"{m.name} · {m.minecraft} / {m.loader}", pid)
            f, v = card(m.name, f"Minecraft {m.minecraft}   /   {m.loader.capitalize()} {m.loader_version or ''}   /   {len(m.artifacts)} installed files")
            actions = QHBoxLayout()
            actions.addWidget(button("▶  Play", lambda checked=False,p=pid: self.play(p), True))
            actions.addWidget(button("Manage", lambda checked=False,p=pid: self.manage(p)))
            actions.addWidget(button("Share setup", lambda checked=False,p=pid: self.export_manifest(p)))
            actions.addStretch()
            actions.addWidget(label("ISOLATED PROFILE", "eyebrow"))
            v.addLayout(actions)
            self.profile_cards.addWidget(f)
        if previous:
            index = self.profile_choice.findData(previous)
            if index >= 0:
                self.profile_choice.setCurrentIndex(index)
        if not profiles:
            f,v = card("A fresh start", "Create a Fabric profile for mods, or a vanilla profile for the original experience. Your existing Minecraft installation stays separate.")
            v.addWidget(button("Create your first profile", self.new_profile, True))
            self.profile_cards.addWidget(f)
        self.profile_cards.addStretch()

    def new_profile(self):
        d = QDialog(self)
        d.setWindowTitle("Create profile")
        d.setMinimumWidth(410)
        v = QVBoxLayout(d)
        form = QFormLayout()
        name, version, loader = QLineEdit("My new world"), QLineEdit("1.21.1"), QComboBox()
        loader.addItems(["fabric", "vanilla"])
        form.addRow("Name", name)
        form.addRow("Minecraft version", version)
        form.addRow("Mod loader", loader)
        v.addLayout(form)
        v.addWidget(label("The Fabric loader version is pinned when the profile is created. Minecraft and Java runtime files download on first launch.", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        v.addWidget(buttons)
        if d.exec():
            args = name.text().strip(), version.text().strip(), loader.currentText()
            self.run(lambda progress: self.launcher.create(*args), lambda pid: (self.reload_profiles(pid), self.status.setText("Profile created. Discover mods to make it yours.")))

    def play(self, pid, address=None):
        self.run(lambda progress: self.launcher.launch(pid, address, progress), lambda process: self.status.setText(f"Minecraft is running · PID {process}"))

    def manage(self, pid):
        m = self.launcher.manifest(pid)
        d = QDialog(self)
        d.setWindowTitle(m.name)
        d.resize(650,450)
        v = QVBoxLayout(d)
        v.addWidget(label(m.name, "title"))
        v.addWidget(label("Pinned files · install additional content from Discover mods", "muted"))
        rows = self.scroll(v)
        for a in m.artifacts:
            f, _ = card(a.name, f"{a.provider} / {a.kind} / {a.version}\n{a.filename}")
            rows.addWidget(f)
        if not m.artifacts:
            rows.addWidget(label("No mods or resource packs installed yet.", "muted"))
        rows.addStretch()
        v.addWidget(button("Open profile folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.launcher.profile_path(pid))))))
        d.exec()

    def export_manifest(self, pid):
        filename, _ = QFileDialog.getSaveFileName(self, "Share exact setup", "setup.verdant.json", "Verdant setup (*.json)")
        if filename:
            self.run(lambda progress: write_json(filename, self.launcher.share(pid).model_dump()), lambda _: self.status.setText("Setup exported. Friends can import it into a separate profile."))

    def import_manifest(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Import setup", "", "Verdant setup (*.json)")
        if filename:
            try:
                if Path(filename).stat().st_size > 2_000_000:
                    raise ValueError("Manifest is too large")
                m = Manifest.model_validate_json(Path(filename).read_text())
                self.confirm_sync(m)
            except Exception as exc:
                self.error(str(exc))

    def confirm_sync(self, manifest, address=None):
        total = len(manifest.artifacts)
        summary = f"{manifest.name}\nMinecraft {manifest.minecraft} · {manifest.loader} {manifest.loader_version or ''}\n\nDownload and verify {total} files and {len(manifest.configs)} shared configs in a new isolated profile? Mods execute code when Minecraft starts; only join setups you trust."
        if address:
            summary += f"\n\nAfter syncing, connect to {address}."
        if QMessageBox.question(self, "Sync friend's setup", summary) != QMessageBox.StandardButton.Yes:
            return
        def task(progress):
            pid = self.launcher.sync(manifest, progress)
            # The profile is available even if login/launch subsequently fails.
            if address:
                try:
                    self.launcher.launch(pid, address, progress)
                except Exception as exc:
                    return pid, type(exc).__name__ + ": synced successfully, but launch did not finish. Sign in and use Join address below."
            return pid, "Setup synced and verified." + (" Minecraft started." if address else "")
        def done(result):
            pid, message = result
            self.reload_profiles(pid)
            self.status.setText(message)
            if address:
                self.join_address.setText(address)
            self.navigate(0)
        self.run(task, done)

    def make_discover(self):
        box = self.page("Make it your Minecraft", "Compatible mods and resource packs, with required dependencies included.")
        self.profile_choice = QComboBox()
        box.addWidget(self.profile_choice)
        row = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItems(["Modrinth", "CurseForge"])
        self.kind = QComboBox()
        self.kind.addItem("Mods", "mod")
        self.kind.addItem("Resource packs", "resourcepack")
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search for Sodium, Create, a new adventure…")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.provider)
        row.addWidget(self.kind)
        row.addWidget(self.query,1)
        row.addWidget(button("Search", self.search, True))
        box.addLayout(row)
        self.search_cards = self.scroll(box)
        f,_ = card("Find your next favorite", "Choose a profile, then search the live catalog. Results match your Minecraft version and loader. CurseForge requires an API key in Settings.")
        self.search_cards.addWidget(f)
        self.search_cards.addStretch()

    def search(self):
        pid = self.profile_choice.currentData()
        if not pid:
            self.error("Create a profile first.")
            return
        provider, query, kind = self.provider.currentText().lower(), self.query.text(), self.kind.currentData()
        def done(results):
            clear(self.search_cards)
            manifest = self.launcher.manifest(pid)
            installed = {(a.provider, a.project) for a in manifest.artifacts}
            for item in results:
                f,v = card(item["name"], item["description"])
                row = QHBoxLayout()
                row.addWidget(label(f"{int(item['downloads']):,} downloads  ·  {provider}", "eyebrow"))
                row.addStretch()
                has = (provider, item["id"]) in installed
                b = button("Installed ✓" if has else "+ Install", lambda checked=False,it=item: self.install(pid, provider, it), not has)
                b.setEnabled(not has)
                row.addWidget(b)
                v.addLayout(row)
                self.search_cards.addWidget(f)
            if not results:
                self.search_cards.addWidget(label("No compatible results. Try another search or profile.", "muted"))
            self.search_cards.addStretch()
            self.status.setText(f"{len(results)} compatible results")
        self.run(lambda progress: self.launcher.catalog().search(provider, query, self.launcher.manifest(pid), kind), done)

    def install(self, pid, provider, item):
        def done(count):
            self.reload_profiles(pid)
            clear(self.search_cards)
            f,_ = card(f"{item['name']} is ready", f"Installed {count} verified files, including required dependencies. Search again to add more.")
            self.search_cards.addWidget(f)
            self.search_cards.addStretch()
            self.status.setText("Installation complete.")
        self.run(lambda progress: self.launcher.install(pid, provider, item["id"], item["kind"], progress), done)

    def make_friends(self):
        box = self.page("Better with your people", "Share a setup. Sync the right files. Jump into the same world.")
        controls = QHBoxLayout()
        controls.addWidget(button("Create identity", self.register))
        controls.addWidget(button("Add friend", self.add_friend))
        controls.addWidget(button("Refresh", self.refresh_friends, True))
        controls.addStretch()
        box.addLayout(controls)
        self.friend_code = label("Connect to a friends service in Settings to get started.", "muted", True)
        self.friend_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.friend_code)
        controls2 = QHBoxLayout()
        controls2.addWidget(button("Publish world", self.publish_world))
        controls2.addWidget(button("Stop sharing", lambda: self.friend_action("DELETE", "/world")))
        self.join_address = QLineEdit()
        self.join_address.setPlaceholderText("Server address or LAN IP:port")
        controls2.addWidget(self.join_address,1)
        controls2.addWidget(button("Join address", self.direct_join))
        box.addLayout(controls2)
        self.friends_cards = self.scroll(box)
        f,_ = card("Your party starts here", "This alpha connects to a private friends directory. Both players use the same service. Published worlds need a reachable server, LAN address, or existing VPN connection; built-in tunneling is not available yet.")
        self.friends_cards.addWidget(f)
        self.friends_cards.addStretch()

    def register(self):
        name, ok = QInputDialog.getText(self, "Friends identity", "Display name (separate from your Microsoft account)")
        if not ok:
            return
        invite, ok = QInputDialog.getText(self, "Server invitation", "Invitation secret from the friends service operator", QLineEdit.EchoMode.Password)
        if not ok:
            return
        def task(progress):
            client = FriendsClient(self.launcher)
            if self.launcher.vault.get(client.key_name):
                raise ValueError("This device already has an identity on this service. Use Refresh.")
            return client.register(name, invite)
        self.run(task, lambda result: self.friend_code.setText("Your friend code: " + result["code"]))

    def friend_action(self, method, path, body=None):
        self.run(lambda progress: FriendsClient(self.launcher).request(method, path, body), lambda _: self.status.setText("Done. Refresh to see the latest friends list."))

    def add_friend(self):
        code, ok = QInputDialog.getText(self, "Add a friend", "Paste their friend code")
        if ok:
            self.friend_action("POST", "/friends/request", {"code": code.strip()})

    def refresh_friends(self):
        def task(progress):
            c = FriendsClient(self.launcher)
            c.request("POST", "/heartbeat")
            return c.request("GET", "/me"), c.request("GET", "/friends")
        self.run(task, self.render_friends)

    def render_friends(self, result):
        me, data = result
        self.friend_code.setText(f"{me['name']}  ·  Your friend code: {me['code']}")
        clear(self.friends_cards)
        for r in data["requests"]:
            f,v = card(r["name"], "Wants to be your friend")
            row = QHBoxLayout()
            row.addWidget(button("Accept", lambda checked=False, uid=r["id"]: self.friend_action("POST", f"/friends/{uid}/accept"), True))
            row.addWidget(button("Decline", lambda checked=False, uid=r["id"]: self.friend_action("DELETE", f"/friends/{uid}")))
            v.addLayout(row)
            self.friends_cards.addWidget(f)
        for friend in data["friends"]:
            world = World.model_validate(friend["world"]) if friend["world"] else None
            detail = f"Playing {world.manifest.name} · {world.manifest.minecraft}" if world else ("Online · no world shared" if friend["online"] else "Offline")
            f,v = card(friend["name"], detail)
            if world:
                v.addWidget(button("Sync & join →", lambda checked=False,w=world: self.confirm_sync(w.manifest, w.address), True))
            v.addWidget(button("Remove friend", lambda checked=False, uid=friend["id"]: self.friend_action("DELETE", f"/friends/{uid}")))
            self.friends_cards.addWidget(f)
        if not data["friends"] and not data["requests"]:
            self.friends_cards.addWidget(label("No friends yet. Exchange friend codes to send a request.", "muted"))
        self.friends_cards.addStretch()
        self.status.setText("Friends are up to date.")

    def publish_world(self):
        profiles = self.launcher.profiles()
        if not profiles:
            self.error("Create a profile first.")
            return
        d = QDialog(self)
        d.setWindowTitle("Publish a world")
        d.setMinimumWidth(500)
        v = QVBoxLayout(d)
        form = QFormLayout()
        choice = QComboBox()
        for pid,m in profiles:
            choice.addItem(m.name,pid)
        address = QLineEdit()
        address.setPlaceholderText("play.example.com:25565 or LAN IP:port")
        configs = QLineEdit()
        configs.setPlaceholderText("config/example.toml, config/example.json")
        form.addRow("Profile", choice)
        form.addRow("Reachable address", address)
        form.addRow("Optional shared configs", configs)
        v.addLayout(form)
        v.addWidget(label("Start your world and Open to LAN, or use a server with this exact setup. This publishes connection details to accepted friends; it does not start a server or open router ports. Only select configs you have reviewed for private data.", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        v.addWidget(buttons)
        if d.exec():
            pid, addr = choice.currentData(), address.text().strip()
            paths = [p.strip() for p in configs.text().split(",") if p.strip()]
            def task(progress):
                m = self.launcher.share(pid, paths)
                world = World(address=addr, manifest=m)
                return FriendsClient(self.launcher).request("PUT", "/world", world.model_dump())
            self.run(task, lambda _: self.status.setText("World shared with your accepted friends."))

    def direct_join(self):
        pid = self.profile_choice.currentData()
        if not pid:
            self.error("Select a profile under Discover mods first.")
            return
        address = self.join_address.text().strip()
        try:
            World(address=address, manifest=self.launcher.manifest(pid))
        except Exception:
            self.error("Enter a valid hostname or IPv4 address with an optional port.")
            return
        self.play(pid, address)

    def tick(self):
        if self.job:
            return
        def task(progress):
            client = FriendsClient(self.launcher)
            if self.launcher.vault.get(client.key_name):
                client.request("POST", "/heartbeat")
        self.run(task, quiet=True)

    def make_settings(self):
        box = self.page("A setup that fits you", "Local preferences, account integration, and your friends service.")
        f,v = card("Runtime & connections", "Credentials are stored in your operating system’s keyring. An approved Microsoft application is required for game login.")
        form = QFormLayout()
        self.memory = QSpinBox()
        self.memory.setRange(1024,65536)
        self.memory.setSingleStep(1024)
        self.memory.setSuffix(" MiB")
        self.memory.setValue(self.launcher.config["memory_mb"])
        self.java = QLineEdit(self.launcher.config["java"])
        self.java.setPlaceholderText("Automatic Mojang runtime (recommended)")
        self.client_id = QLineEdit(self.launcher.config["microsoft_client_id"])
        self.redirect = QLineEdit(self.launcher.config["redirect_uri"])
        self.cfkey = QLineEdit()
        self.cfkey.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfkey.setPlaceholderText("Leave blank to keep existing key")
        self.friends_url = QLineEdit(self.launcher.config["friends_url"])
        for title, widget in [("Memory limit",self.memory),("Java executable",self.java),("Microsoft Client ID",self.client_id),("OAuth redirect URI",self.redirect),("CurseForge API key",self.cfkey),("Friends service URL",self.friends_url)]:
            form.addRow(title,widget)
        v.addLayout(form)
        v.addWidget(button("Save preferences", self.save_settings, True))
        box.addWidget(f)
        box.addWidget(label("This alpha supports Fabric and vanilla profiles. Forge, NeoForge, automatic world hosting, and built-in NAT traversal are planned. No cloud account is required for local profiles or mod downloads.", "muted", True))
        box.addStretch()

    def save_settings(self):
        config = {"memory_mb":self.memory.value(), "java":self.java.text().strip(), "microsoft_client_id":self.client_id.text().strip(), "redirect_uri":self.redirect.text().strip(), "friends_url":self.friends_url.text().strip()}
        try:
            if self.cfkey.text().strip():
                self.launcher.vault.set("curseforge-api-key", self.cfkey.text().strip())
                self.cfkey.clear()
            self.launcher.save_settings(config)
            self.status.setText("Preferences saved.")
        except Exception as exc:
            self.error(f"Could not save preferences: {type(exc).__name__}. Check your OS keyring.")

    def login(self):
        try:
            url,state,verifier = self.launcher.begin_login()
        except Exception as exc:
            self.error(str(exc))
            self.navigate(3)
            return
        webbrowser.open(url)
        redirect_url, ok = QInputDialog.getText(self, "Complete Microsoft sign-in", "Sign in in your browser. After the redirect, paste the full address here.\nA localhost connection error is expected in this alpha; copy its address bar.")
        if ok:
            self.run(lambda progress: self.launcher.finish_login(redirect_url,state,verifier), lambda name: (self.account_label.setText(name), self.status.setText("Signed in as " + name)))

    def closeEvent(self, event):
        if self.job is not None:
            self.status.setText("Wait for the current operation to finish before closing.")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Verdant")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    # Prevent simultaneous writers from two launcher instances.
    from PySide6.QtCore import QLockFile
    launcher = Launcher()
    lock = QLockFile(str(launcher.root / "launcher.lock"))
    if not lock.tryLock(100):
        QMessageBox.warning(None, "Verdant", "Another Verdant launcher is already using this data directory.")
        return 1
    window = Window(launcher)
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
