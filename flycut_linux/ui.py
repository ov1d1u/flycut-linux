"""Translucent single-entry bezel and regular settings window."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QPlainTextEdit, QFormLayout, QSpinBox, QCheckBox, QSlider,
                              QDialog, QDialogButtonBox)


class Preview(QWidget):
    chosen = Signal(str)
    remove = Signal(int)
    settings_requested = Signal()
    dismissed = Signal()

    def __init__(self, opacity=82):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Flycut Linux")
        self.resize(500, 320)
        self.opacity = opacity
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
        self.counter = QLabel()
        layout.addWidget(self.counter)
        hint = QLabel("↑/↓ or J/K browse · Enter select · Delete remove · Esc close")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b8bfcc; font-size: 11px;")
        layout.addWidget(hint)
        self.setStyleSheet("QLabel {color: #f3f4f6;} QPushButton {padding: 4px 8px;}")
        self.refresh([])

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 18, 18)
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
            self.selected_id, text = self.rows[self.index]
            self.text.setPlainText(text)
            self.counter.setText(f"{self.index + 1} of {len(self.rows)} · {len(text):,} characters")
        else:
            self.selected_id = None
            self.text.setPlainText("Your clipboard history will appear here.\nCopy some text to get started.")
            self.counter.setText("History is empty")

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
        if watched == self.text.viewport():
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
        self.auto = QCheckBox("Insert automatically when selecting an entry")
        self.auto.setChecked(values["auto_paste"])
        self.layout.addRow(self.auto)
        self.type_content = QCheckBox("Type clipboard content")
        self.type_content.setChecked(values["type_content"])
        self.type_content.setToolTip("Use ydotool type instead of Shift+Insert. Typing depends on ydotool's keyboard-layout and character support.")
        self.layout.addRow(self.type_content)
        self.delay = QSpinBox()
        self.delay.setRange(50, 3000)
        self.delay.setSuffix(" ms")
        self.delay.setValue(values["paste_delay"])
        self.layout.addRow("Delay after closing preview", self.delay)
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
                             auto_paste=self.auto.isChecked(), type_content=self.type_content.isChecked(),
                             paste_delay=self.delay.value(), autostart=self.autostart.isChecked()))
