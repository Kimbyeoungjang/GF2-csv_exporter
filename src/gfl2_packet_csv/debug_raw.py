from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import threading
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Hashable

from .wire_probe import WireDecodeError, parse_fields


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wire_shape(data: bytes) -> dict[str, Any]:
    """Return protobuf structure without recording field values.

    Only field-number/wire-type occurrence counts and length-delimited size
    buckets are retained. No varint/fixed/string/bytes values are persisted.
    """
    try:
        fields = parse_fields(data, max_fields=30_000)
    except WireDecodeError as exc:
        return {"valid_protobuf": False, "error": str(exc)}

    field_counts: Counter[str] = Counter()
    length_buckets: Counter[str] = Counter()
    for number, wire, value in fields:
        field_counts[f"{int(number)}:{int(wire)}"] += 1
        if wire == 2:
            size = len(value)
            if size == 0:
                bucket = "0"
            elif size <= 16:
                bucket = "1-16"
            elif size <= 64:
                bucket = "17-64"
            elif size <= 256:
                bucket = "65-256"
            elif size <= 1024:
                bucket = "257-1024"
            else:
                bucket = "1025+"
            length_buckets[bucket] += 1
    return {
        "valid_protobuf": True,
        "field_counts": dict(sorted(field_counts.items())),
        "length_delimited_size_buckets": dict(sorted(length_buckets.items())),
    }


class SanitizedDebugRecorder:
    """Collect privacy-preserving packet diagnostics.

    The archive deliberately contains *no packet bodies* and *no assembled
    game payload bodies*. Exact local/remote addresses and ports are also not
    stored. Each packet/payload gets an ephemeral keyed content tag that is
    useful for detecting retransmissions/duplicates inside one archive but
    cannot be recomputed from the archive because the key is never persisted.

    The historical filename ``gfl2_packet_debug_raw.zip`` is kept for tooling
    compatibility even though its contents are metadata-only from schema v2.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.root = self.output_dir / "debug_raw"
        self.packet_index = self.root / "matched_tcp_index.jsonl"
        self.payload_index = self.root / "game_payload_index.jsonl"
        self._lock = threading.RLock()
        self._tag_key = secrets.token_bytes(32)
        self._flow_ids: dict[Hashable, int] = {}
        self._flow_base_seq: dict[int, int] = {}
        self._packet_records: list[dict[str, Any]] = []
        self._payload_records: list[dict[str, Any]] = []
        self._packet_count = 0
        self._packet_bytes = 0
        self._payload_count = 0
        self._payload_bytes = 0
        self._payload_type_counts: dict[int, int] = {}
        self._closed = False
        # v1.4.x stored packet bodies on disk. Remove those legacy diagnostics
        # as soon as a new capture session starts so an upgrade does not leave
        # stale sensitive raw material beside the new metadata-only archive.
        shutil.rmtree(self.root, ignore_errors=True)
        (self.output_dir / "gfl2_packet_debug_raw.zip").unlink(missing_ok=True)

    def _flow_id(self, flow: Hashable) -> int:
        fid = self._flow_ids.get(flow)
        if fid is None:
            fid = len(self._flow_ids) + 1
            self._flow_ids[flow] = fid
        return fid

    def _content_tag(self, data: bytes) -> str:
        # The key exists only in process memory and is intentionally omitted
        # from the archive. Truncation is sufficient for within-bundle
        # duplicate/retransmission correlation without exposing a reusable hash.
        return hmac.new(self._tag_key, data, hashlib.sha256).hexdigest()[:24]

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as fp:
            for record in records:
                fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def record_packet(self, flow: Hashable, seq: int, payload: bytes) -> None:
        if not payload:
            return
        with self._lock:
            if self._closed:
                return
            fid = self._flow_id(flow)
            seq32 = int(seq) & 0xFFFFFFFF
            base = self._flow_base_seq.setdefault(fid, seq32)
            relative_seq = (seq32 - base) & 0xFFFFFFFF
            self._packet_count += 1
            self._packet_bytes += len(payload)
            self._packet_records.append({
                "index": self._packet_count,
                "flow_id": fid,
                "relative_tcp_seq": relative_seq,
                "length": len(payload),
                "content_tag": self._content_tag(payload),
            })

    def record_payload(self, payload_type: int, data: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            self._payload_count += 1
            ptype = int(payload_type)
            self._payload_bytes += len(data)
            self._payload_type_counts[ptype] = self._payload_type_counts.get(ptype, 0) + 1
            self._payload_records.append({
                "index": self._payload_count,
                "payload_type": ptype,
                "length": len(data),
                "content_tag": self._content_tag(data),
                "wire_shape": _wire_shape(data),
            })

    def finalize(
        self,
        *,
        capture_stats: dict[str, Any] | None = None,
        payload_counts: dict[int, int] | None = None,
        framing_stats: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {}

            # Recreate the staging directory to ensure legacy v1 .bin files
            # can never leak into a new sanitized archive after an upgrade.
            shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(self.packet_index, self._packet_records)
            self._write_jsonl(self.payload_index, self._payload_records)

            manifest = {
                "schema_id": "gfl2-packet-csv-debug",
                "schema_version": 2,
                "created_at": _utc_now(),
                "reason": str(reason),
                "privacy_mode": "metadata_only",
                "privacy_guarantees": [
                    "no_tcp_payload_bodies",
                    "no_game_payload_bodies",
                    "no_ip_addresses_or_ports",
                    "no_login_tokens_or_credentials_from_packet_content",
                    "ephemeral_keyed_content_tags_only",
                ],
                "matched_tcp_packets": self._packet_count,
                "matched_tcp_bytes": self._packet_bytes,
                "assembled_game_payloads": self._payload_count,
                "assembled_game_payload_bytes": self._payload_bytes,
                "assembled_payload_type_counts": {
                    str(k): v for k, v in sorted(self._payload_type_counts.items())
                },
                "collector_payload_counts": {
                    str(k): v for k, v in sorted((payload_counts or {}).items())
                },
                "capture_stats": dict(capture_stats or {}),
                "framing_stats": dict(framing_stats or {}),
                "flow_count": len(self._flow_ids),
            }
            (self.root / "debug_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (self.root / "PRIVACY.txt").write_text(
                "This debug archive intentionally stores metadata only.\n"
                "Packet bodies, assembled game payload bodies, IP addresses, ports,\n"
                "login tokens, credentials, and other packet-content values are not stored.\n",
                encoding="utf-8",
            )

            bundle = self.output_dir / "gfl2_packet_debug_raw.zip"
            tmp_bundle = bundle.with_suffix(bundle.suffix + ".tmp")
            tmp_bundle.unlink(missing_ok=True)
            with zipfile.ZipFile(tmp_bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for path in sorted(self.root.rglob("*")):
                    if path.is_file():
                        zf.write(path, path.relative_to(self.root))
            tmp_bundle.replace(bundle)
            manifest["bundle"] = str(bundle)
            self._closed = True
            shutil.rmtree(self.root, ignore_errors=True)
            self._packet_records.clear()
            self._payload_records.clear()
            self._tag_key = b""
            return manifest


# Backward-compatible internal name for modules/tests from v1.4.x.
RawDebugRecorder = SanitizedDebugRecorder
