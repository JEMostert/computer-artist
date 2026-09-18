#!/usr/bin/env python3
"""Real Qt Wayland clients for the seat experiment; not an input simulator."""
import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPlainTextEdit, QDialog, QPushButton

role = sys.argv[1]
out = Path(os.environ['CA_EXPERIMENT_OUTPUT'])
app = QApplication(sys.argv)
app.setApplicationName('computer-artist-' + role)


def record(event, **values):
    with (out / (role + '-events.jsonl')).open('a') as f:
        f.write(json.dumps({'event': event, **values}) + '\n')


class Canvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(720, 620)
        self.image = QImage(1000, 900, QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(QColor('#f8f7f3'))
        self.previous = None
        self.moves = 0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawImage(QPointF(0, 0), self.image)

    def mousePressEvent(self, event):
        self.previous = event.position()
        record('press', x=self.previous.x(), y=self.previous.y())

    def mouseMoveEvent(self, event):
        if self.previous is not None and event.buttons() & Qt.MouseButton.LeftButton:
            p = QPainter(self.image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor('#5064bb'), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(self.previous, event.position())
            p.end()
            self.previous = event.position()
            self.moves += 1
            self.update()

    def mouseReleaseEvent(self, event):
        self.previous = None
        self.image.save(str(out / 'canvas.png'))
        record('release', moves=self.moves)

    def keyPressEvent(self, event):
        record('key', text=event.text(), key=event.key(), modifiers=event.modifiers().value)


class HumanEditor(QPlainTextEdit):
    def mousePressEvent(self, event):
        record('press', x=event.position().x(), y=event.position().y())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        record('release', x=event.position().x(), y=event.position().y())
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        record('move', x=event.position().x(), y=event.position().y())
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        record('wheel', delta=event.pixelDelta().y(), angle=event.angleDelta().y())
        super().wheelEvent(event)


window = QWidget()
window.setWindowTitle('Agent canvas' if role == 'agent' else 'Your typing space')
layout = QVBoxLayout(window)
heading = QLabel('AGENT' if role == 'agent' else 'YOU')
heading.setStyleSheet('font-size: 26px; font-weight: bold; color: #b4b9c8;')
layout.addWidget(heading)
layout.addWidget(QLabel('The gray cursor draws here.' if role == 'agent' else 'Click below and type while the agent draws.'))
if role == 'agent':
    content = Canvas()
    window.resize(750, 740)
else:
    content = HumanEditor()
    previous = os.environ.get('CA_PREVIOUS_TEXT')
    if previous and Path(previous).is_file():
        content.setPlainText(Path(previous).read_text())
    content.setPlaceholderText('Your mouse and keyboard stay yours.\n\nTry typing a sentence and holding Shift.')
    content.textChanged.connect(lambda: (out / 'human-text.txt').write_text(content.toPlainText()))
    window.resize(500, 740)
layout.addWidget(content)
window.setStyleSheet('QWidget { background: #20232b; color: #eef0f6; font-size: 17px; } QPlainTextEdit { background: #15171d; padding: 14px; }')
window.show()
content.setFocus()
# A test-only trigger exercises application-created dialog activation.
def maybe_dialog():
    trigger = out / (role + '-open-dialog')
    if not trigger.exists():
        return
    trigger.unlink()
    dialog = QDialog(window)
    dialog.setWindowTitle('Agent test dialog')
    dialog.setModal(True)
    dialog.resize(300, 160)
    dialog_layout = QVBoxLayout(dialog)
    dialog_layout.addWidget(QLabel('Application-created dialog'))
    close = QPushButton('Close')
    close.clicked.connect(dialog.accept)
    dialog_layout.addWidget(close)
    window.test_dialog = dialog
    dialog.show()
    dialog.activateWindow()

trigger_timer = QTimer(window)
trigger_timer.timeout.connect(maybe_dialog)
trigger_timer.start(50)
record('started')
sys.exit(app.exec())
