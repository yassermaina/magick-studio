<#
.SYNOPSIS
  Builds Magick Studio for Windows: bundled ImageMagick, PyInstaller app, Inno Setup installer.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\build.ps1
  powershell -File windows\build.ps1 -Steps imagemagick     # only stage ImageMagick
#>
param(
    [string[]]$Steps = @("imagemagick", "app", "installer")
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Build = Join-Path $Root "build\windows"
$ImDir = Join-Path $Build "imagemagick"
$Dist = Join-Path $Build "dist"
New-Item -ItemType Directory -Force -Path $Build | Out-Null

# Version comes from the shared backend so both frontends always agree.
$Version = (& python -c "import sys; sys.path.insert(0, r'$Root'); import image_processor as ip; print(ip.APP_VERSION)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "Could not read APP_VERSION" }

# Official portable ImageMagick (static build: no DLLs, no registry, no installer).
$ImVersion = "7.1.2-35"
$ImFile = "ImageMagick-$ImVersion-portable-Q16-HDRI-x64.7z"
$ImUrl = "https://github.com/ImageMagick/ImageMagick/releases/download/$ImVersion/$ImFile"
$ImSha256 = "34db4364da26c88d6b8cc5bbc1fff22b85d00d3b399c82fe85ee4790efacbbf1"

function Step-ImageMagick {
    $archive = Join-Path $Build $ImFile
    if (-not (Test-Path $archive)) {
        Write-Host "Downloading $ImUrl"
        Invoke-WebRequest -Uri $ImUrl -OutFile $archive -UseBasicParsing
    }
    $hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $ImSha256) { throw "ImageMagick checksum mismatch: $hash" }

    $full = Join-Path $Build "imagemagick-full"
    if (Test-Path $full) { Remove-Item -Recurse -Force $full }
    & 7z x -y "-o$full" $archive | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7z failed" }

    # Only magick.exe is needed ("magick identify" replaces identify.exe).
    if (Test-Path $ImDir) { Remove-Item -Recurse -Force $ImDir }
    New-Item -ItemType Directory -Path $ImDir | Out-Null
    Copy-Item (Join-Path $full "magick.exe") $ImDir
    Copy-Item (Join-Path $full "*.xml") $ImDir
    Copy-Item (Join-Path $full "sRGB.icc"), (Join-Path $full "LICENSE.txt"), (Join-Path $full "NOTICE.txt") $ImDir

    # Verify at runtime which formats this build can write.
    $magick = Join-Path $ImDir "magick.exe"
    & $magick -version
    $formats = & $magick -list format
    foreach ($f in "JPEG", "PNG", "WEBP") {
        if (-not ($formats -match "^\s*$f\*?\s+(\S+\s+)?rw")) { throw "Bundled ImageMagick cannot write $f" }
    }
    $avif = [bool]($formats -match "^\s*AVIF\*?\s+(\S+\s+)?rw")
    Write-Host "Bundled ImageMagick $ImVersion OK (JPEG, PNG, WEBP; AVIF=$avif)"
}

function Step-App {
    if (-not (Test-Path (Join-Path $ImDir "magick.exe"))) { throw "Run the imagemagick step first" }

    # License texts shipped with the app.
    $licenses = Join-Path $Build "licenses"
    if (Test-Path $licenses) { Remove-Item -Recurse -Force $licenses }
    New-Item -ItemType Directory -Path $licenses | Out-Null
    Copy-Item (Join-Path $Root "windows\licenses\*") $licenses
    $pyLicense = (& python -c "import sys, os; print(os.path.join(sys.base_prefix, 'LICENSE.txt'))").Trim()
    if (-not (Test-Path $pyLicense)) { throw "Python LICENSE.txt not found at $pyLicense" }
    Copy-Item $pyLicense (Join-Path $licenses "Python-LICENSE.txt")

    # Windows file properties (Details tab).
    $v = ($Version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
    $versionFile = Join-Path $Build "version_info.txt"
    @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v), prodvers=($v)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Magick Studio contributors'),
      StringStruct('FileDescription', 'Magick Studio'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('InternalName', 'MagickStudio'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'MagickStudio.exe'),
      StringStruct('ProductName', 'Magick Studio'),
      StringStruct('ProductVersion', '$Version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding UTF8 $versionFile

    & python -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name MagickStudio `
        --icon (Join-Path $Root "assets\icon.ico") `
        --version-file $versionFile `
        --paths $Root `
        --add-data "$ImDir;imagemagick" `
        --add-data "$(Join-Path $Root 'assets\icon.ico');assets" `
        --add-data "$licenses;licenses" `
        --exclude-module tkinter `
        --distpath $Dist `
        --workpath (Join-Path $Build "work") `
        --specpath $Build `
        (Join-Path $Root "windows\magick_studio_win.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $app = Join-Path $Dist "MagickStudio"
    Copy-Item (Join-Path $Root "LICENSE") (Join-Path $app "LICENSE.txt")
    Copy-Item (Join-Path $Root "windows\THIRD-PARTY-NOTICES.txt") $app
    Write-Host "Application built in $app"
}

function Step-Installer {
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        foreach ($p in "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
            if (Test-Path $p) { $iscc = $p; break }
        }
    }
    if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }
    & $iscc "/DAppVersion=$Version" "/DSourceDir=$(Join-Path $Dist 'MagickStudio')" `
        "/DOutputDir=$(Join-Path $Build 'installer')" (Join-Path $Root "windows\installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
}

foreach ($step in $Steps) {
    switch ($step) {
        "imagemagick" { Step-ImageMagick }
        "app"         { Step-App }
        "installer"   { Step-Installer }
        default       { throw "Unknown step: $step" }
    }
}
