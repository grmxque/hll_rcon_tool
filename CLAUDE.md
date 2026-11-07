# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The engineering rules in `AGENTS.md` (imported above) apply here. Its "Project-specific instructions" section was written
for another project (CRM, prospects, Laravel-style concepts) and does **not** describe this codebase; the
general sections still apply.

## This repository is a fork

`origin` = `grmxque/hll_rcon_tool` (deployed on our VPS), `upstream` = `MarechJ/hll_rcon_tool` (official CRCON).
`master` is upstream plus our own modules, and upstream releases are merged in regularly. Every change must keep
that merge cheap:

- Put fork code in **new files** (e.g. `rcon/automods/antisteamroll.py`, `rcon/user_config/auto_mod_antisteamroll.py`,
  `rcongui/src/pages/settings/_data/automods/anti-steamroll.js`, `docs/anti-steamroll.md`). In upstream files,
  limit changes to one- or two-line insertions (imports, registry entries). Do not refactor or reformat upstream code.
- **Never add permissions to `RconUser.Meta` or add Django migrations** for fork features: a numbered
  `rconweb/api/migrations/00NN_*` file collides with upstream's next migration and breaks `migrate`.
  Add permissions to `FORK_PERMISSIONS` in `rconweb/api/fork_permissions.py` (created by a `post_migrate` hook).
- Avoid fork alembic revisions. If one is ever unavoidable, every upstream merge will need `alembic merge heads`.
- Never edit `.github/workflows/*`, `default.env` or `docker-templates/*`. Fork-specific deployment settings
  live in the VPS's untracked `.env`.
- Integrate upstream with `git merge upstream/master`, never a rebase (the branch is published and deployed).
- Update and deployment procedure: `docs/fork-maintenance.md`.

## Commands

Python ≥ 3.12, managed with `uv` (`pyproject.toml`, `uv.lock`). Frontend: Node + npm in `rcongui/`.

```bash
uv sync                                          # install backend + dev deps into .venv
source dev.env && uv run pytest tests/          # full suite (Postgres + Redis running, see docs/local-dev.md)
DEBUG=1 SERVER_NUMBER=1 HLL_GAME=hll uv run pytest tests/test_antisteamroll_automod.py::test_name   # single test, no services
REACT_APP_API_URL=/api/ npm run build            # (in rcongui/) production build needs this variable
uv run ruff check --fix . && uv run ruff format .   # lint + format (also run by pre-commit)

cd rcongui && npm ci
npm run dev                                      # Vite dev server (needs a running backend, see example.env)
npm run lint
node --experimental-vm-modules "src/pages/settings/[configs]/anti-steamroll.test.mjs"   # node:test file
```

Local stack (Postgres/Redis in Docker, Django + Vite native, no game server): `docs/local-dev.md`.
Tests import `rcon` modules that need Redis or `DEBUG=1` plus `SERVER_NUMBER`/`HLL_GAME`; the DB-backed tests
need PostgreSQL (`source dev.env`, as in `.github/workflows/pr.yml`).

Many backend tests also run without PostgreSQL: `BaseUserConfig.load_from_db` returns defaults when `HLL_DB_URL` is unset,
and Redis is replaced with `fakeredis` / mocks. There is no Django test setup (`rconweb/api/tests.py` is empty),
so tests import `rcon.*` directly and fake the Django boundary.

Full stack runs only in Docker (`Dockerfile`, `Dockerfile-frontend`). `compose.yaml` is copied from
`docker-templates/one-server.yaml` or `ten-servers.yaml` and is git-ignored, like `.env`.

## Architecture

**Containers** (see `entrypoint.sh`): one image, several roles chosen by the command argument.
- `maintenance`: runs once per install. `alembic upgrade head`, Redis cache migrations (`rcon/cache_migrations`),
  then `manage.py makemigrations --no-input && migrate`.
- `backend_N` (`web`): Django (`rconweb/`) served by daphne/gunicorn, one per game server.
- `supervisor_N`: background loops from `config/default-supervisord.conf` (`automod`, `log_event_loop`,
  `log_recorder`, `scoreboard`, `broadcasts`, `blacklists`, rq `workers`, `cron`, …). Started by supervisord or, if
  `CRCON_USE_PROCESS_SUPERVISOR` is set, by the in-house arbiter in `rcon/process_supervisor/` (see its README).
  Most loops are `rcon/cli.py` click commands.
- `webhook_service`: a single Discord webhook queue (`rcon/webhook_service.py`).
- `frontend_N`: nginx serving the built `rcongui` React app (Vite, MUI, React Router, TanStack Query).
- PostgreSQL + Redis. Multi-server installs share the DB and use per-server Redis databases.

**Two persistence stacks**: game data (players, logs, maps, user configs…) uses SQLAlchemy models in
`rcon/models.py` with **alembic** migrations in `alembic/versions`. Django (`rconweb/api/models.py`) only owns
auth/users/permissions/audit, with **Django migrations** in `rconweb/api/migrations`.

**API surface is generated from `RconAPI`**: `rcon/api_commands.py::RconAPI` (extends `rcon/rcon.py::Rcon`, the
game-server RCON client built on the `hllrcon` library) holds the business commands. `rconweb/api/views.py` maps
each method in `ENDPOINT_PERMISSIONS` (Django permission codenames) and `RCON_ENDPOINT_HTTP_METHODS`, and both dicts
must list the same methods or the process exits at startup. `expose_api_endpoint` wraps each method as
`/api/<method_name>`, with auth, permission checks and audit logging. Game-specific endpoints come from
`rconweb/api/game_endpoints.py`. Extra non-RconAPI routes are registered in `rconweb/api/urls.py`.

**User configs** (`rcon/user_config/*.py`): pydantic models extending `BaseUserConfig` (`rcon/user_config/utils.py`),
stored as JSON in the `UserConfig` table and **scoped by game + server number**. `load_from_db(game=..., server_number=...)`
reads them, and `RconAPI._validate_user_config` validates and saves them. Exposing a new config takes:
`get_/validate_/set_<name>_config` on `RconAPI`, entries in `ENDPOINT_PERMISSIONS` and `RCON_ENDPOINT_HTTP_METHODS`,
a `describe_<name>_config` view (JSON schema for the UI form) in `rconweb/api/user_settings.py` + `urls.py`, the
module import in `rcon/user_config/__init__.py`, and frontend wiring in `rcongui/src/pages/settings/_data/mappings.js`
(+ `nav-data.js`, `fetchUtils.js`).

**Game profiles**: CRCON supports HLL WW2 and HLL Vietnam (`HLL_GAME=hll|hllv`). Per-game maps, roles and weapons
live in `rcon/game/hll` and `rcon/game/hllv`. Use `rcon.game.get_game_profile()` / `game_switch()` rather than
hard-coding one game.

**Automods** (`rcon/automods/`): `automod.py` loads each automod's config for the current scope every cycle
(`enabled_moderators`). It builds `PunitionsToApply` from the team view (squad-level `punitions_to_apply`, or
server-level `server_punitions_to_apply`), then applies message/punish/kick/ban with escalation state kept in Redis.
Log-event reactions use decorators from `rcon/logs/loop.py` (`on_kill`, `on_connected`, `on_chat`, …).

**Caching**: `rcon/cache_utils.py` (`ttl_cache`, `invalidates`, `RedisCached`) caches many RCON reads (team view and
game state for ~2 s). Account for it when reasoning about freshness.
