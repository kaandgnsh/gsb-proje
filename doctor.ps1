# Local Assessment AI — doctor. Ayrıntı: .\doctor.ps1 --help
. "$PSScriptRoot\_common.ps1"
Invoke-LaiSetup -Command 'doctor' -Arguments $args
