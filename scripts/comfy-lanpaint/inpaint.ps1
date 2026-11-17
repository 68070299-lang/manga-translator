param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Mask,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$Prompt = "",
    [int]$Seed = 42
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$BackendRoot = Join-Path $RepoRoot "backend"
$Python = "python"
foreach ($Candidate in @(".venv-gpu", ".venv-cpu", ".venv-directml", ".venv")) {
    $CandidatePython = Join-Path $BackendRoot "$Candidate\Scripts\python.exe"
    if (Test-Path $CandidatePython) {
        $Python = $CandidatePython
        break
    }
}
$Arguments = @(
    "-m", "comfy_lanpaint.cli",
    "--image", $Image,
    "--mask", $Mask,
    "--output", $Output,
    "--seed", "$Seed"
)
if ($Prompt) {
    $Arguments += @("--prompt", $Prompt)
}
& $Python @Arguments
