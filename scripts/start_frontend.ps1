# start_frontend.ps1 - Start Next.js Frontend locally

Write-Host "=== Starting VisionAI Aegis Frontend ===" -ForegroundColor Green

Push-Location frontend
if (-not (Test-Path "node_modules")) {
    Write-Host "Installing npm dependencies..." -ForegroundColor Yellow
    npm install
}

Write-Host "Launching Next.js Dev Server at http://localhost:3000..." -ForegroundColor Cyan
npm run dev
Pop-Location
