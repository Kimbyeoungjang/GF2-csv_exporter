from __future__ import annotations

from gfl2_packet_csv.update_check import (
    DEFAULT_GITHUB_REPOSITORY,
    configured_repository,
    is_newer_version,
    normalize_repository,
)


def test_normalize_github_repository() -> None:
    assert normalize_repository("owner/repo") == "owner/repo"
    assert normalize_repository("https://github.com/owner/repo") == "owner/repo"
    assert normalize_repository("https://github.com/owner/repo.git/") == "owner/repo"
    assert normalize_repository("https://example.com/owner/repo") == ""
    assert normalize_repository("") == ""


def test_update_version_comparison_is_numeric() -> None:
    assert is_newer_version("v1.5.1", "1.5.0")
    assert is_newer_version("1.10.0", "1.9.9")
    assert not is_newer_version("1.5.0", "1.5.0")
    assert not is_newer_version("1.4.9", "1.5.0")
    assert not is_newer_version("nightly", "1.5.0")


def test_release_repository_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("GFL2_PACKET_CSV_GITHUB_REPO", raising=False)
    assert DEFAULT_GITHUB_REPOSITORY == "Kimbyeoungjang/GF2-csv_exporter"
    assert configured_repository() == "Kimbyeoungjang/GF2-csv_exporter"
