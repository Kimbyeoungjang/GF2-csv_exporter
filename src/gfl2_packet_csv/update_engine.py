"""Optional, user-approved GitHub Release updater for the frozen Windows Exporter.

Only the GUI/runtime files in the official release are replaced. Existing CSV,
packet backup, settings and logs are never touched. Downloads are staged and
checked before the running executable is asked to exit.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import unquote, urlsplit

from .atomic_output import write_verified_zip

REPOSITORY = "Kimbyeoungjang/GF2-csv_exporter"
RELEASE_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"
EXECUTABLE = "GFL2-Packet-CSV-Exporter.exe"
HELPER = "windows-packet-capture.exe"
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_EXTRACTED_BYTES = 900 * 1024 * 1024
MAX_MEMBERS = 25_000
RECOVERY_NAME = "gfl2_update_recovery.zip"


@dataclass(frozen=True)
class StagedUpdate:
    work_dir: Path
    stage_dir: Path
    recovery_path: Path | None = None


def _asset_url_valid(url: str) -> bool:
    """Initial asset URL must point at this project's GitHub release page."""
    p = urlsplit(url)
    return (p.scheme == "https" and p.netloc.lower() == "github.com" and
            url.startswith(RELEASE_PREFIX) and
            re.fullmatch(r"GFL2-Packet-CSV-Exporter-v[0-9][A-Za-z0-9.\-]*-win64\.zip",
                         unquote(p.path.rsplit("/", 1)[-1])) is not None and
            not p.query and not p.fragment)


def _flat_name(name: str) -> str | None:
    # Some releases contain all files below one wrapping top-level directory.
    if "\\" in name or name.startswith("/") or "\x00" in name:
        raise ValueError("업데이트 ZIP에 허용하지 않은 경로가 있습니다.")
    parts = name.removesuffix("/").split("/")
    # Windows path aliases, NTFS alternate data streams, and reserved
    # device names are rejected even when tests run on another OS.
    if not parts or any(x in (".", "..", "") or x.endswith((" ", "."))
                        or re.search(r'[<>:"|?*\x00-\x1f]', x)
                        or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', x.split('.')[0], re.I)
                        for x in parts):
        raise ValueError("업데이트 ZIP 경로가 올바르지 않습니다.")
    if len(parts) >= 2 and parts[0].startswith("GFL2-Packet-CSV-Exporter") and parts[0] != EXECUTABLE:
        parts = parts[1:]
    if not parts:
        return None
    flat = "/".join(parts)
    if flat in {EXECUTABLE, HELPER, "_internal"} or flat.startswith("_internal/"):
        return flat
    return None


