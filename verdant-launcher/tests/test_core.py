import base64
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verdant.core import Launcher
from verdant.models import Artifact, Manifest, SharedConfig
from verdant.providers import Catalog

PAYLOAD = b"pretend-jar-content"


def artifact(**changes):
    data = dict(provider="modrinth", project="sodium", version="abc123", name="Sodium", filename="sodium.jar", algorithm="sha512", digest=hashlib.sha512(PAYLOAD).hexdigest())
    return Artifact(**(data | changes))


def manifest(**changes):
    return Manifest(**(dict(name="Test", minecraft="1.21.1", loader="fabric", loader_version="0.16.10", artifacts=[artifact()]) | changes))


@pytest.mark.parametrize("filename", ["../evil.jar", "/tmp/evil.jar", "a\\evil.jar", "CON.jar", "a:evil.jar", "thing.exe"])
def test_reject_unsafe_artifacts(filename):
    with pytest.raises(ValidationError):
        artifact(filename=filename)


def test_reject_duplicate_projects_and_casefolded_filenames():
    with pytest.raises(ValidationError):
        manifest(artifacts=[artifact(), artifact(version="other")])
    with pytest.raises(ValidationError):
        manifest(artifacts=[artifact(), artifact(project="other", filename="SODIUM.jar")])


@pytest.mark.parametrize("path", ["../evil.json", "/config/test.json", "config/../../evil.json", "config/a.py", "config\\test.json", "config/CON.json"])
def test_reject_unsafe_config(path):
    data = b"{}"
    with pytest.raises(ValidationError):
        SharedConfig(path=path, content=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())


def test_no_unverified_download_url_in_manifest():
    data = artifact().model_dump() | {"url": "http://127.0.0.1/secret"}
    with pytest.raises(ValidationError):
        Artifact.model_validate(data)


class FakeCatalog:
    def __init__(self, fail=False):
        self.fail = fail

    def validate_manifest(self, manifest):
        pass

    def download(self, a, target, progress):
        target.write_bytes(PAYLOAD)
        if self.fail:
            raise ValueError("Network failed")


def test_sync_is_atomic_and_isolated(tmp_path, monkeypatch):
    launcher = Launcher(tmp_path)
    monkeypatch.setattr(launcher, "catalog", lambda: FakeCatalog())
    first = launcher.sync(manifest())
    second = launcher.sync(manifest())
    assert first != second
    assert launcher.verify(first) == manifest()
    assert len(launcher.profiles()) == 2
    monkeypatch.setattr(launcher, "catalog", lambda: FakeCatalog(True))
    with pytest.raises(ValueError):
        launcher.sync(manifest())
    assert len(launcher.profiles()) == 2
    assert not list((tmp_path / "profiles").glob(".sync-*"))


def test_verify_detects_changed_and_unmanaged_mods(tmp_path, monkeypatch):
    launcher = Launcher(tmp_path)
    monkeypatch.setattr(launcher, "catalog", lambda: FakeCatalog())
    pid = launcher.sync(manifest())
    root = launcher.profile_path(pid)
    (root / "mods" / "extra.jar").write_bytes(b"extra")
    with pytest.raises(ValueError, match="Unmanaged"):
        launcher.verify(pid)
    (root / "mods" / "extra.jar").unlink()
    (root / "mods" / "sodium.jar").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Modified"):
        launcher.verify(pid)


def test_configs_written_only_inside_new_instance(tmp_path, monkeypatch):
    launcher = Launcher(tmp_path)
    monkeypatch.setattr(launcher, "catalog", lambda: FakeCatalog())
    content = b'{"key": "value"}'
    config = SharedConfig(path="config/example/test.json", content=base64.b64encode(content).decode(), sha256=hashlib.sha256(content).hexdigest())
    pid = launcher.sync(manifest(configs=[config]))
    assert (launcher.profile_path(pid) / config.path).read_bytes() == content


def modrinth_fixtures():
    def version(pid, dependencies=()):
        return {"id": pid+"v", "project_id": pid, "game_versions": ["1.21.1"], "loaders": ["fabric"], "files": [{"filename": pid+".jar", "primary": True, "hashes": {"sha512": "a"*128}}], "dependencies": list(dependencies)}
    dep = {"dependency_type": "required", "project_id": "api", "version_id": "apiv"}
    return {"/project/main": {"project_type":"mod", "client_side":"required", "title":"Main"}, "/project/api": {"project_type":"mod", "client_side":"required", "title":"API"}, "/project/main/version": [version("main", [dep])], "/version/apiv":version("api")}


def test_required_dependency_resolution(monkeypatch):
    catalog = Catalog()
    data = modrinth_fixtures()
    monkeypatch.setattr(catalog, "api", lambda provider,path,params=None:data[path])
    resolved = catalog.resolve("modrinth", "main", manifest(artifacts=[]))
    assert [a.project for a in resolved] == ["api", "main"]


