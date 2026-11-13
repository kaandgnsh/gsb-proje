# Local Assessment AI — install. Ayrıntı: .\install.ps1 --help
. "$PSScriptRoot\_common.ps1"
Invoke-LaiSetup -Command 'install' -Arguments $args
