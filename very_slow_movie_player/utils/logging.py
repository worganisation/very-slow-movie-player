"""Shared Loguru output for the VSMP service and display drivers."""

from __future__ import annotations

from sys import stderr

from loguru import logger

__all__ = ["logger"]

# systemd collects stderr. Disable variable inspection because playback settings may
# include credentials, and keep DEBUG messages visible as with the previous logger.
logger.remove()
_ = logger.add(stderr, level="DEBUG", backtrace=False, diagnose=False)
