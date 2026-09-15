"""Run the unified backend with one process and one indexing executor."""

import uvicorn

from .application.runtime import RuntimeSettings


def main() -> None:
    settings = RuntimeSettings()
    uvicorn.run("app.main:app", host=settings.backend_host, port=settings.backend_port, workers=1)


if __name__ == "__main__":
    main()
