"""Centralized structured logging setup."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger


class StructuredLogger:
    """Configure loguru for plain terminal output and optional JSON file logging."""

    def __init__(
        self,
        level: str = "INFO",
        log_file: Optional[Path] = None,
        json_file: bool = False,
    ):
        self.level = level.upper()
        self.log_file = log_file
        self.json_file = json_file

    def configure(self) -> None:
        # Remove default loguru sink so we can set our own.
        logger.remove()

        # Console sink with colored, human-readable output.
        logger.add(
            sys.stderr,
            level=self.level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            colorize=True,
        )

        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            if self.json_file:
                logger.add(
                    str(self.log_file),
                    level=self.level,
                    format="{message}",
                    serialize=True,
                    rotation="10 MB",
                    retention="7 days",
                )
            else:
                logger.add(
                    str(self.log_file),
                    level=self.level,
                    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
                    rotation="10 MB",
                    retention="7 days",
                )


def configure_logging(level: str = "INFO", log_file: Optional[Path] = None, json_file: bool = False) -> None:
    StructuredLogger(level=level, log_file=log_file, json_file=json_file).configure()
