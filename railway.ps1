# railway.ps1 — Деплой на Railway (без карты, free trial $5)
# Запуск: ПКМ на файле -> Run with PowerShell

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  botvot — Деплой на Railway" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Шаг 1: Устанавливаем Railway CLI ──────────────────────────────────
Write-Host "Проверяю Railway CLI..." -ForegroundColor Yellow
$railwayCmd = Get-Command railway -ErrorAction SilentlyContinue
if (-not $railwayCmd) {
    Write-Host "Устанавливаю @railway/cli через npm..." -ForegroundColor Yellow
    npm install -g @railway/cli
    # Обновляем PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
Write-Host "Railway CLI: $(railway --version 2>&1)" -ForegroundColor Green

# ── Шаг 2: Авторизация ────────────────────────────────────────────────
Write-Host ""
Write-Host "Авторизуюсь в Railway..." -ForegroundColor Yellow
$whoami = railway whoami 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Откроется браузер — войдите через GitHub." -ForegroundColor Yellow
    railway login --browserless
}
Write-Host "Railway: $(railway whoami 2>&1)" -ForegroundColor Green

# ── Шаг 3: Создаём / привязываем проект ───────────────────────────────
Write-Host ""
Write-Host "Инициализирую проект..." -ForegroundColor Yellow
$hasProject = Test-Path ".railway"
if (-not $hasProject) {
    # Создаём новый проект botvot
    railway init --name "botvot"
    Write-Host "Проект создан" -ForegroundColor Green
} else {
    Write-Host "Проект уже привязан" -ForegroundColor Green
}

# ── Шаг 4: Создаём volume для SQLite ─────────────────────────────────
Write-Host ""
Write-Host "Настраиваю volume для базы данных..." -ForegroundColor Yellow
# railway volume create требует dashboard или CLI v3+, пропускаем — БД создастся на эфемерном диске
# Для продакшна после деплоя добавьте volume вручную в dashboard

# ── Шаг 5: Загружаем переменные из .env ──────────────────────────────
Write-Host ""
Write-Host "Загружаю переменные окружения..." -ForegroundColor Yellow

if (-not (Test-Path ".env")) {
    Write-Host "ОШИБКА: нет .env файла!" -ForegroundColor Red
    exit 1
}

$vars = @()
Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line.Split("=", 2)
        if ($parts.Length -eq 2) {
            $key = $parts[0].Trim()
            $val = $parts[1].Trim()
            if ($val -and $val -notmatch "your_.*_here" -and $val -notmatch "your_.*_invite") {
                $vars += "$key=$val"
            }
        }
    }
}

# Переопределяем путь к БД для Railway
$vars += "DATABASE_PATH=/app/data/course_platform.db"
$vars += "PROD_DATABASE_PATH=/app/data/course_platform.db"

foreach ($v in $vars) {
    $key = $v.Split("=")[0]
    $val = $v.Substring($key.Length + 1)
    railway variables set "$key=$val" --yes 2>&1 | Out-Null
}
Write-Host "Загружено $($vars.Count) переменных" -ForegroundColor Green

# ── Шаг 6: Деплой ────────────────────────────────────────────────────
Write-Host ""
Write-Host "Деплою на Railway..." -ForegroundColor Yellow
railway up --detach

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Готово! Бот задеплоен на Railway" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Логи:    railway logs" -ForegroundColor Cyan
Write-Host "Статус:  railway status" -ForegroundColor Cyan
Write-Host "Дашборд: https://railway.com/dashboard" -ForegroundColor Cyan
Write-Host ""
Write-Host "ВАЖНО: Добавьте постоянный volume в дашборде Railway:" -ForegroundColor Yellow
Write-Host "  Project -> Add Volume -> Mount path: /app/data" -ForegroundColor Yellow
Write-Host "  Это нужно чтобы база данных сохранялась при рестартах" -ForegroundColor Yellow
Write-Host ""
