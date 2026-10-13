<#
.SYNOPSIS
  Installe le plugin Pear Connect dans Pear Desktop 3.12.0 (ou le désinstalle).

.DESCRIPTION
  Pear Desktop ne charge pas de plugins externes : ils sont compilés dans l'application.
  Ce script remplace donc resources\app.asar par une version de Pear 3.12.0 qui contient
  en plus le plugin « Pear Connect » (code : branche pear-connect du fork), puis le configure :
    1. vérifie que Pear Desktop installé est bien la version 3.12.0 ;
    2. ferme Pear, sauvegarde app.asar d'origine (app.asar.original) et config.json ;
    3. installe le nouvel app.asar ;
    4. copie l'autorité de certification du homelab et configure le plugin
       (serveur, nom de l'ordinateur, jeton) dans %APPDATA%\YouTube Music\config.json ;
    5. désactive l'ancien agent pearsync (tâche planifiée), que le plugin remplace ;
    6. relance Pear.

  À lancer depuis le dossier qui contient app.asar (ou app-asar.zip), jetons.txt et pearconnect-ca.crt :
    powershell -ExecutionPolicy Bypass -File .\install-pear-connect.ps1 -Device the-kernel
  Retour à Pear d'origine :
    powershell -ExecutionPolicy Bypass -File .\install-pear-connect.ps1 -Uninstall

  Une mise à jour de Pear Desktop remplace app.asar : le plugin disparaît (Pear reste
  fonctionnel). Il faudra alors une version du plugin construite pour la nouvelle version.
#>
[CmdletBinding(DefaultParameterSetName = 'Install')]
param(
    [Parameter(ParameterSetName = 'Install', Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')][string]$Device,
    [Parameter(ParameterSetName = 'Install')][string]$Server = 'https://192.168.1.168:8443',
    [Parameter(ParameterSetName = 'Install')][string]$TokensFile,
    [Parameter(ParameterSetName = 'Install')][string]$CaFile,
    [Parameter(ParameterSetName = 'Install')][string]$Asar,
    [Parameter(ParameterSetName = 'Install')][switch]$ImportCaForBrowser,
    [Parameter(ParameterSetName = 'Uninstall', Mandatory = $true)][switch]$Uninstall,
    [string]$PearDir
)
$ErrorActionPreference = 'Stop'
$ExpectedVersion = '3.12.0'

# Windows PowerShell 5.1 : $PSScriptRoot est vide pendant l'évaluation des paramètres.
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $TokensFile) { $TokensFile = Join-Path $here 'jetons.txt' }
if (-not $CaFile) { $CaFile = Join-Path $here 'pearconnect-ca.crt' }
if (-not $Asar) { $Asar = Join-Path $here 'app.asar' }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Find-PearDir {
    # 1. Pear en cours d'exécution
    $p = Get-Process -Name 'YouTube Music' -ErrorAction SilentlyContinue | Where-Object Path | Select-Object -First 1
    if ($p) { return Split-Path -Parent $p.Path }
    # 2. Emplacements d'installation habituels
    $candidates = @()
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        foreach ($name in 'Programs\youtube-music', 'Programs\pear-desktop', 'YouTube Music', 'youtube-music', 'Pear Desktop') {
            $candidates += (Join-Path $base $name)
        }
    }
    # 3. Programmes installés (registre)
    foreach ($root in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                      'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                      'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*') {
        Get-ItemProperty $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like '*YouTube Music*' -or $_.DisplayName -like '*Pear*' } |
            ForEach-Object {
                if ($_.InstallLocation) { $candidates += $_.InstallLocation.Trim('"') }
                if ($_.DisplayIcon) { $candidates += (Split-Path -Parent ($_.DisplayIcon -replace '"|,\d+$', '')) }
            }
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path (Join-Path $c 'YouTube Music.exe'))) { return $c }
    }
    return $null
}

