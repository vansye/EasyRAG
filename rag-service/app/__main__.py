"""Run the unified backend with one process and one indexing executor."""

import logging

import uvicorn

from .application.runtime import RuntimeSettings


def main() -> None:
    logging.basicConfig(format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("app.application.questions").setLevel(logging.INFO)
    settings = RuntimeSettings()
    uvicorn.run("app.main:app", host=settings.backend_host, port=settings.backend_port, workers=1)


if __name__ == "__main__":
    main()
