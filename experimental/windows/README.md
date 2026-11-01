# Windows support — feasibility study (experimental)

**Status: investigation only.** Nothing in this folder is installed or used by the
Linux application. Nothing here has run on Windows yet.

| File | Purpose |
| --- | --- |
| `qt_main.py` | PySide6 proof-of-concept frontend reusing `image_processor.py` unchanged |
| `test_qt_poc.py` | Tests for the PoC (startup, import, resize, WebP, cancel, missing ImageMagick) |
| `backend-windows.patch` | Proposed Windows changes to `image_processor.py`. **Not applied.** |

## Verdict

Windows 10/11 support is **feasible**. The recommended route is a **shared Python backend
with a PySide6 frontend for Windows** (Architecture B). The GTK4/Libadwaita frontend stays
the Linux frontend.

## Evidence

Labels used below:
- **Verified** = tested in this study.
- **Static** = inspected, not executed.
- **Source** = vendor documentation.
- **Assumed** = not yet checked.

### Platform facts

| Topic | Finding | Basis |
| --- | --- | --- |
| PyGObject on Windows | No Windows wheels on PyPI (sdist only). Official route is MSYS2 or conda-forge; gvsbuild builds the stack with Visual Studio, and its maintainers warn their prebuilt binaries are untested. | Source |
| Libadwaita on Windows | Not covered by PyGObject's Windows docs. Possible through MSYS2 packages, but GNOME styling looks foreign on Windows. | Source / Assumed |
| PySide6 on Windows | PyPI wheels for Windows x64 and ARM64 (abi3, Python 3.10–3.15). LGPL-3.0. | Source |
| ImageMagick for Windows | Official 7.1.2-35 builds: installers plus **portable static** `.7z` archives (no installer, no registry). | Source |
| Portable `magick.exe` (x64 Q16-HDRI) | 12 MB archive, 249 MB unpacked: 7 static executables of 31 MB each. Only `magick.exe` (31 MB) and about 0.8 MB of config files are needed. No `convert.exe` is shipped. | Static |
| Formats in `magick.exe` | Strings show libjpeg-turbo, libpng, libwebp, libheif and the **AOM AV1 encoder**, so AVIF output is likely available. | Static |
| `magick.exe` subsystem | PE32+ **console** program, so a windowed app must pass `CREATE_NO_WINDOW` or a console window flashes on every call. | Static |

### Proof of concept (on Fedora 44)

Run on Fedora 44 with the Qt `offscreen` platform:

| Check | Result | Basis |
| --- | --- | --- |
| PoC tests | Startup, import (spaces, Unicode, invalid file), preview, web preset, resize to 1600 px, WebP output, no temp files left, original kept, cancellation, missing-ImageMagick state: **3/3 tests pass**. Not run on Windows. | Verified |
| PyInstaller `--onedir --windowed` build | 161 MB on Linux and starts correctly. Linux bundles ICU and X11/Wayland libraries, so a Windows build is expected to be smaller. | Verified (Linux only) |

## Linux-specific assumptions in the current code

**Backend (`image_processor.py`):**
- *Portable:* argument-list subprocess calls, `os.replace`, `O_EXCL` reservation, `tempfile`.
- *Needs changes (see the patch):*
  1. Console windows from `magick.exe`: pass `CREATE_NO_WINDOW`.
  2. The `convert` fallback could pick up `C:\Windows\System32\convert.exe`, an unrelated disk
     tool. Disabled on Windows.
  3. A bundled `magick.exe` must be found. Uses the `MAGICK_STUDIO_MAGICK` environment variable.
- *Minor:*
  - `os.access(dir, W_OK)` only checks the read-only attribute on Windows (failures are still
    caught later).
  - `chmod`/umask are no-ops.
  - Hidden folders are detected by a leading dot, not by the Windows hidden attribute.
  - Paths over 260 characters may fail.

**Frontend (`main.py`):** entirely GTK4/Libadwaita/GdkPixbuf; not reusable under PySide6.

**Installers:** `install.sh` and `uninstall.sh` are Linux-only (DNF/APT, `~/.local`,
`.desktop` files). Windows needs its own installer.

## Architecture comparison

