"""Legacy compatibility shim for the canonical web API entrypoint."""

from web.api.main import app, create_app

__all__ = ["app", "create_app"]
