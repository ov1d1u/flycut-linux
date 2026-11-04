"""wl-paste child: emit one length-prefixed entry, without logging it.

Each frame is a 4-byte payload length, a 1-byte kind, then the payload.
Kind 0 is UTF-8 text; the others are the image formats in IMAGE_TYPES.
"""
import os
import struct
import subprocess
import sys
from .storage import IMAGE_TYPES, MAX_BYTES, MAX_IMAGE_BYTES, ImageClip

HEADER = struct.Struct("!IB")
# Offers that mean "this copy is text". Apps such as spreadsheets add a
# rendered picture of the copied text; that picture is not worth saving.
TEXT_TYPES = {"text/plain", "text/plain;charset=utf-8", "UTF8_STRING", "STRING", "TEXT"}


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"BM") and len(data) > 26:
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def offers_text():
    try:
        result = subprocess.run(["wl-paste", "--list-types"], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.strip() in TEXT_TYPES for line in result.stdout.decode("utf-8", "replace").splitlines())


def frame(stream, state, mode="text", has_text=offers_text):
    if state not in ("data", ""):
        return b""
    if mode == "image":
        data = stream.read(MAX_IMAGE_BYTES + 1)
        mime = image_type(data) if data and len(data) <= MAX_IMAGE_BYTES else None
        if not mime or has_text():
            return b""
        return HEADER.pack(len(data), IMAGE_TYPES.index(mime) + 1) + data
    data = stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES or b"\x00" in data:
        return b""
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return b""
    return HEADER.pack(len(data), 0) + data


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= HEADER.size:
            size, kind = HEADER.unpack(self.buffer[:HEADER.size])
            if kind > len(IMAGE_TYPES) or size > (MAX_IMAGE_BYTES if kind else MAX_BYTES):
                self.buffer.clear()
                raise ValueError("Invalid clipboard frame")
            if len(self.buffer) < size + HEADER.size:
                break
            payload = bytes(self.buffer[HEADER.size:HEADER.size + size])
            del self.buffer[:HEADER.size + size]
            if kind:
                result.append(ImageClip.of(IMAGE_TYPES[kind - 1], payload))
            else:
                result.append(payload.decode("utf-8", errors="strict"))
        return result


def main():
    mode = "image" if sys.argv[1:] == ["image"] else "text"
    payload = frame(sys.stdin.buffer, os.environ.get("CLIPBOARD_STATE", ""), mode)
    if payload:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
