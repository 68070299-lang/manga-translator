param()

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$PidFile = Join-Path $RepoRoot "backend\.runtime\logs\comfyui.pid"
if (-not (Test-Path $PidFile)) {
    Write-Host "No ComfyUI PID file found."
    exit 0
}
$ComfyPid = [int](Get-Content $PidFile)
$Process = Get-Process -Id $ComfyPid -ErrorAction SilentlyContinue
if ($Process) {
    Stop-Process -Id $ComfyPid
    $Process.WaitForExit(15000)
    Write-Host "Stopped ComfyUI PID $ComfyPid"
} else {
    Write-Host "ComfyUI PID $ComfyPid is not running."
}
Remove-Item $PidFile -Force
