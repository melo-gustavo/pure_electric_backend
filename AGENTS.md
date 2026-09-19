# AGENTS.md

Guidelines for AI agents working in this repository. **Always follow these conventions** unless the user explicitly says otherwise.

## Stack

- Python 3.14+, managed with `uv` (`.venv`, `uv.lock`)
- FastAPI (async) + Pydantic v2
- SQLAlchemy 2.0 **async** (typed `Mapped[...]` style) on PostgreSQL (`postgresql+asyncpg`)
- Testing: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`)
- No extra comments in code unless asked.
- Every function must have a one-line docstring describing its intent (English).

## Project layout

```
models/        SQLAlchemy ORM models (inherit Base from models.base_model)
schemas/       Pydantic v2 request/response schemas
enums/         Shared enums (e.g. DocumentType in enums/user.py)
repositories/  Data access layer (one class per model)
routes/        FastAPI routers (thin: Depends + call repository)
databases/     Async engine/session setup (postgres.py)
seeders/       Startup data seeding (user_seeder.py)
utils/         Shared helpers (security.py, logger.py)
tests/         pytest suite
```

Dependency direction: `routes → repositories → models`, `schemas` and `utils` imported where needed. `tests/` never depends on a live database.

## Commands

- Install/sync deps: `uv sync`
- Run tests: `uv run pytest` (spell `-q` optional). Always run after code changes.
- Lint: `uv run ruff check . --exclude .venv --exclude migrations`
- Format: `uv run ruff format . --exclude .venv` (then run `--check` to verify)
- Run a script: `uv run python -m ...`
- Migrations: see the [Migrations (`migrations/`)](#migrations-migrations) section below.

Ruff is the lint + format standard (config in `pyproject.toml`). `B008`
(`Depends()` in argument defaults) is intentionally ignored since it matches the
FastAPI DI idiom used across `routes/`. Always keep the project ruff-clean.

## Models (`models/`)

- Inherit `Base` from `models.base_model`.
- Use SQLAlchemy 2.0 typed style (`Mapped[...]` + `mapped_column`).
- Primary key pattern:

```python
id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
```

- Mark identifiers unique (`unique=True`) and required fields `nullable=False`.
- Type annotation must match column nullability (e.g. `Mapped[str | None]` with `nullable=True`).

## Enums (`enums/`)

- One module per domain (e.g. `enums/user.py`), shared with enums/users elsewhere.
- Use `enum.StrEnum` (Python 3.14) with uppercase values matching DB storage,
  e.g. `class DocumentType(StrEnum): CPF = "CPF"`.
- Map to a column with `SQLEnum(DocumentType, name="<snake_name>")`; the enum
  becomes a native PostgreSQL enum type. Import as `from sqlalchemy import Enum as SQLEnum`
  to avoid clashing with `enum.Enum`.

## Repositories (`repositories/`)

- One class per model (e.g. `UserRepository`), all methods `@staticmethod`, first arg `AsyncSession`.
- Lookup methods return the model or `None` (`scalar_one_or_none`).
- Add a `get_<model>_by_id_or_404` helper for routes that must 404.
- Raise `HTTPException` from the repository with explicit HTTP status codes and English `detail` messages:
  - not found → `404`, `"User not found."`
  - duplicate phone/email/cpf/cnpj → `409`, `"A user with this <field> already exists."`
- Use `data.model_dump(exclude_unset=True)` so only provided fields are applied on create/update.
- Hash passwords with `SecurityUtils.hash_password` on **both** create and update; never store plaintext.
- Commit and `await db.refresh(obj)` before returning created/updated objects.

## Schemas (`schemas/`)

- Pydantic v2 `BaseModel`, named `<Entity>Create`, `<Entity>Update`, `<Entity>Out`.
- Output schemas use `ConfigDict(from_attributes=True)`.
- **Never** include `password` (or any secret) in output schemas.
- Use `EmailStr` for email fields (requires `email-validator`, already installed).
- Use `Field(default=None, min_length=..., examples=[...])` for validation and OpenAPI docs.
- Keep optional unique identifiers nullable (`str | None`) so they can be cleared.

## Routes (`routes/`)

- One `APIRouter(prefix="/<resource>s", tags=["<resource>s"])` per resource.
- Register every router in `routes/include_router.py` by adding it to the `ROUTERS` tuple (new resources are NOT wired via `app.include_router`).
- `main.py` registers everything in one call: `include_app_routers(app)`.
- Endpoints are `async def`, receive the Pydantic payload, and inject the session with `db: AsyncSession = Depends(get_session)`.
- Always set `response_model`; set explicit `status_code` for 201 (create) and 204 (delete).

## Database (`databases/`)

- Async engine against Postgres: `postgresql+asyncpg://...`.
- All config from env vars with defaults: `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME`, `DATABASE_HOST`, `DATABASE_PORT`.
- `.env` is loaded via `load_dotenv()` at module import; keep secrets only in `.env` (gitignored).
- Use `async_sessionmaker(engine, expire_on_commit=False)`; expose a single `get_session()` async generator dependency.

## Migrations (`migrations/`)

- Async environment in `migrations/env.py`: `async_engine_from_config` + `asyncio.run`, URL comes from `databases.postgres.DATABASE_URL`.
- For autogenerate, set `target_metadata = Base.metadata` in `env.py` and import every model module so its tables are registered.
- Migration files are auto-prefixed with a sequential number (patched via `ScriptDirectory._rev_path` in `env.py`): `0001_<name>.py`, `0002_<name>.py`, ... Deleting a file makes the next one reuse its number.
- Native Postgres enums render as `sa.Enum('CPF', 'CNPJ', name='document_type')` inside migrations.
- Commands:

```bash
uv run alembic revision --autogenerate -m "<name>"   # generate (number + name)
uv run alembic upgrade head                          # apply pending
uv run alembic downgrade -1                          # rollback one step
```

- To re-roll a migration: `uv run alembic downgrade base`, delete the file(s), then regenerate.

## Security (`utils/security.py`)

- Passwords hashed with `bcrypt` (random salt), stored in bcrypt's native format.
- Use `SecurityUtils.hash_password(password)` and `SecurityUtils.verify_password(password, stored)`.
- bcrypt ignores bytes beyond 72; keep password schema `max_length=72`.
- Do not implement a different hashing scheme without updating `verify_password`.

## Logging (`utils/logger.py`)

- All logging configuration lives in `utils/logger.py`:
  - `setup_logging()` — call once at app startup (in `main.py`). It reads `LOG_LEVEL` and `SQL_ECHO`.
  - `get_logger(__name__)` — obtain a configured module logger.
- Never inline `logging.basicConfig` in other modules; use `get_logger(__name__)`.

## Tests (`tests/`)

- `conftest.py` overrides `get_session` with an in-memory SQLite engine:
  `sqlite+aiosqlite://` + `StaticPool` + `connect_args={"check_same_thread": False}`, then `Base.metadata.create_all`.
- Requests go through `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.
- Clear `app.dependency_overrides` after each test.
- Test CRUD happy paths plus 404/409/422 cases. Assert passwords are hashed (never equal plaintext) and absent from responses.