def _safe_extract(archive_path: Path, output: Path, *, progress: Callable[[int], None]) -> None:
    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError("업데이트 ZIP의 파일 수가 비정상적으로 많습니다.")
        if sum(i.file_size for i in infos) > MAX_EXTRACTED_BYTES:
            raise ValueError("업데이트 ZIP 압축 해제 크기가 제한을 초과합니다.")
        # Validate the entire selected tree before writing any files. Windows
        # aliases can occur in parent directories even when leaf names differ.
        nodes: dict[str, tuple[str, bool]] = {}
        explicit: set[str] = set()
        files: list[tuple[zipfile.ZipInfo, str]] = []
        for info in infos:
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise ValueError("업데이트 ZIP에 심볼릭 링크가 포함되어 있습니다.")
            flat = _flat_name(info.orig_filename)
            if not flat:
                continue
            directory = info.is_dir()
            if (flat == "_internal" and not directory) or (flat in {EXECUTABLE, HELPER} and directory):
                raise ValueError("업데이트 ZIP에 파일과 폴더 경로 충돌이 있습니다.")
            parts = flat.split("/")
            for i in range(1, len(parts) + 1):
                path = "/".join(parts[:i])
                key = path.casefold()
                node = (path, directory or i < len(parts))
                if key in nodes and nodes[key] != node:
                    raise ValueError("업데이트 ZIP에 중복 또는 충돌 경로가 있습니다.")
                nodes[key] = node
            if flat.casefold() in explicit:
                raise ValueError("업데이트 ZIP에 중복 경로가 있습니다.")
            explicit.add(flat.casefold())
            if not directory:
                files.append((info, flat))
        selected = {flat: info for info, flat in files}
        if any(name not in selected or selected[name].file_size == 0 for name in (EXECUTABLE, HELPER)):
            raise ValueError("업데이트 패키지에 필수 실행 파일이 없습니다.")
        if not any(name.startswith("_internal/") and info.file_size > 0 for name, info in selected.items()):
            raise ValueError("업데이트 패키지에 _internal 런타임 파일이 없습니다.")
        total = max(1, sum(info.file_size for info, _ in files))
        for info, flat in files:
            dest = output.joinpath(*PurePosixPath(flat).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, dest.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    extracted += len(chunk)
                    if extracted > MAX_EXTRACTED_BYTES:
                        raise ValueError("업데이트 ZIP 크기가 제한을 초과했습니다.")
                    progress(min(99, 90 + int(9 * extracted / total)))


def _same_volume_work_dir(app_dir: Path) -> Path:
    """No system-temp fallback: cross-volume directory moves are not atomic."""
    root = app_dir.resolve(strict=True)
    device = root.stat().st_dev
    for parent in (root.parent, root):
        try:
            if parent.stat().st_dev != device:
                continue
            work = Path(tempfile.mkdtemp(prefix=".gfl2_exporter_update_", dir=parent))
            if work.stat().st_dev == device:
                return work
            shutil.rmtree(work, ignore_errors=True)
        except OSError:
            continue
    raise ValueError("프로그램과 동일한 드라이브에 업데이트 임시 폴더를 만들 수 없습니다.")


def download_and_stage(release, app_dir: Path, *, progress: Callable[[int], None]) -> StagedUpdate:
    """Build a validated staging folder. On ANY error, leave the app unchanged."""
    url = str(getattr(release, "asset_url", ""))
    if not _asset_url_valid(url):
        raise ValueError("공식 Windows 릴리스 파일 주소를 확인할 수 없습니다.")
    digest = str(getattr(release, "asset_digest", "") or "")
    if digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("공식 릴리스의 체크섬 형식이 잘못되었습니다.")
    advertised = int(getattr(release, "asset_size", 0) or 0)
    if not 0 <= advertised <= MAX_DOWNLOAD_BYTES:
        raise ValueError("업데이트 다운로드 크기가 너무 큽니다.")
    working = _same_volume_work_dir(app_dir)
    try:
        archive_file = working / "release.zip"
        progress(0)
        req = urllib.request.Request(url, headers={"User-Agent": "GFL2-CSV-Exporter-updater/1.6"})
        sha = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(req, timeout=30) as response, archive_file.open("wb") as out:
            response_size = int(response.headers.get("Content-Length") or 0)
            if not 0 <= response_size <= MAX_DOWNLOAD_BYTES:
                raise ValueError("업데이트 다운로드 크기가 제한을 초과합니다.")
            total = advertised or response_size
            while block := response.read(1024 * 256):
                count += len(block)
                if count > MAX_DOWNLOAD_BYTES:
                    raise ValueError("다운로드 용량이 제한을 초과했습니다.")
                sha.update(block)
                out.write(block)
                if total:
                    progress(min(90, int(count * 90 / total)))
        if (advertised and count != advertised) or (response_size and count != response_size):
            raise ValueError("다운로드 파일 크기가 릴리스 정보와 다릅니다.")
        if digest and sha.hexdigest().lower() != digest.split(":", 1)[1].lower():
            raise ValueError("다운로드 체크섬이 일치하지 않습니다.")
        stage = working / "payload"
        stage.mkdir()
        _safe_extract(archive_file, stage, progress=progress)
        archive_file.unlink(missing_ok=True)
        progress(100)
        return StagedUpdate(work_dir=working, stage_dir=stage)
    except Exception:
        shutil.rmtree(working, ignore_errors=True)
        raise


def _plain_stat(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("복구 백업에 링크 또는 재분석 지점이 있습니다.")
    return info


def prepare_recovery(staged: StagedUpdate, app_dir: Path) -> StagedUpdate:
    """Keep a verified, independent previous-runtime ZIP before installer launch.

    Only the fixed runtime allowlist is included. No account CSV/settings/logs.
    The archive remains after successful install and is replaced atomically on
    the next update attempt. It is a manual recovery asset, not auto-rollback.
    """
    root = app_dir.resolve(strict=True)
    expected: dict[str, tuple[int, str]] = {}
    aliases: set[str] = set()
    total = 0

    def add(path: Path) -> None:
        nonlocal total
        info = _plain_stat(path)
        name = path.relative_to(root).as_posix()
        if not stat.S_ISREG(info.st_mode) or _flat_name(name) != name or name.casefold() in aliases:
            raise ValueError("복구 백업 파일 경로가 올바르지 않습니다.")
        aliases.add(name.casefold())
        total += info.st_size
        if len(expected) >= MAX_MEMBERS or total > MAX_EXTRACTED_BYTES:
            raise ValueError("복구 백업 크기 제한을 초과했습니다.")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        expected[name] = (info.st_size, digest.hexdigest())

    for name in (EXECUTABLE, HELPER):
        add(root / name)
    internal = root / "_internal"
    if not stat.S_ISDIR(_plain_stat(internal).st_mode):
        raise ValueError("기존 런타임 폴더가 없습니다.")
    pending = [internal]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            info = _plain_stat(path)
            name = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                if _flat_name(name + "/placeholder") != name + "/placeholder" or name.casefold() in aliases:
                    raise ValueError("복구 백업 폴더 경로가 올바르지 않습니다.")
                aliases.add(name.casefold())
                pending.append(path)
            else:
                add(path)
    if len(expected) <= 2:
        raise ValueError("기존 런타임 파일이 없습니다.")
    recovery = root / RECOVERY_NAME
    write_verified_zip(recovery, root, expected)
    return StagedUpdate(staged.work_dir, staged.stage_dir, recovery)


_UPDATER_PS = r'''param(
    [Parameter(Mandatory=$true)][string]$AppDir,
    [Parameter(Mandatory=$true)][string]$WorkDir,
    [Parameter(Mandatory=$true)][int]$AppPid
)
$ErrorActionPreference = 'Stop'
$stage = Join-Path $WorkDir 'payload'
$backup = Join-Path $WorkDir 'backup'
$items = @('GFL2-Packet-CSV-Exporter.exe','windows-packet-capture.exe','_internal')
$form = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'GFL2 CSV Exporter 업데이트'
    $form.Width = 390
    $form.Height = 115
    $form.StartPosition = 'CenterScreen'
    $form.ControlBox = $false
    $form.TopMost = $true
    $label = New-Object System.Windows.Forms.Label
    $label.Text = '프로그램 종료 대기 중…'
    $label.SetBounds(16,12,340,21)
    $progress = New-Object System.Windows.Forms.ProgressBar
    $progress.SetBounds(16,40,340,15)
    $progress.Style = 'Blocks'
    $progress.Minimum = 0
    $progress.Maximum = 100
    $progress.Value = 0
    $form.Controls.Add($label)
    $form.Controls.Add($progress)
    $form.Show()
    [System.Windows.Forms.Application]::DoEvents()
} catch { $form = $null }
function Set-InstallStatus([string]$message, [int]$percent) {
    if ($null -ne $form) {
        $label.Text = $message
        $progress.Value = [Math]::Max(0, [Math]::Min(100, $percent))
        [System.Windows.Forms.Application]::DoEvents()
    }
}
New-Item -Path $backup -ItemType Directory -Force | Out-Null
$moved = @()
$installed = @()
try {
    try { Wait-Process -Id $AppPid -Timeout 120 -ErrorAction Stop } catch {
        if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
            throw 'Exporter가 아직 실행 중입니다.'
        }
    }
    Set-InstallStatus '기존 프로그램 백업 중…' 10
    foreach ($name in $items) {
        $dest = Join-Path $AppDir $name
        if (Test-Path -LiteralPath $dest) {
            Move-Item -LiteralPath $dest -Destination $backup -ErrorAction Stop
            $moved += $name
        }
    }
    Set-InstallStatus '새 버전 설치 중…' 45
    foreach ($name in $items) {
        Move-Item -LiteralPath (Join-Path $stage $name) -Destination $AppDir -ErrorAction Stop
        $installed += $name
    }
    Set-InstallStatus '새 버전 시작 중…' 90
    Start-Process -FilePath (Join-Path $AppDir 'GFL2-Packet-CSV-Exporter.exe') -WorkingDirectory $AppDir
    Set-InstallStatus '업데이트 완료' 100
    if ($null -ne $form) { $form.Close() }
} catch {
    $reason = $_.Exception.Message
    $rollbackErrors = @()
    foreach ($name in $installed) {
        $dest = Join-Path $AppDir $name
        try {
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction Stop }
            if (Test-Path -LiteralPath $dest) { throw "Removal failed: $name" }
        } catch { $rollbackErrors += "Remove $name : $($_.Exception.Message)" }
    }
    foreach ($name in $moved) {
        $old = Join-Path $backup $name
        $dest = Join-Path $AppDir $name
        try {
            if (-not (Test-Path -LiteralPath $old)) { throw "Backup missing: $name" }
            if (Test-Path -LiteralPath $dest) { throw "Destination occupied: $name" }
            Move-Item -LiteralPath $old -Destination $AppDir -ErrorAction Stop
            if (-not (Test-Path -LiteralPath $dest) -or (Test-Path -LiteralPath $old)) { throw "Restore failed: $name" }
        } catch { $rollbackErrors += "Restore $name : $($_.Exception.Message)" }
    }
    if ($null -ne $form) { $form.Close() }
    $errorLines = @($reason) + $rollbackErrors
    Set-Content -LiteralPath (Join-Path $WorkDir 'update_error.txt') -Value $errorLines -Encoding UTF8
    $oldExe = Join-Path $AppDir 'GFL2-Packet-CSV-Exporter.exe'
    if ($rollbackErrors.Count -eq 0 -and (Test-Path -LiteralPath $oldExe) -and -not (Get-Process -Id $AppPid -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $oldExe -WorkingDirectory $AppDir
    }
    exit 1
}
# Remove staging and previous binaries after a successful new process launch.
Remove-Item -LiteralPath $WorkDir -Force -Recurse -ErrorAction SilentlyContinue
'''


def launch_staged_update(staged: StagedUpdate, app_dir: Path, *, process_id: int | None = None) -> None:
    """Spawn a separate installer so the currently running EXE can exit."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("자동 교체는 Windows 배포용 EXE에서만 지원합니다.")
    if staged.recovery_path != app_dir.resolve() / RECOVERY_NAME or not staged.recovery_path.is_file():
        raise RuntimeError("설치 전 복구 ZIP이 준비되지 않았습니다.")
    stage_root = staged.work_dir.resolve()
    script = stage_root / "apply_update.ps1"
    script.write_text(_UPDATER_PS, encoding="utf-8-sig")
    subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-AppDir", str(app_dir.resolve()), "-WorkDir", str(stage_root),
         "-AppPid", str(process_id or os.getpid())],
        cwd=str(app_dir),
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                       getattr(subprocess, "DETACHED_PROCESS", 0)),
        close_fds=True,
    )
