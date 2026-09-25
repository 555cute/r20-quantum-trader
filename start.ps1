# ==============================================================================
# R20 Quantum Trader - PowerShell Start Script for Windows / DSH
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "🚀 [R20 Quantum Trader] Initializing system environment..." -ForegroundColor Cyan

# 1. Environment variables
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "$ScriptDir;$ScriptDir\scripts;$env:PYTHONPATH"

# 2. Virtual environment check
$PythonExe = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "❌ Error: Virtual environment .venv not found. Run deploy/install or python -m venv .venv" -ForegroundColor Red
    exit 1
}

# 3. Create required runtime directories
foreach ($dir in @("data", "logs", "backups")) {
    $fullPath = Join-Path $ScriptDir $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
}

# 4. Check or create .env
$EnvFile = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir "env.example"
if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Host "📝 Creating .env from env.example..." -ForegroundColor Yellow
        Copy-Item $EnvExample $EnvFile
    }
}

# 5. Initialize default instrument pool if not present
$PoolFile = Join-Path $ScriptDir "data\instrument_pool.json"
if (-not (Test-Path $PoolFile)) {
    Write-Host "📋 Initializing default instrument pool..." -ForegroundColor Yellow
    & $PythonExe -c "from scripts.instrument_pool import save_instruments, DEFAULT_INSTRUMENTS; save_instruments(DEFAULT_INSTRUMENTS)"
}

# 6. Launch Backend Engine
Write-Host "✨ Launching R20 Quantum Trader on http://127.0.0.1:8080 ..." -ForegroundColor Green
& $PythonExe -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
