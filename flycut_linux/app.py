"""Application lifecycle, tray, IPC and Wayland focus coordination."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QEvent, QObject, QLockFile, QProcess, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
from .portal import Shortcuts
from .processes import Paster, Watcher
from .storage import History, Settings
from .ui import Preferences, Preview

APP_ID = "org.flycut.FlycutLinux"


class InputState(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.keys = set()
        self.buttons = set()
        self.shortcut_held = False

    def eventFilter(self, _watched, event):
        kind = event.type()
        if kind == QEvent.KeyPress and not event.isAutoRepeat():
            self.keys.add(event.key())
        elif kind == QEvent.KeyRelease and not event.isAutoRepeat():
            self.keys.discard(event.key())
        elif kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            self.buttons.add(event.button())
        elif kind == QEvent.MouseButtonRelease:
            self.buttons.discard(event.button())
        elif kind == QEvent.ApplicationDeactivate:
            self.keys.clear()
            self.buttons.clear()
        return False

    def released(self):
        return not self.keys and not self.buttons and not self.shortcut_held


class Application(QObject):
    def __init__(self, qt, server):
        super().__init__()
        self.qt = qt
        self.server = server
        self.settings = Settings()
        self.history = History(limit=self.settings.values["history_limit"])
        self.preview = Preview(self.settings.values["opacity"])
        self.preview.refresh(self.history.entries())
        self.input = InputState(self)
        qt.installEventFilter(self.input)
        self.paster = Paster(self.preview.hide, self.focus_returned, self,
                             ready_to_hide=self.input.released)
        self.paster.error.connect(self.report)
        self.preview.chosen.connect(lambda text: self.paster.select(text, self.settings.values))
        self.preview.dismissed.connect(self.paster.cancel)
        self.preview.remove.connect(self.remove)
        self.preview.settings_requested.connect(self.show_settings)
        self.watcher = Watcher(self)
        self.watcher.captured.connect(self.capture)
        self.watcher.error.connect(self.report)
        self.shortcuts = Shortcuts(self)
        self.shortcuts.activated.connect(self.shortcut)
        self.shortcuts.released.connect(self.shortcut_released)
        self.shortcuts.binding.connect(self.update_binding)
        self.shortcuts.error.connect(self.report)
        self.binding = "Connecting to KDE…"
        self.last_error = ""
        self.preferences = None
        self.daemon_status = "not checked"
        self.daemon_probe = QProcess(self)
        self.daemon_probe.finished.connect(self.daemon_checked)
        self.daemon_probe.errorOccurred.connect(lambda _: self.daemon_checked(1, QProcess.CrashExit))
        self.daemon_timeout = QTimer(self)
        self.daemon_timeout.setSingleShot(True)
        self.daemon_timeout.timeout.connect(self.daemon_probe.kill)
        self.clients = set()
        self.tray = QSystemTrayIcon(QIcon(str(Path(__file__).with_name("flycut-linux.svg"))), self)
        self.tray.setToolTip("Flycut Linux — clipboard history")
        menu = QMenu()
        menu.addAction("Clipboard history", self.show_history)
        menu.addAction("Settings…", self.show_settings)
        self.pause = menu.addAction("Pause capture")
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self.pause_capture)
        menu.addAction("Clear history…", self.clear)
        menu.addSeparator()
        menu.addAction("Quit", qt.quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_history() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self.server.newConnection.connect(self.connection)
        qt.aboutToQuit.connect(self.shutdown)
        self.shortcuts.start()
        self.watcher.start()
        if self.settings.warning:
            QTimer.singleShot(0, lambda: self.report(self.settings.warning))

    def focus_returned(self):
        return self.input.released() and QApplication.activeWindow() is None and not self.preview.isVisible()

    def shortcut(self, token):
        self.input.shortcut_held = True
        self.show_history(token)

    def shortcut_released(self):
        self.input.shortcut_held = False
        # Modifiers may have been pressed before Flycut received keyboard focus.
        for key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            self.input.keys.discard(key)

    def show_history(self, token=""):
        self.paster.cancel()
        self.preview.refresh(self.history.entries(), reset=True)
        self.activate(self.preview, token)

    def activate(self, window, token=""):
        # Qt's Wayland xdg-shell plugin consumes this on activation.
        if token:
            os.environ["XDG_ACTIVATION_TOKEN"] = token
        window.show()
        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen().geometry().center())
        if screen:
            window.move(screen.availableGeometry().center() - window.rect().center())
        window.raise_()
        window.activateWindow()
        if window.windowHandle():
            window.windowHandle().requestActivate()
        if token:
            os.environ.pop("XDG_ACTIVATION_TOKEN", None)

    def capture(self, text):
        try:
            if not self.pause.isChecked() and self.history.add(text):
                self.preview.refresh(self.history.entries())
        except Exception as exc:
            self.report(f"Could not save clipboard history: {exc}")

    def remove(self, entry_id):
        self.history.delete(entry_id)
        self.preview.refresh(self.history.entries())

    def pause_capture(self, paused):
        if paused:
            self.watcher.stop()
        else:
            self.watcher.start()
        self.tray.setToolTip("Flycut Linux — capture paused" if paused else "Flycut Linux — clipboard history")

    def clear(self):
        if QMessageBox.question(None, "Clear clipboard history", "Delete all saved Flycut entries? This does not clear the system clipboard.") == QMessageBox.Yes:
            self.paster.cancel()
            self.history.clear()
            self.preview.refresh([])

    def update_binding(self, binding):
        self.binding = binding
        if self.preferences:
            self.preferences.binding_label.setText(binding)

    def diagnostics(self):
        lines = [f"{name}: {'found' if shutil.which(name) else 'MISSING'}" for name in ("wl-copy", "wl-paste", "ydotool")]
        path = os.environ.get("YDOTOOL_SOCKET", os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket"))
        lines.append(f"ydotoold: {self.daemon_status} ({path})")
        lines.append("KDE shortcut: " + self.binding)
        if self.last_error:
            lines.append(self.last_error)
        return "\n".join(lines)

    def daemon_checked(self, code, status):
        self.daemon_timeout.stop()
        self.daemon_probe.readAllStandardOutput()
        self.daemon_probe.readAllStandardError()
        self.daemon_status = "connected" if code == 0 and status == QProcess.NormalExit else "unavailable; check service and socket permissions"
        if self.preferences:
            self.preferences.diagnostics.setText(self.diagnostics())

    def show_settings(self):
        self.paster.cancel()
        self.preview.hide()
        if self.preferences is None:
            self.preferences = Preferences(self.settings.values, self.binding, self.diagnostics())
            self.preferences.saved.connect(self.save_settings)
            self.preferences.configure_shortcut.connect(self.shortcuts.configure)
            self.preferences.finished.connect(self.preferences_closed)
        self.preferences.diagnostics.setText(self.diagnostics())
        self.activate(self.preferences)
        if self.daemon_probe.state() == QProcess.NotRunning:
            self.daemon_timeout.start(2000)
            self.daemon_probe.start("ydotool", ["debug"])

    def preferences_closed(self, _result):
        self.preferences.deleteLater()
        self.preferences = None

    def save_settings(self, values):
        old = dict(self.settings.values)
        try:
            self.settings.set_autostart(values["autostart"])
            self.settings.values.update(values)
            self.settings.save()
            self.history.set_limit(values["history_limit"])
        except OSError as exc:
            self.settings.values = old
            self.report(f"Could not save settings: {exc}")
            return
        self.preview.opacity = values["opacity"]
        self.preview.update()
        self.preview.refresh(self.history.entries())
        self.preferences.accept()

    def report(self, message):
        if message == self.last_error:
            return
        self.last_error = message
        print(message, file=sys.stderr)
        self.tray.showMessage("Flycut Linux", message, QSystemTrayIcon.Warning, 8000)
        if self.preferences:
            self.preferences.diagnostics.setText(self.diagnostics())

    def connection(self):
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            self.clients.add(client)
            client.setProperty("buffer", b"")
            client.readyRead.connect(lambda c=client: self.command(c))
            client.disconnected.connect(lambda c=client: self.release_client(c))
            if client.bytesAvailable():
                self.command(client)
            QTimer.singleShot(2000, client.disconnectFromServer)

    def release_client(self, client):
        self.clients.discard(client)
        client.deleteLater()

    def command(self, client):
        data = client.property("buffer") + bytes(client.readAll())
        if len(data) > 4096:
            client.disconnectFromServer()
            return
        client.setProperty("buffer", data)
        if b"\n" not in data:
            return
        try:
            request = json.loads(data.split(b"\n", 1)[0])
            if request.get("command") == "settings":
                self.show_settings()
            else:
                self.show_history(str(request.get("token", "")))
        except (ValueError, AttributeError):
            pass
        client.disconnectFromServer()

    def shutdown(self):
        self.paster.cancel()
        self.watcher.stop()
        self.shortcuts.stop()
        self.daemon_probe.kill()
        self.daemon_probe.waitForFinished(1000)
        self.history.close()
        self.server.close()
        self.tray.hide()


def main():
    parser = argparse.ArgumentParser(description="Flycut clipboard history for KDE Wayland")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true", help="Open clipboard history")
    group.add_argument("--settings", action="store_true", help="Open settings")
    parser.add_argument("--version", action="version", version="Flycut Linux 0.1.0")
    args = parser.parse_args()
    if not os.environ.get("WAYLAND_DISPLAY"):
        parser.error("a Wayland session is required; X11 is not supported")
    os.environ["QT_QPA_PLATFORM"] = "wayland"
    app = QApplication([sys.argv[0]])
    app.setApplicationName("flycut-linux")
    app.setApplicationDisplayName("Flycut Linux")
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    runtime = QStandardPaths.writableLocation(QStandardPaths.RuntimeLocation)
    name = os.path.join(runtime, "flycut-linux.sock")
    lock = QLockFile(os.path.join(runtime, "flycut-linux.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        client = QLocalSocket()
        client.connectToServer(name)
        if not client.waitForConnected(1500):
            print("Flycut is already starting or its command socket is unavailable.", file=sys.stderr)
            return 1
        command = "settings" if args.settings else "show"
        payload = dict(command=command, token=os.environ.pop("XDG_ACTIVATION_TOKEN", ""))
        client.write(json.dumps(payload).encode() + b"\n")
        client.waitForBytesWritten(1000)
        client.disconnectFromServer()
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(name):
        print("Could not create private Flycut command socket: " + server.errorString(), file=sys.stderr)
        return 1
    controller = Application(app, server)
    if args.settings:
        QTimer.singleShot(0, controller.show_settings)
    elif args.show:
        token = os.environ.pop("XDG_ACTIVATION_TOKEN", "")
        QTimer.singleShot(0, lambda: controller.show_history(token))
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())
