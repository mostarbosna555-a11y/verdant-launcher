# Verdant — Minecraft, together

A native, Linux-first Minecraft Java launcher alpha written in Python and Qt. The name is provisional. It is independent software, not an official Minecraft product.

![Desktop preview](preview.png)

## Run the desktop app

Requires Python 3.11+, a graphical desktop, a working system keyring (for example GNOME Keyring or KWallet via Secret Service), and Java for the Fabric installer. Minecraft's own Java runtime is installed by the launcher library. A compatible custom Java executable can be selected in Settings.

```bash
./run.sh
```

Or install explicitly:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[server,test]'
.venv/bin/verdant
```

On Debian/Ubuntu, Qt may additionally need `libxcb-cursor0`, `libxkbcommon-x11-0`, and `libegl1`. Run from a desktop login session with its D-Bus/keyring available. `QT_QPA_PLATFORM=offscreen` is for rendering/tests, not normal desktop use.

Data lives in `~/.local/share/verdant` on Linux (honors `XDG_DATA_HOME`), `%LOCALAPPDATA%/Verdant` on Windows, or `~/Library/Application Support/Verdant` on macOS. Override with `VERDANT_HOME`. This app does not read or modify your existing `.minecraft` installation. Windows/macOS code paths are present but have not been platform-tested.

## Implemented

- Native dark interface, persistent isolated profiles, vanilla and pinned Fabric loaders.
- Live Modrinth search, compatible versions, required dependency resolution, one-click installs.
- CurseForge search/download adapter using your own API key, respecting unavailable third-party downloads. It is implemented but not live-tested without a key.
- Resource pack downloads and activation, SHA-512/SHA-1 provider checksum verification.
- Export/import of versioned manifests, exact version synchronization into a new profile, failed-sync cleanup.
- Explicitly selected config sharing, path/type/size checks and SHA-256 checksums. No automatic upload of your config directory.
- Private friends service: invitation-gated registration, mutual friendship approval, online presence, private world manifests, removal and identity deletion endpoints.
- Sync & join: review a friend's setup, create an isolated profile, download pinned files from the original catalogs, install runtime, then connect.
- Microsoft PKCE authentication integration, OS-keyring refresh tokens, official Minecraft runtime installation and launch-command construction. Live account login/launch still needs an approved application ID and your sign-in.

## First session

1. Create a Fabric profile. `1.21.1` is an editable example default, not a claim about the latest Minecraft release.
2. Open Discover mods, select the profile, and search Modrinth. Click Install. Required dependencies are included.
3. Configure Microsoft login below, sign in, then Play.
4. For multiplayer, start a reachable Minecraft server, or open your singleplayer world to LAN. Use a LAN/VPN address if your friend shares that network. The launcher does not establish router connectivity yet.
5. Both players point Settings → Friends service URL at the same deployed service and create separate identities with the service's invitation secret.
6. Exchange friend codes; one sends a request, the other accepts. Refresh to see changes.
7. Host: Publish world, choosing the matching profile and reachable address. Optional config paths are comma-separated relative paths such as `config/example.toml`; review their contents for secrets before sharing.
8. Guest: Sync & join. If sign-in fails after synchronization, the downloaded profile remains available. Select it in Discover mods and use Friends → Join address to retry.

Published status means the host has announced a world, not that the launcher has probed the Minecraft server. Status expires after three minutes without a launcher heartbeat. Stop sharing when the world closes. Friend identities are independent display names, not verified Minecraft identities.

## Microsoft login

Owning Minecraft is necessary; a custom launcher also needs its own Microsoft application registration with access to the Minecraft APIs. This repository intentionally does not borrow another launcher's client ID and does not include an offline-auth bypass.

Follow the upstream [Microsoft login integration guide](https://minecraft-launcher-lib.readthedocs.io/en/7.1/tutorial/microsoft_login.html) and its linked Minecraft API approval process. Register a public/native application supporting personal Microsoft accounts, configure the loopback redirect URI, and obtain the required Minecraft API access. Do not embed a client secret in the desktop app.

Enter your approved Client ID and exact redirect URI in Settings. The default is `http://localhost:42873/`. Click Sign in. The alpha uses the system browser and a manual redirect handoff: after authentication, a localhost connection error is expected because no callback listener is running; copy the full redirected address into the launcher. PKCE and state validation are performed before exchanging the authorization code. Only the refresh token is persisted, using the OS keyring. Never send that redirect URL or a token in chat.

The login integration has not been validated against a real Microsoft account in this environment. Application approval, account entitlement, and multiplayer privileges can prevent launching or joining. Automatic browser callback handling and account switching remain follow-up work.

## CurseForge

