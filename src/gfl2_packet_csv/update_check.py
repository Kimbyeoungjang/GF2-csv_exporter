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
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)(?:[-.]?rc(\d+))?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    version: str
    tag_name: str
    page_url: str
    asset_url: str = ""
    asset_size: int = 0
    asset_digest: str = ""


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
    if owner in {".", ".."} or repo in {".", ".."}:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return ""
    return f"{owner}/{repo}"


def configured_repository() -> str:
    return normalize_repository(os.environ.get(_REPOSITORY_ENV) or DEFAULT_GITHUB_REPOSITORY)


def _version_key(value: str) -> tuple[int, int, int, int, int] | None:
    """Compare stable and release-candidate builds without ignoring RC suffixes.

    A final 1.6.0 is newer than a 1.6.0 release candidate, which is newer than
    1.6.0-RC4. This is limited to our release scheme; unknown suffixes
    are rejected rather than silently considered stable.
    """
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        return None
    major, minor, patch, candidate = match.groups()
    return (int(major), int(minor), int(patch),
            1 if candidate is None else 0,
            int(candidate) if candidate is not None else 0)


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
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return None
            payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        return None

    if not isinstance(payload, dict):
        return None
    tag_name = str(payload.get("tag_name") or "").strip()
    page_url = str(payload.get("html_url") or "").strip()
    if not tag_name or not page_url or _version_key(tag_name) is None:
        return None
    version = tag_name.lstrip("vV")
    expected_asset = f"GFL2-Packet-CSV-Exporter-v{version}-win64.zip"
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    asset = next((a for a in assets if isinstance(a, dict) and
                  a.get("name") == expected_asset), None)
    asset_url = str(asset.get("browser_download_url") or "") if asset else ""
    asset_size = asset.get("size", 0) if asset else 0
    if type(asset_size) is not int or asset_size < 0:
        asset_url = ""
        asset_size = 0
    asset_digest = str(asset.get("digest") or "") if asset else ""
    return ReleaseInfo(version=version, tag_name=tag_name, page_url=page_url,
                       asset_url=asset_url, asset_size=asset_size, asset_digest=asset_digest)
