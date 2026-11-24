# =============================================================================
#  Click Companion - Windows installer
#  Installs: uv (Python manager), Python 3.12, Ollama, the Companion app, the AI models
#  that fit this computer, a firewall rule for your home network, and auto-start.
#  Safe to run again (it skips what's already done).
# =============================================================================
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Scripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Scripts
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Pyw = Join-Path $Venv "Scripts\pythonw.exe"
function Step($t) { Write-Host "`n==> $t" -ForegroundColor Magenta }
function Ok($t = "OK") { Write-Host $t -ForegroundColor Green }
function Warn($t) { Write-Host $t -ForegroundColor Yellow }

Step "Checking this computer"
if (-not [Environment]::Is64BitOperatingSystem) { throw "Click Companion needs 64-bit Windows." }
try { Invoke-WebRequest -UseBasicParsing -Uri "https://ollama.com" -TimeoutSec 15 | Out-Null } catch { throw "No internet connection." }
Ok

Step "Installing uv (manages Python for Click)"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Ok

Step "Installing Ollama (runs the AI models on this computer)"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    $exe = Join-Path $env:TEMP "OllamaSetup.exe"
    Invoke-WebRequest -UseBasicParsing -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $exe
    Start-Process -FilePath $exe -ArgumentList "/SILENT" -Wait
    $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
}
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null; break }
    catch { if ($i -eq 0) { Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden }; Start-Sleep 1 }
}
Ok

Step "Installing the Click Companion (5-10 minutes)"
uv venv --python 3.12 $Venv --allow-existing
uv pip install --python $Py -r (Join-Path $Root "requirements.txt") -r (Join-Path $Root "requirements-windows.txt")
Ok

Step "Checking your hardware"
& $Py (Join-Path $Scripts "print_models.py") --summary
$images = (& $Py (Join-Path $Scripts "print_models.py") --images).Trim()
if ($images -eq "yes") {
    Step "Installing image generation for your NVIDIA GPU (large download, 10-30 minutes)"
    uv pip install --python $Py torch --index-url https://download.pytorch.org/whl/cu124
    uv pip install --python $Py -r (Join-Path $Root "requirements-images.txt")
    Ok
} else {
    Warn "No NVIDIA GPU found, so image generation is off (everything else works)."
}

Step "Downloading the AI models for this computer (large download)"
$models = & $Py (Join-Path $Scripts "print_models.py")
foreach ($m in $models) { if ($m.Trim()) { ollama pull $m.Trim() } }
if ($images -eq "yes") {
    Push-Location $Root
    & $Py -m companion.imagegen --download
    Pop-Location
}
Ok

Step "Allowing the Click box to reach this computer (home network only)"
Write-Host "Windows will ask for permission once. Click Yes."
$rule = "New-NetFirewallRule -DisplayName 'Click Companion' -Direction Inbound -Protocol TCP -LocalPort 47321 -Action Allow -Profile Private -ErrorAction SilentlyContinue | Out-Null"
try { Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", $rule; Ok } catch { Warn "Skipped. If Windows asks later whether to allow Python on private networks, click Allow." }

Step "Starting Click Companion automatically when you sign in"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Startup"), [Environment]::GetFolderPath("Programs"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Click Companion.lnk"))
    $lnk.TargetPath = $Pyw
    $lnk.Arguments = "-m companion"
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "Click Companion"
    $lnk.Save()
}
Ok

Step "Starting Click Companion"
Start-Process -FilePath $Pyw -ArgumentList "-m", "companion" -WorkingDirectory $Root
Write-Host ""
Write-Host "Done! Look for the purple Click icon near the clock (it may be under the ^ arrow)." -ForegroundColor Green
Write-Host "Next: in the Click chat open Settings > Your computer > Pair a computer,"
Write-Host "then right-click the tray icon > 'Pair with Click...' and type the code."
