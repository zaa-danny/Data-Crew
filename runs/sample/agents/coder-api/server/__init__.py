"""Backend API package for the data app (stdlib only)."""

from .app import load_records, load_summary, make_server, main

__all__ = ["load_records", "load_summary", "make_server", "main"]
