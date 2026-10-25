param(
    [switch]$Clean,
    # Python launcher to use; the release workflow passes 'python' (actions/setup-python).
    [string]$Python = 'py'
)

$ErrorActionPreference = 'Stop'

if ($Clean) {
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

# Native tools write notices to stderr; Windows PowerShell 5.1 would stop on them, so they
# are judged by their exit code instead.
function Invoke-Native([string]$What, [scriptblock]$Command) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $saved }
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

Invoke-Native 'pip install' { & $Python -m pip install -r requirements.txt pyinstaller }
Invoke-Native 'PyInstaller' { & $Python -m PyInstaller --clean --noconfirm LmOpenAiProvider.spec }

$manifest = Get-Content (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
$version = if ($manifest.version) { [string]$manifest.version } else { '0.0.0' }
$dist = Join-Path $PSScriptRoot 'dist'
$exePath = Join-Path $dist 'LmOpenAiProvider.exe'

# The exe and the manifest must name the same version (the updater compares the manifest).
$exeVersion = (& $exePath --version).Trim()
if ($exeVersion -ne $version) { throw "Exe version $exeVersion does not match manifest.json version $version" }

Copy-Item (Join-Path $PSScriptRoot 'manifest.json') $dist -Force
Copy-Item (Join-Path $PSScriptRoot 'README.md') $dist -Force
Copy-Item (Join-Path $PSScriptRoot 'LICENSE') (Join-Path $dist 'LICENSE.txt') -Force

$zipPath = Join-Path $dist "LmOpenAiProvider-$version.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

Push-Location $dist
try {
    Compress-Archive -Path @(
        'LmOpenAiProvider.exe',
        'manifest.json',
        'README.md',
        'LICENSE.txt'
    ) -DestinationPath $zipPath -CompressionLevel Optimal
} finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Plugin zip:' $zipPath
