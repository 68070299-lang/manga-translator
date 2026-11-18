param(
  [ValidateSet("cpu", "gpu", "directml")]
  [string]$Runtime = "gpu",

  [ValidateRange(1, 65535)]
  [int]$Port = 8001,

  [ValidateSet("fast", "balanced", "quality")]
  [string]$LanPaintProfile = "fast"
)

# Run the translation API locally (keeps running until you close the window)
$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  [Console]::Error.WriteLine("Install uv first: https://docs.astral.sh/uv/getting-started/installation/")
  exit 1
}

if (-not [string]::IsNullOrEmpty($env:MANGA_TRANSLATOR_TESTING)) {
  [Console]::Error.WriteLine("MANGA_TRANSLATOR_TESTING is set in this shell. That test-only mode disables backend authentication and ignores backend/.env. Remove it (Remove-Item Env:MANGA_TRANSLATOR_TESTING) and start again.")
  exit 5
}

function Test-BackendAuthConfigured {
  if (-not [string]::IsNullOrWhiteSpace($env:BACKEND_AUTH_TOKEN)) {
    return $true
  }

  $envFile = Join-Path $PSScriptRoot ".env"
  if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    return $false
  }

  foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if ($trimmed -match '^(?:export\s+)?BACKEND_AUTH_TOKEN\s*=\s*(.*)$') {
      $value = $Matches[1].Trim()
      if ($value.Length -ge 2 -and (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
      )) {
        $value = $value.Substring(1, $value.Length - 2).Trim()
      }
      return -not [string]::IsNullOrWhiteSpace($value)
    }
  }

  return $false
}

if (-not (Test-BackendAuthConfigured)) {
  [Console]::Error.WriteLine("Backend authentication is not configured. Set BACKEND_AUTH_TOKEN in the process environment or backend/.env before starting.")
  exit 2
}

$activeListener = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
  Where-Object { $_.Port -eq $Port } |
  Select-Object -First 1
if ($activeListener) {
  $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
  $owner = if ($connection) { $connection.OwningProcess } else { "unknown" }
  [Console]::Error.WriteLine("Port $Port is already in use by process $owner. Stop that process or pass a different -Port.")
  exit 3
}

$hadUseGpu = Test-Path Env:USE_GPU
$previousUseGpu = $env:USE_GPU
$hadProjectEnvironment = Test-Path Env:UV_PROJECT_ENVIRONMENT
$previousProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
$hadLanPaintProfile = Test-Path Env:COMFY_LANPAINT_PROFILE
$previousLanPaintProfile = $env:COMFY_LANPAINT_PROFILE
$previousLocation = Get-Location
$exitCode = 0

try {
  Set-Location $PSScriptRoot
  $env:USE_GPU = if ($Runtime -eq "cpu") { "false" } else { "true" }
  $env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot ".venv-$Runtime"
  $env:COMFY_LANPAINT_PROFILE = $LanPaintProfile
  Write-Host "Starting backend at http://127.0.0.1:$Port with $Runtime runtime and $LanPaintProfile LanPaint profile"
  & uv sync --locked --no-default-groups --group $Runtime --python 3.12
  $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }

  if ($exitCode -eq 0 -and $Runtime -eq "gpu") {
    $providerProbe = "import onnxruntime as ort, sys; providers = ort.get_available_providers(); print('ONNX providers:', providers); sys.exit(0 if 'CUDAExecutionProvider' in providers else 4)"
    & uv run --no-sync python -c $providerProbe
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    if ($exitCode -ne 0) {
      [Console]::Error.WriteLine("GPU runtime requested, but CUDAExecutionProvider is unavailable.")
    }
  }

  if ($exitCode -eq 0) {
    & uv run --no-sync uvicorn manga_backend.main:app --host 127.0.0.1 --port $Port
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
  }
}
finally {
  Set-Location $previousLocation
  if ($hadUseGpu) {
    $env:USE_GPU = $previousUseGpu
  }
  else {
    Remove-Item Env:USE_GPU -ErrorAction SilentlyContinue
  }
  if ($hadProjectEnvironment) {
    $env:UV_PROJECT_ENVIRONMENT = $previousProjectEnvironment
  }
  else {
    Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
  }
  if ($hadLanPaintProfile) {
    $env:COMFY_LANPAINT_PROFILE = $previousLanPaintProfile
  }
  else {
    Remove-Item Env:COMFY_LANPAINT_PROFILE -ErrorAction SilentlyContinue
  }
}

if ($exitCode -ne 0) {
  exit $exitCode
}
