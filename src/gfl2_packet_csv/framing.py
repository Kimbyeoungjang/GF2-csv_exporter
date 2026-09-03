from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Payload:
    msg_id: int
    payload_type: int
    data: bytes
    end_of_message: bool


class GameMessageFramer:
    """Incrementally frame the GF2 TCP byte stream with passive resync.

    Passive capture may begin after a TCP stream has already started.  The
    framer therefore validates the complete inner payload layout and skips
    bytes until a plausible message boundary is found instead of trusting the
    first five captured bytes as a header forever.
    """

    HEADER_SIZE = 5
    PAYLOAD_HEADER_SIZE = 4
    MAX_BUFFER = 2 * 1024 * 1024

    def __init__(self):
        self.buffer = bytearray()
        self.resync_dropped = 0
        self.frames_decoded = 0
        self.payloads_decoded = 0
        self.invalid_complete_frames = 0
        self.candidate_resyncs = 0
        self.incomplete_waits = 0
        self.max_buffer_seen = 0
        # Once a valid application frame has been decoded on this TCP flow,
        # preserve that synchronization across incremental TCP chunks.  A
        # large legitimate frame can span many packets; scanning its partial
        # protobuf body for a later "plausible" header corrupts the stream
        # whenever body bytes happen to resemble a frame header.
        self._synced = False

    def feed(self, content: bytes) -> list[Payload]:
        if content:
            self.buffer.extend(content)
            self.max_buffer_seen = max(self.max_buffer_seen, len(self.buffer))
        output: list[Payload] = []
        while True:
            message = self._pop_message()
            if message is None:
                break
            msg_id, body = message
            payloads = self._payloads(msg_id, body)
            self.frames_decoded += 1
            self.payloads_decoded += len(payloads)
            output.extend(payloads)
        if len(self.buffer) > self.MAX_BUFFER:
            # Keep enough tail bytes to catch a split header while preventing
            # an invalid mid-stream length from growing without bound.
            drop = len(self.buffer) - 64 * 1024
            if drop > 0:
                del self.buffer[:drop]
                self.resync_dropped += drop
        return output

    def _pop_message(self) -> tuple[int, bytes] | None:
        while len(self.buffer) >= self.HEADER_SIZE:
            total_len = int.from_bytes(self.buffer[3:5], "little") + self.HEADER_SIZE
            if total_len >= self.HEADER_SIZE + self.PAYLOAD_HEADER_SIZE and total_len <= len(self.buffer):
                body = bytes(self.buffer[self.HEADER_SIZE:total_len])
                if self._valid_payload_layout(body):
                    msg_id = int.from_bytes(self.buffer[0:3], "little")
                    del self.buffer[:total_len]
                    self._synced = True
                    return msg_id, body
                # A complete but invalid frame means synchronization was lost
                # (for example capture began mid-stream or bytes were missing).
                # Drop one byte and re-enter conservative resync mode.
                del self.buffer[0]
                self.resync_dropped += 1
                self.invalid_complete_frames += 1
                self._synced = False
                continue

            # A valid-looking header whose advertised frame is merely
            # incomplete must be allowed to finish once this flow has already
            # synchronized.  The previous implementation searched *inside*
            # the partial body for another plausible frame and could discard
            # large one-shot login payloads such as 11101/23201.
            if self._synced:
                self.incomplete_waits += 1
                return None

            # Before the first validated frame, capture may genuinely have
            # started in the middle of an existing TCP stream.  Only in that
            # unsynchronized startup state do we search for a later complete
            # candidate.
            offset = self._find_complete_candidate()
            if offset is not None:
                del self.buffer[:offset]
                self.resync_dropped += offset
                self.candidate_resyncs += 1
                continue
            return None
        return None

    def diagnostics(self) -> dict[str, int | bool]:
        return {
            "frames_decoded": self.frames_decoded,
            "payloads_decoded": self.payloads_decoded,
            "resync_dropped_bytes": self.resync_dropped,
            "invalid_complete_frames": self.invalid_complete_frames,
            "candidate_resyncs": self.candidate_resyncs,
            "incomplete_waits": self.incomplete_waits,
            "buffer_bytes": len(self.buffer),
            "max_buffer_seen": self.max_buffer_seen,
            "synced": self._synced,
        }

    def _find_complete_candidate(self) -> int | None:
        max_offset = len(self.buffer) - (self.HEADER_SIZE + self.PAYLOAD_HEADER_SIZE)
        for offset in range(1, max(1, max_offset + 1)):
            if len(self.buffer) - offset < self.HEADER_SIZE:
                break
            total = int.from_bytes(self.buffer[offset + 3:offset + 5], "little") + self.HEADER_SIZE
            if total < self.HEADER_SIZE + self.PAYLOAD_HEADER_SIZE:
                continue
            end = offset + total
            if end > len(self.buffer):
                continue
            if self._valid_payload_layout(bytes(self.buffer[offset + self.HEADER_SIZE:end])):
                return offset
        return None

    @classmethod
    def _valid_payload_layout(cls, message: bytes) -> bool:
        if len(message) < cls.PAYLOAD_HEADER_SIZE:
            return False
        offset = 0
        count = 0
        while offset < len(message):
            if len(message) - offset < cls.PAYLOAD_HEADER_SIZE:
                return False
            payload_type = int.from_bytes(message[offset:offset + 2], "little")
            data_len = int.from_bytes(message[offset + 2:offset + 4], "little")
            if payload_type <= 0:
                return False
            end = offset + cls.PAYLOAD_HEADER_SIZE + data_len
            if end > len(message):
                return False
            offset = end
            count += 1
            if count > 4096:
                return False
        return count > 0 and offset == len(message)

    def _payloads(self, msg_id: int, message: bytes) -> list[Payload]:
        output: list[Payload] = []
        offset = 0
        while offset < len(message):
            payload_type = int.from_bytes(message[offset:offset + 2], "little")
            payload_len = int.from_bytes(message[offset + 2:offset + 4], "little") + self.PAYLOAD_HEADER_SIZE
            end = offset + payload_len
            data_start = offset + self.PAYLOAD_HEADER_SIZE
            offset = end
            output.append(Payload(msg_id, payload_type, message[data_start:end], offset == len(message)))
        return output


class PayloadAssembler:
    def __init__(self):
        self._prev: Payload | None = None
        self._chunks: list[bytes] = []

    def feed(self, payload: Payload) -> list[tuple[int, bytes]]:
        output: list[tuple[int, bytes]] = []
        if self._prev is not None:
            if self._can_append(payload):
                self._prev = payload
                self._chunks.append(payload.data)
                if payload.msg_id != 0 and payload.end_of_message:
                    output.append((payload.payload_type, b"".join(self._chunks)))
                    self._reset()
                return output
            output.append((self._prev.payload_type, b"".join(self._chunks)))
            self._reset()
        if payload.msg_id != 0 and payload.end_of_message:
            output.append((payload.payload_type, payload.data))
        else:
            self._prev = payload
            self._chunks = [payload.data]
        return output

    def _can_append(self, payload: Payload) -> bool:
        previous = self._prev
        if previous is None or previous.payload_type != payload.payload_type:
            return False
        return previous.msg_id == 0 or previous.msg_id == payload.msg_id

    def _reset(self) -> None:
        self._prev = None
        self._chunks = []

    def flush(self) -> list[tuple[int, bytes]]:
        if self._prev is None:
            return []
        output = [(self._prev.payload_type, b"".join(self._chunks))]
        self._reset()
        return output
