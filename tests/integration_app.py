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
                          PATH=str(commands) + os.pathsep + os.environ["PATH"])
        scripts = {
            "wl-paste": "import os,subprocess,sys,time\nsubprocess.run(sys.argv[4:],input=os.environ['FLYCUT_TEST_TEXT'].encode(),env=dict(os.environ,CLIPBOARD_STATE='data'),check=True)\ntime.sleep(60)\n",
            "wl-copy": "import os,sys,pathlib\npathlib.Path(os.environ['FLYCUT_TEST_ROOT'],'copied').write_bytes(sys.stdin.buffer.read())\n",
            "ydotool": "import os,sys,pathlib,json\nroot=pathlib.Path(os.environ['FLYCUT_TEST_ROOT'])\nif sys.argv[1]=='key': (root/'keys').write_text(json.dumps(sys.argv[1:]))\nif sys.argv[1]=='type':\n (root/'typed-args').write_text(json.dumps(sys.argv[1:]))\n (root/'typed').write_bytes(sys.stdin.buffer.read())\n",
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
            until(app, lambda: (root / "keys").exists())
            assert (root / "copied").read_bytes() == payload.encode()
            assert json.loads((root / "keys").read_text()) == ["key", "42:1", "110:1", "110:0", "42:0"]
            assert not controller.preview.isVisible()
            until(app, lambda: not controller.paster.busy)
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
            until(app, lambda: (root / "typed").exists() and not controller.paster.busy)
            assert (root / "typed").read_bytes() == payload.encode()
            assert json.loads((root / "typed-args").read_text()) == ["type", "--key-delay", "0", "--key-hold", "0", "--file", "-", "--escape", "0"]
            controller.pause.setChecked(True)
            controller.capture("not saved")
            assert len(controller.history.entries()) == 1
            controller.history.close()
            from flycut_linux.storage import History
            controller.history = History()
            assert controller.history.entries()[0][1] == payload
            print("PASS: full app capture, exact copy/paste/type subprocesses, selection/release, IPC, settings, autostart, pause, persistence and shutdown")
        finally:
            if controller:
                controller.shutdown()
            portal.terminate()
            portal.wait(timeout=3)


if __name__ == "__main__":
    main()
