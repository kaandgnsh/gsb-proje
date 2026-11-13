# Local Assessment AI — ortak PowerShell yardımcıları. Doğrudan çalıştırılmaz.
# Yönetici yetkisi gerekmez: kurulum %LOCALAPPDATA% altında kullanıcı düzeyindedir.
$ErrorActionPreference = 'Stop'

function Find-LaiPython {
    $check = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
    $candidates = @(@('py', '-3'), @('python'), @('python3'))
    foreach ($c in $candidates) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $pre = @($c | Select-Object -Skip 1)
        & $exe.Source @pre -c $check 2>$null
        if ($LASTEXITCODE -eq 0) { return ,(@($exe.Source) + $pre) }
    }
    return $null
}

function Invoke-LaiSetup {
    param([Parameter(Mandatory)] [string] $Command, [object[]] $Arguments = @())
    $py = Find-LaiPython
    if (-not $py) {
        Write-Error ("Python 3.11 veya üzeri bulunamadı. https://www.python.org/downloads/windows/ " +
                     "adresinden ya da 'winget install Python.Python.3.12' ile kurun, sonra tekrar deneyin.")
        exit 127
    }
    $src = Join-Path $PSScriptRoot 'src'
    if ($env:PYTHONPATH) { $env:PYTHONPATH = "$src;$env:PYTHONPATH" } else { $env:PYTHONPATH = $src }
    $exe = $py[0]
    $pre = @($py | Select-Object -Skip 1)
    & $exe @pre -m lai_setup $Command @Arguments
    exit $LASTEXITCODE
}
