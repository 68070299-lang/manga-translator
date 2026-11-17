param(
    [int]$Port = 8188,
    [string]$RuntimeRoot = "",
    [string]$VenvPath = "",
    [ValidateSet("fast", "balanced", "compatibility")]
    [string]$Profile = "balanced",
    [switch]$DisableCustomKernels,
    [switch]$DisablePinnedMemory,
    [switch]$SkipWarmup
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $RepoRoot "backend\.runtime\ComfyUI"
}
if (-not $VenvPath) {
    $VenvPath = Join-Path $RepoRoot "backend\.venv-comfy"
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$Python = Join-Path ([System.IO.Path]::GetFullPath($VenvPath)) "Scripts\python.exe"
$HealthUrl = "http://127.0.0.1:$Port/system_stats"

function Invoke-LanPaintWarmup {
    if ($SkipWarmup) {
        return
    }
    $WarmupPython = $null
    foreach ($Candidate in @(".venv-gpu", ".venv-cpu", ".venv-directml", ".venv")) {
        $CandidatePython = Join-Path $RepoRoot "backend\$Candidate\Scripts\python.exe"
        if (Test-Path $CandidatePython) {
            $WarmupPython = $CandidatePython
            break
        }
    }
    $WarmupScript = Join-Path $PSScriptRoot "warmup.py"
    if ($WarmupPython -and (Test-Path $WarmupScript)) {
        & $WarmupPython $WarmupScript
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "LanPaint warmup failed; the first real request will load models instead."
        }
    }
    else {
        Write-Warning "Backend Python environment is unavailable (create backend\.venv-gpu, .venv-cpu or .venv first); skipped LanPaint warmup."
    }
}

try {
    $null = Invoke-RestMethod -TimeoutSec 2 -Uri $HealthUrl
    Write-Host "ComfyUI is already running at http://127.0.0.1:$Port"
    Invoke-LanPaintWarmup
    exit 0
} catch {
}

if (-not (Test-Path $Python)) {
    throw "ComfyUI environment is missing. Run install.ps1 first: $Python"
}
if (-not (Test-Path (Join-Path $RuntimeRoot "main.py"))) {
    throw "ComfyUI runtime is missing. Run install.ps1 first: $RuntimeRoot"
}

$LogRoot = Join-Path $RepoRoot "backend\.runtime\logs"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$Stdout = Join-Path $LogRoot "comfyui.out.log"
$Stderr = Join-Path $LogRoot "comfyui.err.log"
$Arguments = @(
    "main.py",
    "--listen", "127.0.0.1",
    "--port", "$Port",
    "--disable-auto-launch",
    "--use-pytorch-cross-attention",
    "--mmap-torch-files",
    "--disable-api-nodes"
)

if ($Profile -eq "compatibility") {
    $Arguments += @("--lowvram", "--reserve-vram", "0.8", "--cpu-vae")
} elseif ($Profile -eq "balanced") {
    $Arguments += @("--lowvram", "--reserve-vram", "0.6")
} else {
    $Arguments += @("--reserve-vram", "0.45")
}

if ($DisablePinnedMemory) {
    $Arguments += "--disable-pinned-memory"
}

if (-not $DisableCustomKernels -and $Profile -ne "compatibility") {
    $Arguments += @("--fast", "fp8_matrix_mult", "cublas_ops")
    $TritonAvailable = (& $Python -c "import importlib.util; print('1' if importlib.util.find_spec('triton') else '0')").Trim()
    if ($TritonAvailable -eq "1") {
        $Arguments += "--enable-triton-backend"
    }
}
$Process = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $RuntimeRoot -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$Process.Id | Set-Content (Join-Path $LogRoot "comfyui.pid")

$Deadline = (Get-Date).AddMinutes(10)
while ((Get-Date) -lt $Deadline) {
    if ($Process.HasExited) {
        $Tail = if (Test-Path $Stderr) { Get-Content $Stderr -Tail 60 | Out-String } else { "" }
        throw "ComfyUI exited with code $($Process.ExitCode).`n$Tail"
    }
    try {
        $null = Invoke-RestMethod -TimeoutSec 3 -Uri $HealthUrl
        Write-Host "ComfyUI ready at http://127.0.0.1:$Port (PID $($Process.Id))"
        Write-Host "Optimization profile: $Profile"
        Write-Host "Pinned memory: $(if ($DisablePinnedMemory) { 'disabled' } else { 'enabled' })"
        Invoke-LanPaintWarmup
        Write-Host "Logs: $Stdout and $Stderr"
        exit 0
    } catch {
        Start-Sleep -Seconds 2
    }
}
throw "Timed out waiting for ComfyUI. Check $Stderr"
