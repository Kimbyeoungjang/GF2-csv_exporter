from __future__ import annotations

from typing import Any, Iterator


class WireDecodeError(ValueError):
    pass


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for i in range(10):
        if offset >= len(data):
            raise WireDecodeError("truncated varint")
        byte = data[offset]
        offset += 1
        if i == 9 and byte > 1:
            raise WireDecodeError("varint uint64 overflow")
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise WireDecodeError("varint too long")


def _iter_fields(data: bytes, *, max_fields: int, max_bytes: int, layout_only: bool = False) -> Iterator[tuple[int, int, Any]]:
    """Parse protobuf wire fields without a schema.

    Only the standard wire types used by the account payload decoders are
    supported. This helper intentionally performs no semantic interpretation.
    """
    if not isinstance(data, bytes) or len(data) > max_bytes:
        raise WireDecodeError("payload too large")

    offset = 0
    count = 0
    while offset < len(data):
        if count >= max_fields:
            raise WireDecodeError("too many fields")
        key, offset = _varint(data, offset)
        number, wire = key >> 3, key & 7
        if number <= 0 or number >= (1 << 29):
            raise WireDecodeError("invalid field number")

        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire == 1:
            end = offset + 8
            if end > len(data):
                raise WireDecodeError("truncated fixed64")
            value = end - offset if layout_only else data[offset:end]
            offset = end
        elif wire == 2:
            size, offset = _varint(data, offset)
            end = offset + size
            if end > len(data):
                raise WireDecodeError("truncated length-delimited field")
            value = end - offset if layout_only else data[offset:end]
            offset = end
        elif wire == 5:
            end = offset + 4
            if end > len(data):
                raise WireDecodeError("truncated fixed32")
            value = end - offset if layout_only else data[offset:end]
            offset = end
        else:
            raise WireDecodeError(f"unsupported wire type {wire}")
        count += 1
        yield number, wire, (None if layout_only and wire != 2 else value)


def parse_fields(data: bytes, *, max_fields: int = 20_000, max_bytes: int = 8 * 1024 * 1024) -> list[tuple[int, int, Any]]:
    """Decode wire values for existing semantic decoders."""
    return list(_iter_fields(data, max_fields=max_fields, max_bytes=max_bytes))


def iter_field_layout(data: bytes, *, max_fields: int = 20_000, max_bytes: int = 8 * 1024 * 1024) -> Iterator[tuple[int, int, int | None]]:
    """Yield only field numbers, wire types and length-delimited sizes.

    Do not allocate slices for field bodies or retain decoded scalar values.
    """
    yield from _iter_fields(data, max_fields=max_fields, max_bytes=max_bytes, layout_only=True)

