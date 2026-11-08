"""Private friends directory. One worker, SQLite, TLS at the reverse proxy."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .models import World


class Registration(BaseModel):
    name: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_ -]+$")
    invite: str = Field(max_length=200)


class FriendRequest(BaseModel):
    code: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


def create_app(database=None, invite=None):
    db_path = Path(database or os.environ.get("VERDANT_DB", "./work/friends.sqlite3"))
    registration_invite = invite if invite is not None else os.environ.get("VERDANT_INVITE", "")

    @contextmanager
    def db():
        con = sqlite3.connect(db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    @asynccontextmanager
    async def lifespan(app):
        if len(registration_invite) < 24:
            raise RuntimeError("Set VERDANT_INVITE to a random secret of at least 24 characters")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with db() as con:
            con.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                    code TEXT UNIQUE NOT NULL, seen REAL NOT NULL DEFAULT 0, world TEXT
                );
                CREATE TABLE IF NOT EXISTS friendships(
                    sender TEXT REFERENCES users(id), recipient TEXT REFERENCES users(id),
                    accepted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(sender,recipient), CHECK(sender != recipient)
                );
            ''')
        yield

    app = FastAPI(title="Verdant Friends", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    windows = defaultdict(deque)

    @app.middleware("http")
    async def limits(request: Request, call_next):
        # Bound the streamed body as well as requests with a Content-Length header.
        total = 0
        chunks = []
        async for chunk in request.stream():
            total += len(chunk)
            if total > 2_000_000:
                return JSONResponse({"detail": "Body too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        if len(windows) > 10000:
            for old_ip in list(windows):
                if not windows[old_ip] or windows[old_ip][-1] < now - 60:
                    del windows[old_ip]
        bucket = windows[ip]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 120:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        bucket.append(now)
        return await call_next(request)

    def user(request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 200:
            raise HTTPException(401, "Sign in required")
        digest = hashlib.sha256(header[7:].encode()).hexdigest()
        with db() as con:
            result = con.execute("SELECT * FROM users WHERE token_hash=?", (digest,)).fetchone()
        if result is None:
            raise HTTPException(401, "Invalid identity")
        return dict(result)

    @app.get("/health")
    def health():
        return {"ok": True, "version": "0.1.0"}

    @app.post("/register")
    def register(body: Registration):
        if not secrets.compare_digest(body.invite, registration_invite):
            raise HTTPException(403, "Invalid server invitation")
        token, uid, code = secrets.token_urlsafe(32), secrets.token_urlsafe(16), secrets.token_urlsafe(12)
        with db() as con:
            con.execute("INSERT INTO users(id,name,token_hash,code,seen) VALUES(?,?,?,?,?)", (uid, body.name, hashlib.sha256(token.encode()).hexdigest(), code, time.time()))
        return {"token": token, "code": code, "id": uid}

    @app.get("/me")
    def me(current=Depends(user)):
        return {k: current[k] for k in ("id", "name", "code")}

    @app.post("/heartbeat")
    def heartbeat(current=Depends(user)):
        with db() as con:
            con.execute("UPDATE users SET seen=? WHERE id=?", (time.time(), current["id"]))
        return {"ok": True}

    @app.get("/friends")
    def friends(current=Depends(user)):
        with db() as con:
            rows = con.execute('''SELECT u.id,u.name,u.seen,u.world FROM users u JOIN friendships f
                ON ((f.sender=? AND f.recipient=u.id) OR (f.recipient=? AND f.sender=u.id))
                WHERE f.accepted=1''', (current["id"], current["id"])).fetchall()
            pending = con.execute('''SELECT u.id,u.name FROM users u JOIN friendships f
                ON f.sender=u.id WHERE f.recipient=? AND f.accepted=0''', (current["id"],)).fetchall()
        return {"friends": [{"id": r["id"], "name": r["name"], "online": time.time()-r["seen"] < 180, "world": json.loads(r["world"]) if r["world"] and time.time()-r["seen"] < 180 else None} for r in rows], "requests": [dict(r) for r in pending]}

    @app.post("/friends/request")
    def send_request(body: FriendRequest, current=Depends(user)):
        with db() as con:
            target = con.execute("SELECT id FROM users WHERE code=?", (body.code,)).fetchone()
            if target is None or target["id"] == current["id"]:
                raise HTTPException(404, "Friend code not found")
            old = con.execute("SELECT 1 FROM friendships WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)", (current["id"], target["id"], target["id"], current["id"])).fetchone()
            if old:
                raise HTTPException(409, "A friendship or request already exists")
            con.execute("INSERT INTO friendships(sender,recipient) VALUES(?,?)", (current["id"], target["id"]))
        return {"ok": True}

    @app.post("/friends/{sender}/accept")
    def accept(sender: str, current=Depends(user)):
        with db() as con:
            updated = con.execute("UPDATE friendships SET accepted=1 WHERE sender=? AND recipient=? AND accepted=0", (sender, current["id"]))
            if updated.rowcount != 1:
                raise HTTPException(404, "Request not found")
        return {"ok": True}

    @app.delete("/friends/{other}")
    def remove(other: str, current=Depends(user)):
        with db() as con:
            con.execute("DELETE FROM friendships WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)", (current["id"], other, other, current["id"]))
        return {"ok": True}

    @app.put("/world")
    def publish(body: World, current=Depends(user)):
        with db() as con:
            con.execute("UPDATE users SET world=?,seen=? WHERE id=?", (body.model_dump_json(), time.time(), current["id"]))
        return {"ok": True}

    @app.delete("/world")
    def unpublish(current=Depends(user)):
        with db() as con:
            con.execute("UPDATE users SET world=NULL WHERE id=?", (current["id"],))
        return {"ok": True}

    @app.delete("/me")
    def delete_identity(current=Depends(user)):
        with db() as con:
            con.execute("DELETE FROM friendships WHERE sender=? OR recipient=?", (current["id"], current["id"]))
            con.execute("DELETE FROM users WHERE id=?", (current["id"],))
        return {"ok": True}

    return app


app = create_app()
