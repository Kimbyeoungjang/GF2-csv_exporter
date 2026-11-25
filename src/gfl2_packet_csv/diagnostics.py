from __future__ import annotations

import json
import shutil
import threading
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Hashable

from .wire_probe import WireDecodeError, iter_field_layout

# Debug is a diagnostic aid, not a recording database. Bound the per-event
# indices independently of total counts to protect users on busy servers.
MAX_PACKET_INDEX_ROWS = 50_000
MAX_PAYLOAD_INDEX_ROWS = 50_000
MAX_TRACKED_FLOWS = 4096
MAX_SHAPE_FIELDS = 256
MAX_SHAPE_KEYS_TOTAL = 100_000
MAX_SHAPE_ROWS = 10_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wire_shape(data: bytes) -> dict[str, Any]:
    """Return protobuf structure without recording field values.

    Only field-number/wire-type occurrence counts and length-delimited size
    buckets are retained. No varint/fixed/string/bytes values are persisted.
    """
    field_counts: Counter[str] = Counter()
    length_buckets: Counter[str] = Counter()
    try:
        for number, wire, size in iter_field_layout(data, max_fields=30_000):
            key = f"{number}:{wire}"
            if key not in field_counts and len(field_counts) >= MAX_SHAPE_FIELDS:
                return {"status": "shape_limit"}
            field_counts[key] += 1
            if wire == 2:
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
    except WireDecodeError:
        return {"valid_protobuf": False, "error": "WireDecodeError"}
    return {
        "valid_protobuf": True,
        "field_counts": dict(sorted(field_counts.items())),
        "length_delimited_size_buckets": dict(sorted(length_buckets.items())),
    }


