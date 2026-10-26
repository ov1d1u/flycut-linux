"""wl-paste child: emit one length-prefixed UTF-8 entry, without logging it."""
import os
import struct
import sys
from .storage import MAX_BYTES


def frame(stream, state):
    if state not in ("data", ""):
        return b""
    data = stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES or b"\x00" in data:
        return b""
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return b""
    return struct.pack("!I", len(data)) + data


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 4:
            size = struct.unpack("!I", self.buffer[:4])[0]
            if size > MAX_BYTES:
                self.buffer.clear()
                raise ValueError("Oversized clipboard frame")
            if len(self.buffer) < size + 4:
                break
            payload = bytes(self.buffer[4:4 + size])
            del self.buffer[:4 + size]
            result.append(payload.decode("utf-8", errors="strict"))
        return result


def main():
    payload = frame(sys.stdin.buffer, os.environ.get("CLIPBOARD_STATE", ""))
    if payload:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
