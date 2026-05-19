import tomllib
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import app_settings


def _get_app_version() -> str:
    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")
    return "0.0.0"


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_name"] = app_settings.app_name
templates.env.globals["app_version"] = _get_app_version()
