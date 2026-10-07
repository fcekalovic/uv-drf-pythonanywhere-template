# uv-drf-pythonanywhere-template

A production-minded starter template for **Django 5.2 LTS** and **Django REST
Framework**, managed with [`uv`](https://docs.astral.sh/uv/) for local
development and installed with plain `pip` in production. It ships with a JSON
health check endpoint and a manual, `main`-only deploy workflow for
**PythonAnywhere's** free tier.

Use it as the starting point for a new project: clone/use-as-template, rename
the package, and start building in `core/`.

## Features

- `uv` for dependency management, with `requirements.txt` auto-synced from the
  lockfile via a pre-commit hook.
- Environment-based configuration (`django-environ`) that fails fast on a
  missing `SECRET_KEY`.
- SQLite by default, swappable to PostgreSQL via `DATABASE_URL` with no code
  changes.
- A JSON `/health/` endpoint for platform probes (also reports the running git
  commit).
- An authenticated, disabled-by-default `/deploy/` endpoint plus a **manual,
  `main`-only** GitHub Actions deploy workflow for PythonAnywhere.
- Ruff lint/format wired through pre-commit.

## Prerequisites

- **Python 3.13** (the project pins `>=3.13,<3.14`). Production runs
  **Python 3.13.1** and installs with **pip 24.3.1**.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for local
  development. `uv` will download and manage the Python 3.13 interpreter for
  you, so a system-wide 3.13 is not required locally.

## Local setup

```bash
# 1. Install dependencies (creates .venv and provisions Python 3.13)
uv sync

# 2. Install git pre-commit hooks (lint, format, requirements sync)
uv run pre-commit install

# 3. Create your local environment file and fill in values
cp .env.example .env
#    At minimum, set SECRET_KEY. See .env.example for all variables.

# 4. Apply database migrations
uv run python manage.py migrate

# 5. Run the development server
uv run python manage.py runserver
```

### Rename the package for your project

The template package is named `uv-drf-pythonanywhere-template`. After creating
your repo, rename it:

1. Set `name` (and `description`) in `pyproject.toml`.
2. Regenerate the lockfile and the exported requirements:

   ```bash
   uv lock
   uv export --no-dev --frozen --format requirements-txt -o requirements.txt
   ```

The Django project package itself is the generic `config`, so it needs no
renaming.

### Configuration

Settings are read from environment variables (loaded from `.env` locally) via
`django-environ`:

| Variable        | Required | Default                | Notes                                            |
| --------------- | -------- | ---------------------- | ------------------------------------------------ |
| `SECRET_KEY`    | Yes      | —                      | Startup fails fast if unset.                     |
| `DEBUG`         | No       | `False`                | Never enable in production.                      |
| `ALLOWED_HOSTS` | No       | empty                  | Comma-separated. Required when `DEBUG=False`.    |
| `DATABASE_URL`  | No       | local SQLite file      | See "Database" below.                            |
| `DEPLOY_TOKEN`  | No       | empty                  | Enables `POST /deploy/`. See "Deploys" below.    |

### Database

By default the app uses a local **SQLite** database (`db.sqlite3`), so no
external database service is required to get started.

To use another database, set `DATABASE_URL` — no code changes are needed. For
PostgreSQL, point it at your instance and install the driver:

```bash
# Add the PostgreSQL driver (only when moving off SQLite)
uv add "psycopg[binary]"

# Then set, e.g.:
# DATABASE_URL=postgres://user:password@host:5432/dbname
```

### Health check

An unauthenticated health endpoint is available for platform probes:

```bash
curl http://127.0.0.1:8000/health/
# {"status":"ok","database":"ok","commit":"<hash>"}   -> HTTP 200 when healthy
# {"status":"unhealthy","database":"unreachable",...}  -> HTTP 503 when the DB is down
```

## Production

Production installs with plain `pip` from the pinned, hash-locked
`requirements.txt` (exported from `uv.lock`, dev tools excluded):

```bash
pip install -r requirements.txt
```

`requirements.txt` is generated from `uv.lock` and kept in sync automatically
by a pre-commit hook — do not edit it by hand. After changing dependencies with
`uv add` / `uv remove`, commit the updated `uv.lock`; the hook regenerates
`requirements.txt`.

### Deploys (PythonAnywhere free tier)

The free tier has no SSH and no scheduled tasks, so deploys are driven by an
authenticated endpoint the app exposes and a GitHub Actions workflow that
triggers it.

**The deploy workflow is manual and `main`-only.** It does **not** run on push.
You trigger it from the GitHub UI (**Actions → Deploy → "Run workflow"**), and
it refuses to run on any branch other than `main`.

**How it works:**

1. You click **Run workflow** on the `main` branch.
   `.github/workflows/deploy.yml` sends `POST /deploy/` with an
   `X-Deploy-Token` header.
2. The `/deploy/` endpoint runs a fixed sequence on the server —
   `git pull --ff-only` then `python manage.py migrate --noinput` — and returns
   a per-step JSON report. It accepts no commands or arguments from the request.
3. The workflow then calls the PythonAnywhere API to reload the web app so the
   new code is served. The reload only runs if the deploy step succeeded.

**The endpoint is disabled by default.** When `DEPLOY_TOKEN` is unset or empty,
`POST /deploy/` returns HTTP 404 and runs nothing. To enable it in production,
set `DEPLOY_TOKEN` in your PythonAnywhere **WSGI file's environment** and reload
once manually.

**Required GitHub Actions secrets** (Settings → Secrets and variables →
Actions):

| Secret         | Purpose                                                        |
| -------------- | -------------------------------------------------------------- |
| `DEPLOY_URL`   | Full deploy endpoint URL, e.g. `https://<domain>/deploy/`.     |
| `DEPLOY_TOKEN` | Must match the app's `DEPLOY_TOKEN` env var.                   |
| `PA_USERNAME`  | PythonAnywhere username.                                       |
| `PA_DOMAIN`    | Web app domain, e.g. `<username>.pythonanywhere.com`.          |
| `PA_API_TOKEN` | PythonAnywhere API token (Account → API Token tab).            |

The PythonAnywhere API credentials live only as GitHub Actions secrets — the
application itself never holds them. Never commit any token value.

See the [PythonAnywhere API docs](https://help.pythonanywhere.com/pages/API) for
details on the reload endpoint.

## Project layout

```
.
├── config/            # Django project package (settings, urls, wsgi, asgi)
├── core/              # Application code, including the /health/ and /deploy/ endpoints
├── manage.py
├── pyproject.toml     # Dependencies, Python pin, Ruff config
├── uv.lock            # Locked dependency versions (source of truth)
├── requirements.txt   # Exported from uv.lock for production pip installs
└── .env.example       # Template for local environment variables
```

## Tooling

- **Lint & format:** [Ruff](https://docs.astral.sh/ruff/) —
  `uv run ruff check .` and `uv run ruff format .`
- **Pre-commit hooks:** Ruff lint/format and `requirements.txt` sync —
  `uv run pre-commit run --all-files`
