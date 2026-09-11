# start_backend.ps1 - Start FastAPI Backend locally

Write-Host "=== Starting VisionAI Aegis Backend ===" -ForegroundColor Green

if (-not (Test-Path "venv")) {
    Write-Host "Virtual environment not found. Running setup first..." -ForegroundColor Yellow
    .\scripts\setup_env.ps1
}

# Set PYTHONPATH so app module is importable
$env:PYTHONPATH = (Get-Item "backend").FullName

# Run Uvicorn backend server
Write-Host "Launching Uvicorn server at http://localhost:8000 (Docs: http://localhost:8000/docs)..." -ForegroundColor Cyan
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
