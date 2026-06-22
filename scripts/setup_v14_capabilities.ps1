param([switch]$SkipVisionModel)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Create .venv before running this setup.' }
& $Python -m pip install -r (Join-Path $Root 'requirements.txt')
& $Python -m playwright install chromium
if (-not $SkipVisionModel) {
    if (-not (Get-Command ollama.exe -ErrorAction SilentlyContinue)) {
        throw 'Ollama is required for local vision. Install it, then rerun.'
    }
    & ollama pull gemma3:4b
}
Write-Output 'AXON v1.4 browser and local-vision capabilities are ready.'
