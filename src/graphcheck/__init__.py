"""GraphCheck — semantic observability for property graphs."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version declared in pyproject.toml and stamped into the
    # installed distribution metadata. Avoids a second literal that can drift from the package.
    __version__ = version("graphcheck")
except PackageNotFoundError:  # running from an uninstalled source tree (no distribution metadata)
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