if (-not $PearDir) { $PearDir = Find-PearDir }
if (-not $PearDir) {
    throw "Pear Desktop introuvable. Lance Pear puis relance ce script, ou indique son dossier avec -PearDir `"C:\chemin\vers\le\dossier`" (celui qui contient « YouTube Music.exe »)."
}
Write-Host "Pear Desktop : $PearDir"
$exe = Join-Path $PearDir 'YouTube Music.exe'
$resources = Join-Path $PearDir 'resources'
$installed = Join-Path $resources 'app.asar'
$original = Join-Path $resources 'app.asar.original'
$configFile = Join-Path $env:APPDATA 'YouTube Music\config.json'
$dataDir = Join-Path $env:APPDATA 'pearsync'

function Stop-Pear {
    $procs = Get-Process -Name 'YouTube Music' -ErrorAction SilentlyContinue
    if (-not $procs) { return }
    Write-Host 'Fermeture de Pear Desktop…'
    $procs | ForEach-Object { $_.CloseMainWindow() | Out-Null }
    for ($i = 0; $i -lt 16 -and (Get-Process -Name 'YouTube Music' -ErrorAction SilentlyContinue); $i++) {
        Start-Sleep -Milliseconds 500
    }
    # resté dans la zone de notification : arrêt forcé (la configuration est déjà sur disque)
    Get-Process -Name 'YouTube Music' -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
}

function Read-PearConfig {
    if (-not (Test-Path $configFile)) { throw "Configuration de Pear introuvable : $configFile (lancer Pear une fois)" }
    return Get-Content -Raw -Encoding UTF8 $configFile | ConvertFrom-Json
}

function Write-PearConfig($cfg) {
    # Windows PowerShell 5.1 : sans ceci, certains tableaux sortent en {"value": […], "Count": n}
    Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
    $json = ConvertTo-Json -InputObject $cfg -Depth 64
    [System.IO.File]::WriteAllText($configFile, $json, (New-Object System.Text.UTF8Encoding $false))
}

function Set-Member($obj, [string]$name, $value) {
    if ($obj.PSObject.Properties[$name]) { $obj.$name = $value }
    else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

if (-not (Test-Path $exe)) { throw "Pear Desktop introuvable : $exe (préciser -PearDir)" }

# --- Désinstallation ---------------------------------------------------------------------
if ($Uninstall) {
    Step 'Retour à Pear Desktop d''origine'
    if (-not (Test-Path $original)) { throw "Pas de sauvegarde $original : rien à restaurer." }
    Stop-Pear
    Copy-Item -Force $original $installed
    Remove-Item -Force $original
    $cfg = Read-PearConfig
    if ($cfg.plugins -and $cfg.plugins.PSObject.Properties['pear-connect']) {
        $cfg.plugins.'pear-connect'.enabled = $false
        Write-PearConfig $cfg
    }
    if (Get-ScheduledTask -TaskName 'pearsync-agent' -ErrorAction SilentlyContinue) {
        Enable-ScheduledTask -TaskName 'pearsync-agent' | Out-Null
        Start-ScheduledTask -TaskName 'pearsync-agent'
        Write-Host 'Agent pearsync réactivé.'
    }
    Start-Process -FilePath $exe
    Write-Host "`nPear Desktop d'origine restauré." -ForegroundColor Green
    return
}

# --- Vérifications -------------------------------------------------------------------------
Step 'Vérifications'
$version = (Get-Item $exe).VersionInfo.ProductVersion
if (-not $version.StartsWith($ExpectedVersion)) {
    throw "Pear Desktop $version installé : ce plugin est construit pour la version $ExpectedVersion."
}
Write-Host "Pear Desktop $version"
# app.asar livré compressé (app-asar.zip) : extraction automatique
if (-not (Test-Path $Asar)) {
    $zip = Join-Path $here 'app-asar.zip'
    if (Test-Path $zip) {
        $tmp = Join-Path $env:TEMP 'pear-connect-asar'
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $Asar = Join-Path $tmp 'app.asar'
    }
}
foreach ($f in @($Asar, $CaFile, $TokensFile)) {
    if (-not (Test-Path $f)) { throw "Fichier introuvable : $f" }
}
$line = Get-Content $TokensFile | Where-Object { $_ -match "^\s*$([regex]::Escape($Device))\s*=\s*(\S+)\s*$" } | Select-Object -First 1
if (-not $line) { throw "Aucun jeton pour « $Device » dans $TokensFile (le créer côté serveur avec add-device $Device)." }
$token = ($line -split '=', 2)[1].Trim()

# --- Installation --------------------------------------------------------------------------
Step 'Installation du plugin dans Pear Desktop'
Stop-Pear
if (-not (Test-Path $original)) {
    Copy-Item $installed $original
    Write-Host "Sauvegarde : $original"
}
Copy-Item -Force $Asar $installed
Copy-Item -Force $configFile "$configFile.pearconnect.bak"

Step 'Autorité de certification du homelab'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$caDest = Join-Path $dataDir 'pearconnect-ca.crt'
Copy-Item -Force $CaFile $caDest
Write-Host "Copiée dans $caDest (utilisée par le plugin, sans toucher au magasin de Windows)"
if ($ImportCaForBrowser) {
    # pour la télécommande web en HTTPS dans le navigateur ; Windows demande confirmation
    Import-Certificate -FilePath $caDest -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    Write-Host 'Ajoutée aux autorités racines de confiance de l''utilisateur (navigateurs).'
}

Step 'Configuration du plugin'
$cfg = Read-PearConfig
if (-not $cfg.PSObject.Properties['plugins']) { Set-Member $cfg 'plugins' ([pscustomobject]@{}) }
$plugin = [pscustomobject]@{
    enabled = $true
    server  = $Server.TrimEnd('/')
    device  = $Device
    token   = $token
    caPath  = $caDest
}
Set-Member $cfg.plugins 'pear-connect' $plugin
Write-PearConfig $cfg
& icacls $configFile /inheritance:r /grant:r "$($env:USERNAME):(F)" | Out-Null  # contient le jeton
Write-Host "Écrit : $configFile (sauvegarde : config.json.pearconnect.bak)"

Step 'Ancien agent pearsync'
if (Get-ScheduledTask -TaskName 'pearsync-agent' -ErrorAction SilentlyContinue) {
    Disable-ScheduledTask -TaskName 'pearsync-agent' | Out-Null
    Get-Process pearsync-agent -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host 'Tâche planifiée pearsync-agent désactivée : le plugin la remplace.'
} else {
    Write-Host 'Aucun agent installé.'
}

Step 'Démarrage de Pear Desktop'
Start-Process -FilePath $exe
Write-Host "`nTerminé. Dans Pear : Plugins > Pear Connect (activé). Le bouton « appareils » apparaît dans la barre du lecteur." -ForegroundColor Green
Write-Host "Télécommande web : $Server"
