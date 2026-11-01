#!/usr/bin/env python3
"""EXPERIMENTAL — PySide6 proof of concept for a future Windows frontend.

Not part of the Linux application and not installed by install.sh.
It reuses image_processor.py unchanged to show that the backend is shared.
Run from the repository root:  python experimental/windows/qt_main.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import image_processor as ip  # noqa: E402

FORMAT_LABELS = {"original": "Keep Original", "jpeg": "JPEG", "png": "PNG",
                 "webp": "WebP", "avif": "AVIF"}


def load_pixmap(magick: ip.Magick, path: Path, size: int) -> QPixmap:
    """Scaled, auto-rotated pixmap; falls back to ImageMagick for e.g. AVIF/HEIC."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        with tempfile.TemporaryDirectory(prefix="magick-studio-") as tmp:
            thumb = os.path.join(tmp, "thumb.png")
            try:
                ip.thumbnail(magick, path, size, thumb)
            except ip.MagickError:
                return QPixmap()
            return QPixmap(thumb)
    return QPixmap.fromImage(image.scaled(size, size, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))


class Signals(QObject):
    """Worker threads emit these; Qt delivers them on the GUI thread."""
    item_done = Signal(int, object)          # list row, ProcessResult
    batch_done = Signal(bool)                # cancelled


