# Starts the built exe in server mode the way Pal Engine does and checks the handshake:
# the ready line, the token check, the Origin check, and exit when stdin closes.
param([string]$Exe = (Join-Path $PSScriptRoot '..\dist\LmOpenAiProvider.exe'))

$ErrorActionPreference = 'Stop'
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$homeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("lm-provider-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory $homeDir | Out-Null

$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = (Resolve-Path $Exe)
$info.Arguments = "--serve --parent-pid $PID"
$info.UseShellExecute = $false
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$info.CreateNoWindow = $true
$info.EnvironmentVariables['PAL_ENGINE_SESSION_TOKEN'] = $token
$info.EnvironmentVariables['LM_PROVIDER_HOME'] = $homeDir
$process = [System.Diagnostics.Process]::Start($info)

function Invoke-Status($Uri, $Headers) {
    try { return (Invoke-WebRequest -Uri $Uri -Headers $Headers -UseBasicParsing -TimeoutSec 10).StatusCode }
    catch { if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode } throw }
}

try {
    $readLine = $process.StandardOutput.ReadLineAsync()
    if (-not $readLine.Wait(20000)) { throw "No ready line within 20 s" }
    $ready = $readLine.Result | ConvertFrom-Json
    if ($ready.event -ne 'ready' -or -not $ready.port) { throw "Unexpected first line: $($readLine.Result)" }
    Write-Host "Ready on port $($ready.port), version $($ready.version)"
    $base = "http://127.0.0.1:$($ready.port)"

    $ok = Invoke-Status "$base/health" @{ Authorization = "Bearer $token" }
    if ($ok -ne 200) { throw "/health with the token returned $ok" }
    $noToken = Invoke-Status "$base/health" @{}
    if ($noToken -ne 401) { throw "/health without the token returned $noToken" }
    $origin = Invoke-Status "$base/health" @{ Authorization = "Bearer $token"; Origin = 'https://example.com' }
    if ($origin -ne 403) { throw "/health with an Origin returned $origin" }

    $process.StandardInput.Close()
    if (-not $process.WaitForExit(10000)) { throw "The server did not exit after stdin closed" }
    $stderr = $process.StandardError.ReadToEnd()
    if ($stderr.Contains($token)) { throw "The session token appeared in stderr" }
    Write-Host 'Smoke test passed'
} finally {
    if (-not $process.HasExited) { $process.Kill() }
    Remove-Item -Recurse -Force $homeDir -ErrorAction SilentlyContinue
}
