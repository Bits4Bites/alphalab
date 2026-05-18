# AlphaLab

AI-powered market research lab built with FastAPI, Jinja2, and Bootstrap 5.

## Features

- **Dashboard** — Quick AI-powered market insights from natural language prompts
- **Analyze Ticker** — In-depth stock analysis with Quick Mode for one-page summaries
- **Build Portfolio** — AI-generated portfolio allocation with ticker recommendations
- **Review Portfolio** — Portfolio health assessment and rebalancing suggestions
- **Multi-vendor AI** — Supports Google Gemini, OpenAI, Azure OpenAI, and OpenRouter
- **OAuth Authentication** — GitHub and LinkedIn sign-in with email allowlist access control

## Tech Stack

- **Backend:** Python 3.12+, FastAPI, Pydantic Settings
- **Frontend:** Jinja2 templates, Bootstrap 5, Bootstrap Icons
- **AI:** Google GenAI SDK, OpenAI SDK, Azure Identity
- **Streaming:** Server-Sent Events (SSE) for real-time progress
- **Containerisation:** Docker (multistage build)

## Getting Started

### Prerequisites

- Python 3.12+
- A virtual environment (recommended)

### Installation

```bash
git clone https://github.com/Bits4Bites/alphalab.git
cd alphalab
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt  # for development
```

### Configuration

Copy and configure the environment files:

| File | Purpose |
|------|---------|
| `app_settings.env` | App name, debug mode, base URL, primary markets |
| `sec_settings.env` | Secret key, JWT settings, allowed emails |
| `external_identity_providers.env` | OAuth client IDs/secrets (GitHub, LinkedIn) |
| `ai_vendors.env` | AI vendor endpoints, API keys, models |
| `ai_tasks.env` | AI task-to-vendor/tier/model mapping |

**Environment variable formats:**

```env
# app_settings.env
AL_APP_NAME=AlphaLab
AL_DEBUG=true
AL_BASE_URL=http://localhost:8000
AL_PRIMARY_MARKETS=US,ASX,LSE

# sec_settings.env
AL_SECRET_KEY=your-secret-key
AL_ALLOWED_EMAILS=user1@example.com,user2@example.com

# ai_vendors.env (nested with __)
AL_LLM__OPENAI__PREMIUM__ENDPOINT=https://api.openai.com/v1
AL_LLM__OPENAI__PREMIUM__API_KEY=sk-...
AL_LLM__OPENAI__PREMIUM__MODELS=gpt-4o,gpt-4o-mini

# ai_tasks.env (nested with __)
AL_TASK__DASHBOARD_BUILD_PROMPT__VENDOR=openai
AL_TASK__DASHBOARD_BUILD_PROMPT__TIER=premium
AL_TASK__DASHBOARD_BUILD_PROMPT__MODEL=gpt-4o-mini
```

### Running

```bash
python server.py
```

The app starts at `http://localhost:8000` by default.

**Server environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `LISTEN_HOST` | `127.0.0.1` | Bind address |
| `LISTEN_PORT` | `8000` | Port |
| `ENABLE_RELOAD` | `true` (Windows) | Auto-reload on code changes |
| `NUM_WORKERS` | `1` (Windows) / `2` (Linux) | Uvicorn workers |

### Docker

```bash
docker build --rm -t alphalab:dev .
docker run -p 8000:8000 --env-file app_settings.env alphalab:dev
```

## Development

### Lint & Format

```bash
ruff check .           # lint
ruff check . --fix     # lint + autofix
ruff format .          # format
ruff format --diff .   # format check (CI)
```

### Testing

```bash
python -m pytest tests/ -q
```

### Project Structure

```
alphalab/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Settings classes (pydantic-settings)
│   ├── dependencies.py      # Auth dependency (get_current_user)
│   ├── routers/             # Route handlers
│   │   ├── dashboard.py
│   │   ├── analyze_ticker.py
│   │   ├── build_portfolio.py
│   │   ├── review_portfolio.py
│   │   ├── ai_vendors.py
│   │   ├── ai_tasks.py
│   │   ├── auth.py
│   │   └── health.py
│   ├── services/            # Business logic
│   ├── templates/           # Jinja2 HTML templates
│   ├── static/              # CSS, JS, images
│   └── utils/               # Helpers (ai.py, ticker.py)
├── tests/                   # Pytest test suite
├── server.py                # Uvicorn launcher
├── Dockerfile               # Multistage Docker build
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Dev dependencies
└── pyproject.toml           # Project metadata & tool config
```

## CI/CD

- **CI workflow** (`.github/workflows/ci.yaml`): lint, format check, tests, Docker smoke test
- **CodeQL** (`.github/workflows/codeql.yaml`): weekly security and code quality scanning
- **Dependabot** (`.github/dependabot.yaml`): automated dependency updates

## License

See [LICENSE.md](LICENSE.md) for details.
