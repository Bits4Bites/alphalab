# Copilot Instructions

## Project Overview

AlphaLab is an AI-powered market research lab built with **FastAPI** (Python 3.12+) and a server-rendered frontend using **Jinja2 templates** with **Bootstrap 5**. No JS build step — static assets are served directly.

## Commands

```bash
# Activate virtual environment (required before all commands)
.venv\Scripts\Activate.ps1        # Windows PowerShell
source .venv/bin/activate          # macOS/Linux

# Install dependencies
pip install -r requirements-dev.txt

# Run dev server
python server.py

# Tests
python -m pytest                   # full suite
python -m pytest tests/test_foo.py # single file
python -m pytest -k "test_name"    # single test by name

# Lint & format
ruff check .                       # lint
ruff check . --fix                 # lint + autofix
ruff format .                      # format
```

## Architecture

- **`app/main.py`** — FastAPI app entry point; mounts static files, templates, and routers
- **`app/routers/`** — API route modules, each with its own `APIRouter`; included in `main.py`
- **`app/services/`** — Business logic, kept separate from route handlers
- **`app/models/`** — Data/ORM models
- **`app/schemas/`** — Pydantic request/response schemas
- **`app/templates/`** — Jinja2 HTML templates; all pages extend `base.html`
- **`app/static/`** — CSS and JS served at `/static`
- **`tests/`** — Pytest tests; shared fixtures live in `conftest.py`

### Authentication

- OAuth2 login via external identity providers (GitHub, LinkedIn; extensible to others)
- Flow: `/auth/{provider}/login` → provider OAuth consent → `/auth/{provider}/callback` → JWT cookie → redirect to `/`
- JWT stored in `httponly` cookie named `access_token`; created by `app/services/auth.py`
- OAuth clients configured in `app/services/oauth.py` using `httpx-oauth`; add new providers to `OAUTH_PROVIDERS` dict
- Provider credentials set via env vars: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`

## Conventions

- Each router module defines a `router = APIRouter(tags=[...])` and is registered in `main.py` via `app.include_router()`
- Use Pydantic `BaseSettings` in `app/config.py` for configuration; reads from `.env`
- Type-annotate all function signatures
- Ruff config in `pyproject.toml`: line length 120, rules `E, F, I, W, UP`
