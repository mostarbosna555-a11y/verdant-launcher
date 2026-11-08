from __future__ import annotations

import base64
import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Artifact(StrictModel):
    provider: Literal["modrinth", "curseforge"]
    project: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    name: str = Field(max_length=160)
    filename: str = Field(max_length=200)
    kind: Literal["mod", "resourcepack"] = "mod"
    algorithm: Literal["sha512", "sha1"]
    digest: str

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+ ()\-]{0,199}", value):
            raise ValueError("Unsafe filename")
        if value.endswith((".", " ")) or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
            raise ValueError("Reserved filename")
        return value

    @model_validator(mode="after")
    def check_digest(self):
        length = 128 if self.algorithm == "sha512" else 40
        if not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", self.digest):
            raise ValueError("Invalid content hash")
        if not self.filename.lower().endswith(".jar" if self.kind == "mod" else ".zip"):
            raise ValueError("Unexpected artifact extension")
        return self


class SharedConfig(StrictModel):
    path: str = Field(max_length=240)
    content: str = Field(max_length=350000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_file(self):
        p = PurePosixPath(self.path)
        if p.is_absolute() or ".." in p.parts or "\\" in self.path or ":" in self.path or len(p.parts) < 2 or p.parts[0] != "config":
            raise ValueError("Only safe relative config paths are allowed")
        if p.suffix.lower() not in {".json", ".toml", ".properties", ".cfg", ".txt", ".json5", ".yaml", ".yml"}:
            raise ValueError("Unsupported config file type")
        for part in p.parts:
            Artifact.safe_filename(part)
        data = base64.b64decode(self.content, validate=True)
        if len(data) > 256000 or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("Config size or checksum mismatch")
        return self


class Manifest(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=80)
    minecraft: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,60}$")
    loader: Literal["vanilla", "fabric"] = "fabric"
    loader_version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.+-]{1,60}$")
    artifacts: list[Artifact] = Field(default_factory=list, max_length=500)
    configs: list[SharedConfig] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def invariants(self):
        if self.loader == "fabric" and not self.loader_version:
            raise ValueError("Fabric loader version must be pinned")
        if self.loader == "vanilla" and any(a.kind == "mod" for a in self.artifacts):
            raise ValueError("Vanilla profiles cannot contain mods")
        keys = [(a.provider, a.project) for a in self.artifacts]
        paths = [(a.kind, a.filename.lower()) for a in self.artifacts]
        if len(set(keys)) != len(keys) or len(set(paths)) != len(paths):
            raise ValueError("Duplicate project or filename")
        if len({c.path.lower() for c in self.configs}) != len(self.configs):
            raise ValueError("Duplicate config path")
        if sum(len(c.content) for c in self.configs) > 1_000_000:
            raise ValueError("Shared configs exceed 1 MB encoded")
        return self


class World(StrictModel):
    address: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.\-]+(?::[0-9]{1,5})?$")
    manifest: Manifest

    @field_validator("address")
    @classmethod
    def port_range(cls, value):
        if ":" in value and not 1 <= int(value.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("Invalid server port")
        return value
