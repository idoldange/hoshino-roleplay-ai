"""Minimal console shim so modules ported from arona (which call
`console.log(message, level)`) use this project's `logger`."""

from logger import logger

_LEVELS = {
    "DEBUG": logger.debug,
    "INFO": logger.info,
    "WARN": logger.warning,
    "WARNING": logger.warning,
    "ERROR": logger.error,
    "CRITICAL": logger.critical,
}


class _Console:
    def log(self, message, level="INFO"):
        _LEVELS.get(str(level).upper(), logger.info)(message)


console = _Console()
