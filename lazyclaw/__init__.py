"""LazyClaw - E2E Encrypted AI Agent Platform"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("lazyclaw")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
