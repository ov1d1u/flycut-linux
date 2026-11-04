import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

import sqlite3
from unittest import mock

from flycut_linux.capture import Decoder, frame, image_type
from flycut_linux import storage
from flycut_linux.storage import DEFAULTS, History, ImageClip, MAX_BYTES, MAX_IMAGE_BYTES, Settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + bytes(range(40))
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 20


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_capture_and_fragmented_frames(self):
        entries = ["🙂 日本語\n\n", "  a\t\r\nb  ", "$(touch /tmp/never); `whoami`", "tail\n"]
        encoded = b"".join(frame(io.BytesIO(text.encode()), "data") for text in entries)
        decoder = Decoder()
        received = []
        for offset in range(0, len(encoded), 3):
            received.extend(decoder.feed(encoded[offset:offset + 3]))
        self.assertEqual(entries, received)

    def test_reject_sensitive_binary_empty_invalid_and_large(self):
        for state in ("sensitive", "nil", "clear"):
            self.assertEqual(frame(io.BytesIO(b"secret"), state), b"")
        for data in (b"", b"abc\0", b"\xff", b"x" * (MAX_BYTES + 1)):
            self.assertEqual(frame(io.BytesIO(data), "data"), b"")
        self.assertEqual(len(frame(io.BytesIO(b"x" * MAX_BYTES), "data")), MAX_BYTES + 5)
        with self.assertRaises(ValueError):
            Decoder().feed((MAX_BYTES + 1).to_bytes(4, "big") + b"\0")

    def test_image_capture_mixed_with_text(self):
        no_text = lambda: False
        encoded = (frame(io.BytesIO(b"before"), "data")
                   + frame(io.BytesIO(PNG), "data", "image", no_text)
                   + frame(io.BytesIO(JPEG), "data", "image", no_text)
                   + frame(io.BytesIO(b"after\n"), "data"))
        decoder = Decoder()
        received = []
        for offset in range(0, len(encoded), 7):
            received.extend(decoder.feed(encoded[offset:offset + 7]))
        self.assertEqual(received[0], "before")
        self.assertEqual((received[1].mime, received[1].load()), ("image/png", PNG))
        self.assertEqual((received[2].mime, received[2].load()), ("image/jpeg", JPEG))
        self.assertEqual(received[3], "after\n")

    def test_image_capture_rejections(self):
        no_text = lambda: False
        # Text copies that also offer a rendered picture keep only the text.
        self.assertEqual(frame(io.BytesIO(PNG), "data", "image", lambda: True), b"")
        for state in ("sensitive", "nil", "clear"):
            self.assertEqual(frame(io.BytesIO(PNG), state, "image", no_text), b"")
        for data in (b"", b"not an image", PNG + b"x" * MAX_IMAGE_BYTES):
            self.assertEqual(frame(io.BytesIO(data), "data", "image", no_text), b"")
        self.assertEqual(image_type(b"GIF89a..."), "image/gif")
        self.assertEqual(image_type(b"RIFF\0\0\0\0WEBPVP8 "), "image/webp")
        with self.assertRaises(ValueError):
            Decoder().feed((10).to_bytes(4, "big") + b"\x09")
        with self.assertRaises(ValueError):
            Decoder().feed((MAX_IMAGE_BYTES + 1).to_bytes(4, "big") + b"\x01")

    def test_history_images(self):
        history = History(self.root, limit=5)
        self.assertTrue(history.add("text"))
        self.assertTrue(history.add(ImageClip.of("image/png", PNG)))
        self.assertTrue(history.add(ImageClip.of("image/jpeg", JPEG)))
        self.assertTrue(history.add(ImageClip.of("image/png", PNG)))  # moves to the top
        self.assertFalse(history.add(ImageClip.of("image/tiff", PNG)))
        self.assertFalse(history.add(ImageClip.of("image/png", b"")))
        rows = history.entries()
        self.assertEqual(len(rows), 3)
        self.assertIsInstance(rows[0][1], ImageClip)
        self.assertEqual((rows[0][1].mime, rows[0][1].size, rows[0][1].load()), ("image/png", len(PNG), PNG))
        self.assertEqual(rows[1][1].mime, "image/jpeg")
        self.assertEqual(rows[2][1], "text")
        history.close()
        history = History(self.root, limit=5)
        self.assertEqual(history.entries()[0][1].load(), PNG)
        history.delete(history.entries()[0][0])
        self.assertEqual(history.entries()[0][1].mime, "image/jpeg")
        history.close()

    def test_image_budget_drops_oldest_images_only(self):
        history = History(self.root, limit=10)
        images = [ImageClip.of("image/png", PNG + bytes([n]) * 100) for n in range(3)]
        history.add(images[0])
        history.add("kept text")
        with mock.patch.object(storage, "IMAGE_BUDGET", images[0].size * 2):
            history.add(images[1])
            history.add(images[2])
        rows = history.entries()
        self.assertEqual([row[1] if isinstance(row[1], str) else row[1].load() for row in rows],
                         [images[2].load(), images[1].load(), "kept text"])
        history.close()

    def test_history_migrates_text_only_database(self):
        db = sqlite3.connect(self.root / "history.sqlite3")
        db.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL UNIQUE, position INTEGER NOT NULL)")
        db.executemany("INSERT INTO entries(text, position) VALUES (?, ?)", [("old", 1), ("🙂 newer\n", 2)])
        db.commit()
        db.close()
        history = History(self.root)
        self.assertEqual([row[1] for row in history.entries()], ["🙂 newer\n", "old"])
        history.add("old")
        self.assertEqual([row[1] for row in history.entries()], ["old", "🙂 newer\n"])
        history.add(ImageClip.of("image/png", PNG))
        history.close()
        history = History(self.root)
        self.assertEqual(len(history.entries()), 3)
        history.close()

    def test_history_deduplicate_trim_persist_delete_clear(self):
        history = History(self.root, limit=2)
        history.add("first\n")
        first_id = history.entries()[0][0]
        history.add("🙂\n  second")
        history.add("first\n")
        self.assertEqual([row[1] for row in history.entries()], ["first\n", "🙂\n  second"])
        self.assertEqual(history.entries()[0][0], first_id)
        history.add("third")
        self.assertEqual(len(history.entries()), 2)
        history.close()
        history = History(self.root, limit=2)
        self.assertEqual(history.entries()[0][1], "third")
        history.delete(history.entries()[0][0])
        self.assertEqual(len(history.entries()), 1)
        history.set_limit(1)
        history.clear()
        self.assertEqual(history.entries(), [])
        history.close()
        self.assertEqual(stat.S_IMODE((self.root / "history.sqlite3").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_history_bounds(self):
        history = History(self.root)
        for text in ("", "a\0b", "x" * (MAX_BYTES + 1)):
            self.assertFalse(history.add(text))
        history.close()

    def test_settings_and_autostart(self):
        settings = Settings(self.root / "settings")
        settings.save()
        self.assertEqual(Settings(self.root / "settings").values, DEFAULTS)
        self.assertEqual(stat.S_IMODE(settings.path.stat().st_mode), 0o600)
        settings.values["type_content"] = True
        settings.save()
        self.assertTrue(Settings(self.root / "settings").values["type_content"])
        settings.path.write_text('{"terminal_paste": true}')
        self.assertFalse(Settings(self.root / "settings").values["type_content"])
        with unittest.mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root)}):
            settings.set_autostart(True)
            path = self.root / "autostart/org.flycut.FlycutLinux.desktop"
            self.assertIn("Exec=flycut-linux\n", path.read_text())
            settings.set_autostart(False)
            self.assertFalse(path.exists())
        settings.path.write_text('{"history_limit": -1, "opacity": 999, "paste_delay": "oops"}')
        restored = Settings(self.root / "settings")
        self.assertEqual(restored.values["history_limit"], 1)
        self.assertEqual(restored.values["opacity"], 100)
        self.assertEqual(restored.values["paste_delay"], 150)
        self.assertTrue(restored.values["blur"])
        settings.path.write_text("not json")
        self.assertTrue(Settings(self.root / "settings").warning)


from unittest import mock
