# Run only when service startup has been authorized. No secret is passed in arguments.
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '..\..\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Project Python is required; no service was started.'
}
Push-Location $PSScriptRoot
try {
    & $taskPython -B (Join-Path $PSScriptRoot 'prepare_settings.py')
    if ($LASTEXITCODE -ne 0) { throw 'Settings preparation failed; no service was started.' }
    # Compose validates the required-input extension; no signing secret environment
    # override is passed to the image. The mounted YAML supplies server.secret_key.
    & docker compose --env-file (Join-Path $PSScriptRoot '.env') -f (Join-Path $PSScriptRoot 'docker-compose.yml') up -d
    if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }
} finally {
    Pop-Location
}
