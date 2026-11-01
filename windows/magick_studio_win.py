#!/usr/bin/env python3
"""Magick Studio for Windows — PySide6 (Qt) user interface.

The image processing lives in the shared image_processor.py backend, which
is also used by the GTK 4 Linux interface (main.py).

Diagnostic options (used by CI on the packaged application):
  --smoke-test INPUT_DIR OUTPUT_DIR REPORT.json   process images without a window
  --gui-check REPORT.json [PATHS...]              open the window, write a report, quit
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import image_processor as ip  # noqa: E402

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction, QColor, QDesktopServices, QIcon, QImage, QImageReader, QKeySequence, QPixmap,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGraphicsOpacityEffect, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter,
    QStackedWidget, QStyle, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

APP_NAME = "Magick Studio"
APP_USER_MODEL_ID = "MagickStudio.MagickStudio"
THUMB_SIZE = 48
PREVIEW_SIZE = 720
FORMAT_LABELS = {"original": "Keep original", "jpeg": "JPEG", "png": "PNG",
                 "webp": "WebP", "avif": "AVIF"}


def resource_path(*parts: str) -> Path:
    """Path to bundled data, both from source and from a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def secondary(label: QLabel) -> QLabel:
    """Dim a label for secondary text. Disabled text is too faint in some Windows styles."""
    label.setGraphicsEffect(QGraphicsOpacityEffect(label, opacity=0.7))
    return label


