"""Maidbook — a tidy cache cleaner + health check for macOS.

Public re-exports for programmatic use; most users will just run
``maidbook`` from the command line. See :mod:`maidbook.__main__` for
the argparse entry point.
"""

__version__ = "0.1.0"

from .common import APP_NAME, APP_TAGLINE

__all__ = ["__version__", "APP_NAME", "APP_TAGLINE"]
