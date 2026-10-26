import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from flycut_linux.capture import Decoder, frame
from flycut_linux.storage import DEFAULTS, History, MAX_BYTES, Settings


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
        self.assertEqual(len(frame(io.BytesIO(b"x" * MAX_BYTES), "data")), MAX_BYTES + 4)
        with self.assertRaises(ValueError):
            Decoder().feed((MAX_BYTES + 1).to_bytes(4, "big"))

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
        settings.path.write_text("not json")
        self.assertTrue(Settings(self.root / "settings").warning)


from unittest import mock