def load_image(magick: ip.Magick, path: Path, size: int) -> QImage:
    """Scaled, auto-rotated image. Safe to call from worker threads."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():  # e.g. AVIF/HEIC: let ImageMagick render a PNG preview
        with tempfile.TemporaryDirectory(prefix="magick-studio-") as tmp:
            thumb = os.path.join(tmp, "thumb.png")
            try:
                ip.thumbnail(magick, path, size, thumb)
            except ip.MagickError:
                return QImage()
            return QImage(thumb)   # fully loaded into memory before tmp is removed
    if image.width() > size or image.height() > size:
        image = image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image


@dataclass(eq=False)
class ImageItem:
    path: Path
    widget: QListWidgetItem
    info: ip.ImageInfo | None = None
    error: str = ""
    result: ip.ProcessResult | None = None

    def describe(self) -> str:
        if self.error:
            return self.error
        if not self.info:
            return "Reading…"
        return (f"{self.info.width} × {self.info.height}  ·  {self.info.format}  ·  "
                f"{ip.human_size(self.info.size)}")

    def refresh(self) -> None:
        text = f"{self.path.name}\n{self.describe()}"
        r = self.result
        if r is not None and r.ok:
            word = "smaller" if r.saved_percent >= 0 else "larger"
            text += (f"\n✓ {r.output.name}  ·  {ip.human_size(r.output_size)}  "
                     f"({abs(r.saved_percent):.0f}% {word})")
        elif r is not None:
            text += f"\n✗ {r.error}"
        self.widget.setText(text)
        failed = bool(self.error) or (r is not None and not r.ok)
        self.widget.setForeground(QColor("#c42b1c") if failed else QColor())
        self.widget.setToolTip(str(r.output) if r is not None and r.ok else str(self.path))


class Signals(QObject):
    """Emitted from worker threads; Qt delivers them on the GUI thread."""
    loaded = Signal(object, object, str, QImage)      # item, info, error, thumbnail
    preview = Signal(int, QImage)                     # token, image
    scanned = Signal(list, int, bool)                 # paths, skipped, had_folder
    item_done = Signal(object, object)                # item, ProcessResult
    batch_done = Signal(bool)                         # cancelled


class PreviewLabel(QLabel):
    """Shows an image scaled to fit, keeping its aspect ratio."""

    def __init__(self):
        super().__init__(alignment=Qt.AlignCenter)
        self.setMinimumSize(200, 160)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._image = QImage()

    def set_image(self, image: QImage) -> None:
        self._image = image
        self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        if self._image.isNull():
            self.clear()
            return
        ratio = self.devicePixelRatioF()
        pix = QPixmap.fromImage(self._image).scaled(
            self.size() * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pix.setDevicePixelRatio(ratio)
        self.setPixmap(pix)


class MainWindow(QMainWindow):
    def __init__(self, magick: ip.Magick | None, magick_error: str = ""):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1120, 720)
        self.setMinimumSize(820, 560)
        self.setAcceptDrops(True)

        self.magick = magick
        self.items: list[ImageItem] = []
        self.loader = ThreadPoolExecutor(max_workers=4)
        self.cancel_event: threading.Event | None = None
        self.output_dir: str | None = None
        self.preview_token = 0
        self.formats = ["original"] + (magick.available_output_formats() if magick else [])

        self.signals = Signals(self)
        self.signals.loaded.connect(self._item_loaded)
        self.signals.preview.connect(self._preview_loaded)
        self.signals.scanned.connect(self._add_found)
        self.signals.item_done.connect(self._item_done)
        self.signals.batch_done.connect(self._batch_done)

        self._build_actions()
        self._build_toolbar()
        splitter = QSplitter(Qt.Horizontal, childrenCollapsible=False)
        splitter.addWidget(self._build_images_pane())
        splitter.addWidget(self._build_settings_pane())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([680, 440])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.addWidget(splitter, 1)
        layout.addLayout(self._build_action_bar())
        self.setCentralWidget(central)

        if magick is None:
            for w in (self.act_add_files, self.act_add_folder, self.empty_add_files,
                      self.empty_add_folder):
                w.setEnabled(False)
            self.empty_title.setText("ImageMagick not found")
            self.empty_text.setText(magick_error)
        self.update_state()

    # ------------------------------------------------------------- layout

    def _build_actions(self):
        style = self.style()

        def action(text, icon, slot, shortcut=None, tip=None):
            a = QAction(style.standardIcon(icon) if icon is not None else QIcon(), text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            self.addAction(a)
            return a

        self.act_add_files = action("Add Images", QStyle.SP_FileIcon, self.choose_files,
                                    "Ctrl+O", "Add image files (Ctrl+O)")
        self.act_add_folder = action("Add Folder", QStyle.SP_DirIcon, self.choose_folder,
                                     "Ctrl+Shift+O", "Add all images in a folder (Ctrl+Shift+O)")
        self.act_remove = action("Remove Selected", None, self.remove_selected, "Delete")
        self.act_clear = action("Clear List", None, self.clear_list)
        self.act_remove_failed = action("Remove Failed Images", None, self.remove_failed)
        self.act_start = action("Start Processing", None, self.start_processing, "Ctrl+Return")
        self.act_quit = action("Quit", None, self.close, "Ctrl+Q")
        self.act_shortcuts = action("Keyboard Shortcuts", None, self.show_shortcuts, "F1")
        self.act_about = action("About Magick Studio", None, self.show_about)

    def _build_toolbar(self):
        bar = QToolBar("Main", movable=False, floatable=False)
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(18, 18))
        bar.addAction(self.act_add_files)
        bar.addAction(self.act_add_folder)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        menu = QMenu(self)
        for a in (self.act_remove, self.act_clear, self.act_remove_failed):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_shortcuts)
        menu.addAction(self.act_about)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        more = QToolButton(text="Menu", popupMode=QToolButton.InstantPopup)
        more.setMenu(menu)
        more.setToolTip("Main menu")
        bar.addWidget(more)
        self.addToolBar(bar)

    def _build_images_pane(self) -> QWidget:
        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.list.setSpacing(2)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.list.addAction(self.act_remove)
        self.list.currentItemChanged.connect(lambda *_: self.show_preview())

        self.preview = PreviewLabel()
        self.preview_info = QLabel("Select an image to preview it", alignment=Qt.AlignCenter,
                                   wordWrap=True)
        secondary(self.preview_info)
        preview_box = QWidget()
        pv = QVBoxLayout(preview_box)
        pv.setContentsMargins(0, 6, 0, 0)
        pv.addWidget(self.preview, 1)
        pv.addWidget(self.preview_info)

        split = QSplitter(Qt.Vertical, childrenCollapsible=False)
        split.addWidget(self.list)
        split.addWidget(preview_box)
        split.setSizes([360, 280])

        # Empty state
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        self.empty_title = QLabel("No images", alignment=Qt.AlignCenter)
        font = self.empty_title.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        self.empty_title.setFont(font)
        self.empty_text = QLabel("Add images or a folder, or drag and drop them here.",
                                 alignment=Qt.AlignCenter, wordWrap=True)
        ev.addWidget(self.empty_title)
        ev.addWidget(self.empty_text)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.empty_add_files = QPushButton("Add Images")
        self.empty_add_files.setDefault(True)
        self.empty_add_files.clicked.connect(self.choose_files)
        self.empty_add_folder = QPushButton("Add Folder")
        self.empty_add_folder.clicked.connect(self.choose_folder)
        buttons.addWidget(self.empty_add_files)
        buttons.addWidget(self.empty_add_folder)
        buttons.addStretch(1)
        ev.addLayout(buttons)
        ev.addStretch(2)

        self.images_stack = QStackedWidget()
        self.images_stack.addWidget(empty)
        self.images_stack.addWidget(split)
        return self.images_stack

    def _build_settings_pane(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setSpacing(12)

        presets = QGroupBox("Presets")
        pl = QVBoxLayout(presets)
        self.web_button = QPushButton("Optimize for Web")
        self.web_button.setToolTip("WebP, max 1600 × 1600, quality 82, metadata removed, "
                                   "no upscaling")
        self.web_button.setMinimumHeight(34)
        self.web_button.clicked.connect(self.apply_web_preset)
        self.web_button.setEnabled("webp" in self.formats)
        pl.addWidget(self.web_button)
        row = QHBoxLayout()
        for key, label in (("high", "High Quality"), ("balanced", "Balanced"),
                           ("small", "Small File")):
            b = QPushButton(label)
            b.setToolTip(f"Quality {ip.QUALITY_PRESETS[key]}")
            b.clicked.connect(lambda _=False, q=ip.QUALITY_PRESETS[key]: self.quality.setValue(q))
            row.addWidget(b)
        pl.addLayout(row)
        v.addWidget(presets)

        output = QGroupBox("Output Format")
        of = QFormLayout(output)
        self.format_box = QComboBox()
        self.format_box.addItems([FORMAT_LABELS[f] for f in self.formats])
        self.format_box.currentIndexChanged.connect(lambda *_: self.update_state())
        of.addRow("Format", self.format_box)
        self.format_note = QLabel(wordWrap=True)
        secondary(self.format_note)
        if self.magick and "avif" not in self.formats:
            self.format_note.setText("AVIF is not available in this ImageMagick build.")
        of.addRow("", self.format_note)
        self.quality = QSlider(Qt.Horizontal, minimum=1, maximum=100, value=ip.DEFAULT_QUALITY)
        self.quality.setPageStep(5)
        self.quality_value = QLabel(str(ip.DEFAULT_QUALITY), minimumWidth=28)
        self.quality.valueChanged.connect(lambda val: self.quality_value.setText(str(val)))
        qrow = QHBoxLayout()
        qrow.addWidget(self.quality, 1)
        qrow.addWidget(self.quality_value)
        self.quality_label = QLabel("Quality")
        of.addRow(self.quality_label, qrow)
        self.quality_note = QLabel(wordWrap=True)
        secondary(self.quality_note)
        of.addRow("", self.quality_note)
        self.strip = QCheckBox("Remove metadata (EXIF, GPS); keeps colour profile", checked=True)
        of.addRow(self.strip)
        v.addWidget(output)

        self.resize_box = QGroupBox("Resize images", checkable=True, checked=False)
        self.resize_box.setToolTip("Off keeps the original dimensions")
        rf = QFormLayout(self.resize_box)
        self.width_box = QSpinBox(maximum=65535, value=1600, suffix=" px",
                                  specialValueText="Auto")
        self.height_box = QSpinBox(maximum=65535, value=0, suffix=" px",
                                   specialValueText="Auto")
        rf.addRow("Width", self.width_box)
        rf.addRow("Height", self.height_box)
        self.aspect = QCheckBox("Keep aspect ratio", checked=True)
        self.upscale = QCheckBox("Allow enlarging smaller images")
        rf.addRow(self.aspect)
        rf.addRow(self.upscale)
        v.addWidget(self.resize_box)

        dest = QGroupBox("Destination")
        dl = QVBoxLayout(dest)
        self.output_label = QLabel(wordWrap=True)
        self.output_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self.output_label)
        brow = QHBoxLayout()
        choose = QPushButton("Choose Folder…")
        choose.clicked.connect(self.choose_output)
        self.reset_output = QPushButton("Use Default")
        self.reset_output.clicked.connect(lambda: self.set_output_dir(None))
        brow.addWidget(choose)
        brow.addWidget(self.reset_output)
        brow.addStretch(1)
        dl.addLayout(brow)
        v.addWidget(dest)
        v.addStretch(1)
        self.set_output_dir(None)

        self.settings_panel = panel
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(340)
        return scroll

    def _build_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.status = QLabel()
        self.progress = QProgressBar(visible=False, minimumWidth=220)
        self.progress.setFormat("%v / %m")
        self.cancel_button = QPushButton("Cancel", visible=False)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.start_button = QPushButton("Start Processing")
        self.start_button.setMinimumHeight(34)
        self.start_button.setMinimumWidth(160)
        self.start_button.setDefault(True)
        self.start_button.setToolTip("Start processing (Ctrl+Enter)")
        self.start_button.clicked.connect(self.start_processing)
        bar.addWidget(self.status, 1)
        bar.addWidget(self.progress)
        bar.addWidget(self.cancel_button)
        bar.addWidget(self.start_button)
        return bar

    # -------------------------------------------------------------- state

    @property
    def busy(self) -> bool:
        return self.cancel_event is not None

    def selected_format(self) -> str:
        return self.formats[max(0, self.format_box.currentIndex())]

    def update_state(self) -> None:
        valid = sum(1 for it in self.items if it.info)
        pending = sum(1 for it in self.items if not it.info and not it.error)
        invalid = sum(1 for it in self.items if it.error)
        self.images_stack.setCurrentIndex(1 if self.items else 0)

        fmt = self.selected_format()
        uses_quality = fmt != "png"
        self.quality.setEnabled(uses_quality and not self.busy)
        self.quality_label.setEnabled(uses_quality)
        note = "" if uses_quality else "Not used: PNG is lossless."
        if fmt == "webp" and self.magick and not self.magick.webp_quality_works() \
                and not self.magick.cwebp:
            note = "This ImageMagick ignores WebP quality."
        self.quality_note.setText(note)
        self.quality_note.setVisible(bool(note))
        self.format_note.setVisible(bool(self.format_note.text()))

        for a in (self.act_remove, self.act_clear, self.act_remove_failed):
            a.setEnabled(bool(self.items) and not self.busy)
        can_add = self.magick is not None and not self.busy
        for w in (self.act_add_files, self.act_add_folder, self.empty_add_files,
                  self.empty_add_folder):
            w.setEnabled(can_add)
        ready = valid > 0 and pending == 0 and not self.busy
        self.start_button.setEnabled(ready)
        self.act_start.setEnabled(ready)
        if not self.busy:
            parts = [f"{valid} image{'s' if valid != 1 else ''} ready"]
            if pending:
                parts.append(f"{pending} loading")
            if invalid:
                parts.append(f"{invalid} unsupported")
            self.status.setText(", ".join(parts) if self.items else "No images")

    def set_busy(self, busy: bool) -> None:
        self.settings_panel.setEnabled(not busy)
        self.start_button.setVisible(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(True)
        self.progress.setVisible(busy)
        self.update_state()

    def collect_settings(self) -> ip.Settings:
        return ip.Settings(
            output_format=self.selected_format(),
            resize=self.resize_box.isChecked(),
            width=self.width_box.value(),
            height=self.height_box.value(),
            keep_aspect=self.aspect.isChecked(),
            allow_upscale=self.upscale.isChecked(),
            quality=self.quality.value(),
            strip_metadata=self.strip.isChecked(),
            high_effort=True,
            output_dir=self.output_dir,
        )

    def apply_web_preset(self) -> None:
        s = ip.web_preset()
        self.format_box.setCurrentIndex(self.formats.index(s.output_format))
        self.resize_box.setChecked(True)
        self.width_box.setValue(s.width)
        self.height_box.setValue(s.height)
        self.aspect.setChecked(s.keep_aspect)
        self.upscale.setChecked(s.allow_upscale)
        self.quality.setValue(s.quality)
        self.strip.setChecked(s.strip_metadata)
        self.notify("Web preset applied: WebP, max 1600 × 1600, quality 82")

    def notify(self, text: str) -> None:
        self.statusBar().showMessage(text, 5000)

    def set_output_dir(self, path: str | None) -> None:
        self.output_dir = path
        self.output_label.setText(
            os.path.normpath(path) if path else "An “output” folder next to each image")
        self.reset_output.setEnabled(path is not None)

    # ------------------------------------------------------------- import

    def choose_files(self):
        if self.busy or not self.magick:
            return
        patterns = " ".join(f"*{e}" for e in sorted(ip.SUPPORTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add Images", "", f"Images ({patterns});;All files (*)")
        self.add_paths(files)

    def choose_folder(self):
        if self.busy or not self.magick:
            return
        folder = QFileDialog.getExistingDirectory(self, "Add Folder")
        if folder:
            self.add_paths([folder])

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if not folder:
            return
        try:
            with tempfile.TemporaryFile(dir=folder):
                pass
        except OSError:
            QMessageBox.warning(self, APP_NAME, "That folder is not writable. "
                                "Please choose another folder.")
            return
        self.set_output_dir(folder)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self.magick and not self.busy:
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        """Add files and folders. Folder scanning runs in the background."""
        if not paths or not self.magick:
            return
        exclude = [self.output_dir] if self.output_dir else []

        def collect():
            found, skipped, had_folder = [], 0, False
            for p in map(Path, paths):
                if p.is_dir():
                    had_folder = True
                    found.extend(ip.scan_folder(p, exclude=exclude))
                elif ip.is_supported(p):
                    found.append(p.absolute())
                else:
                    skipped += 1
            self.signals.scanned.emit(found, skipped, had_folder)

        self.notify("Scanning…")
        threading.Thread(target=collect, daemon=True).start()

    def _add_found(self, found: list[Path], skipped: int, had_folder: bool) -> None:
        known = {os.path.normcase(os.path.realpath(it.path)) for it in self.items}
        added = []
        for path in found:
            key = os.path.normcase(os.path.realpath(path))
            if key in known:
                continue
            known.add(key)
            widget = QListWidgetItem(self.style().standardIcon(QStyle.SP_FileIcon), "")
            item = ImageItem(path, widget)
            widget.setData(Qt.UserRole, item)
            item.refresh()
            self.items.append(item)
            self.list.addItem(widget)
            self.loader.submit(self._load_item, item)
            added.append(item)
        if not added and had_folder and not found:
            self.notify("No supported images found in that folder")
        elif not added and found:
            self.notify("Those images are already in the list")
        elif skipped:
            self.notify(f"Skipped {skipped} unsupported file{'s' if skipped != 1 else ''}")
        if added and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self.update_state()

    def _load_item(self, item: ImageItem) -> None:
        try:
            info, error = self.magick.identify(item.path), ""
        except ip.MagickError as exc:
            info, error = None, f"Unsupported or damaged image ({exc})"
        thumb = load_image(self.magick, item.path, THUMB_SIZE * 2) if info else QImage()
        self.signals.loaded.emit(item, info, error, thumb)

    def _item_loaded(self, item: ImageItem, info, error: str, thumb: QImage) -> None:
        if item not in self.items:
            return
        item.info, item.error = info, error
        if not thumb.isNull():
            item.widget.setIcon(QIcon(QPixmap.fromImage(thumb)))
        elif error:
            item.widget.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxWarning))
        item.refresh()
        if self.current_item() is item:
            self.show_preview()
        self.update_state()

    def current_item(self) -> ImageItem | None:
        widget = self.list.currentItem()
        return widget.data(Qt.UserRole) if widget else None

    def remove_items(self, items: list[ImageItem]) -> None:
        if self.busy:
            return
        for item in items:
            if item in self.items:
                self.items.remove(item)
                self.list.takeItem(self.list.row(item.widget))
        if not self.items:
            self.preview.set_image(QImage())
            self.preview_info.setText("Select an image to preview it")
        self.update_state()

    def remove_selected(self):
        self.remove_items([w.data(Qt.UserRole) for w in self.list.selectedItems()])

    def clear_list(self):
        self.remove_items(list(self.items))

    def remove_failed(self):
        self.remove_items([i for i in self.items
                           if i.error or (i.result is not None and not i.result.ok)])

    # ------------------------------------------------------------ preview

    def show_preview(self) -> None:
        self.preview_token += 1
        item = self.current_item()
        if item is None:
            return
        self.preview_info.setText(f"{item.path.name}\n{item.describe()}")
        if not item.info:
            self.preview.set_image(QImage())
            return
        token = self.preview_token
        self.loader.submit(lambda: self.signals.preview.emit(
            token, load_image(self.magick, item.path, PREVIEW_SIZE)))

    def _preview_loaded(self, token: int, image: QImage) -> None:
        if token == self.preview_token:
            self.preview.set_image(image)

    # --------------------------------------------------------- processing

    def start_processing(self) -> None:
        items = [it for it in self.items if it.info]
        if not items or self.busy or not self.start_button.isEnabled():
            return
        settings = self.collect_settings()
        try:
            settings.validate()
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.cancel_event = threading.Event()
        for it in self.items:
            it.result = None
            it.refresh()
        self.batch = items
        self.progress.setRange(0, len(items))
        self.progress.setValue(0)
        self.set_busy(True)
        self.status.setText("Processing…")
        jobs = [(it, it.path) for it in items]   # plain data for the worker
        threading.Thread(target=self._run_batch, args=(jobs, settings, self.cancel_event),
                         daemon=True).start()

    def _run_batch(self, jobs, settings, cancel_event) -> None:
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(ip.process_image, self.magick, path, settings,
                                   cancel_event): item for item, path in jobs}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # defensive: never lose the batch
                    result = ip.ProcessResult(source=item.path, error=str(exc))
                self.signals.item_done.emit(item, result)
        self.signals.batch_done.emit(cancel_event.is_set())

    def _item_done(self, item: ImageItem, result: ip.ProcessResult) -> None:
        item.result = result
        item.refresh()
        self.progress.setValue(sum(1 for it in self.batch if it.result is not None))

    def _batch_done(self, cancelled: bool) -> None:
        self.cancel_event = None
        self.set_busy(False)
        results = [it.result for it in self.batch if it.result is not None]
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok and r.error != "Cancelled"]
        skipped = len(self.batch) - len(ok) - len(failed)
        summary = f"{len(ok)} succeeded, {len(failed)} failed" + \
            (f", {skipped} cancelled" if skipped else "")
        self.status.setText(summary)
        self.last_summary = summary
        self.last_output_dirs = sorted({r.output.parent for r in ok})

        lines = [summary]
        if ok:
            orig = sum(r.original_size for r in ok)
            new = sum(r.output_size for r in ok)
            saved = (1 - new / orig) * 100 if orig else 0
            lines.append(f"{ip.human_size(orig)} → {ip.human_size(new)} "
                         f"({abs(saved):.0f}% {'smaller' if saved >= 0 else 'larger'})")
        for r in failed[:5]:
            lines.append(f"• {r.source.name}: {r.error}")
        if len(failed) > 5:
            lines.append(f"• … and {len(failed) - 5} more")
        if os.environ.get("MAGICK_STUDIO_NO_DIALOGS"):   # automated tests
            return
        box = QMessageBox(QMessageBox.Information, APP_NAME,
                          "Processing cancelled" if cancelled else "Processing complete",
                          parent=self)
        box.setInformativeText("\n".join(lines))
        open_button = None
        if len(self.last_output_dirs) == 1:
            open_button = box.addButton("Open Output Folder", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if open_button is not None and box.clickedButton() is open_button:
            self.open_output()

    def cancel_processing(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("Cancelling…")

    def open_output(self) -> None:
        for folder in getattr(self, "last_output_dirs", []):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -------------------------------------------------------------- misc

    def show_shortcuts(self):
        QMessageBox.information(self, "Keyboard Shortcuts",
                                "Ctrl+O\tAdd images\nCtrl+Shift+O\tAdd folder\n"
                                "Delete\tRemove selected images\nCtrl+Enter\tStart processing\n"
                                "F1\tShow shortcuts\nCtrl+Q\tQuit")

    def show_about(self):
        engine = ("ImageMagick " + str(self.magick.version) + " — " + self.magick.convert_cmd[0]
                  if self.magick else "ImageMagick not found")
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {ip.APP_VERSION}</h3>"
                          "<p>Resize, compress, convert and optimize images with ImageMagick.</p>"
                          f"<p><small>{engine}</small></p>"
                          "<p><a href='https://github.com/yassermaina/magick-studio'>"
                          "github.com/yassermaina/magick-studio</a> · MIT License</p>")

    def closeEvent(self, event):
        if self.cancel_event:
            self.cancel_event.set()
        self.loader.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


# ------------------------------------------------------------------ startup

def create_app(argv: list[str]) -> QApplication:
    if sys.platform == "win32":
        try:  # group taskbar buttons under our icon (matches the installer shortcut)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(ip.APP_VERSION)
    # The classic Windows 10 style has no dark mode; Fusion follows the system theme.
    if app.style().name().lower() == "windowsvista":
        app.setStyle("Fusion")
    for icon in (resource_path("assets", "icon.ico"), resource_path("assets", "icon.svg")):
        if icon.exists():
            app.setWindowIcon(QIcon(str(icon)))
            break
    return app


def detect_magick() -> tuple[ip.Magick | None, str]:
    try:
        return ip.Magick.detect(), ""
    except ip.MagickError as exc:
        return None, str(exc)


def smoke_test(input_dir: str, output_dir: str, report_path: str) -> int:
    """Process every image in input_dir with the web preset; write a JSON report."""
    report: dict = {"version": ip.APP_VERSION, "frozen": bool(getattr(sys, "frozen", False))}
    magick, error = detect_magick()
    report["magick"] = magick.convert_cmd[0] if magick else None
    report["magick_error"] = error
    ok = magick is not None
    if magick:
        report["output_formats"] = magick.available_output_formats()
        settings = ip.web_preset()
        settings.output_dir = output_dir
        report["results"] = []
        for src in ip.scan_folder(input_dir):
            r = ip.process_image(magick, src, settings)
            entry = {"source": str(src), "ok": r.ok, "error": r.error}
            if r.ok:
                info = magick.identify(r.output)
                entry.update(output=str(r.output), width=info.width, height=info.height,
                             format=info.format, saved_percent=round(r.saved_percent, 1))
            report["results"].append(entry)
        ok = bool(report["results"]) and all(e["ok"] for e in report["results"])
    report["ok"] = ok
    Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    return 0 if ok else 1


def gui_check(report_path: str, paths: list[str], app: QApplication,
              win: MainWindow) -> None:
    """Write a startup report once the window is up and images loaded, then quit."""
    deadline = [60]

    def check():
        deadline[0] -= 1
        loading = any(not it.info and not it.error for it in win.items)
        if (loading or (paths and not win.items)) and deadline[0] > 0:
            QTimer.singleShot(500, check)
            return
        shot = str(Path(report_path).with_suffix(".png"))
        win.grab().save(shot)
        Path(report_path).write_text(json.dumps({
            "visible": win.isVisible(), "title": win.windowTitle(),
            "style": app.style().name(), "platform": app.platformName(),
            "color_scheme": str(app.styleHints().colorScheme()),
            "dpr": win.devicePixelRatioF(),
            "magick": win.magick.convert_cmd[0] if win.magick else None,
            "formats": win.formats, "items": len(win.items),
            "valid": sum(1 for it in win.items if it.info),
            "thumbnails": sum(1 for it in win.items if not it.widget.icon().isNull()),
            "status": win.status.text(), "screenshot": shot,
        }, indent=2), encoding="utf-8")
        app.quit()

    QTimer.singleShot(1500, check)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) >= 5 and argv[1] == "--smoke-test":
        return smoke_test(argv[2], argv[3], argv[4])
    app = create_app(argv)
    magick, error = detect_magick()
    win = MainWindow(magick, error)
    win.show()
    report = None
    if len(argv) >= 3 and argv[1] == "--gui-check":
        report, paths = argv[2], argv[3:]
        gui_check(report, paths, app, win)
    else:
        paths = argv[1:]
    if magick and paths:
        win.add_paths(paths)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
