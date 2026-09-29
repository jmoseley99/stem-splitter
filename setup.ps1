<#
    Stem Splitter - one-time setup for a fresh Windows machine.

    Installs FFmpeg (via winget), creates a Python virtual environment, and
    installs all dependencies in the correct order. Run this once; afterwards
    use run.bat (or `streamlit run app.py`) to launch the app.

    Usage (from the project folder, in PowerShell):
        powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "=== Stem Splitter setup ===" -ForegroundColor Cyan

# --- 1. Check Python (3.12.x recommended) -----------------------------------
Write-Host "`n[1/5] Checking Python..." -ForegroundColor Yellow
$pythonCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} else {
    Write-Host "Python was not found." -ForegroundColor Red
    Write-Host "Please install Python 3.12 first:" -ForegroundColor Red
    Write-Host "  1. Go to https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "  2. Download and run the installer" -ForegroundColor Red
    Write-Host "  3. IMPORTANT: tick 'Add python.exe to PATH' on the first screen" -ForegroundColor Red
    Write-Host "  4. After it installs, run this setup again." -ForegroundColor Red
    exit 1
}
& $pythonCmd --version

# --- 2. Install FFmpeg if missing -------------------------------------------
Write-Host "`n[2/5] Checking FFmpeg..." -ForegroundColor Yellow
$ffmpegOnPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
$wingetFfmpeg = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\ffmpeg.exe"
if ($ffmpegOnPath -or (Test-Path $wingetFfmpeg)) {
    Write-Host "FFmpeg already installed."
} else {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing FFmpeg via winget..."
        winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
        # Refresh this session's PATH so the verify step and app can see FFmpeg
        # without needing a new terminal.
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")
        Write-Host "FFmpeg installed."
    } else {
        Write-Host "winget is not available. Please install FFmpeg manually from https://www.gyan.dev/ffmpeg/builds/ and add it to PATH, then re-run." -ForegroundColor Red
        exit 1
    }
}

# --- 3. Create the virtual environment --------------------------------------
Write-Host "`n[3/5] Creating virtual environment (.venv)..." -ForegroundColor Yellow
if (Test-Path ".\.venv") {
    Write-Host ".venv already exists, reusing it."
} else {
    & $pythonCmd -m venv .venv
}
$venvPython = ".\.venv\Scripts\python.exe"

# --- 4. Install dependencies ------------------------------------------------
Write-Host "`n[4/5] Installing dependencies (this can take a few minutes)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip

# PyTorch CPU wheels come from the PyTorch index, so install them first.
Write-Host "Installing PyTorch (CPU build)..."
& $venvPython -m pip install torch==2.2.2 torchaudio==2.2.2 --index-url https://download.pytorch.org/whl/cpu

Write-Host "Installing the rest of the requirements..."
& $venvPython -m pip install -r requirements.txt

# --- 5. Verify the imports work ---------------------------------------------
Write-Host "`n[5/5] Verifying installation..." -ForegroundColor Yellow
& $venvPython -c "import demucs, torch, streamlit, soundfile, numpy, librosa, yt_dlp, truststore; print('All imports OK  -  torch', torch.__version__, '| numpy', numpy.__version__)"

Write-Host "`n=== Setup complete ===" -ForegroundColor Green
Write-Host "(The first stem separation downloads the Demucs model once - a one-time delay.)"
