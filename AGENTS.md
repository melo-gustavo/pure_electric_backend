# AGENTS.md

Guidelines for AI agents working in this repository. **Always follow these conventions** unless the user explicitly says otherwise.

## Stack

- Python 3.14+, managed with `uv` (`.venv`, `uv.lock`)
- FastAPI (async) + Pydantic v2
- SQLAlchemy 2.0 **async** (typed `Mapped[...]` style) on PostgreSQL (`postgresql+asyncpg`)
- Testing: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`)
- No extra comments in code unless asked.
- Every function must have a docstring describing its intent (English). One line is preferred, but it may span more when needed; it is never optional.

## Project layout

```
models/        SQLAlchemy ORM models (inherit Base from models.base_model)
schemas/       Pydantic v2 request/response schemas
enums/         Shared enums (e.g. DocumentType in enums/user.py)
repositories/  Data access layer (one class per model)
routes/        FastAPI routers (thin: Depends + call repository)
workers/       Background consumers (RabbitMQ, thin: call repository)
queues/        RabbitMQ connection/publish helpers
databases/     Async engine/session setup (postgres.py)
seeders/       Startup data seeding (user_seeder.py, product_seeder.py)
images/        Static catalog images served at /images (StaticFiles in main.py)
migrations/    Alembic environment and numbered versions
utils/         Shared helpers (security.py, logger.py)
tests/         pytest suite
run_worker.py  Entry point for the worker process
```

Dependency direction: `routes → repositories → models`, `schemas` and `utils` imported where needed. `tests/` never depends on a live service (Postgres or RabbitMQ).

## Commands

- Install/sync deps: `uv sync`
- Run tests: `uv run pytest` (spell `-q` optional). Always run after code changes.
- Lint: `uv run ruff check . --exclude .venv --exclude migrations`
- Format: `uv run ruff format . --exclude .venv --exclude migrations` (then run `--check` to verify)
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

## Seeders (`seeders/`)

- One module per model (`user_seeder.py`, `product_seeder.py`) exposing `seed_<model>s(db)`.
- Seed data lives in a module-level `SEED_<MODEL>S` list; inserts must be idempotent, keyed by a natural field (`phone` for users, `name` for products).
- Existing rows are left untouched, except a null `image` on a product, which is filled in.
- Register each seeder in the `lifespan` in `main.py`.

## Static files (`images/`)

- Product images live in `images/` and are served by `StaticFiles` mounted at `/images` in `main.py`.
- `Product.image` stores the relative path (e.g. `/images/escape.webp`), never an absolute URL.
- The app must be started from the project root, since the mount uses a relative directory.

## Security (`utils/security.py`)

- Passwords hashed with `bcrypt` (random salt), stored in bcrypt's native format.
- Use `SecurityUtils.hash_password(password)` and `SecurityUtils.verify_password(password, stored)`.
- bcrypt ignores bytes beyond 72; keep password schema `max_length=72`.
- Do not implement a different hashing scheme without updating `verify_password`.

## Logging (`utils/logger.py`)

- All logging configuration lives in `utils/logger.py`:
  - `setup_logging()` — installs the JSON handler once; called by `get_logger` and at app startup (`main.py`). It reads `LOG_LEVEL` and `SQL_ECHO`.
  - `get_logger("<ENTITY>")` — obtain a configured logger named after the entity (`"USER"`, `"PRODUCT"`, `"ORDER"`); infrastructure modules use `"APP"` and `"DATABASE"`. Do not use `__name__`.
- Logs are structured: one JSON object per line with `timestamp`, `level`, `logger`, `message`, every `extra=` field and `exception` when present.
- Put context in `extra=` (`external_id`, `order_id`, `status`), never interpolated into the message. `extra` keys must not clash with `LogRecord` attributes (e.g. `name`, `message`).
- Never inline `logging.basicConfig` in other modules.

## Tests (`tests/`)

- `conftest.py` overrides `get_session` with an in-memory SQLite engine:
  `sqlite+aiosqlite://` + `StaticPool` + `connect_args={"check_same_thread": False}`, then `Base.metadata.create_all`.
- Requests go through `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.
- Clear `app.dependency_overrides` after each test.
- The autouse `mock_publish` fixture in `conftest.py` stubs `repositories.order.publish`; never hit a real RabbitMQ from tests.
- Test CRUD happy paths plus 404/409/422 cases. Assert passwords are hashed (never equal plaintext) and absent from responses.

## Workers (`workers/`)

- One module per resource (e.g. `workers/order.py`), thin: consume from queue → call repository.
- Use `aio_pika` with `connect_robust` for connection resilience.
- Declare queues as `durable=True`; messages published with `DeliveryMode.PERSISTENT`.
- Consumers use `async with message.process():` for automatic ack/nack.
- Worker entry point is `start_worker()` async function; run as separate process.
- Import `RABBITMQ_URL` from `queues.rabbitmq` (single source of truth).
- Use `get_logger("<ENTITY>")` with the uppercase entity name (`"ORDER"`); pass context via `extra=`. Never `print()`.

## Queues (`queues/`)

- Single module `rabbitmq.py` with:
  - `RABBITMQ_URL` from env var (loaded at import).
  - `get_channel()` — returns a robust channel (new connection each call).
  - `publish(queue_name: str, message: str)` — declares queue, publishes persistent message.
- All config from env vars; keep secrets only in `.env`.
- Queues and exchanges are declared lazily on first publish/consume.