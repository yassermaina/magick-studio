# Magick Studio for Windows: developer guide

The Windows edition uses the same backend as Linux (`image_processor.py`) with a
PySide6 (Qt 6) interface. It is packaged with PyInstaller and installed with Inno Setup.
The Linux GTK app does not use any of these files.

```text
windows/
  magick_studio_win.py    PySide6 interface (also has --smoke-test and --gui-check modes)
  tests/test_gui_win.py   GUI tests (real Qt on Windows, offscreen elsewhere)
  build.ps1               build script: ImageMagick → app → installer
  installer.iss           Inno Setup script (per-user installer)
  ci/winci.py             checks for the packaged app and installer, used by CI
  requirements.txt        pinned Python build/test dependencies
  licenses/               LGPL/GPL (Qt) and Apache-2.0 (OpenSSL) texts shipped with the app
  THIRD-PARTY-NOTICES.txt third-party notices shipped with the app
```

## Prerequisites

- Windows 10 or 11, x64
- Python 3.13 (x64) from python.org, with `py`/`python` on `PATH`
- [7-Zip](https://www.7-zip.org) (`7z` on `PATH`), to unpack ImageMagick
- [Inno Setup 6](https://jrsoftware.org/isinfo.php), for the installer step only

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r windows\requirements.txt
```

## Building

```powershell
powershell -ExecutionPolicy Bypass -File windows\build.ps1
```

The script has three steps. Run a subset with `-Steps`:

| Step | Result |
| --- | --- |
| `imagemagick` | Downloads the official portable ImageMagick 7 (Q16-HDRI x64), verifies its SHA-256, and stages `magick.exe` with its config files and licenses in `build\windows\imagemagick`. |
| `app` | PyInstaller one-folder, windowed build in `build\windows\dist\MagickStudio`. |
| `installer` | `build\windows\installer\MagickStudio-Setup-<version>-x64.exe`. |

The version comes from `APP_VERSION` in `image_processor.py`. To update ImageMagick,
change `$ImVersion` and `$ImSha256` in `build.ps1`. The portable build is a single
static `magick.exe` (no DLLs). It needs no installation and does not touch the registry.

## Running from source

```powershell
$env:MAGICK_STUDIO_MAGICK = "$PWD\build\windows\imagemagick\magick.exe"   # optional
python windows\magick_studio_win.py
```

ImageMagick is looked up in this order:

1. `MAGICK_STUDIO_MAGICK` (the full path to a `magick.exe`)
2. the copy bundled with the packaged app (`_internal\imagemagick\magick.exe`)
3. `magick` on `PATH`

On Windows, the app never falls back to `convert.exe`, because that name also belongs
to a Windows system tool.

## Tests

```powershell
$env:MAGICK_STUDIO_MAGICK = "$PWD\build\windows\imagemagick\magick.exe"
python -m pytest tests            # backend, including Windows path tests
python -m pytest windows\tests    # Qt interface
```

On Linux, the Qt tests run offscreen when PySide6 is installed (for example in a venv):
`python -m pytest windows/tests`.

## Checking a packaged build

`windows\ci\winci.py` contains the checks that CI runs on the packaged app:

```powershell
python windows\ci\winci.py samples build\ci\samples
python windows\ci\winci.py bundle-check build\windows\dist\MagickStudio
python windows\ci\winci.py smoke build\windows\dist\MagickStudio\MagickStudio.exe build\ci\samples build\ci
python windows\ci\winci.py gui-check build\windows\dist\MagickStudio\MagickStudio.exe build\ci\samples build\ci\gui.json
python windows\ci\winci.py console-check build\windows\dist\MagickStudio\MagickStudio.exe build\ci\samples build\ci
python windows\ci\winci.py install-test build\windows\installer\MagickStudio-Setup-1.1.1-x64.exe build\ci
python windows\ci\winci.py upgrade-test build\windows\installer\MagickStudio-Setup-1.1.1-x64.exe build\ci
```

`smoke` and `gui-check` run the app with a minimal `PATH`, so they fail if it uses
anything other than its bundled ImageMagick. `install-test` installs, reinstalls and
uninstalls the app for the current user. `upgrade-test` downloads the previous
published installer (`PREVIOUS_VERSION` in `winci.py`), checks its SHA-256 against
the pinned value and the release's `SHA256SUMS.txt`, installs it, upgrades it with
the new installer, checks the version, shortcuts and uninstall entries, processes
JPEG, PNG, WebP and AVIF images and uninstalls. Run both only on a test machine or
VM. After each release, update `PREVIOUS_VERSION` and `PREVIOUS_SHA256`.

## Continuous integration

`.github/workflows/windows.yml` runs on GitHub-hosted `windows-2025` runners
(Windows Server 2025, x64). It runs every step above and uploads the installer,
the portable build, test reports and screenshots as artifacts. The runner also has
its own ImageMagick on `PATH`. The tests set `MAGICK_STUDIO_MAGICK` or a minimal
`PATH`, so only the bundled copy is used.

## Installer behaviour

- Per-user install into `%LOCALAPPDATA%\Programs\Magick Studio`. No administrator
  rights are needed.
- Adds a Start Menu shortcut, plus an optional desktop shortcut.
- Adds an entry to *Settings → Apps* (under `HKCU`).
- Installing over an existing version first removes the old `_internal` folder, so no
  stale files are left.
- Uninstalling removes only the application folder, the shortcuts and the registry
  entry. Images and `output` folders are never touched.

## Licensing

The app is MIT-licensed. The package also includes:

- ImageMagick (ImageMagick License): `_internal\imagemagick\LICENSE.txt`, `NOTICE.txt`.
  `magick.exe` is the unmodified official build. It has about 40 libraries built
  in, some under the LGPL (cairo, glib, pango, librsvg, libheif, libde265 and others).
  `NOTICE.txt` lists each one with its license and the source revision it was
  built from.
- Qt 6 / PySide6 (LGPL-3.0), dynamically linked: `_internal\licenses\`
- Python (PSF License): `_internal\licenses\Python-LICENSE.txt`
- OpenSSL (Apache-2.0), used by Python and Qt: `_internal\licenses\Apache-2.0.txt`
- Mesa llvmpipe (`opengl32sw.dll`, MIT) and the Microsoft Visual C++ runtime

See `THIRD-PARTY-NOTICES.txt`.
