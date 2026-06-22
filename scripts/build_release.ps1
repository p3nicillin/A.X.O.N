param(
    [string]$CertificatePath = $env:AXON_SIGN_CERT_PATH,
    [string]$CertificatePassword = $env:AXON_SIGN_CERT_PASSWORD,
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

if (-not $SkipTests) { & $Python -m pytest -q }
& $Python -m pip install --disable-pip-version-check pyinstaller
& $Python -m PyInstaller --noconfirm --clean AXON.spec

$Executable = Join-Path $Root 'dist\AXON\AXON.exe'
if ($CertificatePath -and (Test-Path $CertificatePath)) {
    $SignTool = (Get-Command signtool.exe -ErrorAction Stop).Source
    & $SignTool sign /f $CertificatePath /p $CertificatePassword /fd SHA256 `
        /tr http://timestamp.digicert.com /td SHA256 $Executable
}

$Iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($Iscc) { & $Iscc.Source (Join-Path $Root 'installer\AXON.iss') }
Write-Output "Release output: $Executable"
