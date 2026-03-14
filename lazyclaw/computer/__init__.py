"""Computer control — dual-mode execution (local + remote connector)."""

from lazyclaw.computer.security import SecurityManager
from lazyclaw.computer.native import NativeExecutor
from lazyclaw.computer.connector_server import ConnectorServer
from lazyclaw.computer.manager import ComputerManager

__all__ = [
    "SecurityManager",
    "NativeExecutor",
    "ConnectorServer",
    "ComputerManager",
]
