"""Private, bounded local history and settings."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile

MAX_BYTES = 1024 * 1024
DEFAULTS = dict(history_limit=100, opacity=82, auto_paste=True, paste_delay=150,
                type_content=False, autostart=False)


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
        self.db.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL UNIQUE, position INTEGER NOT NULL)")
        self.set_limit(limit)

    def entries(self):
        return self.db.execute("SELECT id, text FROM entries ORDER BY position DESC").fetchall()

    def add(self, text):
        if not text or len(text.encode("utf-8")) > MAX_BYTES or "\x00" in text:
            return False
        with self.db:
            self.db.execute("INSERT INTO entries(text, position) VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM entries)) ON CONFLICT(text) DO UPDATE SET position=excluded.position", (text,))
            self._trim()
        return True

    def _trim(self):
        self.db.execute("DELETE FROM entries WHERE id NOT IN (SELECT id FROM entries ORDER BY position DESC LIMIT ?)", (self.limit,))

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
