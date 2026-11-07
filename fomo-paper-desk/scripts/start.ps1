param([switch]$Offline, [switch]$Check)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
    $venvPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $candidates = @()
        if ($env:DESK_PYTHON) { $candidates += $env:DESK_PYTHON }
        foreach ($name in @('python', 'python3')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($command) { $candidates += $command.Source }
        }
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            $resolved = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += $resolved }
        }
        # Codex desktop ships a Python runtime, usable without a system install.
        $runtimePaths = Get-ChildItem -Path "$env:ProgramFiles\WindowsApps\OpenAI.CodexPrimaryRuntime*\dependencies\python\python.exe" -ErrorAction SilentlyContinue
        $candidates += @($runtimePaths | Sort-Object FullName -Descending | ForEach-Object FullName)
        $pythonPath = $null
        foreach ($candidate in $candidates) {
            if ($candidate -like '*\Microsoft\WindowsApps\*') { continue }
            try {
                & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
            } catch { continue }
        }
        if (-not $pythonPath) { throw 'Python 3.12 oder neuer erforderlich. Python installieren, dann Start.cmd erneut oeffnen.' }
        Write-Host 'Lokale Python-Umgebung wird angelegt ...'
        & $pythonPath -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python-Umgebung konnte nicht erstellt werden.' }
    }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $requirementsHash = [BitConverter]::ToString($hasher.ComputeHash([System.IO.File]::ReadAllBytes((Join-Path (Get-Location) 'requirements.txt')))).Replace('-', '')
    } finally { $hasher.Dispose() }
    $stampPath = '.venv\desk-requirements.sha256'
    $installedHash = if (Test-Path -LiteralPath $stampPath) { (Get-Content -LiteralPath $stampPath -Raw).Trim() } else { '' }
    if ($installedHash -ne $requirementsHash) {
        Write-Host 'Abhaengigkeiten werden installiert (beim ersten Start Internet erforderlich) ...'
        & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen. Internetverbindung pruefen und erneut starten.' }
        Set-Content -LiteralPath $stampPath -Value $requirementsHash
    }
    if ($Check) {
        & $venvPython -m desk.main --check
    } elseif ($Offline) {
        & $venvPython -m desk.desktop --offline
    } else {
        & $venvPython -m desk.desktop
    }
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
