# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-11-01

First stable release, for Linux (Fedora 44, Ubuntu 24.04 LTS, Debian 13).

### Added
- GTK 4 / Libadwaita interface: image list with thumbnails, preview, settings panel,
  progress bar, cancellation and results summary. Follows system light/dark style.
- Import via file chooser, folder (recursive), drag and drop and command-line arguments.
- Resize (width/height, aspect ratio, optional enlargement), quality slider with
  High Quality / Balanced / Small File presets, and one-click **Optimize for Web**.
- Output to JPEG, PNG, WebP, or the original format; AVIF when ImageMagick supports it.
- Safe output handling: `output` folder by default, collision-free names, atomic
  writes, no partial files after errors or cancellation, originals never modified.
- Support for ImageMagick 7 (`magick`) and ImageMagick 6 (`convert`/`identify`).
- WebP encoding through `cwebp` when the installed ImageMagick ignores WebP quality
  (ImageMagick 6.9.12 on Ubuntu 24.04).
- User-level `install.sh` / `uninstall.sh` for Fedora, Debian and Ubuntu.
- pytest suite covering processing, filenames, collisions, cancellation and a GUI smoke test.
- Tested on Fedora 44, Ubuntu 24.04 and Debian 13.

### Not included
- Windows support. A proof of concept is kept in `experimental/windows/` for a future
  version. It has been tested on Fedora only, not on Windows.
