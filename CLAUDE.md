# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`be-supervisor-model` is a FastAPI-based operations management service for centrally managing Supervisor configurations across local and remote hosts. It provides auth, configuration file management, and Supervisor process lifecycle control.

## Commands

```bash
# Setup
python3.12 -m pip install -r requirements.txt

# Run (choose dev or prod)
./scripts/run.sh dev
./scripts/run.sh prod

# Run directly
python3.12 -m uvicorn app.main:app --host 0.0.0.0 --port 18880

# Run all tests
PYTHONPATH=$PWD python3.12 -m pytest -q

# Run a single test file
PYTHONPATH=$PWD python3.12 -m pytest -q tests/test_api.py

# Run a single test
PYTHONPATH=$PWD python3.12 -m pytest -q tests/test_api.py::test_api_create_and_read_flow

# Compile-check (py_compile all modules)
python3.12 -m compileall app

# Hash a password for manual user creation
python3.12 scripts/hash_password.py
```

## Architecture

### Layered structure

```
app/main.py            — Application factory (CORS middleware, error handlers, register routers)
app/api/               — FastAPI route handlers (auth.py, supervisor.py), thin layer: receives requests, delegates to services
app/schemas/           — Pydantic request models with field validation (auth.py, supervisor.py)
app/services/          — Business logic layer, all orchestration lives here
app/executor/          — Remote execution abstraction: LocalExecutor (direct subprocess) and AnsibleExecutor (ansible ad-hoc)
app/core/              — Infrastructure: config loading, database connection & migrations, JWT, passwords, logging, security, exceptions, response format
app/templates/         — Jinja2 template for Supervisor INI generation
app/database/migrations/  — 手工执行的数据库 SQL（001 新库基线，002 旧库升级）
tests/                 — pytest tests with comprehensive mocking infrastructure
docs/                  — Business documentation with numbered topic documents (01., 02., ...)
scripts/               — Helper scripts (run.sh, import_supervisor_services.py, hash_password.py)
```

### Key architectural patterns

1. **SupervisorManager** (`app/services/supervisor_manager.py`) is the orchestrator that API routes depend on — it composes all other services (HostService, ConfigFileService, TemplateService, PortCheckService, SupervisorService, SupervisorRegistryService, SupervisorImportService).

2. **Two execution models** determined per-host in `config.yaml`:
   - `local` — direct filesystem and subprocess operations on the control machine
   - `ansible` — remote operations via `ansible -m shell` ad-hoc commands
   Both implement the abstract `RemoteExecutor` interface (`app/executor/base.py`).

3. **Database**: MySQL 8 via Tortoise ORM models + direct repository access. `sys_supervisor_service` is the single source of truth for managed Supervisor services; schema 通过仓库内 SQL 手工维护。

4. **Two management modes** for Supervisor services:
   - `TEMPLATE_MANAGED` — created via the API's POST /services endpoint; uses Jinja2 template rendering
   - `IMPORTED_READONLY` — imported from existing config files on target hosts; stored as baseline snapshots

5. **Security constraints** (enforced in `app/core/security.py`):
   - All user-supplied identifiers (host, programName, jobName, etc.) validated against strict regex patterns
   - Path traversal prevented via `ensure_safe_path_under_dir()` (no `resolve()` to preserve Linux path semantics on macOS)
   - Only `*.ini` files allowed; `.ini.bak` and `.ini.bak.*` are recognized as backups
   - Remote (ansible) hosts are strictly read-only — mutations raise ForbiddenOperationError
   - JWT auth with server-side token table validation (not just JWT decode)

### Data flow for key operations

- **List services**: API → SupervisorManager → SupervisorRegistryService (DB query) + SupervisorService (supervisorctl status) + ConfigFileService (check file drift) → merged response
- **Create service**: API → SupervisorManager → TemplateService (render INI) → ConfigFileService (write file) → SupervisorService (reread + update) → SupervisorRegistryService (insert DB record) → optional rollback on DB failure
- **Import services**: API → SupervisorImportService → ConfigFileService (list/read configs on host) → TemplateService (parse INI) → SupervisorRegistryService (upsert by configPath)

## Testing

Tests use a comprehensive in-memory mock infrastructure in `tests/conftest.py`:

- **FakeMySQLServer** — in-memory dict-based database that matches specific SQL patterns used by the codebase; monkeypatched into `app.core.database`
- **FakeSupervisorCtl** — simulates `supervisorctl status/start/stop/reread/update` against a temp config directory; monkeypatched into `app.executor.local.subprocess.run`
- **test_environment fixture** — creates a temp `supervisord.d` directory and writes a test `config.yaml`, then monkeypatches `APP_CONFIG_PATH`
- **fake_mysql fixture** — monkeypatches `connect_server` and `connect_database`
- **fake_supervisor fixture** — monkeypatches `subprocess.run`
- **client fixture** — creates FastAPI `TestClient` via `app.main.create_app()`
- **seed_user fixture** — helper to pre-populate test users

Tests do not require a real MySQL or Supervisor installation.

## Documentation conventions

- All business documentation in `docs/` uses numbered topic files (`01.主题.md`, `02.主题.md`, ...)
- New topic documents must include: 背景与目标, 修改原因, 业务规则变更, 接口/配置变更, 影响说明, 验证方式
- If changes affect main logic flow or role responsibilities, update `docs/01.业务流程说明.md`
- If changes affect runtime, execution model, or file source semantics, update both `docs/01.业务流程说明.md` and `README.md`

## API endpoints

All under `/admin/api/` prefix:

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /auth/login | No | Login, returns JWT |
| GET | /auth/profile | Yes | Current user info |
| POST | /auth/logout | Yes | Revoke token |
| GET | /supervisor/hosts | Yes | List configured hosts |
| POST | /supervisor/imports | Yes | Import existing configs (DRY_RUN/APPLY) |
| GET | /supervisor/services?host= | Yes | List services for a host |
| GET | /supervisor/services/{programName}?host= | Yes | Service detail + drift detection |
| POST | /supervisor/services | Yes | Create new service (local hosts only) |

Unified response format: `{"code": 200, "msg": "success", "data": {}}`

CORS headers are applied by middleware on all `/admin/api/*` paths; OPTIONS requests return 200 without JWT auth.
