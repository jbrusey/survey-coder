$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

uv sync --extra packaging
uv run pyinstaller --noconfirm --clean "packaging\windows\survey-coder.spec"

$Iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
    $DefaultIscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path $DefaultIscc) {
        $Iscc = Get-Item $DefaultIscc
    }
}

if ($Iscc) {
    & $Iscc "packaging\windows\SurveyCoder.iss"
    Write-Host "Installer created in dist\installer"
} else {
    Write-Warning "Inno Setup 6 was not found. The portable app is available in 'dist\Survey Coder'."
}
