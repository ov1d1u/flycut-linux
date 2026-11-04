"""Private, bounded local history and settings."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

MAX_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
# Total size of saved images; the oldest images are dropped beyond this.
IMAGE_BUDGET = 256 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp")
DEFAULTS = dict(history_limit=100, opacity=82, auto_paste=True, paste_delay=150,
                type_content=False, autostart=False, blur=True, capture_images=True)
SCHEMA = 1


class ImageClip:
    """An image entry. Bytes are read on demand so history stays small in memory."""
    __slots__ = ("mime", "size", "_load")

    def __init__(self, mime, size, load):
        self.mime = mime
        self.size = size
        self._load = load

    @classmethod
    def of(cls, mime, data):
        data = bytes(data)
        return cls(mime, len(data), lambda: data)

    def load(self):
        return self._load()

    def __repr__(self):
        return f"ImageClip({self.mime!r}, {self.size} bytes)"


def digest(kind, data):
    return hashlib.sha256(kind.encode() + b"\0" + data).hexdigest()


def xdg_path(variable, fallback):
    value = os.environ.get(variable, "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


class Settings:
    def __init__(self, directory=None):
        self.directory = private_dir(directory or xdg_path("XDG_CONFIG_HOME", ".config") / "flycut-linux")
        self.path = self.directory / "settings.json"
        self.values = DEFAULTS.copy()
        self.warning = ""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                for key, default in DEFAULTS.items():
                    if type(data.get(key)) is type(default):
                        self.values[key] = data[key]
                self.values["history_limit"] = min(10000, max(1, self.values["history_limit"]))
                self.values["opacity"] = min(100, max(15, self.values["opacity"]))
                self.values["paste_delay"] = min(3000, max(50, self.values["paste_delay"]))
            except (OSError, ValueError) as exc:
                self.warning = f"Could not load settings; using defaults: {exc}"

    def save(self):
        fd, name = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.values, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def set_autostart(self, enabled):
        directory = xdg_path("XDG_CONFIG_HOME", ".config") / "autostart"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "org.flycut.FlycutLinux.desktop"
        if enabled:
            path.write_text("[Desktop Entry]\nType=Application\nName=Flycut Linux\n"
                            "Exec=flycut-linux\nIcon=flycut-linux\nTerminal=false\n")
            path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)
        self.values["autostart"] = enabled


class History:
    def __init__(self, directory=None, limit=100):
        directory = private_dir(directory or xdg_path("XDG_DATA_HOME", ".local/share") / "flycut-linux")
        path = directory / "history.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA secure_delete=ON")
        self._migrate()
        self.set_limit(limit)

    def _migrate(self):
        if self.db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA:
            return
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS entries_v1 (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                            "kind TEXT NOT NULL, text TEXT, data BLOB, size INTEGER NOT NULL, "
                            "digest TEXT NOT NULL UNIQUE, position INTEGER NOT NULL)")
            old = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entries'").fetchone()
            if old:
                # Version 0 stored text only, in a single unique column.
                for entry_id, text, position in self.db.execute("SELECT id, text, position FROM entries").fetchall():
                    data = text.encode("utf-8")
                    self.db.execute("INSERT OR IGNORE INTO entries_v1(id, kind, text, size, digest, position) "
                                    "VALUES (?, 'text', ?, ?, ?, ?)",
                                    (entry_id, text, len(data), digest("text", data), position))
                self.db.execute("DROP TABLE entries")
            self.db.execute("ALTER TABLE entries_v1 RENAME TO entries")
            self.db.execute(f"PRAGMA user_version={SCHEMA}")

    def entries(self):
        rows = self.db.execute("SELECT id, kind, text, size FROM entries ORDER BY position DESC").fetchall()
        return [(entry_id, text if kind == "text" else
                 ImageClip(kind, size, lambda entry_id=entry_id: self.image_data(entry_id)))
                for entry_id, kind, text, size in rows]

    def image_data(self, entry_id):
        row = self.db.execute("SELECT data FROM entries WHERE id=? AND kind != 'text'", (entry_id,)).fetchone()
        return bytes(row[0]) if row else b""

    def add(self, item):
        if isinstance(item, ImageClip):
            data = item.load()
            if not data or len(data) > MAX_IMAGE_BYTES or item.mime not in IMAGE_TYPES:
                return False
            kind, text, blob = item.mime, None, data
        else:
            if not item or "\x00" in item:
                return False
            data = item.encode("utf-8")
            if len(data) > MAX_BYTES:
                return False
            kind, text, blob = "text", item, None
        key = digest("image" if blob is not None else "text", data)
        with self.db:
            self.db.execute("INSERT INTO entries(kind, text, data, size, digest, position) VALUES "
                            "(?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM entries)) "
                            "ON CONFLICT(digest) DO UPDATE SET position=excluded.position",
                            (kind, text, blob, len(data), key))
            self._trim()
        return True

    def _trim(self):
        self.db.execute("DELETE FROM entries WHERE id NOT IN (SELECT id FROM entries ORDER BY position DESC LIMIT ?)", (self.limit,))
        total = 0
        for entry_id, size in self.db.execute("SELECT id, size FROM entries WHERE kind != 'text' "
                                              "ORDER BY position DESC").fetchall():
            total += size
            if total > IMAGE_BUDGET:
                self.db.execute("DELETE FROM entries WHERE id=?", (entry_id,))

    def set_limit(self, limit):
        self.limit = max(1, min(10000, int(limit)))
        with self.db:
            self._trim()

    def delete(self, entry_id):
        with self.db:
            self.db.execute("DELETE FROM entries WHERE id=?", (entry_id,))

    def clear(self):
        with self.db:
            self.db.execute("DELETE FROM entries")

    def close(self):
        self.db.close()
