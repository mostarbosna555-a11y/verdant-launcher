from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import keyring
import minecraft_launcher_lib as mc
import requests

from .models import Manifest, SharedConfig, World
from .providers import Catalog


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def data_directory():
    if os.environ.get("VERDANT_HOME"):
        return Path(os.environ["VERDANT_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Verdant"
    if __import__("sys").platform == "darwin":
        return Path.home() / "Library/Application Support/Verdant"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "verdant"


class Vault:
    def get(self, name):
        return keyring.get_password("verdant-launcher", name)

    def set(self, name, value):
        # Fail closed if the OS secret service is unavailable; never fall back to plaintext.
        keyring.set_password("verdant-launcher", name, value)


class Launcher:
    def __init__(self, root=None):
        self.root = Path(root or data_directory())
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "profiles").mkdir(exist_ok=True)
        self.vault = Vault()
        self.config = {"memory_mb": 4096, "java": "", "microsoft_client_id": "", "redirect_uri": "http://localhost:42873/", "friends_url": "http://127.0.0.1:8787"}
        if (self.root / "settings.json").exists():
            self.config.update(json.loads((self.root / "settings.json").read_text()))
        self.processes = {}

    def save_settings(self, data):
        self.config.update(data)
        write_json(self.root / "settings.json", self.config)

    def catalog(self):
        key = os.environ.get("CURSEFORGE_API_KEY", "")
        if not key:
            try:
                key = self.vault.get("curseforge-api-key") or ""
            except keyring.errors.KeyringError:
                pass  # Public Modrinth downloads do not require secret storage.
        return Catalog(key)

    def profiles(self):
        result = []
        for p in sorted((self.root / "profiles").glob("*/manifest.json")):
            if p.parent.name.startswith("."):
                continue
            try:
                result.append((p.parent.name, Manifest.model_validate_json(p.read_text())))
            except (ValueError, OSError):
                continue
        return result

    def profile_path(self, profile_id):
        if str(uuid.UUID(profile_id)) != profile_id:
            raise ValueError("Invalid profile ID")
        return self.root / "profiles" / profile_id

    def manifest(self, profile_id):
        return Manifest.model_validate_json((self.profile_path(profile_id) / "manifest.json").read_text())

    def create(self, name, minecraft, loader):
        if minecraft not in {v["id"] for v in mc.utils.get_version_list()}:
            raise ValueError("Unknown Minecraft version")
        lv = mc.fabric.get_latest_loader_version() if loader == "fabric" else None
        m = Manifest(name=name, minecraft=minecraft, loader=loader, loader_version=lv)
        pid = str(uuid.uuid4())
        write_json(self.profile_path(pid) / "manifest.json", m.model_dump())
        return pid

    def install(self, profile_id, provider, project, kind="mod", progress=lambda _: None):
        if self.is_running(profile_id):
            raise ValueError("Close this profile's game before changing its mods.")
        old = self.manifest(profile_id)
        # Multiple providers can publish the same mod under unrelated IDs.
        if kind == "mod" and any(a.kind == "mod" and a.provider != provider for a in old.artifacts):
            raise ValueError("This alpha uses one mod provider per profile to avoid duplicate mod IDs. Resource packs may use either provider.")
        catalog = self.catalog()
        progress("Resolving compatible versions and dependencies…")
        added = catalog.resolve(provider, project, old, kind, existing={(a.provider, a.project): a for a in old.artifacts})
        new = Manifest.model_validate({**old.model_dump(), "artifacts": [a.model_dump() for a in old.artifacts + added]})
        catalog.validate_manifest(new)
        root = self.profile_path(profile_id)
        with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temp:
            stage = Path(temp)
            for a in added:
                catalog.download(a, stage / a.filename, progress)
            moved = []
            try:
                for a in added:
                    target = root / ("mods" if a.kind == "mod" else "resourcepacks") / a.filename
                    target.parent.mkdir(exist_ok=True)
                    if target.exists():
                        raise ValueError(f"An unmanaged file already exists: {a.filename}")
                    os.replace(stage / a.filename, target)
                    moved.append(target)
                write_json(root / "manifest.json", new.model_dump())
            except Exception:
                for path in moved:
                    path.unlink(missing_ok=True)
                raise
        return len(added)

    def verify(self, profile_id):
        manifest = self.manifest(profile_id)
        root = self.profile_path(profile_id)
        expected = {"mods": set(), "resourcepacks": set()}
        for a in manifest.artifacts:
            folder = "mods" if a.kind == "mod" else "resourcepacks"
            expected[folder].add(a.filename)
            p = root / folder / a.filename
            if not p.is_file() or p.is_symlink():
                raise ValueError(f"Missing or unsafe file: {a.filename}")
            with p.open("rb") as f:
                if hashlib.file_digest(f, a.algorithm).hexdigest() != a.digest:
                    raise ValueError(f"Modified file: {a.filename}. Re-sync the profile.")
        actual_mods = {p.name for p in (root / "mods").glob("*.jar")}
        if actual_mods != expected["mods"]:
            raise ValueError("Unmanaged mods were found. Exact sync requires a launcher-managed profile.")
        return manifest

    def share(self, profile_id, config_paths=()):
        m = self.verify(profile_id)
        root = self.profile_path(profile_id).resolve()
        configs = []
        for relative in config_paths:
            p = root / relative
            if not p.resolve().is_relative_to(root / "config") or p.is_symlink() or p.stat().st_size > 256000:
                raise ValueError("Unsafe or oversized config")
            content = p.read_bytes()
            configs.append(SharedConfig(path=relative, content=base64.b64encode(content).decode(), sha256=hashlib.sha256(content).hexdigest()))
        return Manifest.model_validate({**m.model_dump(), "configs": [c.model_dump() for c in configs]})

    def sync(self, manifest: Manifest, progress=lambda _: None):
        # Create an isolated instance. Publish it only after every file verifies.
        manifest = Manifest.model_validate(manifest.model_dump())
        pid = str(uuid.uuid4())
        catalog = self.catalog()
        progress("Checking shared versions and required dependencies…")
        catalog.validate_manifest(manifest)
        with tempfile.TemporaryDirectory(prefix=".sync-", dir=self.root / "profiles") as temp:
            root = Path(temp) / "profile"
            root.mkdir()
            for a in manifest.artifacts:
                folder = root / ("mods" if a.kind == "mod" else "resourcepacks")
                folder.mkdir(exist_ok=True)
                catalog.download(a, folder / a.filename, progress)
            for c in manifest.configs:
                p = root / c.path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(base64.b64decode(c.content, validate=True))
            write_json(root / "manifest.json", manifest.model_dump())
            os.replace(root, self.profile_path(pid))
        return pid

    def begin_login(self):
        client = self.config["microsoft_client_id"].strip()
        if not client:
            raise ValueError("Set an approved Microsoft application Client ID in Settings. See README → Microsoft login.")
        return mc.microsoft_account.get_secure_login_data(client, self.config["redirect_uri"])

    def finish_login(self, redirect_url, state, verifier):
        code = mc.microsoft_account.parse_auth_code_url(redirect_url, state)
        account = mc.microsoft_account.complete_login(self.config["microsoft_client_id"], None, self.config["redirect_uri"], code, verifier)
        self.vault.set("microsoft-refresh", account["refresh_token"])
        self.save_settings({"account_name": account["name"]})
        return account["name"]

    def account(self):
        token = self.vault.get("microsoft-refresh")
        if not token:
            raise ValueError("Sign in with Microsoft before launching Minecraft.")
        account = mc.microsoft_account.complete_refresh(self.config["microsoft_client_id"], None, self.config["redirect_uri"], token)
        self.vault.set("microsoft-refresh", account["refresh_token"])
        return account

    def is_running(self, pid):
        process = self.processes.get(pid)
        return process is not None and process.poll() is None

    def launch(self, pid, address=None, progress=lambda _: None):
        if self.is_running(pid):
            raise ValueError("This profile is already running.")
        manifest = self.verify(pid)
        if address:
            World(address=address, manifest=manifest)
        progress("Refreshing Microsoft session…")
        account = self.account()
        runtime = self.root / "runtime"
        callback = {"setStatus": progress}
        progress("Installing Minecraft runtime…")
        mc.install.install_minecraft_version(manifest.minecraft, runtime, callback=callback)
        version = manifest.minecraft
        if manifest.loader == "fabric":
            mc.fabric.install_fabric(manifest.minecraft, runtime, loader_version=manifest.loader_version, callback=callback, java=self.config["java"] or None)
            version = f"fabric-loader-{manifest.loader_version}-{manifest.minecraft}"
        root = self.profile_path(pid)
        packs = ["vanilla"] + [f"file/{a.filename}" for a in manifest.artifacts if a.kind == "resourcepack"]
        # Keep unrelated settings; activate the resource packs belonging to this profile.
        opts_path = root / "options.txt"
        lines = opts_path.read_text().splitlines() if opts_path.exists() else []
        lines = [s for s in lines if not s.startswith("resourcePacks:")]
        opts_path.write_text("\n".join(lines + ["resourcePacks:" + json.dumps(packs)]) + "\n")
        options = {"username": account["name"], "uuid": account["id"], "token": account["access_token"], "gameDirectory": str(root), "launcherName": "Verdant", "launcherVersion": "0.1.0", "jvmArguments": [f"-Xmx{int(self.config['memory_mb'])}M"], "enableLoggingConfig": True}
        if self.config["java"]:
            options["executablePath"] = self.config["java"]
        if address:
            version_info = json.loads((runtime / "versions" / manifest.minecraft / f"{manifest.minecraft}.json").read_text())
            # Quick Play was introduced in 1.20; determine support from official metadata.
            game_args = version_info.get("arguments", {}).get("game", [])
            if "quickPlayMultiplayer" in json.dumps(game_args):
                options["quickPlayMultiplayer"] = address
                options["quickPlayPath"] = str(root / "quickplay.json")
            else:
                host, _, port = address.partition(":")
                options.update(server=host, port=port or "25565")
        command = mc.command.get_minecraft_command(version, runtime, options)
        # Never log the command: it contains the Minecraft access token.
        log = root / "game.log"
        with log.open("ab") as out:
            process = subprocess.Popen(command, cwd=root, stdout=out, stderr=subprocess.STDOUT)
        self.processes[pid] = process
        progress(f"Minecraft started · process {process.pid}")
        return process.pid


class FriendsClient:
    def __init__(self, launcher):
        from urllib.parse import urlparse
        self.launcher = launcher
        self.url = launcher.config["friends_url"].rstrip("/")
        p = urlparse(self.url)
        if p.scheme != "https" and not (p.scheme == "http" and p.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Remote friends services require HTTPS")
        if p.username or p.password or p.query or p.fragment:
            raise ValueError("Invalid friends service URL")
        self.key_name = "friends:" + self.url

    def request(self, method, path, body=None, anonymous=False):
        token = None if anonymous else self.launcher.vault.get(self.key_name)
        if not anonymous and not token:
            raise ValueError("Create a friends identity first.")
        response = requests.request(method, self.url + path, json=body, headers={"Authorization": "Bearer " + token} if token else {}, timeout=30, allow_redirects=False)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.status_code)
            except ValueError:
                detail = response.status_code
            raise ValueError(f"Friends service: {detail}")
        if response.is_redirect:
            raise ValueError("Friends service redirects are not accepted")
        return response.json()

    def register(self, name, invite):
        # Verify secret storage before creating an identity that would otherwise be inaccessible.
        self.launcher.vault.set(self.key_name + ":probe", "ready")
        result = self.request("POST", "/register", {"name": name, "invite": invite}, anonymous=True)
        self.launcher.vault.set(self.key_name, result["token"])
        return result
