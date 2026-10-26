import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent, QObject, QProcess, Signal
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from flycut_linux.processes import Paster
from flycut_linux.storage import DEFAULTS
from flycut_linux.ui import Preview

APP = QApplication.instance() or QApplication([])


class FakeProcess(QObject):
    started = Signal()
    finished = Signal(int, object)
    errorOccurred = Signal(object)
    NormalExit = QProcess.NormalExit

    def __init__(self, parent):
        super().__init__(parent)
        self.calls = []
        self.payload = b""

    def start(self, program, args):
        self.calls.append((program, args))
        self.started.emit()

    def write(self, data):
        self.payload += data

    def closeWriteChannel(self):
        pass

    def kill(self):
        pass


def spin(ms=5):
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.001)


class PasteTests(unittest.TestCase):
    def setUp(self):
        self.hidden = False
        self.released = True
        self.focus = True
        self.errors = []
        self.patch = patch("flycut_linux.processes.QProcess", FakeProcess)
        self.patch.start()
        self.paster = Paster(self.hide, lambda: self.focus,
                             ready_to_hide=lambda: self.released)
        self.paster.error.connect(self.errors.append)
        self.options = dict(DEFAULTS, paste_delay=1)

    def tearDown(self):
        self.paster.cancel()
        self.patch.stop()

    def hide(self):
        self.hidden = True

    def copied(self, code=0):
        self.paster.copy.finished.emit(code, QProcess.NormalExit)
        spin(15)

    def test_order_exact_input_and_repeat_selection(self):
        text = "🙂\n$(never execute)\n  "
        self.assertTrue(self.paster.select(text, self.options))
        self.assertFalse(self.hidden)
        self.assertFalse(self.paster.select("another", self.options))
        self.assertEqual(self.paster.copy.payload, text.encode())
        self.assertEqual(self.paster.keys.calls, [])
        self.copied()
        self.assertTrue(self.hidden)
        self.assertEqual(self.paster.keys.calls, [("ydotool", ["key", "42:1", "110:1", "110:0", "42:0"])])
        self.paster.keys.finished.emit(0, QProcess.NormalExit)
        self.assertFalse(self.paster.busy)

    def test_copy_failure_never_hides_or_sends_keys(self):
        self.paster.select("hello", self.options)
        self.copied(1)
        self.assertFalse(self.hidden)
        self.assertFalse(self.paster.keys.calls)
        self.assertTrue(self.errors)

    def test_release_then_focus_then_paste(self):
        self.released = False
        self.focus = False
        self.paster.select("hello", self.options)
        self.copied()
        self.assertFalse(self.hidden)
        self.released = True
        spin(70)
        self.assertTrue(self.hidden)
        self.assertFalse(self.paster.keys.calls)
        self.focus = True
        spin(70)
        self.assertTrue(self.paster.keys.calls)

    def test_cancel_pending_and_late_copy_callback(self):
        self.paster.select("hello", self.options)
        self.paster.cancel()
        self.copied()
        self.assertFalse(self.hidden)
        self.assertFalse(self.paster.keys.calls)

    def test_copy_only_and_typing(self):
        self.paster.select("hello", dict(self.options, auto_paste=False, type_content=True))
        self.copied()
        self.assertTrue(self.hidden)
        self.assertFalse(self.paster.keys.calls)
        text = "--literal \\n\\t $(not a command)\n\t  " + "x" * 300
        self.paster.select(text, dict(self.options, type_content=True))
        self.copied()
        self.assertEqual(self.paster.keys.calls, [("ydotool", ["type", "--key-delay", "0", "--key-hold", "0", "--file", "-", "--escape", "0"])])
        self.assertEqual(self.paster.keys.payload, text.encode())
        self.assertGreater(self.paster.timeout.interval(), 5000)

    def test_missing_copy_and_daemon(self):
        self.paster.select("hello", self.options)
        self.paster.copy.errorOccurred.emit(QProcess.FailedToStart)
        self.assertFalse(self.paster.keys.calls)
        self.assertTrue(self.errors)
        self.paster.select("hello", self.options)
        self.copied()
        self.paster.keys.finished.emit(1, QProcess.NormalExit)
        self.assertIn("ydotoold", self.errors[-1])

    def test_old_process_callback_cannot_complete_a_new_selection(self):
        self.paster.select("old", self.options)
        old_copy = self.paster.copy
        self.paster.cancel()
        self.paster.select("new", self.options)
        old_copy.finished.emit(0, QProcess.NormalExit)
        spin()
        self.assertFalse(self.hidden)
        self.assertFalse(self.paster.keys.calls)
        self.copied()
        self.assertTrue(self.paster.keys.calls)

    def test_held_keys_and_focus_failure_cancel_without_input(self):
        self.released = False
        self.paster.select("hello", self.options)
        self.copied()
        self.paster.waits = 59
        self.paster.send_keys()
        self.assertFalse(self.hidden)
        self.assertFalse(self.paster.keys.calls)
        self.assertTrue(self.errors)
        self.released = True
        self.focus = False
        self.paster.select("again", self.options)
        self.copied()
        self.paster.waits = 29
        self.paster.send_keys()
        self.assertFalse(self.paster.keys.calls)
        self.assertIn("focus", self.errors[-1])


class PreviewTests(unittest.TestCase):
    def test_focus_loss_dismisses_and_cancels_pending_insertion(self):
        preview = Preview()
        preview.refresh([(1, "test text")])
        dismissed, chosen = [], []
        preview.dismissed.connect(lambda: dismissed.append(True))
        preview.chosen.connect(chosen.append)
        with patch("flycut_linux.processes.QProcess", FakeProcess):
            paster = Paster(preview.hide, lambda: True)
            preview.dismissed.connect(paster.cancel)
            preview.show()
            paster.select("test text", DEFAULTS)
            APP.sendEvent(preview, QEvent(QEvent.WindowDeactivate))
            self.assertFalse(preview.isVisible())
            self.assertEqual(dismissed, [True])
            self.assertFalse(paster.busy)
            paster.copy.finished.emit(0, QProcess.NormalExit)
            spin()
            self.assertFalse(paster.keys.calls)
            self.assertEqual(chosen, [])
        preview.deleteLater()

    def test_deactivation_after_programmatic_hide_does_not_cancel(self):
        preview = Preview()
        dismissed = []
        preview.dismissed.connect(lambda: dismissed.append(True))
        preview.show()
        preview.activateWindow()
        spin()
        preview.hide()
        APP.sendEvent(preview, QEvent(QEvent.WindowDeactivate))
        self.assertEqual(dismissed, [])
        preview.deleteLater()

    def test_navigation_stability_and_cancel(self):
        preview = Preview()
        preview.move(QPoint(20, 20))
        preview.refresh([(2, "newer"), (1, "older")])
        preview.show()
        QTest.keyClick(preview, Qt.Key_Down)
        self.assertEqual(preview.selected_id, 1)
        preview.refresh([(3, "newest"), (2, "newer"), (1, "older")])
        self.assertEqual(preview.selected_id, 1)
        chosen = []
        preview.chosen.connect(chosen.append)
        QTest.keyClick(preview, Qt.Key_Return)
        self.assertEqual(chosen, ["older"])
        QTest.keyClick(preview, Qt.Key_Home)
        self.assertEqual(preview.selected_id, 3)
        QTest.keyClick(preview, Qt.Key_Escape)
        self.assertFalse(preview.isVisible())
        preview.deleteLater()
