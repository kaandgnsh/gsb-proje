$ErrorActionPreference = 'Stop'
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { throw 'Gerçek Windows ortamı gerekir.' }
Push-Location (Join-Path $PSScriptRoot '../..')
try {
    python -m PyInstaller --noconfirm "$PSScriptRoot/lai.spec"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller başarısız.' }
    iscc "$PSScriptRoot/setup.iss"
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup başarısız.' }
} finally {
    Pop-Location
}
