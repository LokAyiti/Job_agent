"""Proxy rotation for browser contexts and HTTP requests."""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Optional

from loguru import logger


@dataclass
class Proxy:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None
    bypass: Optional[str] = None


class ProxyRotator:
    """Rotates through a list of proxies defined in env or config."""

    ENV_VAR = "PROXY_LIST"

    def __init__(self, proxies: Optional[list[str]] = None):
        self._proxies: list[Proxy] = []
        self._index = 0
        if proxies:
            self._proxies = [self._parse(p) for p in proxies if p.strip()]
        else:
            env_proxies = os.environ.get(self.ENV_VAR, "")
            if env_proxies:
                self._proxies = [self._parse(p) for p in env_proxies.split(",") if p.strip()]

    def _parse(self, value: str) -> Proxy:
        # Format: server or user:pass@server or server:port
        value = value.strip()
        if "@" in value:
            auth, server = value.split("@", 1)
            username, password = auth.split(":", 1) if ":" in auth else (auth, "")
            return Proxy(server=server, username=username, password=password or None)
        return Proxy(server=value)

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    def next(self) -> Optional[Proxy]:
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        logger.debug(f"Selected proxy: {proxy.server}")
        return proxy

    def random(self) -> Optional[Proxy]:
        if not self._proxies:
            return None
        proxy = random.choice(self._proxies)
        logger.debug(f"Randomly selected proxy: {proxy.server}")
        return proxy

    def to_playwright_dict(self, proxy: Optional[Proxy] = None) -> Optional[dict]:
        if proxy is None:
            proxy = self.next()
        if proxy is None:
            return None
        result: dict = {"server": proxy.server}
        if proxy.username:
            result["username"] = proxy.username
        if proxy.password:
            result["password"] = proxy.password
        if proxy.bypass:
            result["bypass"] = proxy.bypass
        return result
