"""Unified HTTP entrypoint; resources are created only inside the lifespan."""

from .http import create_app


app = create_app()
