# setup_env.ps1 - Setup local environment for VisionAI Aegis (No Docker)

Write-Host "=== Setting up VisionAI Aegis Local Environment ===" -ForegroundColor Green

# 1. Check Python installation
$pythonPath = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonPath) {
    Write-Host "Error: Python is not installed or not in PATH." -ForegroundColor Red
    exit 1
}

# 2. Create Python virtual environment if it doesn't exist
if (-not (Test-Path "venv")) {
    Write-Host "Creating Python virtual environment in .\venv..." -ForegroundColor Yellow
    python -m venv venv
} else {
    Write-Host "Python virtual environment 'venv' already exists." -ForegroundColor Cyan
}

# 3. Activate venv and install backend requirements
Write-Host "Installing Python backend dependencies..." -ForegroundColor Yellow
.\venv\Scripts\pip.exe install --upgrade pip
.\venv\Scripts\pip.exe install -r backend\requirements.txt

# 4. Install Frontend dependencies
$nodePath = Get-Command node -ErrorAction SilentlyContinue
if ($nodePath) {
    Write-Host "Installing Frontend npm dependencies..." -ForegroundColor Yellow
    Push-Location frontend
    npm install
    Pop-Location
} else {
    Write-Host "Warning: Node.js is not installed. Install Node.js to run the frontend." -ForegroundColor Red
}

# 5. Create .env file if it doesn't exist
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env file from .env.example..." -ForegroundColor Yellow
    Copy-Item .env.example .env
} else {
    Write-Host ".env file already exists." -ForegroundColor Cyan
}

Write-Host "`n=== Setup Complete! ===" -ForegroundColor Green
Write-Host "To start the backend, run: .\scripts\start_backend.ps1"
Write-Host "To start the frontend, run: .\scripts\start_frontend.ps1"
