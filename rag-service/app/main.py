"""Unified HTTP entrypoint; resources are created only inside the lifespan."""

from pathlib import Path

from .http import create_app


app = create_app(frontend_dist=Path(__file__).resolve().parents[2] / 'frontend' / 'dist')
