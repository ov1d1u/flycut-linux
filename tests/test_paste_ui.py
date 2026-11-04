import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent, QObject, QProcess, Signal
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from flycut_linux.processes import PASTE_KEYS, Paster, typing_events
from flycut_linux.portal import keysym
from flycut_linux.storage import DEFAULTS, ImageClip
from PySide6.QtCore import QBuffer, QByteArray
from PySide6.QtGui import QColor, QImage


def png(width=40, height=30):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(200, 30, 30))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)
from flycut_linux import blur
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


class FakeKeyboard(QObject):
    sent = Signal(int)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.ready = True
        self.calls = []
        self.cancelled = []

    def send(self, operation, events):
        self.calls.append((operation, list(events)))

    def cancel(self, operation):
        self.cancelled.append(operation)

    def finish(self):
        self.sent.emit(self.calls[-1][0])

    def events(self):
        return [events for _operation, events in self.calls]


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
        self.keyboard = FakeKeyboard()
        self.paster = Paster(self.hide, lambda: self.focus,
                             ready_to_hide=lambda: self.released, keyboard=self.keyboard)
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
        self.assertEqual(self.keyboard.calls, [])
        self.copied()
        self.assertTrue(self.hidden)
        self.assertEqual(self.keyboard.events(), [PASTE_KEYS])
        self.assertEqual(PASTE_KEYS, [("code", 42, True), ("code", 110, True), ("code", 110, False), ("code", 42, False)])
        self.keyboard.finish()
        self.assertFalse(self.paster.busy)

    def test_copy_failure_never_hides_or_sends_keys(self):
        self.paster.select("hello", self.options)
        self.copied(1)
        self.assertFalse(self.hidden)
        self.assertFalse(self.keyboard.calls)
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
        self.assertFalse(self.keyboard.calls)
        self.focus = True
        spin(70)
        self.assertTrue(self.keyboard.calls)

    def test_cancel_pending_and_late_copy_callback(self):
        self.paster.select("hello", self.options)
        self.paster.cancel()
        self.copied()
        self.assertFalse(self.hidden)
        self.assertFalse(self.keyboard.calls)

    def test_copy_only_and_typing(self):
        self.paster.select("hello", dict(self.options, auto_paste=False, type_content=True))
        self.copied()
        self.assertTrue(self.hidden)
        self.assertFalse(self.keyboard.calls)
        text = "--literal \\n\\t $(not a command)\n\t  " + "x" * 300
        self.paster.select(text, dict(self.options, type_content=True))
        self.copied()
        self.assertEqual(self.keyboard.events(), [typing_events(text)])
        self.assertGreater(self.paster.timeout.interval(), 5000)

    def test_keysyms_for_typing(self):
        self.assertEqual(keysym("a"), ord("a"))
        self.assertEqual(keysym("é"), 0xe9)
        self.assertEqual(keysym("\n"), 0xff0d)
        self.assertEqual(keysym("\t"), 0xff09)
        self.assertEqual(keysym("🙂"), 0x01000000 | 0x1f642)
        self.assertEqual(typing_events("ab"), [("sym", 97, True), ("sym", 97, False), ("sym", 98, True), ("sym", 98, False)])

    def test_first_paste_waits_for_permission_prompt(self):
        self.keyboard.ready = False
        self.paster.select("hello", self.options)
        self.copied()
        self.assertTrue(self.keyboard.calls)
        self.assertGreater(self.paster.timeout.interval(), 60000)

    def test_stale_keyboard_results_are_ignored(self):
        self.paster.select("old", self.options)
        self.copied()
        old = self.keyboard.calls[-1][0]
        self.paster.cancel()
        self.assertEqual(self.keyboard.cancelled, [old])
        self.paster.select("new", self.options)
        self.keyboard.sent.emit(old)
        self.keyboard.failed.emit(old, "late")
        self.assertTrue(self.paster.busy)
        self.assertFalse(self.errors)

    def test_image_is_copied_with_its_type_and_pasted_not_typed(self):
        data = png()
        self.assertTrue(self.paster.select(ImageClip.of("image/png", data), dict(self.options, type_content=True)))
        self.assertEqual(self.paster.copy.calls, [("wl-copy", ["--type", "image/png"])])
        self.assertEqual(self.paster.copy.payload, data)
        self.copied()
        self.assertEqual(self.keyboard.events(), [PASTE_KEYS])

    def test_text_copied_as_utf8_text(self):
        self.paster.select("hello", self.options)
        self.assertEqual(self.paster.copy.calls, [("wl-copy", ["--type", "text/plain;charset=utf-8"])])

    def test_missing_copy_and_denied_permission(self):
        self.paster.select("hello", self.options)
        self.paster.copy.errorOccurred.emit(QProcess.FailedToStart)
        self.assertFalse(self.keyboard.calls)
        self.assertTrue(self.errors)
        self.paster.select("hello", self.options)
        self.copied()
        operation = self.keyboard.calls[-1][0]
        self.keyboard.failed.emit(operation, "Automatic paste needs keyboard permission.")
        self.assertFalse(self.paster.busy)
        self.assertIn("permission", self.errors[-1])
        self.assertEqual(self.keyboard.cancelled, [operation])

    def test_old_process_callback_cannot_complete_a_new_selection(self):
        self.paster.select("old", self.options)
        old_copy = self.paster.copy
        self.paster.cancel()
        self.paster.select("new", self.options)
        old_copy.finished.emit(0, QProcess.NormalExit)
        spin()
        self.assertFalse(self.hidden)
        self.assertFalse(self.keyboard.calls)
        self.copied()
        self.assertTrue(self.keyboard.calls)

    def test_held_keys_and_focus_failure_cancel_without_input(self):
        self.released = False
        self.paster.select("hello", self.options)
        self.copied()
        self.paster.waits = 59
        self.paster.send_keys()
        self.assertFalse(self.hidden)
        self.assertFalse(self.keyboard.calls)
        self.assertTrue(self.errors)
        self.released = True
        self.focus = False
        self.paster.select("again", self.options)
        self.copied()
        self.paster.waits = 29
        self.paster.send_keys()
        self.assertFalse(self.keyboard.calls)
        self.assertIn("focus", self.errors[-1])


