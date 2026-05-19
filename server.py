import os
import platform

import uvicorn

if __name__ == "__main__":
    host = os.getenv("LISTEN_HOST", "127.0.0.1")
    port = int(os.getenv("LISTEN_PORT", "8000"))
    default_reload = platform.system() == "Windows"
    reload = os.getenv("ENABLE_RELOAD", str(default_reload)).lower() in ("true", "1", "yes")
    default_workers = 1 if platform.system() == "Windows" else 2
    workers = int(os.getenv("NUM_WORKERS", str(default_workers)))

    uvicorn.run("app.main:app", host=host, port=port, reload=reload, workers=workers)
