# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Fixes from the v1.1.0 post-release audit
([docs/audits/v1.1.0-post-release.md](docs/audits/v1.1.0-post-release.md)).

### Security
- Each input now gets an explicit ImageMagick decoder chosen from the file's signature.
  Before, ImageMagick picked the decoder from the content, so a "photo.jpg" that was
  really PostScript, PDF, SVG or MVG was passed to Ghostscript or a vector renderer
  (on Linux) or to ImageMagick's SVG renderer, which can embed other local images.
  Such files are now reported as "Not a supported image". Correctly encoded images
  with the wrong extension still work.
- Images over 200 megapixels are refused before decoding. A 380 KB crafted PNG used
  to take more than 3 GB of memory per image.
- Windows: the bundled ImageMagick now uses a restrictive `policy.xml`: no external
  programs, no vector/document/script/network coders, and memory/disk/size limits.
- CI: GitHub Actions are pinned to commit SHAs.

### Fixed
- Linux: *Quit* (Ctrl+Q) during processing hid the window but kept processing in the
  background. It now cancels the batch.
- Linux: a file name that is not valid UTF-8 stopped a folder import at "Scanning…".
- Linux: Ctrl+Enter could start a partial batch while images were still loading.
- Both: the preview no longer waits for all thumbnails to load. Qt thumbnails are
  decoded at reduced size (much faster for large JPEGs).
- Both: an unexpected error while reading an image no longer leaves it at "Reading…",
  and the app always leaves the "processing" state.
- Windows: no error traceback when the window is closed during processing.
- Error messages from ImageMagick 7 now hide file paths (only IM6-style quotes were
  handled before), including paths that contain apostrophes.
- `MAGICK_STUDIO_MAGICK` must be an executable; a bare program name is looked up on PATH.
- Windows installer: setup and uninstall ask you to close a running Magick Studio first;
  requires Windows 10 1809 or later (Qt 6); never deletes an `_internal` folder that
  doesn't belong to Magick Studio; the build disables UPX.
- Windows: corrected license paths in `THIRD-PARTY-NOTICES.txt`; README wording on
  Windows on ARM and upgrades.

### Tests
- New regression tests for disguised non-image files, misnamed images, signature
  detection, empty/truncated files, decompression bombs, real ImageMagick error paths,
  the Linux quit and non-UTF-8 bugs, and the Qt oversized/disguised file handling.
- Linux CI fails instead of silently skipping the GUI and `cwebp` tests.
- Windows CI checks the bundled policy, AVIF under that policy, the dark-mode
  screenshot, the uninstaller's exit code, and that uninstalling is refused while the
  app is running.

## [1.1.0] — 2026-11-01

Adds a Windows edition. The Linux edition keeps working as before.

### Added
- **Windows edition** (x64) with a Qt 6 / PySide6 interface and the same features as the
  Linux app: import with thumbnails and preview, drag and drop, presets, JPEG/PNG/WebP/AVIF
  output, resizing, metadata removal, background batch processing with cancel, a summary
  with an *Open Output Folder* button, keyboard shortcuts, and light/dark mode.
- ImageMagick 7.1.2-35 (official portable x64 build) bundled with the Windows app and
  verified by SHA-256 at build time. AVIF output is included.
- Per-user Windows installer (`MagickStudio-Setup-1.1.0-x64.exe`, Inno Setup). It needs
  no administrator rights and adds a Start Menu shortcut, an optional desktop shortcut
  and an uninstaller. Re-installing removes stale files from the previous version.
- `windows/build.ps1` builds the app (PyInstaller, one folder) and the installer.
- Windows GitHub Actions workflow on `windows-2025`. It runs backend and GUI tests on
  Windows, then builds the package, checks it, converts real images with it, checks for
  console windows, takes screenshots, and tests install, re-install and uninstall.
- Backend tests for Windows: drive-letter and UNC paths, Unicode folders, shell-special
  characters in filenames, hidden folders, console-window suppression, cancellation, and
  ImageMagick detection order.
- README sections for Windows installation, troubleshooting and releases, plus a
  Windows developer guide (`windows/README.md`).

### Changed
- Shared backend (`image_processor.py`):
  - ImageMagick is found from `MAGICK_STUDIO_MAGICK`, then the bundled copy, then `PATH`.
  - ImageMagick output is decoded as UTF-8.
  - Error messages hide Windows paths too.
  - Hidden and system folders are skipped on Windows.
- On Windows, ImageMagick runs without a console window, and the app never falls back to
  `convert.exe`.
- The Linux app reads its version from the shared backend.
- The Windows proof of concept moved from `experimental/windows/` to `windows/`. The
  feasibility study is now `docs/windows-feasibility.md`.

### Known limitations
- Windows testing ran on Windows Server 2025 x64 runners only. Windows 10, Windows 11
  desktops and Windows on ARM were not tested.
- The Windows installer is not code-signed, so SmartScreen may warn when you run it.

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
