<#
.SYNOPSIS
  Installe l'agent pearsync (Pear Connect) sur ce PC Windows.

.DESCRIPTION
  1. installe le paquet pearsync pour l'utilisateur courant (Python 3.11+ requis) ;
  2. écrit %APPDATA%\pearsync\config.toml (instance Pear locale + serveur Connect) ;
  3. demande l'autorisation à l'API de Pear Desktop (cliquer « Autoriser » dans Pear) ;
  4. lance l'agent à chaque ouverture de session (tâche planifiée) et le démarre tout de suite.

  À lancer depuis le dossier qui contient pearsync.zip et jetons.txt :
    powershell -ExecutionPolicy Bypass -File .\install-agent.ps1 -Node the-kernel
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')][string]$Node,
    [string]$Server = 'http://192.168.1.168:8787',
    [string]$TokensFile,
    [string]$Package,
    [int]$PearPort = 26538
)
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 : $PSScriptRoot est vide pendant l'évaluation des paramètres,
# les chemins par défaut sont donc calculés ici.
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $TokensFile) { $TokensFile = Join-Path $here 'jetons.txt' }
if (-not $Package) { $Package = Join-Path $here 'pearsync.zip' }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# --- Python -------------------------------------------------------------------
Step 'Vérification de Python'
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    throw "Lanceur Python « py » introuvable. Installer Python : winget install Python.Python.3.12  puis relancer ce script."
}
$ver = & py -3 -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.11') { throw "Python $ver trouvé, 3.11 ou plus requis." }
Write-Host "Python $ver"

# --- Jeton de l'appareil ----------------------------------------------------------
Step "Jeton de l'appareil « $Node »"
if (-not (Test-Path $TokensFile)) { throw "Fichier de jetons introuvable : $TokensFile" }
$line = Get-Content $TokensFile | Where-Object { $_ -match "^\s*$([regex]::Escape($Node))\s*=\s*(\S+)\s*$" } | Select-Object -First 1
if (-not $line) { throw "Aucun jeton pour « $Node » dans $TokensFile (le créer côté serveur avec add-device $Node)." }
$token = ($line -split '=', 2)[1].Trim()

# --- Paquet -----------------------------------------------------------------------
Step 'Installation de pearsync'
if (-not (Test-Path $Package)) { throw "Paquet introuvable : $Package" }
& py -3 -m pip install --user --upgrade --disable-pip-version-check --no-warn-script-location --quiet $Package
if ($LASTEXITCODE -ne 0) { throw 'pip install a échoué' }

# --- Configuration ------------------------------------------------------------------
Step 'Configuration'
$cfgDir = Join-Path $env:APPDATA 'pearsync'
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfg = Join-Path $cfgDir 'config.toml'
$toml = @"
# Généré par install-agent.ps1 — voir config.example.toml pour toutes les options
client_id = "pearsync-$Node"
local = "$Node"
queue_limit = 25

[instances."$Node"]
url = "http://127.0.0.1:$PearPort"

[pear_app]
reload_mix = true

[relay]
url = "$Server"
node = "$Node"
token = "$token"
"@
# UTF-8 sans BOM : tomllib refuse le BOM
[System.IO.File]::WriteAllText($cfg, $toml, (New-Object System.Text.UTF8Encoding $false))
# Le fichier contient un jeton : accès réservé à l'utilisateur courant
& icacls $cfg /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
Write-Host "Écrit : $cfg"

# --- Connexion au serveur -------------------------------------------------------------
Step 'Test du serveur Connect'
try {
    Invoke-RestMethod -Uri "$Server/healthz" -TimeoutSec 5 | Out-Null
    Write-Host "Serveur joignable ($Server)"
} catch { throw "Serveur Connect injoignable : $Server ($($_.Exception.Message))" }

# --- Autorisation auprès de Pear -------------------------------------------------------
Step 'Autorisation auprès de Pear Desktop'
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$PearPort/swagger" -UseBasicParsing -TimeoutSec 3 | Out-Null
} catch {
    throw "API de Pear injoignable sur le port $PearPort : ouvrir Pear Desktop et activer Plugins > API Server, puis relancer."
}
Write-Host '>>> Clique « Autoriser » dans la fenêtre qui va s''ouvrir dans Pear Desktop <<<' -ForegroundColor Yellow
& py -3 -m pearsync auth $Node
if ($LASTEXITCODE -ne 0) { throw 'Autorisation refusée ou expirée' }

# --- Démarrage automatique -------------------------------------------------------------
Step "Démarrage automatique de l'agent"
$scripts = & py -3 -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
$agentExe = Join-Path $scripts 'pearsync-agent.exe'
if (-not (Test-Path $agentExe)) { throw "pearsync-agent.exe introuvable dans $scripts" }
$action = New-ScheduledTaskAction -Execute $agentExe
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'pearsync-agent' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Pear Connect : rend Pear Desktop pilotable depuis le serveur' -Force | Out-Null
Get-Process pearsync-agent -ErrorAction SilentlyContinue | Stop-Process -Force
Start-ScheduledTask -TaskName 'pearsync-agent'
Start-Sleep -Seconds 4

Step 'Vérification'
& py -3 -m pearsync status
Write-Host "`nTerminé. Journal de l'agent : $cfgDir\agent.log" -ForegroundColor Green
Write-Host "Télécommande : $Server (jeton « telecommande » de jetons.txt)"
