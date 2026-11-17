param(
  [string]$ProductUrl = "https://bookwalker.jp/deace0472d-fc15-43da-942f-f993e0253047/",
  [string]$ViewerUrl = "https://viewer.bookwalker.jp/03/30/viewer.html?cid=ace0472d-fc15-43da-942f-f993e0253047&cty=1",
  [ValidateRange(5, 20)][int]$Pages = 5,
  [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $RepoRoot "backend"
$ExtensionDir = Join-Path $RepoRoot "dist\chrome-mv3"
$HelperPath = Join-Path $PSScriptRoot "prepare-bookwalker-profile.ts"
$GpuPython = Join-Path $BackendDir ".venv-gpu\Scripts\python.exe"
$ProfileDir = Join-Path ([IO.Path]::GetTempPath()) ("manga-bookwalker-uat-" + [guid]::NewGuid().ToString("N"))
if (-not $OutputDir) {
  $OutputDir = Join-Path $RepoRoot ("test-results\bookwalker-real-uat-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
$BootstrapFile = Join-Path $OutputDir "profile.json"
$SummaryFile = Join-Path $OutputDir "summary.json"
$script:BackendProcess = $null
$script:BackendRun = 0
$script:BackendErrorLogs = @()
$script:ChromeProcess = $null
$script:ActiveWindowProcessId = 0

$HadAuthToken = Test-Path Env:BACKEND_AUTH_TOKEN
$PreviousAuthToken = $env:BACKEND_AUTH_TOKEN
$HadUvEnvironment = Test-Path Env:UV_PROJECT_ENVIRONMENT
$PreviousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class BookWalkerNative {
  [StructLayout(LayoutKind.Sequential)]
  public struct Rect { public int Left; public int Top; public int Right; public int Bottom; }

  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out Rect rect);
}
'@

function Write-Uat([string]$Message) {
  Write-Host ("[BookWalker UAT] " + $Message)
}

function Wait-For {
  param([scriptblock]$Check, [int]$TimeoutSeconds, [string]$Failure)
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  $lastError = $null
  do {
    try {
      $value = & $Check
      if ($value) { return $value }
    } catch {
      $lastError = $_
    }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $deadline)
  if ($lastError) { throw "$Failure Last error: $($lastError.Exception.Message)" }
  throw $Failure
}

function Get-FreePort {
  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  $listener.Start()
  try { return ([Net.IPEndPoint]$listener.LocalEndpoint).Port } finally { $listener.Stop() }
}

function New-AuthToken {
  $bytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Get-HttpStatus([string]$Url, [hashtable]$Headers) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec 10
    return [int]$response.StatusCode
  } catch {
    if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
    throw
  }
}

function Start-UatBackend([int]$Port) {
  $script:BackendRun += 1
  $stdout = Join-Path $OutputDir ("backend-$($script:BackendRun)-stdout.log")
  $stderr = Join-Path $OutputDir ("backend-$($script:BackendRun)-stderr.log")
  $script:BackendErrorLogs += $stderr
  $arguments = @("-m", "uvicorn", "manga_backend.main:app", "--host", "127.0.0.1", "--port", [string]$Port)
  $script:BackendProcess = Start-Process -FilePath $GpuPython -ArgumentList $arguments -WorkingDirectory $BackendDir `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
  $healthUrl = "http://127.0.0.1:$Port/health"
  $headers = @{ "X-Manga-Translator-Token" = $env:BACKEND_AUTH_TOKEN }
  $health = Wait-For -TimeoutSeconds 180 -Failure "GPU backend did not become ready." -Check {
    if ($script:BackendProcess.HasExited) { throw "Backend exited with code $($script:BackendProcess.ExitCode)." }
    try {
      $value = Invoke-RestMethod -Uri $healthUrl -Headers $headers -TimeoutSec 5
      if ($value.models_loaded -and $value.device -eq "cuda" -and
          $value.components.detector -and $value.components.ocr -and $value.components.inpainter) { return $value }
    } catch {}
    return $null
  }
  Write-Uat "GPU backend ready on port $Port."
  return $health
}

function Stop-UatBackend {
  if ($script:BackendProcess -and -not $script:BackendProcess.HasExited) {
    Stop-Process -Id $script:BackendProcess.Id -Force -ErrorAction SilentlyContinue
    $script:BackendProcess.WaitForExit(10000) | Out-Null
  }
  $script:BackendProcess = $null
}

function Get-SuccessCount {
  $count = 0
  foreach ($log in $script:BackendErrorLogs) {
    if (Test-Path $log) {
      $matches = Select-String -LiteralPath $log -SimpleMatch "translate success request_id=" -ErrorAction SilentlyContinue
      $count += @($matches).Count
    }
  }
  return $count
}

function Get-UatChromeProcesses {
  return @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine.IndexOf($ProfileDir, [StringComparison]::OrdinalIgnoreCase) -ge 0
  })
}

function Stop-UatChrome {
  foreach ($process in Get-UatChromeProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 750
  $script:ChromeProcess = $null
  $script:ActiveWindowProcessId = 0
}

function Get-UatWindowProcesses {
  $results = @()
  foreach ($item in Get-UatChromeProcesses) {
    $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
    if ($process -and $process.MainWindowHandle -ne 0) { $results += $process }
  }
  return $results
}

function Get-UatRoot {
  $process = $null
  if ($script:ActiveWindowProcessId) {
    $candidate = Get-Process -Id $script:ActiveWindowProcessId -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.MainWindowHandle -ne 0) { $process = $candidate }
  }
  if (-not $process) {
    $process = Wait-For -TimeoutSeconds 30 -Failure "Native Chrome window was not found." -Check {
      @(Get-UatWindowProcesses)[0]
    }
  }
  [BookWalkerNative]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
  return [Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
}

function Find-NamedControl([string]$Name, $ControlType = $null) {
  $root = Get-UatRoot
  $nameCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty, $Name)
  $element = $root.FindFirst([Windows.Automation.TreeScope]::Descendants, $nameCondition)
  if ($element -and $ControlType -and $element.Current.ControlType -ne $ControlType) { return $null }
  return $element
}

function Invoke-NamedControl([string]$Name, [int]$TimeoutSeconds = 30) {
  $element = Wait-For -TimeoutSeconds $TimeoutSeconds -Failure "Control not found: $Name" -Check {
    Find-NamedControl $Name
  }
  $pattern = $element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
  $pattern.Invoke()
}

function Invoke-ControlContaining([string[]]$Labels) {
  $root = Get-UatRoot
  $all = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
  foreach ($element in $all) {
    if ($element.Current.ControlType -notin @([Windows.Automation.ControlType]::Button, [Windows.Automation.ControlType]::Hyperlink)) { continue }
    foreach ($label in $Labels) {
      if ($element.Current.Name -and $element.Current.Name.Contains($label)) {
        $element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
        return $true
      }
    }
  }
  return $false
}

function Set-Address([string]$Url) {
  $root = Get-UatRoot
  $condition = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::NameProperty,
    "Address and search bar"
  )
  $element = $root.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
  if (-not $element) { throw "Chrome address bar was not found." }
  $element.SetFocus()
  $element.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).SetValue($Url)
  [Windows.Forms.SendKeys]::SendWait("{ENTER}")
}

function Get-AddressFromRoot($Root) {
  $nameCondition = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::NameProperty,
    "Address and search bar"
  )
  $element = $Root.FindFirst([Windows.Automation.TreeScope]::Descendants, $nameCondition)
  if (-not $element) { return "" }
  return [string]$element.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
}

function Find-WindowAddress([scriptblock]$Matches) {
  foreach ($process in Get-UatWindowProcesses) {
    $root = [Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
    $address = Get-AddressFromRoot $root
    if ($address -and (& $Matches $address)) {
      $script:ActiveWindowProcessId = $process.Id
      [BookWalkerNative]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
      return $address
    }
  }
  return $null
}

function Wait-ForProduct {
  $expected = ([Uri]$ProductUrl).Host + ([Uri]$ProductUrl).AbsolutePath
  Wait-For -TimeoutSeconds 60 -Failure "Exact product URL did not open first." -Check {
    Find-WindowAddress { param($address) $address.Contains($expected) }
  } | Out-Null
}

function Wait-ForViewer {
  $uri = [Uri]$ViewerUrl
  $path = $uri.Host + $uri.AbsolutePath
  $cidMatch = [regex]::Match($uri.Query, '(?:^|&)cid=([^&]+)')
  $ctyMatch = [regex]::Match($uri.Query, '(?:^|&)cty=([^&]+)')
  $cid = if ($cidMatch.Success) { "cid=$($cidMatch.Groups[1].Value)" } else { "" }
  $cty = if ($ctyMatch.Success) { "cty=$($ctyMatch.Groups[1].Value)" } else { "" }
  Wait-For -TimeoutSeconds 120 -Failure "Exact BookWalker viewer did not render. Addresses: $(@(Get-UatWindowProcesses | ForEach-Object { Get-AddressFromRoot ([Windows.Automation.AutomationElement]::FromHandle($_.MainWindowHandle)) }) -join ', ')" -Check {
    foreach ($process in Get-UatWindowProcesses) {
      $root = [Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
      $address = Get-AddressFromRoot $root
      if ($address -and $address.Contains($path) -and
          (($cid -eq "") -or $address.Contains($cid)) -and
          (($cty -eq "") -or $address.Contains($cty))) {
        $counter = Get-PageCounterFromRoot $root
        if ($counter) {
          $script:ActiveWindowProcessId = $process.Id
          [BookWalkerNative]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
          return $counter
        }
      }
    }
    return $null
  } | Out-Null
}

function Open-ViewerFromProduct {
  Wait-ForProduct
  $freeRead = ([string][char]0x7121) + [char]0x6599 + [char]0x3067 + [char]0x8AAD + [char]0x3080
  $trialRead = ([string][char]0x8A66) + [char]0x3057 + [char]0x8AAD + [char]0x307F
  $read = ([string][char]0x8AAD) + [char]0x3080
  Invoke-ControlContaining @("Allow only essential cookies", "Allow all cookies") | Out-Null
  $clicked = $false
  try {
    $clicked = Wait-For -TimeoutSeconds 5 -Failure "Reader entry was not exposed." -Check {
      if (Invoke-ControlContaining @($freeRead, $trialRead, $read)) { return $true }
      return $null
    }
  } catch {}
  if (-not $clicked) {
    Write-Uat "Reader entry absent from accessibility tree; using the product sample route."
    Set-Address ($ProductUrl.TrimEnd("?") + "?sample=2")
  }
  Wait-ForViewer
  try {
    Wait-For -TimeoutSeconds 10 -Failure "Viewer cookie banner was not exposed." -Check {
      if (Invoke-ControlContaining @("Allow only essential cookies", "Allow all cookies")) { return $true }
      return $null
    } | Out-Null
  } catch {}
}

function Get-PageCounterFromRoot($Root) {
  $all = $Root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
  foreach ($element in $all) {
    if ($element.Current.ControlType -eq [Windows.Automation.ControlType]::Text -and
        $element.Current.Name -match '^\s*\d+\s*/\s*\d+\s*$') { return ($element.Current.Name -replace '\s', '') }
  }
  return ""
}

function Get-PageCounter {
  return Get-PageCounterFromRoot (Get-UatRoot)
}

function Save-AccessibilityDump([string]$Name) {
  $root = Get-UatRoot
  $all = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
  $lines = foreach ($element in $all) {
    if ($element.Current.Name) { $element.Current.ControlType.ProgrammaticName + "|" + $element.Current.Name }
  }
  $lines | Set-Content -LiteralPath (Join-Path $OutputDir $Name) -Encoding UTF8
}

function Click-Native([int]$X, [int]$Y) {
  [BookWalkerNative]::SetCursorPos($X, $Y) | Out-Null
  [BookWalkerNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 80
  [BookWalkerNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

function Move-Reader([ValidateSet("next", "previous")][string]$Direction) {
  $before = Wait-For -TimeoutSeconds 20 -Failure "BookWalker page counter is unavailable." -Check {
    Get-PageCounter
  }
  $root = Get-UatRoot
  $condition = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::ControlTypeProperty,
    [Windows.Automation.ControlType]::Document
  )
  $document = $root.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
  if (-not $document) { throw "BookWalker document surface was not found." }
  $rect = $document.Current.BoundingRectangle
  $xRatio = if ($Direction -eq "next") { 0.15 } else { 0.97 }
  foreach ($yRatio in @(0.50, 0.70, 0.80, 0.35)) {
    $x = [int]($rect.Left + ($rect.Width * $xRatio))
    $y = [int]($rect.Top + ($rect.Height * $yRatio))
    Click-Native $x $y
    $changed = $null
    try {
      $changed = Wait-For -TimeoutSeconds 4 -Failure "Navigation click had no effect." -Check {
        $current = Get-PageCounter
        if ($current -and $current -ne $before) { return $current }
        return $null
      }
    } catch {}
    if ($changed) {
      Start-Sleep -Seconds 4
      return $changed
    }
  }

  $document.SetFocus()
  $key = if ($Direction -eq "next") { "{LEFT}" } else { "{RIGHT}" }
  [Windows.Forms.SendKeys]::SendWait($key)
  $changed = $null
  try {
    $changed = Wait-For -TimeoutSeconds 8 -Failure "Navigation key had no effect." -Check {
      $current = Get-PageCounter
      if ($current -and $current -ne $before) { return $current }
      return $null
    }
  } catch {}
  if ($changed) {
    Start-Sleep -Seconds 4
    return $changed
  }
  throw "Reader did not move $Direction from $before."
}

function Save-WindowScreenshot([string]$Name) {
  $process = Get-Process -Id $script:ActiveWindowProcessId -ErrorAction SilentlyContinue
  if (-not $process) { throw "Cannot capture missing Chrome window." }
  $rect = New-Object BookWalkerNative+Rect
  if (-not [BookWalkerNative]::GetWindowRect($process.MainWindowHandle, [ref]$rect)) { throw "GetWindowRect failed." }
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  $bitmap = New-Object Drawing.Bitmap $width, $height
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $bitmap.Save((Join-Path $OutputDir $Name))
  } finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }
}

function Translate-CurrentPage([string]$Label, [bool]$ExpectRequest) {
  $before = Get-SuccessCount
  Invoke-NamedControl "Translate visible images" 30
  if ($ExpectRequest) {
    Wait-For -TimeoutSeconds 180 -Failure "$Label did not complete a backend translation." -Check {
      $count = Get-SuccessCount
      if ($count -gt $before) { return $count }
      return $null
    } | Out-Null
    # Backend logging precedes the service-worker response and canvas overlay.
    # Let the content script reach its terminal state before changing pages.
    Start-Sleep -Seconds 4
  } else {
    Start-Sleep -Seconds 6
    $after = Get-SuccessCount
    if ($after -ne $before) { throw "$Label made a backend request instead of restoring cache." }
  }
  Wait-For -TimeoutSeconds 20 -Failure "$Label translated overlay was not exposed." -Check {
    Find-NamedControl "Show original image"
  } | Out-Null
}

function Start-NativeChrome($Bootstrap) {
  $arguments = @(
    "--user-data-dir=$ProfileDir",
    "--disable-extensions-except=$($Bootstrap.extensionPath)",
    "--load-extension=$($Bootstrap.extensionPath)",
    "--force-renderer-accessibility",
    "--disable-session-crashed-bubble",
    "--no-first-run",
    "--new-window",
    "--window-position=0,0",
    "--window-size=1280,1000",
    $ProductUrl
  )
  $script:ChromeProcess = Start-Process -FilePath $Bootstrap.executablePath -ArgumentList $arguments -PassThru
  Wait-ForProduct
}

function Return-ToCounter([string]$Expected, [int]$Limit) {
  for ($attempt = 0; $attempt -lt $Limit; $attempt += 1) {
    $current = Get-PageCounter
    if ($current -eq $Expected) { return }
    Move-Reader "previous" | Out-Null
  }
  throw "Could not return to BookWalker counter $Expected."
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null

try {
  if (-not $env:BACKEND_AUTH_TOKEN) { $env:BACKEND_AUTH_TOKEN = New-AuthToken }
  $port = Get-FreePort
  $backendUrl = "http://127.0.0.1:$port"
  $env:UV_PROJECT_ENVIRONMENT = Join-Path $BackendDir ".venv-gpu"

  Write-Uat "Building the extension (dist/chrome-mv3)."
  Push-Location $RepoRoot
  try { & npm run build | Out-Null } finally { Pop-Location }
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $ExtensionDir "manifest.json"))) { throw "Extension build failed." }

  Write-Uat "Syncing locked GPU runtime."
  & uv sync --project $BackendDir --locked --no-default-groups --group gpu --python 3.12 | Out-Null
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $GpuPython)) { throw "Locked GPU runtime sync failed." }

  $health = Start-UatBackend $port
  $missingStatus = Get-HttpStatus "$backendUrl/health" @{}
  $wrongStatus = Get-HttpStatus "$backendUrl/health" @{ "X-Manga-Translator-Token" = "wrong-uat-token" }
  if ($missingStatus -ne 401 -or $wrongStatus -ne 401) { throw "Backend authentication edge checks failed." }

  $env:BOOKWALKER_PROFILE_DIR = $ProfileDir
  $env:BOOKWALKER_BACKEND_URL = $backendUrl
  $env:BOOKWALKER_BOOTSTRAP_FILE = $BootstrapFile
  $env:BOOKWALKER_PRODUCT_URL = $ProductUrl
  Remove-Item Env:BOOKWALKER_INSPECT_ONLY -ErrorAction SilentlyContinue
  Write-Uat "Preparing extension profile and establishing the product reader session."
  & node $HelperPath
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $BootstrapFile)) { throw "Extension profile bootstrap failed." }
  $bootstrap = Get-Content -LiteralPath $BootstrapFile -Raw | ConvertFrom-Json

  Start-NativeChrome $bootstrap
  Open-ViewerFromProduct
  Save-WindowScreenshot "viewer-opened.png"
  Save-AccessibilityDump "viewer-accessibility.txt"
  $firstCounter = Wait-For -TimeoutSeconds 90 -Failure "Initial BookWalker counter was not found." -Check { Get-PageCounter }
  $counters = New-Object Collections.Generic.List[string]

  for ($index = 1; $index -le $Pages; $index += 1) {
    $counter = Get-PageCounter
    if ($counters.Contains($counter)) { throw "Duplicate BookWalker page counter: $counter" }
    $counters.Add($counter)
    Translate-CurrentPage "page $index ($counter)" $true
    Save-WindowScreenshot ("page-{0:D2}-{1}.png" -f $index, $counter.Replace("/", "-"))
    Write-Uat "Translated page $index at $counter."
    if ($index -lt $Pages) { Move-Reader "next" | Out-Null }
  }
  if ((Get-SuccessCount) -ne $Pages) { throw "Expected exactly $Pages backend translations." }

  for ($index = $Pages - 1; $index -ge 1; $index -= 1) { Move-Reader "previous" | Out-Null }
  Return-ToCounter $firstCounter ($Pages + 2)
  Translate-CurrentPage "same-session page 1 cache" $false
  Save-WindowScreenshot "cache-page-1-same-session.png"

  Stop-UatChrome
  Start-NativeChrome $bootstrap
  Open-ViewerFromProduct
  Return-ToCounter $firstCounter ($Pages + 2)
  Translate-CurrentPage "browser-restart page 1 cache" $false
  Save-WindowScreenshot "cache-page-1-after-restart.png"
  Stop-UatChrome

  $env:BOOKWALKER_INSPECT_ONLY = "1"
  & node $HelperPath
  if ($LASTEXITCODE -ne 0) { throw "Persistent cache inspection failed." }
  $cacheInspection = (Get-Content -LiteralPath $BootstrapFile -Raw | ConvertFrom-Json).cache
  if ($cacheInspection.count -lt $Pages -or $cacheInspection.metadataCount -lt $Pages) {
    throw "Persistent cache contains fewer than $Pages pages."
  }
  Remove-Item Env:BOOKWALKER_INSPECT_ONLY -ErrorAction SilentlyContinue

  Start-NativeChrome $bootstrap
  Open-ViewerFromProduct
  Return-ToCounter $firstCounter ($Pages + 2)
  for ($index = 0; $index -lt $Pages; $index += 1) { Move-Reader "next" | Out-Null }
  $recoveryCounter = Get-PageCounter
  $beforeRecovery = Get-SuccessCount
  Stop-UatBackend
  Invoke-NamedControl "Translate visible images" 30
  Start-Sleep -Seconds 6
  if ((Get-SuccessCount) -ne $beforeRecovery) { throw "Offline backend edge unexpectedly translated a page." }
  $health = Start-UatBackend $port
  Translate-CurrentPage "backend recovery page ($recoveryCounter)" $true
  Save-WindowScreenshot "backend-recovery.png"

  $summary = [ordered]@{
    productUrl = $ProductUrl
    viewerUrl = $ViewerUrl
    device = $health.device
    pages = $counters
    initialTranslations = $Pages
    totalTranslations = Get-SuccessCount
    sameSessionCacheHit = $true
    browserRestartCacheHit = $true
    persistentCacheEntries = $cacheInspection.count
    persistentMetadataEntries = $cacheInspection.metadataCount
    missingAuthStatus = $missingStatus
    wrongAuthStatus = $wrongStatus
    backendRecoveryCounter = $recoveryCounter
    backendRecoveryPassed = $true
  }
  $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $SummaryFile -Encoding UTF8
  Write-Uat "PASS. Summary: $SummaryFile"
} finally {
  Stop-UatChrome
  Stop-UatBackend
  foreach ($name in @("BOOKWALKER_PROFILE_DIR", "BOOKWALKER_BACKEND_URL", "BOOKWALKER_BOOTSTRAP_FILE", "BOOKWALKER_PRODUCT_URL", "BOOKWALKER_INSPECT_ONLY")) {
    Remove-Item ("Env:" + $name) -ErrorAction SilentlyContinue
  }
  if ($HadAuthToken) { $env:BACKEND_AUTH_TOKEN = $PreviousAuthToken } else { Remove-Item Env:BACKEND_AUTH_TOKEN -ErrorAction SilentlyContinue }
  if ($HadUvEnvironment) { $env:UV_PROJECT_ENVIRONMENT = $PreviousUvEnvironment } else { Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue }
  $resolvedProfile = [IO.Path]::GetFullPath($ProfileDir)
  $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
  if ($resolvedProfile.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
      [IO.Path]::GetFileName($resolvedProfile).StartsWith("manga-bookwalker-uat-")) {
    Remove-Item -LiteralPath $resolvedProfile -Recurse -Force -ErrorAction SilentlyContinue
  }
}
