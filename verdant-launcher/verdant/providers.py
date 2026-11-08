from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import requests

from .models import Artifact, Manifest

USER_AGENT = "VerdantLauncher/0.1.0 (private development alpha)"


class Catalog:
    def __init__(self, curseforge_key=""):
        self.key = curseforge_key
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def api(self, provider, path, params=None):
        if provider == "curseforge":
            if not self.key:
                raise ValueError("Add your CurseForge API key in Settings first.")
            base = "https://api.curseforge.com/v1"
            headers = {"x-api-key": self.key}
        else:
            base = "https://api.modrinth.com/v2"
            headers = {}
        response = self.session.get(base + path, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def search(self, provider, query, manifest, kind="mod"):
        if kind == "mod" and manifest.loader == "vanilla":
            raise ValueError("Select a Fabric profile to install mods.")
        if provider == "modrinth":
            facets = [[f"project_type:{kind}"], [f"versions:{manifest.minecraft}"]]
            if kind == "mod":
                facets.append([f"categories:{manifest.loader}"])
            data = self.api(provider, "/search", {"query": query, "facets": json.dumps(facets), "limit": 30})
            return [{"id": h["project_id"], "name": h["title"], "description": h["description"], "downloads": h["downloads"], "kind": kind} for h in data["hits"]]
        params = {"gameId": 432, "classId": 6 if kind == "mod" else 12, "searchFilter": query, "gameVersion": manifest.minecraft, "pageSize": 30, "sortField": 2, "sortOrder": "desc"}
        if kind == "mod":
            params["modLoaderType"] = 4
        data = self.api(provider, "/mods/search", params)["data"]
        return [{"id": str(h["id"]), "name": h["name"], "description": h["summary"], "downloads": h["downloadCount"], "kind": kind} for h in data]

    def resolve(self, provider, project, manifest, kind="mod", pinned=None, existing=None):
        resolved = dict(existing or {})
        added = []
        visiting = set()

        def visit(pid, version=None, requested_kind="mod"):
            key = (provider, str(pid))
            if key in resolved:
                if version and resolved[key].version != str(version):
                    raise ValueError(f"Dependency version conflict for {pid}. Create a fresh profile.")
                return
            if key in visiting:
                return
            if len(visiting) + len(resolved) >= 500:
                raise ValueError("Dependency graph exceeds 500 projects")
            visiting.add(key)
            if provider == "modrinth":
                meta = self.api(provider, f"/project/{pid}")
                actual_kind = meta["project_type"]
                if actual_kind not in {"mod", "resourcepack"} or actual_kind != requested_kind:
                    raise ValueError("Unsupported dependency type")
                if actual_kind == "mod" and meta.get("client_side") == "unsupported":
                    raise ValueError(f"{meta['title']} is server-only and cannot be added to a client profile.")
                if version:
                    v = self.api(provider, f"/version/{version}")
                else:
                    params = {"game_versions": json.dumps([manifest.minecraft])}
                    if actual_kind == "mod":
                        params["loaders"] = json.dumps([manifest.loader])
                    versions = self.api(provider, f"/project/{pid}/version", params)
                    if not versions:
                        raise ValueError(f"No compatible version of {meta['title']}")
                    v = versions[0]
                if v["project_id"] != str(pid) or manifest.minecraft not in v["game_versions"] or (actual_kind == "mod" and manifest.loader not in v["loaders"]):
                    raise ValueError("Pinned mod is incompatible with this profile")
                f = next((f for f in v["files"] if f.get("primary")), v["files"][0])
                a = Artifact(provider=provider, project=str(pid), version=v["id"], name=meta["title"], filename=f["filename"], kind=actual_kind, algorithm="sha512", digest=f["hashes"]["sha512"])
                # Record before descending so cyclic dependencies terminate and pinned conflicts are checked.
                resolved[key] = a
                for dep in v["dependencies"]:
                    if dep["dependency_type"] == "required":
                        dep_id = dep.get("project_id")
                        if not dep_id and dep.get("version_id"):
                            dep_id = self.api(provider, f"/version/{dep['version_id']}")["project_id"]
                        if not dep_id:
                            raise ValueError("A required dependency has no downloadable project")
                        visit(dep_id, dep.get("version_id"))
                    elif dep["dependency_type"] == "incompatible" and (provider, dep.get("project_id")) in resolved:
                        raise ValueError("An incompatible dependency is installed")
            else:
                meta = self.api(provider, f"/mods/{pid}")["data"]
                if meta.get("classId") != (6 if requested_kind == "mod" else 12):
                    raise ValueError("Unsupported CurseForge project type")
                if version:
                    v = self.api(provider, f"/mods/{pid}/files/{version}")["data"]
                else:
                    params = {"gameVersion": manifest.minecraft, "pageSize": 50}
                    if requested_kind == "mod":
                        params["modLoaderType"] = 4
                    versions = self.api(provider, f"/mods/{pid}/files", params)["data"]
                    versions = [f for f in versions if f.get("isAvailable", False)]
                    if not versions:
                        raise ValueError("No compatible CurseForge file")
                    v = max(versions, key=lambda f: f["fileDate"])
                if manifest.minecraft not in v["gameVersions"] or (requested_kind == "mod" and "Fabric" not in v["gameVersions"]):
                    raise ValueError("CurseForge file is incompatible")
                if not v.get("downloadUrl") or not v.get("isAvailable", False):
                    raise ValueError("The author has disabled third-party downloads for this file.")
                digest = next((h["value"] for h in v["hashes"] if h["algo"] == 1), None)
                if not digest:
                    raise ValueError("CurseForge file has no SHA-1 checksum")
                a = Artifact(provider=provider, project=str(pid), version=str(v["id"]), name=meta["name"], filename=v["fileName"], kind=requested_kind, algorithm="sha1", digest=digest.lower())
                resolved[key] = a
                for dep in v["dependencies"]:
                    if dep["relationType"] == 3:
                        visit(str(dep["modId"]))
                    elif dep["relationType"] == 5 and (provider, str(dep["modId"])) in resolved:
                        raise ValueError("An incompatible dependency is installed")
            visiting.remove(key)
            added.append(a)

        visit(project, pinned, kind)
        return added

    def validate_manifest(self, manifest):
        """Check the complete pinned graph, including reverse incompatibilities."""
        present = {(a.provider, a.project): a for a in manifest.artifacts}
        mod_providers = {a.provider for a in manifest.artifacts if a.kind == "mod"}
        if len(mod_providers) > 1:
            raise ValueError("Mixed mod providers are not supported in this alpha")
        for a in manifest.artifacts:
            if a.provider == "modrinth":
                v = self.api(a.provider, f"/version/{a.version}")
                if v["project_id"] != a.project or manifest.minecraft not in v["game_versions"] or (a.kind == "mod" and manifest.loader not in v["loaders"]):
                    raise ValueError(f"Incompatible pinned version: {a.name}")
                for dep in v["dependencies"]:
                    pid = dep.get("project_id")
                    vid = dep.get("version_id")
                    if not pid and vid:
                        pid = self.api(a.provider, f"/version/{vid}")["project_id"]
                    installed = present.get((a.provider, pid))
                    if dep["dependency_type"] == "required" and (not installed or (vid and installed.version != vid)):
                        raise ValueError(f"Missing or mismatched required dependency for {a.name}")
                    if dep["dependency_type"] == "incompatible" and installed and (not vid or installed.version == vid):
                        raise ValueError(f"An installed mod conflicts with {a.name}")
            else:
                v = self.api(a.provider, f"/mods/{a.project}/files/{a.version}")["data"]
                if manifest.minecraft not in v["gameVersions"] or (a.kind == "mod" and "Fabric" not in v["gameVersions"]):
                    raise ValueError(f"Incompatible pinned version: {a.name}")
                for dep in v["dependencies"]:
                    installed = present.get((a.provider, str(dep["modId"])))
                    if dep["relationType"] == 3 and not installed:
                        raise ValueError(f"Missing required dependency for {a.name}")
                    if dep["relationType"] == 5 and installed:
                        raise ValueError(f"An installed mod conflicts with {a.name}")

    def artifact_url(self, artifact):
        # URLs from friend manifests are deliberately unsupported. Resolve against the provider.
        if artifact.provider == "modrinth":
            v = self.api("modrinth", f"/version/{artifact.version}")
            if v["project_id"] != artifact.project:
                raise ValueError("Project identity mismatch")
            f = next((f for f in v["files"] if f["filename"] == artifact.filename and f["hashes"].get(artifact.algorithm) == artifact.digest), None)
            if not f:
                raise ValueError("Provider checksum does not match the shared manifest")
            return f["url"]
        v = self.api("curseforge", f"/mods/{artifact.project}/files/{artifact.version}")["data"]
        if str(v["modId"]) != artifact.project or v["fileName"] != artifact.filename or not any(h["algo"] == 1 and h["value"].lower() == artifact.digest for h in v["hashes"]):
            raise ValueError("CurseForge file identity or checksum mismatch")
        if not v.get("downloadUrl") or not v.get("isAvailable", False):
            raise ValueError("Third-party distribution is unavailable for this file")
        return v["downloadUrl"]

    def download(self, artifact: Artifact, destination: Path, progress=lambda _: None):
        url = self.artifact_url(artifact)
        allowed = {"cdn.modrinth.com"} if artifact.provider == "modrinth" else {"edge.forgecdn.net", "mediafilez.forgecdn.net", "media.forgecdn.net"}
        # Validate every redirect before connecting; a catalog cannot redirect to the LAN.
        for _ in range(6):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.port not in (None, 443) or parsed.username:
                raise ValueError("Download URL is outside the provider CDN")
            response = self.session.get(url, stream=True, timeout=(15, 60), allow_redirects=False)
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers["Location"])
                response.close()
                continue
            break
        else:
            raise ValueError("Too many download redirects")
        with response:
            response.raise_for_status()
            digest = hashlib.new(artifact.algorithm)
            total = 0
            progress(f"Downloading {artifact.name}")
            try:
                with destination.open("xb") as stream:
                    for chunk in response.iter_content(256 * 1024):
                        total += len(chunk)
                        if total > 512 * 1024 * 1024:
                            raise ValueError("Artifact exceeds 512 MiB")
                        digest.update(chunk)
                        stream.write(chunk)
                if digest.hexdigest() != artifact.digest:
                    raise ValueError(f"Checksum verification failed: {artifact.name}")
            except Exception:
                destination.unlink(missing_ok=True)
                raise