class SanitizedDebugRecorder:
    """Collect privacy-preserving packet diagnostics.

    The archive deliberately contains *no packet bodies* and *no assembled
    game payload bodies*. Exact local/remote addresses and ports, TCP sequence
    numbers, credential strings and reusable content fingerprints are not
    recorded, including when debug is enabled.

    Public diagnostics have a different filename from historical research archives.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.root = self.output_dir / "debug_diagnostics"
        self.packet_index = self.root / "matched_tcp_index.jsonl"
        self.payload_index = self.root / "game_payload_index.jsonl"
        self._lock = threading.RLock()
        self._flow_ids: dict[Hashable, int] = {}
        self._packet_records: list[dict[str, Any]] = []
        self._payload_records: list[dict[str, Any]] = []
        self._packet_count = 0
        self._packet_bytes = 0
        self._payload_count = 0
        self._payload_bytes = 0
        self._payload_type_counts: dict[int, int] = {}
        self._closed = False
        self._flow_limit_reached = False
        self._shape_keys = 0
        self._shape_rows = 0
        # v1.4.x stored packet bodies on disk. Remove those legacy diagnostics
        # as soon as a new capture session starts so an upgrade does not leave
        # stale sensitive raw material beside the new metadata-only archive.
        shutil.rmtree(self.root, ignore_errors=True)
        (self.output_dir / "gfl2_packet_diagnostics.zip").unlink(missing_ok=True)

    def _flow_id(self, flow: Hashable) -> int:
        fid = self._flow_ids.get(flow)
        if fid is None:
            if len(self._flow_ids) >= MAX_TRACKED_FLOWS:
                self._flow_limit_reached = True
                return 0  # overflow bucket, not an actual flow identifier
            fid = len(self._flow_ids) + 1
            self._flow_ids[flow] = fid
        return fid

    @staticmethod
    def _length_bucket(size: int) -> str:
        if size == 0: return "0"
        if size <= 128: return "1-128"
        if size <= 1024: return "129-1024"
        if size <= 8192: return "1025-8192"
        return "8193+"

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
            self._packet_count += 1
            self._packet_bytes += len(payload)
            if len(self._packet_records) < MAX_PACKET_INDEX_ROWS:
                self._packet_records.append({
                    "index": self._packet_count,
                    "flow_id": fid,
                    "length_bucket": self._length_bucket(len(payload)),
                })

    def record_payload(self, payload_type: int, data: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            self._payload_count += 1
            ptype = int(payload_type)
            self._payload_bytes += len(data)
            self._payload_type_counts[ptype] = self._payload_type_counts.get(ptype, 0) + 1
            if len(self._payload_records) < MAX_PAYLOAD_INDEX_ROWS:
                if self._shape_keys < MAX_SHAPE_KEYS_TOTAL and self._shape_rows < MAX_SHAPE_ROWS:
                    shape = _wire_shape(data)
                    self._shape_rows += 1
                    n = len(shape.get("field_counts", {}))
                    if self._shape_keys + n > MAX_SHAPE_KEYS_TOTAL:
                        shape = {"status": "shape_budget_exhausted"}
                        self._shape_keys = MAX_SHAPE_KEYS_TOTAL
                    else:
                        self._shape_keys += n
                else:
                    shape = {"status": "shape_budget_exhausted"}
                self._payload_records.append({
                    "index": self._payload_count,
                    "payload_type": ptype,
                    "length_bucket": self._length_bucket(len(data)),
                    "wire_shape": shape,
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

            # Recreate staging from a fixed set of known-safe metadata records.
            shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(self.packet_index, self._packet_records)
            self._write_jsonl(self.payload_index, self._payload_records)

            manifest = {
                "schema_id": "gfl2-packet-csv-debug",
                "schema_version": 2,
                "created_at": _utc_now(),
                "reason": reason if reason in {"complete", "incomplete", "cancelled", "capture_error", "flush_error", "offline_decode", "capture_finished", "offline_validation", "test"} else "unclassified",
                "privacy_mode": "metadata_only",
                "privacy_guarantees": [
                    "no_tcp_payload_bodies",
                    "no_game_payload_bodies",
                    "no_ip_addresses_or_ports",
                    "no_login_tokens_or_credentials_from_packet_content",
                    "no_content_fingerprints_or_sequence_numbers",
                ],
                "matched_tcp_packets": self._packet_count,
                "matched_tcp_bytes": self._packet_bytes,
                "assembled_game_payloads": self._payload_count,
                "assembled_game_payload_bytes": self._payload_bytes,
                "assembled_payload_type_counts": {
                    str(k): v for k, v in sorted(self._payload_type_counts.items())
                },
                "collector_payload_counts": {
                    str(k): v for k, v in sorted((k,v) for k,v in (payload_counts or {}).items()
                                             if type(k) is int and 0 < k <= 0xFFFF and type(v) is int and v >= 0)
                },
                "capture_stats": {k: v for k, v in (capture_stats or {}).items()
                                  if k in {"captured_packets", "inbound_packets", "outbound_packets", "packet_count", "drop_count"}
                                  and type(v) is int and v >= 0},
                "framing_stats": {k: v for k, v in (framing_stats or {}).items()
                                  if k in {"framed_messages", "active_framers", "active_buffer_bytes",
                                           "active_synced_framers", "max_buffer_seen"}
                                  and type(v) is int and v >= 0},
                "flow_count": len(self._flow_ids),
                "flow_count_capped": self._flow_limit_reached,
                "shape_rows_analyzed": self._shape_rows,
                "index_record_limits": {
                    "packet_rows": MAX_PACKET_INDEX_ROWS,
                    "payload_rows": MAX_PAYLOAD_INDEX_ROWS,
                },
                "index_rows_written": {
                    "packet_rows": len(self._packet_records),
                    "payload_rows": len(self._payload_records),
                },
                "index_rows_omitted": {
                    "packet_rows": self._packet_count - len(self._packet_records),
                    "payload_rows": self._payload_count - len(self._payload_records),
                },
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

            bundle = self.output_dir / "gfl2_packet_diagnostics.zip"
            tmp_bundle = bundle.with_suffix(bundle.suffix + ".tmp")
            tmp_bundle.unlink(missing_ok=True)
            with zipfile.ZipFile(tmp_bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for name in ("debug_manifest.json", "PRIVACY.txt", "matched_tcp_index.jsonl", "game_payload_index.jsonl"):
                    zf.write(self.root / name, name)
            tmp_bundle.replace(bundle)
            manifest["bundle"] = str(bundle)
            self._closed = True
            shutil.rmtree(self.root, ignore_errors=True)
            self._flow_ids.clear()
            self._packet_records.clear()
            self._payload_records.clear()
            return manifest

