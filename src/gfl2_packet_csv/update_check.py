from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

# Official public repository used for optional update checks.
# An environment variable can override it for development/testing.
DEFAULT_GITHUB_REPOSITORY = "Kimbyeoungjang/GF2-csv_exporter"
_REPOSITORY_ENV = "GFL2_PACKET_CSV_GITHUB_REPO"
_API_BASE = "https://api.github.com/repos"
_TIMEOUT_SECONDS = 4.0
_VERSION_RE = re.compile(r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[._-].*)?$")


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    tag_name: str
    page_url: str


def normalize_repository(value: str | None) -> str:
    """Return ``owner/repo`` for a GitHub repository input, or ``""``."""
    text = (value or "").strip()
    if not text:
        return ""
    text = text.rstrip("/").removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if text.casefold().startswith(prefix):
            text = text[len(prefix):]
            break
    parts = [part for part in text.split("/") if part]
    if len(parts) != 2:
        return ""
    owner, repo = parts
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return ""
    return f"{owner}/{repo}"


def configured_repository() -> str:
    return normalize_repository(os.environ.get(_REPOSITORY_ENV) or DEFAULT_GITHUB_REPOSITORY)


def _version_key(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(value.strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def is_newer_version(latest: str, current: str) -> bool:
    latest_key = _version_key(latest)
    current_key = _version_key(current)
    if latest_key is None or current_key is None:
        return False
    return latest_key > current_key


def fetch_latest_release(repository: str, *, timeout: float = _TIMEOUT_SECONDS) -> ReleaseInfo | None:
    """Read the latest stable GitHub Release without blocking app startup on errors."""
    repo = normalize_repository(repository)
    if not repo:
        return None
    request = urllib.request.Request(
        f"{_API_BASE}/{repo}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "GFL2-Packet-CSV-Exporter-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        return None

    if not isinstance(payload, dict):
        return None
    tag_name = str(payload.get("tag_name") or "").strip()
    page_url = str(payload.get("html_url") or "").strip()
    if not tag_name or not page_url or _version_key(tag_name) is None:
        return None
    return ReleaseInfo(version=tag_name.lstrip("vV"), tag_name=tag_name, page_url=page_url)
