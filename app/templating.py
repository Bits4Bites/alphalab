from fastapi.templating import Jinja2Templates

from app import config
from app.utils import local_storage

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_name"] = config.app_settings.app_name
templates.env.globals["app_version"] = config.app_settings.app_version
templates.env.globals["local_storage_user_key"] = local_storage.derive_user_key
