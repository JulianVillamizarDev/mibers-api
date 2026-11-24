# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Mibers API**: internal Django REST API for a social-media agency (clients, content pieces with review/approval, scheduling,
Instagram/Facebook publishing, WhatsApp delivery to clients, admin alerts, calendar, metrics). Requirements live in
`context.txt` (Spanish: `HU-xx` stories, `RFxx` functional, `RNFxx` non-functional; numbering has gaps in the source). Cite
requirement IDs in comments, tests and commits. `README.md` has the full overview, API table and deployment notes.

User-facing strings, domain terms and commit messages are in Spanish (`cliente`, `pieza`, `Aprobada`, `Administrador`/`Personal`).
Commits follow `tipo: descripcion corta` (feat/fix/chore/docs/test), one per feature, no push, no co-author line.

## Commands

Windows + a local venv at `.venv` (`.venv/Scripts/python ...`). Settings: `config.settings.{dev,prod,test}`; `manage.py` defaults to dev.

```bash
pytest                                   # whole suite (SQLite, ~20 s); settings come from pyproject (config.settings.test)
pytest modules/publishing/tests/test_tick.py::test_a_la_hora_crea_una_tarea_por_red_con_la_cuenta_y_el_copy_de_cada_una
pytest --cov=modules/publishing --cov=modules/core --cov-fail-under=60    # RNF14
ruff check . && ruff format .            # line length 110 (not enforced in tests)
lint-imports                             # import-linter layer contract - must stay green
python manage.py makemigrations --check --dry-run
python manage.py run_worker [--once] [--interval N]    # job queue + scheduler tick
python manage.py createadmin --username X              # no public registration
docker compose -f docker-compose.dev.yml up --build    # db + web + worker (dev); docker/Dockerfile.prod for Render
```

Environment quirks:
- Local Python is 3.15 and `PyYAML` has no wheel there, so `drf-spectacular` is **not installed locally**. Settings enable it only
  if importable (`settings.HAS_SPECTACULAR`); it is in `requirements/base.txt` for Docker (Python 3.12). Do not make it mandatory.
- Tests run on **SQLite**. Tests marked `@pytest.mark.postgres` (append-only audit trigger) are skipped unless `DATABASE_URL`
  is PostgreSQL; the real PostgreSQL path (trigger, `SKIP LOCKED`) is unverified locally. Docker daemon was not running during development.
- CI (`.github/workflows/ci.yml`, GitHub Actions, Python 3.12) runs ruff, `lint-imports`, `makemigrations --check`, the suite on SQLite
  (coverage >= 90 %) and on PostgreSQL 16 (so the `postgres` tests do run there), and builds `docker/Dockerfile.prod`. A push to `main`
  with everything green triggers the Dokploy deploy via its API (`desplegar` job; Dokploy "Auto Deploy" must stay off). Keep CI green
  before merging into `main`.
- `ffprobe` is not installed locally; video tests patch `modules.content.media.probe_video` (see `content/tests/helpers.py`).
- In a Bash heredoc, avoid mixing apostrophes with large file bodies (the shell rejected them); use the Write tool for source files.
- SimpleJWT reads `AccessToken.lifetime` at import time; to change it in a test, `monkeypatch.setattr(AccessToken, "lifetime", ...)`.

## Architecture

Modular monolith: each `modules/<name>` is a Django app (`models`, `services` = writes and business rules, `serializers`,
`views` = thin `APIView`s, `urls`, `tests/`). Dependencies only go **downward**, enforced by import-linter (`pyproject.toml`):

```
publishing | metrics | legal  >  content  >  clients  >  notifications  >  accounts  >  audit  >  core
```

- Cross-module FKs use string references (`"clients.Cliente"`) so they do not count as imports. A lower module must never import a
  higher one: use a **Django signal** instead (`content.signals.piece_state_changed`, `notifications.services.envio_actualizado`);
  `publishing/handlers.py` subscribes to both and registers the scheduler tick.
- Shared test fixtures (`admin_client`, `staff_client`, per-test `MEDIA_ROOT`) are in `modules/conftest.py`; it builds users via
  `get_user_model()` so lower modules' tests don't import higher ones.
- Errors: raise `core.exceptions.DomainError(detail, code, status)`; the handler returns `{"code", "detail"}`. A `DomainError` raised
  inside `transaction.atomic()` rolls the transaction back, so raise after the block if state must persist (see `accounts.services.login`).
- IDs are UUIDs; times are stored UTC and shown as `America/Bogota`. Use `core.timezone` helpers for Colombian day boundaries.

### Things that span modules (read before changing)

- **No Tech Provider**: Meta does not allow publishing to client accounts, so publishing is **manual-assisted**. At the scheduled
  time the tick (`publishing.services.run_tick`, every 60 s) creates one `TareaPublicacion` per network and reminds the owner by
  WhatsApp; staff publish and confirm with the link (`confirmar`), or report failure (`fallar`) and may `reintentar`. Accounts are
  registered manually (`CuentaSocial`, admin only); metrics are entered manually. `publishing/publishers.py` is the seam for a future
  Graph API publisher. WhatsApp Cloud API (agency number) is real; `WHATSAPP_BACKEND=fake` simulates it (`FakeGateway`).
- **Piece state machine** (`content/state_machine.py`, `content.services.change_state`): the only way to change `estado`; it validates
  against the DB row, not the instance. Editing files or per-network copy of an Aprobada/Programada/Fallida piece sends it back to
  `En revision` (RF12) and the signal cancels its pending publication tasks. Only the admin approves (also enforced in the service).
- **Never publish unapproved content**: a due piece that is not `Programada` is blocked with a deduplicated alert (RF15).
  Publication accounts must belong to the piece's client and be `VINCULADA` (RNF09).
- **Client delivery (RF18)** happens after the first network is confirmed, only to contacts with `autorizado` (Ley 1581), one WhatsApp
  message per file; the piece becomes `Compartida` when all sends are accepted. The read-only query of those sends (`GET envios`,
  `GET piezas/{id}/envios`) lives in `publishing/envios.py`: it filters `EnvioWhatsApp` by `proposito=CLIENTE`, resolves pieces in one
  query (no N+1) and always masks the phone number (RNF02).
- **Job queue** (`core/jobs.py`, `Job` model): Postgres-backed with `SKIP LOCKED`, retries with backoff, `@periodic` tasks. Modules
  register handlers/periodic tasks in `AppConfig.ready()`. API and worker share one container in prod (`docker/Procfile`, `honcho`).
- **Security**: passwords Argon2; contact data/phone numbers use `core.fields.EncryptedTextField` (AES-256-GCM, `FIELD_ENCRYPTION_KEY`,
  not queryable); JWT carries a `sid` checked against `UserSession` for 30-min idle expiry and logout; 5 failed logins lock 15 min;
  `must_change_password` limits a user to views marked `allow_password_change_pending`.
- **Audit** (`audit.services.record`): append-only (model, queryset and a PostgreSQL trigger); only `purge_expired` (12 months) may
  delete. Files in `/media` are never served by URL; use `core.files.serve_file` behind authenticated endpoints.
- Media rules per (network, piece type) are in `content/media_rules.py` and are conservative starting values to verify against Meta's docs.
