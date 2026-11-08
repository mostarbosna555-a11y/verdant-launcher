# Validation — 2026-11-08

Environment: Linux x86_64, Python 3.13.5, PySide6 6.12.0, minecraft-launcher-lib 7.1.

Passed:

- 30 automated tests: manifest/path validation, dependency checks, staging/rollback, file integrity, authenticated launch flow and Quick Play options, HTTPS enforcement, private friend authorization, invitation checks, deletion, request size limits.
- Python compilation of all application modules.
- Native Qt construction and offscreen rendering of all four pages. Visually inspected library, settings and live catalog screens. Fixed collapsing catalog cards and checked a minimum readable card height.
- Live Modrinth search for Sodium on Fabric/Minecraft 1.21.1; real jar download; provider SHA-512 verification; isolated exact-profile sync with a second real download.
- Official Minecraft 1.21.1 runtime/assets/Java installation and Fabric loader 0.20.1 installation using the launcher library. Installation completed successfully.
- Background Qt catalog query and result rendering, with thread completion and UI re-enabling.
- Docker Compose configuration validation.
- Friends-service Docker image build. A temporary read-only, non-root container passed HTTP health, invitation registration, and bearer-authenticated identity checks; the container was removed after testing.

Not verified:

- Microsoft live account authentication, entitlement and actual game launch/join. These require an approved Microsoft application ID and the owner's login.
- CurseForge API/download requests, because no key was supplied.
- Two-machine Minecraft multiplayer, router traversal, or remote Oracle deployment.
- Windows/macOS execution; packaging/signing; Caddy public TLS issuance on a real domain.

The automated suite emits one upstream Starlette warning about the HTTPX test-client integration; no test failures occurred. This is an alpha, not a release-readiness certification.
