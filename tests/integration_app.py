"""Full app smoke test with disposable data, private bus and fake external tools.

PYTHONPATH=. FLYCUT_PRIVATE_TEST_BUS=1 dbus-run-session -- python tests/integration_app.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

assert os.environ.get("FLYCUT_PRIVATE_TEST_BUS") == "1", "Run on a private test bus"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from flycut_linux.app import Application
from flycut_linux.processes import PASTE_KEYS, typing_events
from integration_portal import take

PASTE = [list(event) for event in PASTE_KEYS]


def until(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for app state"


def main():
    payload = "🙂 Unicode\n  trailing whitespace\t\n$(never execute)\n"
    with tempfile.TemporaryDirectory(prefix="flycut-app-smoke-") as temporary:
        root = Path(temporary)
        commands = root / "bin"
        commands.mkdir()
        os.environ.update(XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
                          FLYCUT_TEST_ROOT=str(root), FLYCUT_TEST_TEXT=payload,
                          FLYCUT_TEST_IMAGE=str(root / "image.png"),
                          PATH=str(commands) + os.pathsep + os.environ["PATH"])
        scripts = {
            # The image watcher delivers only once the test writes image.png.
            "wl-paste": ("import os,subprocess,sys,time\n"
                         "if sys.argv[1:]==['--list-types']: print('image/png'); sys.exit()\n"
                         "image=os.environ['FLYCUT_TEST_IMAGE']\n"
                         "if sys.argv[2]=='image' and not os.path.exists(image): time.sleep(60); sys.exit()\n"
                         "data=open(image,'rb').read() if sys.argv[2]=='image' else os.environ['FLYCUT_TEST_TEXT'].encode()\n"
                         "subprocess.run(sys.argv[4:],input=data,env=dict(os.environ,CLIPBOARD_STATE='data'),check=True)\n"
                         "time.sleep(60)\n"),
            "wl-copy": "import os,sys,pathlib,json\nroot=pathlib.Path(os.environ['FLYCUT_TEST_ROOT'])\n(root/'copied-args').write_text(json.dumps(sys.argv[1:]))\n(root/'copied').write_bytes(sys.stdin.buffer.read())\n",
        }
        for name, script in scripts.items():
            path = commands / name
            path.write_text(f"#!{sys.executable}\n" + script)
            path.chmod(0o700)
        portal = subprocess.Popen([sys.executable, str(Path(__file__).with_name("integration_portal.py")), "--server"], stdout=subprocess.PIPE, text=True)
        controller = None
        try:
            assert portal.stdout.readline().strip() == "ready"
            app = QApplication([])
            app.setQuitOnLastWindowClosed(False)
            server = QLocalServer()
            server.setSocketOptions(QLocalServer.UserAccessOption)
            name = str(root / "app.sock")
            assert server.listen(name), server.errorString()
            controller = Application(app, server)
            until(app, lambda: controller.history.entries() and controller.binding == "Ctrl+Alt+V"
                  and controller.preview.isVisible() and not controller.input.shortcut_held)
            assert controller.history.entries()[0][1] == payload
            controller.show_history()
            QTest.keyClick(controller.preview, Qt.Key_Return)
            until(app, lambda: (root / "copied").exists() and not controller.paster.busy)
            assert (root / "copied").read_bytes() == payload.encode()
            assert not controller.preview.isVisible()
            state = take()
            assert state["events"] == PASTE, state
            assert state["selects"] == [""], state
            assert (root / "config/flycut-linux/remote-desktop-token").read_text() == "token-1"
            assert "Paste permission: granted" in controller.diagnostics()
            client = QLocalSocket()
            client.connectToServer(name)
            assert client.waitForConnected(1000)
            client.write(b'{"command":')
            client.waitForBytesWritten(1000)
            app.processEvents()
            client.write(b'"settings"}\n')
            client.waitForBytesWritten(1000)
            until(app, lambda: controller.preferences is not None and controller.preferences.isVisible())
            controller.preferences.autostart.setChecked(True)
            controller.preferences.opacity.setValue(65)
            controller.preferences.type_content.setChecked(True)
            controller.preferences.save()
            assert controller.preferences is None
            assert controller.preview.opacity == 65
            assert (root / "config/autostart/org.flycut.FlycutLinux.desktop").exists()
            assert controller.settings.values["type_content"]
            controller.show_history()
            QTest.keyClick(controller.preview, Qt.Key_Return)
            until(app, lambda: not controller.paster.busy)
            assert take()["events"] == [list(event) for event in typing_events(payload)]
            # Images: captured by the second watcher, then pasted with keys even in typing mode.
            from PySide6.QtGui import QColor, QImage
            from flycut_linux.storage import History, ImageClip
            image = QImage(64, 48, QImage.Format_RGB32)
            image.fill(QColor(30, 200, 30))
            assert image.save(str(root / "image.png"))
            png = (root / "image.png").read_bytes()
            controller.image_watcher.stop()
            controller.image_watcher.start()
            until(app, lambda: isinstance(controller.history.entries()[0][1], ImageClip))
            assert controller.history.entries()[0][1].load() == png
            controller.show_history()
            assert "PNG image · 64×48" in controller.preview.counter.text()
            QTest.keyClick(controller.preview, Qt.Key_Return)
            until(app, lambda: not controller.paster.busy)
            assert take()["events"] == PASTE
            assert (root / "copied").read_bytes() == png
            assert json.loads((root / "copied-args").read_text()) == ["--type", "image/png"]
            controller.pause.setChecked(True)
            controller.capture("not saved")
            assert len(controller.history.entries()) == 2
            controller.history.close()
            controller.history = History()
            assert controller.history.entries()[0][1].load() == png
            assert controller.history.entries()[1][1] == payload
            # A restarted app reuses the saved permission instead of asking again.
            assert controller.settings.restore_token() == "token-1"
            controller.reset_paste_permission()
            assert controller.settings.restore_token() == ""
            assert "Paste permission: not requested yet" in controller.diagnostics()
            print("PASS: full app capture, text and image copy, portal paste/type, selection/release, IPC, settings, autostart, pause, persistence and shutdown")
        finally:
            if controller:
                controller.shutdown()
            portal.terminate()
            portal.wait(timeout=3)


if __name__ == "__main__":
    main()
