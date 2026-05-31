from fastapi.templating import Jinja2Templates

from app import config

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_name"] = config.app_settings.app_name
templates.env.globals["app_version"] = config.app_settings.app_version