| | A: GTK4/Libadwaita on Windows | B: shared backend + PySide6 frontend |
| --- | --- | --- |
| Development effort | Medium–high; toolchain risk (MSYS2/gvsbuild, PyInstaller hooks for GI, schemas, icons, pixbuf loaders) | Medium; about 600 lines of new UI code (PoC: about 330) |
| Maintenance | One UI codebase, but a fragile Windows build stack to keep updated | Two UI codebases on one backend; standard pip-based tooling |
| Size | Large (GTK, GLib, Pango, librsvg, icon theme); not measured | Estimated 110–150 MB including ImageMagick; not measured on Windows |
| Performance | Equal: processing time is ImageMagick | Equal |
| Packaging reliability | Low–medium; few Python GTK4 apps ship on Windows | High; PyInstaller and Qt are a well-trodden path |
| User experience | GNOME look on Windows; native file dialogs | Native Windows 11 style, HiDPI, native dialogs |
| Long-term | Depends on community Windows ports | Qt has first-class Windows support |

**Recommendation: B.** It is the simplest reliable option. It reuses the tested backend,
uses standard wheels and tooling, and leaves the Linux application untouched.

## Packaging strategy (proposed)

1. **Python app:** PyInstaller `--onedir --windowed`. Startup is faster than `--onefile`, and
   it triggers fewer antivirus false positives. Build on Windows (no cross-compiling) with
   Python 3.12 or 3.13.
2. **ImageMagick:** bundle `magick.exe` plus its `*.xml`, `sRGB.icc`, `LICENSE.txt` and
   `NOTICE.txt` from the official portable x64 Q16-HDRI archive in an `imagemagick\` folder.
   Point `MAGICK_STUDIO_MAGICK` at it on startup, and fall back to an ImageMagick on `PATH`.
   This adds about 32 MB (about 12 MB compressed). `cwebp` is not needed: ImageMagick 7
   honours WebP quality.
3. **Icon:** generate a `.ico` from `assets/icon.svg`
   (`magick icon.svg -define icon:auto-resize=256,64,48,32,16 icon.ico`) and pass it to
   PyInstaller and the installer.
4. **Installer:** Inno Setup, per-user install to `%LOCALAPPDATA%\Programs\Magick Studio`
   (no admin rights), with a Start Menu shortcut, an optional desktop icon, and an uninstaller
   that never touches user images.
5. **Code signing:** strongly recommended to avoid SmartScreen warnings.
6. **Licences:** ship the LGPL notice for Qt/PySide6 (dynamic linking via `--onedir` is fine)
   and ImageMagick's licence files.

## Windows tests still required

Everything below needs a real Windows 10 and Windows 11 machine. A GitHub Actions
`windows-latest` runner can cover most of the non-visual items.

**ImageMagick behaviour**
- `magick -list format` confirms JPEG, PNG, WebP and AVIF write support.
- Output targets such as `WEBP:C:\…` (format prefix plus drive letter) work.
- Unicode and space-containing paths work in arguments.
- UNC paths (`\\server\share\…`) work.

**Process behaviour**
- No console window flashes with `CREATE_NO_WINDOW`.
- Cancel (`TerminateProcess`) leaves no locked or partial temp files.
- Antivirus file locking does not break `os.replace`.

**File behaviour**
- Paths over 260 characters.
- Read-only folders and OneDrive-synced folders as output.

**User interface**
- Drag and drop from Explorer.
- Native file dialogs.
- HiDPI scaling at 150–200 %.
- Dark mode.

**Packaging**
- Frozen-app startup time.
- SmartScreen behaviour (signed and unsigned).
- Installer, Start Menu entry, uninstaller.
- Windows on ARM64.

## Implementation plan (future)

1. Apply `backend-windows.patch` and add Windows-specific unit tests. *(½ day)*
2. Promote `qt_main.py` to `windows/` and reach feature parity. *(3–5 days)*
   - Background identify and thumbnails (the PoC identifies on the GUI thread).
   - Results summary with "Open output folder".
   - Quality presets, keyboard shortcuts, and settings persistence.
3. PyInstaller spec, ImageMagick bundling and `.ico` icon. *(1–2 days)*
4. Inno Setup installer with Start Menu integration and a safe uninstaller. *(1 day)*
5. GitHub Actions `windows-latest` job: run the tests and build the bundle. *(1 day)*
6. Manual testing on Windows 10 and 11, then fixes. *(2–3 days)*

**Estimated total: about 1.5–2.5 developer-weeks.**

## Running the PoC (Linux or Windows)

```bash
python -m pip install PySide6-Essentials pytest
python experimental/windows/qt_main.py
QT_QPA_PLATFORM=offscreen python -m pytest experimental/windows
```

On Windows, ImageMagick 7 must be on `PATH`, or `MAGICK_STUDIO_MAGICK` must point to
`magick.exe`. That variable requires `backend-windows.patch`.