def test_dependency_version_conflict(monkeypatch):
    catalog = Catalog()
    data = modrinth_fixtures()
    monkeypatch.setattr(catalog, "api", lambda provider,path,params=None:data[path])
    old = artifact(project="api", version="old")
    with pytest.raises(ValueError, match="conflict"):
        catalog.resolve("modrinth", "main", manifest(artifacts=[]), existing={("modrinth","api"):old})


def test_download_rejects_untrusted_host(tmp_path, monkeypatch):
    c = Catalog()
    monkeypatch.setattr(c, "artifact_url", lambda a:"http://127.0.0.1/internal")
    with pytest.raises(ValueError, match="outside"):
        c.download(artifact(), tmp_path / "test.jar")


def test_checksum_mismatch_removes_partial_file(tmp_path, monkeypatch):
    class Response:
        is_redirect = False
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): return iter([b"wrong data"])
    c = Catalog()
    monkeypatch.setattr(c, "artifact_url", lambda a:"https://cdn.modrinth.com/test.jar")
    monkeypatch.setattr(c.session, "get", lambda *args,**kwargs: Response())
    target = tmp_path / "test.jar"
    with pytest.raises(ValueError, match="Checksum"):
        c.download(artifact(), target)
    assert not target.exists()


def test_shared_manifest_missing_dependency_is_rejected(monkeypatch):
    c = Catalog()
    data = modrinth_fixtures()
    data['/version/mainv'] = data['/project/main/version'][0]
    monkeypatch.setattr(c, 'api', lambda provider,path,params=None:data[path])
    main = artifact(project='main',version='mainv',filename='main.jar')
    with pytest.raises(ValueError,match='Missing'):
        c.validate_manifest(manifest(artifacts=[main]))


def test_shared_manifest_loader_mismatch_is_rejected(monkeypatch):
    c = Catalog()
    data = modrinth_fixtures()['/version/apiv']
    data['loaders'] = ['forge']
    monkeypatch.setattr(c, 'api', lambda *args,**kwargs:data)
    with pytest.raises(ValueError,match='Incompatible'):
        c.validate_manifest(manifest(artifacts=[artifact(project='api',version='apiv')]))


def test_reverse_incompatibility_is_rejected(monkeypatch):
    c = Catalog()
    data = modrinth_fixtures()
    data['/version/mainv'] = data['/project/main/version'][0]
    data['/version/apiv']['dependencies'] = [{'dependency_type':'incompatible','project_id':'main','version_id':None}]
    monkeypatch.setattr(c,'api',lambda provider,path,params=None:data[path])
    with pytest.raises(ValueError,match='conflicts'):
        c.validate_manifest(manifest(artifacts=[artifact(project='main',version='mainv',filename='main.jar'), artifact(project='api',version='apiv',filename='api.jar')]))


def test_remote_friends_requires_tls(tmp_path):
    from verdant.core import FriendsClient
    l = Launcher(tmp_path)
    l.config['friends_url'] = 'http://public.example.com'
    with pytest.raises(ValueError,match='HTTPS'):
        FriendsClient(l)


def test_launch_authentication_precedes_runtime_download(tmp_path, monkeypatch):
    import uuid
    import verdant.core as core
    launcher = Launcher(tmp_path)
    pid = str(uuid.uuid4())
    core.write_json(launcher.profile_path(pid)/'manifest.json',manifest(artifacts=[]).model_dump())
    def fail():
        raise ValueError('Sign in required')
    monkeypatch.setattr(launcher,'account',fail)
    monkeypatch.setattr(core.mc.install,'install_minecraft_version',lambda *args,**kwargs:pytest.fail('Download should not start without auth'))
    with pytest.raises(ValueError,match='Sign in'):
        launcher.launch(pid)


def test_launch_uses_quick_play_and_no_shell(tmp_path, monkeypatch):
    import uuid
    from types import SimpleNamespace
    import verdant.core as core
    launcher = Launcher(tmp_path)
    pid = str(uuid.uuid4())
    core.write_json(launcher.profile_path(pid)/'manifest.json',manifest(artifacts=[],loader='vanilla',loader_version=None).model_dump())
    runtime = tmp_path/'runtime'/'versions'/'1.21.1'
    core.write_json(runtime/'1.21.1.json',{'arguments':{'game':['--quickPlayMultiplayer']}})
    monkeypatch.setattr(launcher,'account',lambda:{'name':'Player','id':'uuid','access_token':'test-only-token'})
    monkeypatch.setattr(core.mc.install,'install_minecraft_version',lambda *args,**kwargs:None)
    seen = {}
    def command(version,path,options):
        seen.update(options)
        return ['java','test']
    monkeypatch.setattr(core.mc.command,'get_minecraft_command',command)
    def spawn(args,**kwargs):
        assert args == ['java','test']
        assert 'shell' not in kwargs
        return SimpleNamespace(pid=123,poll=lambda:None)
    monkeypatch.setattr(core.subprocess,'Popen',spawn)
    assert launcher.launch(pid,'play.example.com:25565') == 123
    assert seen['quickPlayMultiplayer'] == 'play.example.com:25565'
    assert seen['token'] == 'test-only-token'
    assert launcher.is_running(pid)
    assert 'test-only-token' not in (launcher.profile_path(pid)/'game.log').read_text()
