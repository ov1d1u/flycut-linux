"""Translucent single-entry bezel and regular settings window."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QPlainTextEdit, QFormLayout, QSpinBox, QCheckBox, QSlider,
                              QDialog, QDialogButtonBox, QSizePolicy)

from . import blur
from .storage import ImageClip

RADIUS = 18


def file_size(size):
    for unit in ("bytes", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:,} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024


class Preview(QWidget):
    chosen = Signal(object)  # str for text, ImageClip for images
    remove = Signal(int)
    settings_requested = Signal()
    dismissed = Signal()

    def __init__(self, opacity=82, blur_background=True):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Flycut Linux")
        self.resize(500, 320)
        self.opacity = opacity
        self.blur_background = blur_background
        # Start KWindowSystem early so its blur manager is likely bound by
        # the time the preview is first shown.
        blur.available()
        self.rows = []
        self.selected_id = None
        self.index = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        title = QLabel("Flycut")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        button = QPushButton("Settings")
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(self.settings_requested)
        header.addWidget(button)
        layout.addLayout(header)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFocusPolicy(Qt.NoFocus)
        self.text.setFrameStyle(0)
        self.text.setStyleSheet("QPlainTextEdit { background: transparent; color: #f3f4f6; font-size: 15px; }")
        self.text.viewport().installEventFilter(self)
        layout.addWidget(self.text, 1)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        # Ignored size policy keeps large pixmaps from growing the window.
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image.installEventFilter(self)
        self.image.hide()
        layout.addWidget(self.image, 1)
        self.picture = None  # (entry id, decoded QImage) for the shown image
        self.counter = QLabel()
        layout.addWidget(self.counter)
        hint = QLabel("↑/↓ or J/K browse · Enter select · Delete remove · Esc close")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b8bfcc; font-size: 11px;")
        layout.addWidget(hint)
        self.setStyleSheet("QLabel {color: #f3f4f6;} QPushButton {padding: 4px 8px;}")
        self.refresh([])

    def panel_path(self):
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), RADIUS, RADIUS)
        return path

    def set_blur(self, enabled):
        self.blur_background = enabled
        self.apply_blur()

    def apply_blur(self):
        # Blur only inside the rounded panel so the corners stay clear.
        # Without compositor support this does nothing and the plain
        # translucent panel is shown, as before.
        handle = self.windowHandle()
        if handle is None:
            return False
        region = QRegion(self.panel_path().toFillPolygon().toPolygon())
        return blur.set_blur(handle, self.blur_background, region)

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_blur()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.blur_background and self.isVisible():
            self.apply_blur()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = self.panel_path()
        painter.fillPath(path, QColor(24, 28, 38, round(self.opacity * 2.55)))
        painter.setPen(QColor(255, 255, 255, 48))
        painter.drawPath(path)

    def refresh(self, rows, reset=False):
        previous = None if reset else self.selected_id
        self.rows = rows
        self.index = next((i for i, row in enumerate(rows) if row[0] == previous),
                          min(self.index, max(0, len(rows) - 1)))
        if reset:
            self.index = 0
        self.render()

    def render(self):
        if self.rows:
            self.selected_id, item = self.rows[self.index]
            position = f"{self.index + 1} of {len(self.rows)}"
            if isinstance(item, ImageClip):
                self.show_image(item, position)
                return
            self.show_text(item)
            self.counter.setText(f"{position} · {len(item):,} characters")
        else:
            self.selected_id = None
            self.show_text("Your clipboard history will appear here.\nCopy some text or an image to get started.")
            self.counter.setText("History is empty")

    def show_text(self, text):
        self.image.hide()
        self.image.clear()
        self.picture = None
        self.text.show()
        self.text.setPlainText(text)

    def show_image(self, item, position):
        if self.picture is None or self.picture[0] != self.selected_id:
            image = QImage()
            image.loadFromData(item.load())
            self.picture = (self.selected_id, image)
        image = self.picture[1]
        kind = item.mime.split("/", 1)[1].upper()
        self.text.hide()
        self.text.clear()
        self.image.show()
        if image.isNull():
            self.image.setText("Image preview unavailable")
            self.counter.setText(f"{position} · {kind} image · {file_size(item.size)}")
        else:
            self.scale_image()
            self.counter.setText(f"{position} · {kind} image · {image.width()}×{image.height()} · {file_size(item.size)}")

    def scale_image(self):
        if not self.picture or self.picture[1].isNull():
            return
        image = self.picture[1]
        ratio = self.devicePixelRatioF()
        box = self.image.contentsRect().size() * ratio
        if box.isEmpty():
            return
        if image.width() > box.width() or image.height() > box.height():
            image = image.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(ratio)
        self.image.setPixmap(pixmap)

    def navigate(self, amount):
        if self.rows:
            self.index = max(0, min(len(self.rows) - 1, self.index + amount))
            self.render()

    def choose(self):
        if self.rows:
            self.chosen.emit(self.rows[self.index][1])

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Down, Qt.Key_Right, Qt.Key_J):
            self.navigate(1)
        elif key in (Qt.Key_Up, Qt.Key_Left, Qt.Key_K):
            self.navigate(-1)
        elif key == Qt.Key_Home:
            self.navigate(-len(self.rows))
        elif key == Qt.Key_End:
            self.navigate(len(self.rows))
        elif key == Qt.Key_PageDown:
            self.navigate(10)
        elif key == Qt.Key_PageUp:
            self.navigate(-10)
        elif key in (Qt.Key_Return, Qt.Key_Enter) and not event.isAutoRepeat():
            self.choose()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_id is not None:
            self.remove.emit(self.selected_id)
        elif key == Qt.Key_Escape:
            self.dismissed.emit()
            self.hide()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        image = getattr(self, "image", None)  # filters can fire during construction
        if watched is image and event.type() == QEvent.Resize:
            self.scale_image()
        if watched is image or watched == self.text.viewport():
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self.choose()
                return True
            if event.type() == QEvent.Wheel:
                self.navigate(1 if event.angleDelta().y() < 0 else -1)
                return True
        return super().eventFilter(watched, event)

    def event(self, event):
        if (event.type() == QEvent.WindowDeactivate and self.isVisible()
                and not getattr(self, "_hiding", False)):
            # A visible window losing activation is a cancellation. A window
            # already hidden by selection must still finish its insertion.
            self.hide()
            self.dismissed.emit()
        return super().event(event)

    def hide(self):
        # Qt can deliver WindowDeactivate synchronously before hide() has
        # cleared visibility. Do not treat our own hide as focus loss.
        self._hiding = True
        try:
            super().hide()
        finally:
            self._hiding = False

    def closeEvent(self, event):
        self.dismissed.emit()
        self.hide()
        event.ignore()


class Preferences(QDialog):
    saved = Signal(dict)
    configure_shortcut = Signal()
    reset_permission = Signal()

    def __init__(self, values, binding, diagnostics):
        super().__init__()
        self.setWindowTitle("Flycut Linux Settings")
        self.resize(490, 440)
        self.layout = QFormLayout(self)
        self.binding_label = QLabel(binding)
        self.binding_label.setWordWrap(True)
        self.layout.addRow("Global hotkey", self.binding_label)
        configure = QPushButton("Change hotkey…")
        configure.clicked.connect(self.configure_shortcut)
        self.layout.addRow("", configure)
        self.limit = QSpinBox()
        self.limit.setRange(1, 10000)
        self.limit.setValue(values["history_limit"])
        self.layout.addRow("Entries to remember", self.limit)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(15, 100)
        self.opacity.setValue(values["opacity"])
        self.layout.addRow("Background opacity", self.opacity)
        self.blur = QCheckBox("Blur what is behind the preview")
        self.blur.setChecked(values["blur"])
        self.blur.setToolTip("Needs a compositor with background blur, such as KWin with its Blur effect enabled. "
                             "Elsewhere the preview stays plainly translucent.")
        self.layout.addRow(self.blur)
        self.auto = QCheckBox("Insert automatically when selecting an entry")
        self.auto.setChecked(values["auto_paste"])
        self.layout.addRow(self.auto)
        self.type_content = QCheckBox("Type clipboard content")
        self.type_content.setChecked(values["type_content"])
        self.type_content.setToolTip("Type the text instead of pressing Shift+Insert. Typing uses your active keyboard layout; "
                                     "characters that layout cannot produce may be skipped. Images are always pasted with Shift+Insert.")
        self.layout.addRow(self.type_content)
        self.capture_images = QCheckBox("Save copied images")
        self.capture_images.setChecked(values["capture_images"])
        self.capture_images.setToolTip("Keep PNG, JPEG, GIF, BMP and WebP images (up to 32 MiB each) in history.")
        self.layout.addRow(self.capture_images)
        self.delay = QSpinBox()
        self.delay.setRange(50, 3000)
        self.delay.setSuffix(" ms")
        self.delay.setValue(values["paste_delay"])
        self.layout.addRow("Delay after closing preview", self.delay)
        reset = QPushButton("Reset paste permission")
        reset.setToolTip("Forget KDE's saved keyboard permission. Flycut asks again on the next automatic paste.")
        reset.clicked.connect(self.reset_permission)
        self.layout.addRow("", reset)
        self.autostart = QCheckBox("Start Flycut when I log in")
        self.autostart.setChecked(values["autostart"])
        self.layout.addRow(self.autostart)
        self.diagnostics = QLabel(diagnostics)
        self.diagnostics.setWordWrap(True)
        self.diagnostics.setTextFormat(Qt.PlainText)
        self.layout.addRow("Status", self.diagnostics)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.layout.addRow(buttons)

    def save(self):
        self.saved.emit(dict(history_limit=self.limit.value(), opacity=self.opacity.value(),
                             blur=self.blur.isChecked(), capture_images=self.capture_images.isChecked(),
                             auto_paste=self.auto.isChecked(), type_content=self.type_content.isChecked(),
                             paste_delay=self.delay.value(), autostart=self.autostart.isChecked()))
