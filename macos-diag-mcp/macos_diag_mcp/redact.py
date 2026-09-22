"""Masks identity in anything handed back to the client.

The playbook's rule — summarize instead of quoting, warn before anything leaves
the machine — was advisory text until now. This enforces it: every result from a
tool that touches system data is walked and masked before it is returned.

Two things it deliberately does not do. It does not mask process names, system
paths, subsystems, error codes or symbols, because those are the diagnosis. And
it does not mask the user's home path out of existence — it rewrites it to `~`,
which `Path.expanduser()` resolves, so a report path stays usable as the
argument to the next tool call.

Normalization in `logs` is a grouping mechanism and redacts nothing; this is the
part that redacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ordered: e-mail before hostname (an address contains a domain), MAC before
# IPv6 (a MAC matches the IPv6 shape), home path before the bare username.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
# Three colons minimum, so a 07:45:01 timestamp is not read as an address.
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)
_SSID_QUOTED = re.compile(r'((?:SSID|ssid)\s*[:=]?\s*)"[^"]*"')
_SSID_BARE = re.compile(r"((?:SSID|ssid)\s*[:=]\s*)(\S+)")
_DOT_LOCAL = re.compile(r"\b[\w-]+\.local\b")
_OTHER_HOME = re.compile(r"/Users/[^/\s\"']+")


@dataclass(frozen=True)
class Redactor:
    """Masks identity in strings, and recursively in whatever a tool returns."""

    enabled: bool
    home: str
    username: str
    hostname: str
    min_username_length: int
    home_placeholder: str
    user_placeholder: str
    host_placeholder: str
    ssid_placeholder: str
    ip_placeholder: str
    mac_placeholder: str
    email_placeholder: str

    def text(self, value: str) -> str:
        if not self.enabled or not value:
            return value

        out = _EMAIL.sub(self.email_placeholder, value)
        out = _MAC.sub(self.mac_placeholder, out)
        out = _IPV6.sub(self.ip_placeholder, out)
        out = _IPV4.sub(self.ip_placeholder, out)
        out = _SSID_QUOTED.sub(rf'\1"{self.ssid_placeholder}"', out)
        out = _SSID_BARE.sub(rf"\1{self.ssid_placeholder}", out)

        # This user's home first, so it becomes a path the next call can reuse.
        if self.home:
            out = out.replace(self.home, self.home_placeholder)
        out = _OTHER_HOME.sub(f"/Users/{self.user_placeholder}", out)

        if self.hostname:
            base = self.hostname.removesuffix(".local")
            if base:
                out = re.sub(
                    rf"\b{re.escape(base)}(\.local)?\b", self.host_placeholder, out
                )
        out = _DOT_LOCAL.sub(f"{self.host_placeholder}.local", out)

        # A short username is likely a common word ("dev", "adm"); masking it as
        # a bare word would eat real text for no gain. The home path above has
        # already covered the case that matters.
        if self.username and len(self.username) >= self.min_username_length:
            out = re.sub(rf"\b{re.escape(self.username)}\b", self.user_placeholder, out)
        return out

    def mapping(self, value: dict) -> dict:
        """walk() for a tool's result, typed as the dict it hands back."""
        if not self.enabled:
            return value
        return {key: self.walk(item) for key, item in value.items()}

    def walk(self, value: object) -> Any:
        """Redact every string inside a result, however deeply nested."""
        if not self.enabled:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.walk(item) for item in value)
        return value


def build(settings, home: Path | None = None, hostname: str = "") -> Redactor:
    """A Redactor for this machine. `home`/`hostname` are injectable for tests."""
    resolved = home if home is not None else Path.home()
    if not hostname:
        import socket  # noqa: PLC0415 - only needed once, at construction

        hostname = socket.gethostname()
    return Redactor(
        enabled=settings.enabled,
        home=str(resolved),
        username=resolved.name,
        hostname=hostname,
        min_username_length=settings.min_username_length,
        home_placeholder=settings.home_placeholder,
        user_placeholder=settings.user_placeholder,
        host_placeholder=settings.host_placeholder,
        ssid_placeholder=settings.ssid_placeholder,
        ip_placeholder=settings.ip_placeholder,
        mac_placeholder=settings.mac_placeholder,
        email_placeholder=settings.email_placeholder,
    )
