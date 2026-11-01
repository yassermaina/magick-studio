#!/usr/bin/env python3
"""Magick Studio — a simple GTK4/Libadwaita front end for ImageMagick."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import image_processor as ip  # noqa: E402

APP_ID = "io.github.magickstudio.MagickStudio"
VERSION = ip.APP_VERSION
THUMB_SIZE = 48
PREVIEW_SIZE = 720

FORMAT_LABELS = {
    "original": "Keep Original",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WebP",
    "avif": "AVIF",
}


def load_texture(magick: ip.Magick, path: Path, size: int) -> Gdk.Texture | None:
    """Load a scaled, correctly oriented texture. Safe to call from a worker thread."""
    pixbuf = None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    except GLib.Error:
        # GdkPixbuf has no loader for this format (e.g. AVIF/HEIC): use ImageMagick.
        try:
            with tempfile.TemporaryDirectory(prefix="magick-studio-") as tmp:
                thumb = os.path.join(tmp, "thumb.png")
                ip.thumbnail(magick, path, size, thumb)
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(thumb)
        except (ip.MagickError, GLib.Error, OSError):
            return None
    fmt = (Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha()
           else Gdk.MemoryFormat.R8G8B8)
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), fmt,
                                 pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())


class ImageItem:
    """One imported image and its list row."""

    def __init__(self, path: Path):
        self.path = path
        self.info: ip.ImageInfo | None = None
        self.error = ""
        self.result: ip.ProcessResult | None = None

        self.row = Adw.ActionRow(title=GLib.markup_escape_text(path.name),
                                 subtitle="Reading…")
        self.row.set_title_lines(1)
        self.row.set_subtitle_lines(2)
        self.row.set_tooltip_text(str(path))
        self.row.item = self

        self.thumb = Gtk.Image(pixel_size=THUMB_SIZE, icon_name="image-x-generic-symbolic",
                               valign=Gtk.Align.CENTER)
        self.row.add_prefix(self.thumb)

        self.status = Gtk.Image(visible=False)
        self.row.add_suffix(self.status)
        self.remove_button = Gtk.Button(icon_name="list-remove-symbolic",
                                        valign=Gtk.Align.CENTER,
                                        tooltip_text="Remove from list")
        self.remove_button.add_css_class("flat")
        self.row.add_suffix(self.remove_button)

    def describe(self) -> str:
        if self.error:
            return self.error
        if not self.info:
            return "Reading…"
        return (f"{self.info.width} × {self.info.height}  ·  {self.info.format}  ·  "
                f"{ip.human_size(self.info.size)}")

    def refresh(self) -> None:
        text = self.describe()
        r = self.result
        if r is not None and r.ok:
            change = r.saved_percent
            word = "smaller" if change >= 0 else "larger"
            text += (f"\n→ {r.output.name}  ·  {ip.human_size(r.output_size)}  "
                     f"({abs(change):.0f}% {word})")
        elif r is not None:
            text += f"\nFailed: {r.error}"
        self.row.set_subtitle(GLib.markup_escape_text(text))

        if self.error or (r is not None and not r.ok):
            self.status.set_from_icon_name("dialog-error-symbolic")
            self.status.set_css_classes(["error"])
            self.status.set_tooltip_text(self.error or r.error)
            self.status.set_visible(True)
        elif r is not None:
            self.status.set_from_icon_name("object-select-symbolic")
            self.status.set_css_classes(["success"])
            self.status.set_tooltip_text(str(r.output))
            self.status.set_visible(True)
        else:
            self.status.set_visible(False)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, magick: ip.Magick | None, magick_error: str):
        super().__init__(application=app, title="Magick Studio")
        self.set_default_size(1100, 720)
        self.set_size_request(760, 520)

        self.magick = magick
        self.items: list[ImageItem] = []
        self.loader = ThreadPoolExecutor(max_workers=4)
        self.cancel_event: threading.Event | None = None
        self.output_dir: str | None = None
        self.preview_token = 0
        self.formats = ["original"] + (magick.available_output_formats() if magick else [])

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(self._build_body())
        toolbar.add_bottom_bar(self._build_action_bar())
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        self.connect("close-request", self.on_close_request)

        self.update_state()
        if magick is None:
            self.add_images_button.set_sensitive(False)
            self.add_folder_button.set_sensitive(False)
            self.empty_page.set_child(None)
            self.empty_page.set_title("ImageMagick Not Found")
            self.empty_page.set_icon_name("dialog-warning-symbolic")
            self.empty_page.set_description(GLib.markup_escape_text(magick_error))

    # ----------------------------------------------------------------- layout

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Magick Studio",
                                                subtitle="Resize · Compress · Convert"))

        self.add_images_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Images"),
            tooltip_text="Add image files (Ctrl+O)")
        self.add_images_button.connect("clicked", lambda *_: self.choose_files())
        self.add_folder_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="folder-symbolic", label="Add Folder"),
            tooltip_text="Add all images in a folder")
        self.add_folder_button.connect("clicked", lambda *_: self.choose_folder())

        menu = Gio.Menu()
        menu.append("Clear List", "win.clear")
        menu.append("Remove Failed Images", "win.remove-failed")
        menu.append("Keyboard Shortcuts", "app.shortcuts")
        menu.append("About Magick Studio", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text="Main Menu", primary=True)

        header.pack_end(menu_button)
        header.pack_end(self.add_folder_button)
        header.pack_end(self.add_images_button)

        for name, callback in (("clear", self.clear_list),
                               ("remove-failed", self.remove_failed)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
        return header

    def _build_body(self) -> Gtk.Widget:
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                          shrink_start_child=False, shrink_end_child=False)
        paned.set_start_child(self._build_images_pane())
        paned.set_end_child(self._build_settings_pane())
        paned.set_position(640)
        return paned

    def _build_images_pane(self) -> Gtk.Widget:
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self.on_row_selected)
        list_clamp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        list_clamp.append(self.listbox)
        list_scroll = Gtk.ScrolledWindow(child=list_clamp, vexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.preview = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN,
                                   hexpand=True, vexpand=True)
        self.preview_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                       css_classes=["dim-label", "caption"])
        self.preview_label.set_label("Select an image to preview it")
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                              margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        preview_box.append(self.preview)
        preview_box.append(self.preview_label)

        split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL,
                          shrink_start_child=False, shrink_end_child=False)
        split.set_start_child(list_scroll)
        split.set_end_child(preview_box)
        split.set_position(330)

        self.empty_page = Adw.StatusPage(
            icon_name="image-x-generic-symbolic", title="No Images",
            description="Add images or a folder, or drag and drop them here.")
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for label, cb, css in (("Add Images", self.choose_files, "suggested-action"),
                               ("Add Folder", self.choose_folder, None)):
            b = Gtk.Button(label=label, css_classes=["pill"] + ([css] if css else []))
            b.connect("clicked", lambda *_a, c=cb: c())
            buttons.append(b)
        self.empty_page.set_child(buttons)

        self.images_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.images_stack.add_named(self.empty_page, "empty")
        self.images_stack.add_named(split, "list")
        self.images_stack.set_size_request(360, -1)
        return self.images_stack

    def _build_settings_pane(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        self.settings_page = page

        # Presets
        presets = Adw.PreferencesGroup(title="Presets")
        web = Gtk.Button(label="Optimize for Web", css_classes=["pill", "suggested-action"],
                         tooltip_text="WebP, max 1600 × 1600, quality 82, "
                                      "metadata removed, no upscaling",
                         margin_bottom=12)
        web.connect("clicked", lambda *_: self.apply_web_preset())
        if "webp" not in self.formats:
            web.set_sensitive(False)
        presets.add(web)
        quality_box = Gtk.Box(css_classes=["linked"], homogeneous=True)
        for key, label in (("high", "High Quality"), ("balanced", "Balanced"),
                           ("small", "Small File")):
            b = Gtk.Button(label=label,
                           tooltip_text=f"Quality {ip.QUALITY_PRESETS[key]}")
            b.connect("clicked", lambda *_a, q=ip.QUALITY_PRESETS[key]:
                      self.quality_adj.set_value(q))
            quality_box.append(b)
        presets.add(quality_box)
        page.add(presets)

        # Format and quality
        output = Adw.PreferencesGroup(title="Output Format")
        self.format_row = Adw.ComboRow(
            title="Format",
            model=Gtk.StringList.new([FORMAT_LABELS[f] for f in self.formats]))
        if self.magick and "avif" not in self.formats:
            self.format_row.set_subtitle("AVIF unavailable in this ImageMagick build")
        self.format_row.connect("notify::selected", lambda *_: self.update_state())
        output.add(self.format_row)

        self.quality_adj = Gtk.Adjustment(value=ip.DEFAULT_QUALITY, lower=1, upper=100,
                                          step_increment=1, page_increment=5)
        scale = Gtk.Scale(adjustment=self.quality_adj, digits=0, draw_value=True,
                          value_pos=Gtk.PositionType.LEFT, hexpand=True,
                          valign=Gtk.Align.CENTER)
        scale.set_size_request(160, -1)
        self.quality_adj.connect("value-changed",
                                 lambda a: a.set_value(round(a.get_value())))
        self.quality_row = Adw.ActionRow(title="Quality")
        self.quality_row.add_suffix(scale)
        output.add(self.quality_row)

        self.strip_row = Adw.SwitchRow(title="Remove Metadata", active=True,
                                       subtitle="Strips EXIF/GPS, keeps colour profile")
        output.add(self.strip_row)
        page.add(output)

        # Resize
        resize = Adw.PreferencesGroup(title="Resize")
        self.resize_row = Adw.ExpanderRow(title="Resize Images", show_enable_switch=True,
                                          enable_expansion=False, expanded=True,
                                          subtitle="Off keeps the original dimensions")
        self.width_row = Adw.SpinRow.new_with_range(0, 65535, 1)
        self.width_row.set_title("Width")
        self.width_row.set_subtitle("Pixels, 0 = automatic")
        self.width_row.set_value(1600)
        self.height_row = Adw.SpinRow.new_with_range(0, 65535, 1)
        self.height_row.set_title("Height")
        self.height_row.set_subtitle("Pixels, 0 = automatic")
        self.height_row.set_value(0)
        self.aspect_row = Adw.SwitchRow(title="Keep Aspect Ratio", active=True)
        self.upscale_row = Adw.SwitchRow(title="Allow Enlarging",
                                         subtitle="Upscale images smaller than the target")
        for row in (self.width_row, self.height_row, self.aspect_row, self.upscale_row):
            self.resize_row.add_row(row)
        resize.add(self.resize_row)
        page.add(resize)

        # Destination
        dest = Adw.PreferencesGroup(title="Destination")
        self.output_row = Adw.ActionRow(title="Output Folder")
        self.output_row.set_subtitle_lines(2)
        choose = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Choose output folder", css_classes=["flat"])
        choose.connect("clicked", lambda *_: self.choose_output_dir())
        self.reset_output = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                                       tooltip_text="Use default “output” folder",
                                       css_classes=["flat"])
        self.reset_output.connect("clicked", lambda *_: self.set_output_dir(None))
        self.output_row.add_suffix(self.reset_output)
        self.output_row.add_suffix(choose)
        self.output_row.set_activatable_widget(choose)
        dest.add(self.output_row)
        page.add(dest)
        self.set_output_dir(None)

        page.set_size_request(380, -1)
        return page

    def _build_action_bar(self) -> Gtk.ActionBar:
        bar = Gtk.ActionBar()
        self.status_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, margin_start=6)
        bar.pack_start(self.status_label)

        self.start_button = Gtk.Button(label="Start Processing",
                                       css_classes=["suggested-action", "pill"])
        self.start_button.connect("clicked", lambda *_: self.start_processing())
        self.cancel_button = Gtk.Button(label="Cancel", css_classes=["destructive-action", "pill"],
                                        visible=False)
        self.cancel_button.connect("clicked", lambda *_: self.cancel_processing())
        self.progress = Gtk.ProgressBar(show_text=True, valign=Gtk.Align.CENTER,
                                        visible=False, width_request=240)
        bar.pack_end(self.start_button)
        bar.pack_end(self.cancel_button)
        bar.pack_end(self.progress)
        return bar

    # ---------------------------------------------------------------- state

    @property
    def busy(self) -> bool:
        return self.cancel_event is not None

    def selected_format(self) -> str:
        return self.formats[self.format_row.get_selected()]

    def update_state(self) -> None:
        valid = sum(1 for it in self.items if it.info)
        pending = sum(1 for it in self.items if not it.info and not it.error)
        invalid = sum(1 for it in self.items if it.error)
        self.images_stack.set_visible_child_name("list" if self.items else "empty")

        fmt = self.selected_format()
        uses_quality = fmt in ("jpeg", "webp", "avif", "original")
        self.quality_row.set_sensitive(uses_quality)
        subtitle = "" if uses_quality else "Not used: PNG is lossless"
        if fmt == "webp" and self.magick and not self.magick.webp_quality_works() \
                and not self.magick.cwebp:
            subtitle = "Ignored by this ImageMagick for WebP; install the “webp” package"
        self.quality_row.set_subtitle(subtitle)

        if not self.busy:
            parts = [f"{valid} image{'s' if valid != 1 else ''} ready"]
            if pending:
                parts.append(f"{pending} loading")
            if invalid:
                parts.append(f"{invalid} unsupported")
            self.status_label.set_label(", ".join(parts) if self.items else "No images")
            self.start_button.set_sensitive(valid > 0 and pending == 0)

    def set_busy(self, busy: bool) -> None:
        self.settings_page.set_sensitive(not busy)
        self.add_images_button.set_sensitive(not busy)
        self.add_folder_button.set_sensitive(not busy)
        for it in self.items:
            it.remove_button.set_sensitive(not busy)
        for name in ("clear", "remove-failed"):
            self.lookup_action(name).set_enabled(not busy)
        self.start_button.set_visible(not busy)
        self.cancel_button.set_visible(busy)
        self.cancel_button.set_sensitive(True)
        self.progress.set_visible(busy)

    def collect_settings(self) -> ip.Settings:
        return ip.Settings(
            output_format=self.selected_format(),
            resize=self.resize_row.get_enable_expansion(),
            width=int(self.width_row.get_value()),
            height=int(self.height_row.get_value()),
            keep_aspect=self.aspect_row.get_active(),
            allow_upscale=self.upscale_row.get_active(),
            quality=int(self.quality_adj.get_value()),
            strip_metadata=self.strip_row.get_active(),
            high_effort=True,
            output_dir=self.output_dir,
        )

    def apply_web_preset(self) -> None:
        s = ip.web_preset()
        self.format_row.set_selected(self.formats.index(s.output_format))
        self.resize_row.set_enable_expansion(True)
        self.width_row.set_value(s.width)
        self.height_row.set_value(s.height)
        self.aspect_row.set_active(s.keep_aspect)
        self.upscale_row.set_active(s.allow_upscale)
        self.quality_adj.set_value(s.quality)
        self.strip_row.set_active(s.strip_metadata)
        self.toast("Web preset applied: WebP, max 1600 × 1600, quality 82")

    def set_output_dir(self, path: str | None) -> None:
        self.output_dir = path
        self.output_row.set_subtitle(GLib.markup_escape_text(
            path or "“output” folder next to each image"))
        self.reset_output.set_visible(path is not None)

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    # ---------------------------------------------------------------- import

    def _image_filter(self) -> Gio.ListStore:
        f = Gtk.FileFilter(name="Images")
        for ext in sorted(ip.SUPPORTED_EXTENSIONS):
            f.add_suffix(ext.lstrip("."))
        f.add_mime_type("image/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        return store

    def choose_files(self) -> None:
        if self.busy or not self.magick:
            return
        dialog = Gtk.FileDialog(title="Add Images", filters=self._image_filter())
        dialog.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # dismissed
        self.add_paths([f.get_path() for f in files if f.get_path()])

    def choose_folder(self) -> None:
        if self.busy or not self.magick:
            return
        dialog = Gtk.FileDialog(title="Add Folder")
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder and folder.get_path():
            self.add_paths([folder.get_path()])

    def choose_output_dir(self) -> None:
        dialog = Gtk.FileDialog(title="Choose Output Folder")
        dialog.select_folder(self, None, self._on_output_chosen)

    def _on_output_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = folder.get_path() if folder else None
        if not path:
            self.toast("Please choose a local folder")
        elif not os.access(path, os.W_OK):
            self.toast("That folder is not writable")
        else:
            self.set_output_dir(path)

    def on_drop(self, _target, value, _x, _y) -> bool:
        if self.busy or not self.magick:
            return False
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        self.add_paths(paths)
        return bool(paths)

    def add_paths(self, paths: list[str]) -> None:
        """Add files and folders. Folder scanning happens in the background."""
        exclude = [self.output_dir] if self.output_dir else []

        def collect():
            found, skipped = [], 0
            for p in paths:
                p = Path(p)
                if p.is_dir():
                    found.extend(ip.scan_folder(p, exclude=exclude))
                elif ip.is_supported(p):
                    found.append(p.absolute())
                else:
                    skipped += 1
            GLib.idle_add(self._add_found, found, skipped, any(Path(p).is_dir() for p in paths))

        self.status_label.set_label("Scanning…")
        threading.Thread(target=collect, daemon=True).start()

    def _add_found(self, found: list[Path], skipped: int, had_folder: bool) -> bool:
        known = {os.path.realpath(it.path) for it in self.items}
        added = 0
        for path in found:
            real = os.path.realpath(path)
            if real in known:
                continue
            known.add(real)
            item = ImageItem(path)
            item.remove_button.connect("clicked", lambda *_a, i=item: self.remove_item(i))
            self.items.append(item)
            self.listbox.append(item.row)
            self.loader.submit(self._load_item, item)
            added += 1
        if added == 0 and had_folder and not found:
            self.toast("No supported images found in that folder")
        elif added == 0 and found:
            self.toast("Those images are already in the list")
        elif skipped:
            self.toast(f"Skipped {skipped} unsupported file{'s' if skipped != 1 else ''}")
        if added and self.listbox.get_selected_row() is None:
            self.listbox.select_row(self.items[0].row)
        self.update_state()
        return False

    def _load_item(self, item: ImageItem) -> None:
        try:
            info = self.magick.identify(item.path)
            error = ""
        except ip.MagickError as exc:
            info, error = None, f"Unsupported or damaged image ({exc})"
        texture = load_texture(self.magick, item.path, THUMB_SIZE * 2) if info else None
        GLib.idle_add(self._item_loaded, item, info, error, texture)

    def _item_loaded(self, item: ImageItem, info, error: str, texture) -> bool:
        item.info, item.error = info, error
        if texture:
            item.thumb.set_from_paintable(texture)
        elif error:
            item.thumb.set_from_icon_name("image-missing-symbolic")
        item.refresh()
        if self.listbox.get_selected_row() is item.row:
            self.on_row_selected(self.listbox, item.row)
        self.update_state()
        return False

    def remove_item(self, item: ImageItem) -> None:
        if self.busy:
            return
        self.items.remove(item)
        self.listbox.remove(item.row)
        if not self.items:
            self.preview.set_paintable(None)
            self.preview_label.set_label("Select an image to preview it")
        self.update_state()

    def clear_list(self) -> None:
        for item in list(self.items):
            self.remove_item(item)

    def remove_failed(self) -> None:
        for item in [i for i in self.items
                     if i.error or (i.result is not None and not i.result.ok)]:
            self.remove_item(item)

    # --------------------------------------------------------------- preview

    def on_row_selected(self, _listbox, row) -> None:
        self.preview_token += 1
        if row is None:
            return
        item: ImageItem = row.item
        self.preview_label.set_label(f"{item.path.name}\n{item.describe()}")
        if not item.info:
            self.preview.set_paintable(None)
            return
        token = self.preview_token

        def load():
            texture = load_texture(self.magick, item.path, PREVIEW_SIZE)
            GLib.idle_add(self._preview_loaded, token, texture)

        self.loader.submit(load)

    def _preview_loaded(self, token: int, texture) -> bool:
        if token == self.preview_token:
            self.preview.set_paintable(texture)
        return False

    # ------------------------------------------------------------ processing

    def start_processing(self) -> None:
        items = [it for it in self.items if it.info]
        if not items or self.busy:
            return
        settings = self.collect_settings()
        try:
            settings.validate()
        except ValueError as exc:
            self.toast(str(exc))
            return

        self.cancel_event = threading.Event()
        for it in self.items:
            it.result = None
            it.refresh()
        self.set_busy(True)
        self.progress.set_fraction(0)
        self.progress.set_text(f"0 / {len(items)}")
        self.status_label.set_label("Processing…")
        threading.Thread(target=self._run_batch, args=(items, settings, self.cancel_event),
                         daemon=True).start()

    def _run_batch(self, items, settings, cancel_event) -> None:
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(ip.process_image, self.magick, it.path, settings,
                                   cancel_event): it for it in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # defensive: never kill the batch
                    result = ip.ProcessResult(source=item.path, error=str(exc))
                GLib.idle_add(self._item_done, item, result, len(items))
        GLib.idle_add(self._batch_done, items, cancel_event.is_set())

    def _item_done(self, item: ImageItem, result: ip.ProcessResult, total: int) -> bool:
        item.result = result
        item.refresh()
        done = sum(1 for it in self.items if it.result is not None)
        self.progress.set_fraction(done / total)
        self.progress.set_text(f"{done} / {total}")
        return False

    def _batch_done(self, items: list[ImageItem], cancelled: bool) -> bool:
        self.cancel_event = None
        self.set_busy(False)
        results = [it.result for it in items if it.result is not None]
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok and r.error != "Cancelled"]
        skipped = len(items) - len(ok) - len(failed)
        self.update_state()

        orig = sum(r.original_size for r in ok)
        new = sum(r.output_size for r in ok)
        lines = [f"{len(ok)} succeeded, {len(failed)} failed"
                 + (f", {skipped} cancelled" if skipped else "")]
        if ok:
            saved = (1 - new / orig) * 100 if orig else 0
            lines.append(f"{ip.human_size(orig)} → {ip.human_size(new)} "
                         f"({abs(saved):.0f}% {'smaller' if saved >= 0 else 'larger'})")
        if failed:
            lines.append("")
            for r in failed[:5]:
                lines.append(f"• {r.source.name}: {r.error}")
            if len(failed) > 5:
                lines.append(f"• … and {len(failed) - 5} more")
        self.status_label.set_label(lines[0])

        dialog = Adw.AlertDialog(
            heading="Processing Cancelled" if cancelled else "Processing Complete",
            body="\n".join(lines))
        dialog.add_response("close", "Close")
        out_dirs = {r.output.parent for r in ok}
        if len(out_dirs) == 1:
            dialog.add_response("open", "Open Output Folder")
            dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("open")
            folder = out_dirs.pop()
            dialog.connect("response", lambda _d, resp:
                           resp == "open" and self.open_folder(folder))
        dialog.present(self)
        return False

    def cancel_processing(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.set_sensitive(False)
            self.status_label.set_label("Cancelling…")

    def open_folder(self, folder: Path) -> None:
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder))).launch(self, None, None)

    def on_close_request(self, *_args) -> bool:
        if self.cancel_event:
            self.cancel_event.set()
        self.loader.shutdown(wait=False, cancel_futures=True)
        return False


class MagickStudio(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.window: MainWindow | None = None
        self.pending_paths: list[str] = []

        for name, callback, accels in (
            ("about", self.on_about, []),
            ("quit", lambda *_: self.quit(), ["<primary>q"]),
            ("add-images", lambda *_: self.window and self.window.choose_files(), ["<primary>o"]),
            ("add-folder", lambda *_: self.window and self.window.choose_folder(),
             ["<primary><shift>o"]),
            ("start", lambda *_: self.window and self.window.start_processing(),
             ["<primary>Return"]),
            ("shortcuts", self.on_shortcuts, ["<primary>question"]),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        if self.window is None:
            try:
                magick, error = ip.Magick.detect(), ""
            except ip.MagickError as exc:
                magick, error = None, str(exc)
            self.window = MainWindow(self, magick, error)
        self.window.present()
        if self.pending_paths and self.window.magick:
            self.window.add_paths(self.pending_paths)
        self.pending_paths = []

    def do_open(self, files, _n_files, _hint):
        self.pending_paths = [f.get_path() for f in files if f.get_path()]
        self.activate()

    def on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="Magick Studio",
            application_icon=APP_ID,
            version=VERSION,
            developer_name="Magick Studio contributors",
            comments="Resize, compress, convert and optimize images with ImageMagick.",
            license_type=Gtk.License.MIT_X11,
        )
        if self.window and self.window.magick:
            about.set_debug_info(f"ImageMagick {self.window.magick.version} "
                                 f"({' '.join(self.window.magick.convert_cmd)})\n"
                                 f"Output formats: {', '.join(self.window.formats)}")
        about.present(self.window)

    def on_shortcuts(self, *_args):
        dialog = Adw.AlertDialog(
            heading="Keyboard Shortcuts",
            body="Ctrl+O — Add images\nCtrl+Shift+O — Add folder\n"
                 "Ctrl+Enter — Start processing\nCtrl+Q — Quit")
        dialog.add_response("close", "Close")
        dialog.present(self.window)


def main() -> int:
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
        print(f"Magick Studio needs Libadwaita 1.5 or newer (found "
              f"{Adw.get_major_version()}.{Adw.get_minor_version()}).", file=sys.stderr)
        return 1
    return MagickStudio().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