class MainWindow(QMainWindow):
    def __init__(self, magick: ip.Magick | None, error: str = ""):
        super().__init__()
        self.setWindowTitle("Magick Studio (Windows PoC)")
        self.resize(1000, 640)
        self.magick = magick
        self.cancel_event: threading.Event | None = None
        self.output_dir: str | None = None
        self.formats = ["original"] + (magick.available_output_formats() if magick else [])
        self.signals = Signals(self)
        self.signals.item_done.connect(self._item_done)
        self.signals.batch_done.connect(self._batch_done)

        # Left: image list + preview
        self.add_files_btn = QPushButton("Add Images")
        self.add_folder_btn = QPushButton("Add Folder")
        self.add_files_btn.clicked.connect(self.choose_files)
        self.add_folder_btn.clicked.connect(self.choose_folder)
        self.list = QListWidget()
        self.list.setIconSize(QSize(48, 48))
        self.list.currentItemChanged.connect(self._show_preview)
        self.preview = QLabel("Select an image to preview it", alignment=Qt.AlignCenter)
        self.preview.setMinimumHeight(220)
        left = QWidget()
        lv = QVBoxLayout(left)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_files_btn)
        buttons.addWidget(self.add_folder_btn)
        lv.addLayout(buttons)
        lv.addWidget(self.list, 2)
        lv.addWidget(self.preview, 1)

        # Right: settings
        self.web_btn = QPushButton("Optimize for Web")
        self.web_btn.clicked.connect(self.apply_web_preset)
        self.format_box = QComboBox()
        self.format_box.addItems([FORMAT_LABELS[f] for f in self.formats])
        self.quality = QSlider(Qt.Horizontal, minimum=1, maximum=100, value=ip.DEFAULT_QUALITY)
        self.quality_label = QLabel(str(ip.DEFAULT_QUALITY))
        self.quality.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        qrow = QHBoxLayout()
        qrow.addWidget(self.quality)
        qrow.addWidget(self.quality_label)
        self.strip = QCheckBox("Remove metadata", checked=True)
        self.resize_box = QGroupBox("Resize", checkable=True, checked=False)
        self.width = QSpinBox(maximum=65535, value=1600, specialValueText="Auto")
        self.height = QSpinBox(maximum=65535, value=0, specialValueText="Auto")
        self.aspect = QCheckBox("Keep aspect ratio", checked=True)
        self.upscale = QCheckBox("Allow enlarging")
        rf = QFormLayout(self.resize_box)
        rf.addRow("Width", self.width)
        rf.addRow("Height", self.height)
        rf.addRow(self.aspect)
        rf.addRow(self.upscale)
        self.output_label = QLabel("“output” folder next to each image", wordWrap=True)
        out_btn = QPushButton("Choose Output Folder…")
        out_btn.clicked.connect(self.choose_output)

        right = QWidget()
        form = QFormLayout(right)
        form.addRow(self.web_btn)
        form.addRow("Format", self.format_box)
        form.addRow("Quality", qrow)
        form.addRow(self.strip)
        form.addRow(self.resize_box)
        form.addRow("Output", self.output_label)
        form.addRow(out_btn)
        self.settings_panel = right

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([600, 400])

        # Bottom bar
        self.status = QLabel()
        self.progress = QProgressBar(visible=False)
        self.start_btn = QPushButton("Start Processing")
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel", visible=False)
        self.cancel_btn.clicked.connect(self.cancel)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.cancel_btn)
        bottom.addWidget(self.start_btn)

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.addWidget(splitter, 1)
        cv.addLayout(bottom)
        self.setCentralWidget(central)
        self.setAcceptDrops(True)

        if magick is None:
            for w in (self.add_files_btn, self.add_folder_btn, self.start_btn):
                w.setEnabled(False)
            self.status.setText(f"ImageMagick not found. {error}")
        else:
            self.update_status()

    # -------------------------------------------------------------- import
    def choose_files(self):
        patterns = " ".join(f"*{e}" for e in sorted(ip.SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add Images", "",
                                                f"Images ({patterns})")
        self.add_paths(files)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add Folder")
        if folder:
            self.add_paths([folder])

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if folder:
            self.output_dir = folder
            self.output_label.setText(folder)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def add_paths(self, paths):
        known = {os.path.realpath(self.list.item(i).data(Qt.UserRole)["path"])
                 for i in range(self.list.count())}
        exclude = [self.output_dir] if self.output_dir else []
        for p in map(Path, paths):
            found = ip.scan_folder(p, exclude) if p.is_dir() else (
                [p.absolute()] if ip.is_supported(p) else [])
            for path in found:
                if os.path.realpath(path) in known:
                    continue
                known.add(os.path.realpath(path))
                item = QListWidgetItem(path.name)
                try:
                    info = self.magick.identify(path)
                    item.setText(f"{path.name}\n{info.width} × {info.height} · {info.format}"
                                 f" · {ip.human_size(info.size)}")
                    item.setIcon(QIcon(load_pixmap(self.magick, path, 96)))
                    item.setData(Qt.UserRole, {"path": str(path), "ok": True})
                except ip.MagickError as exc:
                    item.setText(f"{path.name}\nUnsupported or damaged image ({exc})")
                    item.setData(Qt.UserRole, {"path": str(path), "ok": False})
                self.list.addItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.update_status()

    def _show_preview(self, item):
        if item is None or not item.data(Qt.UserRole)["ok"]:
            self.preview.setPixmap(QPixmap())
            return
        pix = load_pixmap(self.magick, Path(item.data(Qt.UserRole)["path"]), 480)
        self.preview.setPixmap(pix)

    def valid_items(self):
        items = [self.list.item(i) for i in range(self.list.count())]
        return [it for it in items if it.data(Qt.UserRole)["ok"]]

    def update_status(self):
        n = len(self.valid_items())
        bad = self.list.count() - n
        self.status.setText(f"{n} image{'s' if n != 1 else ''} ready"
                            + (f", {bad} unsupported" if bad else ""))
        self.start_btn.setEnabled(n > 0)

    # ------------------------------------------------------------ settings
    def apply_web_preset(self):
        s = ip.web_preset()
        self.format_box.setCurrentIndex(self.formats.index(s.output_format))
        self.resize_box.setChecked(True)
        self.width.setValue(s.width)
        self.height.setValue(s.height)
        self.aspect.setChecked(s.keep_aspect)
        self.upscale.setChecked(s.allow_upscale)
        self.quality.setValue(s.quality)
        self.strip.setChecked(s.strip_metadata)

    def collect_settings(self) -> ip.Settings:
        return ip.Settings(
            output_format=self.formats[self.format_box.currentIndex()],
            resize=self.resize_box.isChecked(), width=self.width.value(),
            height=self.height.value(), keep_aspect=self.aspect.isChecked(),
            allow_upscale=self.upscale.isChecked(), quality=self.quality.value(),
            strip_metadata=self.strip.isChecked(), output_dir=self.output_dir)

    # ---------------------------------------------------------- processing
    def start(self):
        items = self.valid_items()
        settings = self.collect_settings()
        try:
            settings.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Settings", str(exc))
            return
        self.cancel_event = threading.Event()
        self.results = []
        self.total = len(items)
        self.settings_panel.setEnabled(False)
        self.start_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.progress.setRange(0, self.total)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        # Only plain data crosses into worker threads, never Qt widgets.
        jobs = [(self.list.row(it), it.data(Qt.UserRole)["path"]) for it in items]
        threading.Thread(target=self._run, args=(jobs, settings, self.cancel_event),
                         daemon=True).start()

    def _run(self, jobs, settings, cancel_event):
        with ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) // 2))) as pool:
            futures = {pool.submit(ip.process_image, self.magick, path, settings,
                                   cancel_event): row for row, path in jobs}
            for f in as_completed(futures):
                self.signals.item_done.emit(futures[f], f.result())
        self.signals.batch_done.emit(cancel_event.is_set())

    def _item_done(self, row: int, result: ip.ProcessResult):
        item = self.list.item(row)
        self.results.append(result)
        first = item.text().split("\n")[0]
        if result.ok:
            word = "smaller" if result.saved_percent >= 0 else "larger"
            item.setText(f"{first}\n→ {result.output.name} · {ip.human_size(result.output_size)}"
                         f" ({abs(result.saved_percent):.0f}% {word})")
        else:
            item.setText(f"{first}\nFailed: {result.error}")
        self.progress.setValue(len(self.results))

    def _batch_done(self, cancelled):
        self.cancel_event = None
        self.settings_panel.setEnabled(True)
        self.start_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.progress.setVisible(False)
        ok = [r for r in self.results if r.ok]
        failed = [r for r in self.results if not r.ok and r.error != "Cancelled"]
        self.status.setText(f"{len(ok)} succeeded, {len(failed)} failed"
                            + (" (cancelled)" if cancelled else ""))
        self.last_output_dirs = {r.output.parent for r in ok}

    def cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_btn.setEnabled(False)

    def open_output(self):
        for d in getattr(self, "last_output_dirs", ()):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))


def main() -> int:
    app = QApplication(sys.argv)
    try:
        magick, error = ip.Magick.detect(), ""
    except ip.MagickError as exc:
        magick, error = None, str(exc)
    win = MainWindow(magick, error)
    win.show()
    if magick and len(sys.argv) > 1:
        win.add_paths(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
