"""Event-driven clipboard capture and paste sequencing; no shell execution."""
import sys
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from .capture import Decoder
from .storage import MAX_BYTES


class Watcher(QObject):
    captured = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.decoder = Decoder()
        self.running = False
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(5000)
        self.retry.timeout.connect(self.start)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(self.stderr)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed)

    def start(self):
        self.running = True
        if self.process.state() != QProcess.NotRunning:
            return
        self.decoder = Decoder()
        self.process.start("wl-paste", ["--type", "text", "--watch", sys.executable,
                                       "-m", "flycut_linux.capture"])

    def read(self):
        try:
            for text in self.decoder.feed(bytes(self.process.readAllStandardOutput())):
                self.captured.emit(text)
        except ValueError:
            self.error.emit("Invalid clipboard capture stream; restarting watcher.")
            self.process.kill()

    def stderr(self):
        # Do not forward arbitrary subprocess output that might contain clipboard text.
        self.process.readAllStandardError()

    def failed(self, _error):
        if self.running:
            self.error.emit("Clipboard capture unavailable. Check wl-clipboard 2.3+ and the Wayland session.")
            self.retry.start()

    def finished(self, *_args):
        if self.running:
            self.error.emit("Clipboard watcher stopped; retrying in five seconds.")
            self.retry.start()

    def stop(self):
        self.running = False
        self.retry.stop()
        self.process.kill()
        self.process.waitForFinished(1000)


class Paster(QObject):
    copied = Signal()
    done = Signal()
    error = Signal(str)

    def __init__(self, hide, safe_to_paste, parent=None, ready_to_hide=lambda: True):
        super().__init__(parent)
        self.hide = hide
        self.safe_to_paste = safe_to_paste
        self.ready_to_hide = ready_to_hide
        self.copy = None
        self.keys = None
        self.operation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.send_keys)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self.fail("Paste timed out; no further keys will be sent."))
        self.busy = False
        self.waits = 0
        self.stage = "idle"

    def select(self, text, settings):
        if self.busy:
            return False
        self.operation += 1
        operation = self.operation
        for process in (self.copy, self.keys):
            if process:
                process.deleteLater()
        self.copy = QProcess(self)
        self.keys = QProcess(self)

        def current(callback):
            def dispatch(*args):
                if self.operation == operation and self.busy:
                    callback(*args)
            return dispatch

        self.copy.started.connect(current(self.write))
        self.copy.finished.connect(current(self.copy_finished))
        self.copy.errorOccurred.connect(current(lambda _: self.fail("Could not run wl-copy.")))
        self.keys.started.connect(current(self.write_typing))
        self.keys.finished.connect(current(self.keys_finished))
        self.keys.errorOccurred.connect(current(lambda _: self.fail("Could not contact ydotoold. Check the service and socket permissions.")))
        self.busy = True
        self.data = text.encode("utf-8")
        if not self.data or len(self.data) > MAX_BYTES:
            self.fail("Clipboard entry is empty or exceeds 1 MiB.")
            return False
        self.options = dict(settings)
        self.waits = 0
        self.stage = "copying"
        self.timeout.start(5000)
        self.copy.start("wl-copy", ["--type", "text/plain;charset=utf-8"])
        return True

    def write(self):
        self.copy.write(self.data)
        self.copy.closeWriteChannel()

    def write_typing(self):
        if self.options.get("type_content", False):
            self.keys.write(self.data)
        self.keys.closeWriteChannel()

    def copy_finished(self, code, status):
        if not self.busy:
            return
        if code != 0 or status != QProcess.NormalExit:
            self.fail("Could not set the Wayland clipboard; automatic paste was cancelled.")
            return
        self.copied.emit()
        self.stage = "release"
        self.timer.start(0)

    def send_keys(self):
        if not self.busy:
            return
        if self.stage == "release":
            if not self.ready_to_hide():
                self.waits += 1
                if self.waits >= 60:
                    self.fail("Paste cancelled: release the selection keys and try again.")
                else:
                    self.timer.start(50)
                return
            self.hide()
            if not self.options["auto_paste"]:
                self.complete()
                return
            self.stage = "focus"
            self.waits = 0
            self.timer.start(self.options["paste_delay"])
            return
        if not self.safe_to_paste():
            self.waits += 1
            if self.waits >= 30:
                self.fail("Paste cancelled: release the selection keys and return focus to the target app.")
            else:
                self.timer.start(50)
            return
        if self.options.get("type_content", False):
            # Stdin keeps text out of argv, avoids command-size limits, and
            # preserves literal backslashes instead of interpreting escapes.
            args = ["type", "--key-delay", "0", "--key-hold", "0",
                    "--file", "-", "--escape", "0"]
            # No intentional per-key delays. Still allow large entries time
            # to traverse the daemon and compositor input pipeline.
            self.timeout.start(5000 + len(self.data) * 100)
        else:
            args = ["key", "42:1", "110:1", "110:0", "42:0"]
        self.stage = "keys"
        self.keys.start("ydotool", args)

    def keys_finished(self, code, status):
        if not self.busy:
            return
        if code != 0 or status != QProcess.NormalExit:
            self.fail("ydotool could not insert text. Check ydotoold, YDOTOOL_SOCKET and /dev/uinput access; the entry remains copied.")
        else:
            self.complete()

    def complete(self):
        self.busy = False
        self.stage = "idle"
        self.timeout.stop()
        self.timer.stop()
        self.data = b""
        self.done.emit()

    def fail(self, message):
        if not self.busy:
            return
        self.complete()
        self.copy.kill()
        self.keys.kill()
        self.error.emit(message)

    def cancel(self):
        if self.busy:
            self.complete()
            self.copy.kill()
            self.keys.kill()
