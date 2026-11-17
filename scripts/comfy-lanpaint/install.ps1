param(
    [string]$RuntimeRoot = "",
    [string]$VenvPath = "",
    [switch]$SkipModels,
    [string]$VaeSource = "",
    [switch]$IncludeDistilled = $true,
    [switch]$DisableCustomKernels
)

$ErrorActionPreference = "Stop"
$ComfyCommit = "a464ac33588ae182f81a090d910cfbf21e255b73"
$LanPaintCommit = "c9c79988f1038bb7699cb23e1709b533bd4e582c"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $RepoRoot "backend\.runtime\ComfyUI"
}
if (-not $VenvPath) {
    $VenvPath = Join-Path $RepoRoot "backend\.venv-comfy"
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$VenvPath = [System.IO.Path]::GetFullPath($VenvPath)

if (-not (Test-Path (Join-Path $RuntimeRoot ".git"))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $RuntimeRoot) | Out-Null
    git clone https://github.com/Comfy-Org/ComfyUI.git $RuntimeRoot
}
git -C $RuntimeRoot fetch --depth 1 origin $ComfyCommit
git -C $RuntimeRoot checkout --detach $ComfyCommit

if (-not (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) {
    python -m venv $VenvPath
}
$Python = Join-Path $VenvPath "Scripts\python.exe"
& $Python -m pip install --upgrade pip wheel
& $Python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
& $Python -m pip install -r (Join-Path $RuntimeRoot "requirements.txt") huggingface_hub
if (-not $DisableCustomKernels) {
    # Torch 2.11 is paired with Triton 3.6. Comfy-Kitchen dispatches supported
    # AdaLN/RoPE operations to these maintained fused kernels on Windows.
    & $Python -m pip install "triton-windows>=3.6,<3.7"
}

$LanPaintRoot = Join-Path $RuntimeRoot "custom_nodes\LanPaint"
if (-not (Test-Path (Join-Path $LanPaintRoot ".git"))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $LanPaintRoot) | Out-Null
    git clone https://github.com/scraed/LanPaint.git $LanPaintRoot
}
git -C $LanPaintRoot fetch --depth 1 origin $LanPaintCommit
git -C $LanPaintRoot checkout --detach $LanPaintCommit

if (-not $SkipModels) {
    if (-not $VaeSource -and $env:SD_CPP_MODELS_DIR) {
        $VaeSource = Join-Path $env:SD_CPP_MODELS_DIR "flux2-vae.safetensors"
    }
    $ModelArguments = @("--comfy-root", $RuntimeRoot)
    if ($VaeSource -and (Test-Path $VaeSource)) {
        $ModelArguments += @("--vae-source", $VaeSource)
    } else {
        Write-Warning "No local flux2-vae.safetensors found (pass -VaeSource or set SD_CPP_MODELS_DIR); skipping the VAE copy step, the VAE will be downloaded instead."
    }
    if ($IncludeDistilled) {
        $ModelArguments += "--include-distilled"
    }
    & $Python (Join-Path $PSScriptRoot "download_models.py") $ModelArguments
}

Write-Host "ComfyUI commit: $ComfyCommit"
Write-Host "LanPaint commit: $LanPaintCommit"
Write-Host "Runtime ready: $RuntimeRoot"
Write-Host "Start with: powershell -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'start.ps1')`""
