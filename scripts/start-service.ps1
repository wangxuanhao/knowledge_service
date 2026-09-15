param([int]$Port = 8100, [switch]$Demo, [string]$EnvironmentName = 'llm_model')
$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $PSScriptRoot
$serviceConda = Get-Command conda -ErrorAction Stop
Push-Location $serviceRoot
try {
    $serviceArguments = @('run','--no-capture-output','-n',$EnvironmentName,'python','-m', 'knowledge_service', '--port', "$Port")
    if ($Demo) { $serviceArguments += '--demo' }
    & $serviceConda.Source @serviceArguments
} finally {
    Pop-Location
}
