from __future__ import annotations

from typing import Any


class WireDecodeError(ValueError):
    pass


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if offset >= len(data):
            raise WireDecodeError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise WireDecodeError("varint too long")


def parse_fields(data: bytes, *, max_fields: int = 20_000) -> list[tuple[int, int, Any]]:
    """Parse protobuf wire fields without a schema.

    Only the standard wire types used by the account payload decoders are
    supported. This helper intentionally performs no semantic interpretation.
    """
    if len(data) > 8 * 1024 * 1024:
        raise WireDecodeError("payload too large")

    offset = 0
    fields: list[tuple[int, int, Any]] = []
    while offset < len(data):
        if len(fields) >= max_fields:
            raise WireDecodeError("too many fields")
        key, offset = _varint(data, offset)
        number, wire = key >> 3, key & 7
        if number <= 0:
            raise WireDecodeError("invalid field number")

        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire == 1:
            end = offset + 8
            if end > len(data):
                raise WireDecodeError("truncated fixed64")
            value = data[offset:end]
            offset = end
        elif wire == 2:
            size, offset = _varint(data, offset)
            end = offset + size
            if end > len(data):
                raise WireDecodeError("truncated length-delimited field")
            value = data[offset:end]
            offset = end
        elif wire == 5:
            end = offset + 4
            if end > len(data):
                raise WireDecodeError("truncated fixed32")
            value = data[offset:end]
            offset = end
        else:
            raise WireDecodeError(f"unsupported wire type {wire}")
        fields.append((number, wire, value))
    return fields
