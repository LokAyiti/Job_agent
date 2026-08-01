"""Proxy parsing utilities for the Scrapling service."""
import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ProxyConfig:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None


def parse_proxy_list(value: Optional[str]) -> List[ProxyConfig]:
    """Parse a comma-separated proxy string into structured proxy configs.

    Supported formats:
        - http://proxy.example.com:8080
        - user:pass@proxy.example.com:8080
        - http://user:pass@proxy.example.com:8080
    """
    if not value:
        return []

    result: List[ProxyConfig] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue

        if "@" in part:
            auth, server = part.split("@", 1)
            if ":" in auth:
                username, password = auth.split(":", 1)
            else:
                username, password = auth, ""
            result.append(ProxyConfig(server=server, username=username, password=password or None))
        else:
            result.append(ProxyConfig(server=part))

    return result


def as_strings(proxies: List[ProxyConfig]) -> List[str]:
    """Return proxies in the string format accepted by FetcherSession."""
    result = []
    for p in proxies:
        if p.username:
            result.append(f"{p.username}:{p.password or ''}@{p.server}")
        else:
            result.append(p.server)
    return result


def as_dicts(proxies: List[ProxyConfig]) -> List[dict]:
    """Return proxies in the dict format accepted by browser sessions."""
    result = []
    for p in proxies:
        if p.username:
            result.append({"server": p.server, "username": p.username, "password": p.password or ""})
        else:
            result.append({"server": p.server})
    return result


def proxies_from_env() -> List[ProxyConfig]:
    return parse_proxy_list(os.environ.get("PROXY_LIST", ""))