Apply for your own key through [CurseForge's API process](https://support.curseforge.com/support/solutions/articles/9000208346-about-the-curseforge-api-and-how-to-apply-for-a-key), then save it in Settings or set `CURSEFORGE_API_KEY` in your launch environment. A missing download URL/author distribution restriction fails explicitly. The launcher does not scrape around that restriction or rehost jars. API and CDN integration follow [CurseForge's documentation](https://docs.curseforge.com/rest-api/).

To avoid silently installing duplicate mods published under different provider IDs, this alpha permits one mod provider per profile. Resource packs may use either provider. Already installed versions remain pinned; resolving an incompatible required version fails rather than silently upgrading your setup.

## Run the friends service locally

```bash
.venv/bin/python -m pip install -e '.[server]'
export VERDANT_INVITE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export VERDANT_DB="$PWD/work/friends.sqlite3"
.venv/bin/uvicorn verdant.server:app --host 127.0.0.1 --port 8787 --workers 1
```

Use `http://127.0.0.1:8787` in Settings. The invitation secret must be at least 24 characters; distribute it privately to the people you want to admit. Each person receives a distinct random bearer identity token saved in their OS keyring; the server stores only its hash. This is a small private-group service, not a hardened public SaaS. Keep one worker for its in-memory request rate limiter. There is no account recovery UI yet: losing your keyring means creating a new identity. Deletion/revocation is supported by `DELETE /me` with that identity's bearer token.

## Oracle / remote deployment

No Oracle account or VM has been created. Account registration requires the owner's details, verification, and payment-card check in Oracle's own signup flow. Do not share payment details here.

Oracle's current public [Free Tier offer](https://www.oracle.com/cloud/free/) advertises 2 Arm OCPUs / 12 GB total memory and up to 10 TB/month outbound transfer. This is not a guaranteed 16 GB allocation. Confirm the exact free entitlement, available capacity, and selected shape in your own tenancy before provisioning; the 30-day trial credit is separate from Always Free. This directory service needs far less memory than a Minecraft game server.

Deployment files are prepared for an Arm or x86 Linux VM with Docker Compose and a DNS hostname. They deploy the friends directory with HTTPS, not a Minecraft server or game-traffic relay.

1. Create an eligible VM after completing signup, install Docker/Compose, and point a hostname at its public IP.
2. Allow inbound TCP 80/443 in the OCI network security rules and host firewall. Keep the internal 8787 service port private. SSH access should be restricted to your administrative network.
3. Copy this repository to the VM. Set `VERDANT_DOMAIN` and a random `VERDANT_INVITE` in your shell or a protected local `.env` file; do not commit the latter.
4. From this directory run:

```bash
export VERDANT_DOMAIN=friends.your-domain.example
export VERDANT_INVITE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose -f deploy/compose.yaml up -d --build
```

Caddy obtains a TLS certificate. Set the launcher's service URL to `https://` plus your hostname. The launcher rejects remote plaintext HTTP. SQLite and Caddy data persist in Docker volumes. Back up the database volume; stop the service during a simple file-copy backup or use SQLite's backup API.

The directory intentionally has no public mod-file uploads, arbitrary download URLs, remote commands, or payment functionality. Rate limiting is conservative behind the reverse proxy (requests share the proxy IP). Deployment has not been executed against an Oracle VM.

## Current limits

- Fabric and vanilla only; Forge, NeoForge, Quilt, modpack archives, mod removal/upgrades, and JVM tuning UI are not implemented.
- No bundled NAT traversal, automatic world hosting, background Minecraft companion mod, or automatic world-close detection. Direct connectivity must already exist. The friends server does not transport Minecraft traffic.
- Sync covers managed client mods/resource packs and explicitly selected config files. It does not infer the contents of an unrelated server, include datapacks/world saves, or promise to repair missing content that the host never included.
- Each sync makes a new profile; existing saves and personal setups are preserved. Disk reuse and profile cleanup UI are future work.
- Imports/downloads are staged and failed network operations roll back. A power loss between a normal install's file moves and manifest commit may leave unmanaged files, which verification detects and refuses to launch. Sync publishes a fully staged profile by a same-filesystem rename.
- Downloads run in a background Qt thread with status updates. Cancellation, byte-level progress, retries/resume, and game-crash notifications are not implemented.
- Public-release hardening, installers/signing, dependency audits, real-account validation, and multiple-platform QA are still required.

## Development and checks

```bash
.venv/bin/python -m pip install -e '.[test,server]'
.venv/bin/python -m pytest -q
```

Tests cover traversal and reserved filenames, config integrity, duplicate files, atomic sync failures, modified/unmanaged mods, dependency resolution/conflicts, download-host restrictions/checksums, friend authorization, invite validation, deletion and request limits. See `VALIDATION.md` for the checks actually run for this delivery.

Module map: `ui.py` is the Qt desktop; `core.py` handles profiles, authentication and launch; `providers.py` implements catalog/download adapters; `models.py` validates shared manifests; `server.py` is the private friends API. The server needs neither Qt nor launcher account credentials.

## Publishing this repository

Upload the source files at the repository root, including `.gitignore`, `.dockerignore`, and `deploy/.env.example`. Do not upload the ZIP itself as a substitute for the source, the outer Codex workspace, or its `work/` directory. GitHub browser uploads do not apply your local `.gitignore`; use the cleaned GitHub-ready ZIP or Git from this project directory.

No real `.env` file or credentials are distributed. Desktop settings are saved in the application data directory; account refresh tokens and API keys use the OS keyring. The desktop application does not automatically load `.env` files.

For deployment, copy `deploy/.env.example` to `deploy/.env`, set your own domain and generated invitation secret, and run:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
```

The real `.env`, databases, local settings, logs, profiles, runtimes, and Python caches are excluded by `.gitignore`. The checked-in test credentials are synthetic fixtures, not live credentials.
