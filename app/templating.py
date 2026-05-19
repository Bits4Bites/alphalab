from fastapi.templating import Jinja2Templates

from app.config import app_settings

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_name"] = app_settings.app_name
