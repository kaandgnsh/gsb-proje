# Local Assessment AI — uninstall. Ayrıntı: .\uninstall.ps1 --help
. "$PSScriptRoot\_common.ps1"
Invoke-LaiSetup -Command 'uninstall' -Arguments $args
