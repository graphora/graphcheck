"""GraphCheck — semantic observability for property graphs."""

# Single source of truth for the version. `pyproject.toml` declares the version as dynamic and
# reads it from here at build time (see `[tool.hatch.version]`), so the built distribution and
# `--version` always agree without a second literal or a runtime metadata lookup (the latter is
# slow enough on Windows to breach the cold-start budget for the `--version` fast path).
__version__ = "0.3.0"

__all__ = ["__version__"]
