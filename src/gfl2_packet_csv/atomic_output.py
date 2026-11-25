"""Crash-resistant publication of a single local output file.

The temporary file lives beside the destination, is unique to this writer,
and is never used as a recovery input. This is not a multi-file transaction
or a guarantee of persistence across power loss on every filesystem.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile


@contextmanager
def atomic_output(destination: Path):
    destination = Path(destination)
    fd, name = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    temporary = Path(name)
    try:
        os.close(fd)
        yield temporary
        # All writer handles must have been closed before publication.
        with temporary.open('r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # Do not hide the original write/replace failure.
            pass


def write_verified_zip(destination: Path, root: Path, expected: dict[str, tuple[int, str]]) -> None:
    """Publish exactly the expected members, with verified bytes and CRCs."""
    with atomic_output(destination) as temporary:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in expected:
                # Missing input is an error, never an omitted ZIP member.
                archive.write(root / name, name)
        with zipfile.ZipFile(temporary, 'r') as archive:
            names = archive.namelist()
            if len(names) != len(expected) or set(names) != set(expected):
                raise ValueError('ZIP member inventory changed during export')
            for name, (size, digest) in expected.items():
                if archive.getinfo(name).file_size != size:
                    raise ValueError('ZIP member size mismatch')
                actual = hashlib.sha256()
                # Reading to EOF also checks each member's ZIP CRC.
                with archive.open(name) as handle:
                    for block in iter(lambda: handle.read(65536), b''):
                        actual.update(block)
                if actual.hexdigest() != digest:
                    raise ValueError('ZIP member digest mismatch')