class PreviewTests(unittest.TestCase):
    def test_focus_loss_dismisses_and_cancels_pending_insertion(self):
        preview = Preview()
        preview.refresh([(1, "test text")])
        dismissed, chosen = [], []
        preview.dismissed.connect(lambda: dismissed.append(True))
        preview.chosen.connect(chosen.append)
        with patch("flycut_linux.processes.QProcess", FakeProcess):
            keyboard = FakeKeyboard()
            paster = Paster(preview.hide, lambda: True, keyboard=keyboard)
            preview.dismissed.connect(paster.cancel)
            preview.show()
            paster.select("test text", DEFAULTS)
            APP.sendEvent(preview, QEvent(QEvent.WindowDeactivate))
            self.assertFalse(preview.isVisible())
            self.assertEqual(dismissed, [True])
            self.assertFalse(paster.busy)
            paster.copy.finished.emit(0, QProcess.NormalExit)
            spin()
            self.assertFalse(keyboard.calls)
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

    def test_image_preview_and_selection(self):
        preview = Preview()
        image = ImageClip.of("image/png", png(1200, 900))
        preview.refresh([(2, image), (1, "text")])
        preview.show()
        spin(20)
        self.assertTrue(preview.image.isVisible())
        self.assertFalse(preview.text.isVisible())
        self.assertIn("PNG image · 1200×900", preview.counter.text())
        pixmap = preview.image.pixmap()
        self.assertFalse(pixmap.isNull())
        # Large images are scaled down to fit the panel.
        self.assertLessEqual(pixmap.width() / pixmap.devicePixelRatio(), preview.image.width())
        chosen = []
        preview.chosen.connect(chosen.append)
        QTest.keyClick(preview, Qt.Key_Return)
        self.assertIs(chosen[0], image)
        QTest.keyClick(preview, Qt.Key_Down)
        self.assertTrue(preview.text.isVisible())
        self.assertFalse(preview.image.isVisible())
        self.assertEqual(preview.text.toPlainText(), "text")
        preview.refresh([(3, ImageClip.of("image/png", b"\x89PNG\r\n\x1a\nbroken"))], reset=True)
        self.assertEqual(preview.image.text(), "Image preview unavailable")
        preview.hide()
        preview.deleteLater()

    def test_blur_requested_inside_rounded_panel_and_toggled(self):
        calls = []
        with patch("flycut_linux.ui.blur.set_blur", lambda window, on, region: calls.append((window, on, region)) or True):
            preview = Preview(blur_background=True)
            preview.show()
            self.assertTrue(calls)
            window, enabled, region = calls[-1]
            self.assertIs(window, preview.windowHandle())
            self.assertTrue(enabled)
            # The rounded corners stay outside the blurred area.
            self.assertFalse(region.contains(QPoint(0, 0)))
            self.assertTrue(region.contains(preview.rect().center()))
            preview.set_blur(False)
            self.assertFalse(calls[-1][1])
            preview.hide()
        preview.deleteLater()

    def test_blur_falls_back_quietly_without_support(self):
        with patch.object(blur, "_load", lambda: None):
            preview = Preview(blur_background=True)
            preview.show()
            self.assertFalse(preview.apply_blur())
            self.assertTrue(preview.isVisible())
            preview.hide()
        preview.deleteLater()
        self.assertTrue(blur.status())
