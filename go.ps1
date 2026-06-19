# go.ps1 — Один скрипт делает всё: коммит + деплой на Fly.io
# Запуск: cd C:\Users\jfsag\Documents\GitHub\botvot && .\go.ps1

$ErrorActionPreference = "Stop"
$APP_NAME = "botvot-prod"
$REGION = "waw"
$VOLUME_NAME = "botvot_data"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  botvot — Коммит + Деплой на Fly.io" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $PSScriptRoot

# ── Шаг 1: Снимаем git lock если есть ─────────────────────────────────
$lockFile = ".git\index.lock"
if (Test-Path $lockFile) {
    Write-Host "Убираю git lock..." -ForegroundColor Yellow
    Remove-Item $lockFile -Force
    Write-Host "Lock убран" -ForegroundColor Green
}

# ── Шаг 2: Git коммит и push ───────────────────────────────────────────
Write-Host "Коммичу изменения..." -ForegroundColor Yellow
git add bots/sales_bot.py core/database.py core/models.py fly.toml deploy_fly.ps1 DEPLOY_FREE.md Dockerfile .github/
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "feat: KOOB partner integration + Fly.io deployment"
    Write-Host "Коммит создан" -ForegroundColor Green
} else {
    Write-Host "Нечего коммитить (уже закоммичено)" -ForegroundColor Gray
}

Write-Host "Пушу в GitHub..." -ForegroundColor Yellow
git push origin main
Write-Host "Push выполнен" -ForegroundColor Green

# ── Шаг 3: Устанавливаем flyctl если нет ──────────────────────────────
if (-not (Get-Command flyctl -ErrorAction SilentlyContinue)) {
    Write-Host "Устанавливаю flyctl..." -ForegroundColor Yellow
    iwr https://fly.io/install.ps1 -useb | iex
    # Обновляем PATH для текущей сессии
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}
Write-Host "flyctl: $(flyctl version 2>&1 | Select-Object -First 1)" -ForegroundColor Green

# ── Шаг 4: Авторизация на Fly.io ──────────────────────────────────────
$whoami = flyctl auth whoami 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Нужна авторизация на Fly.io — откроется браузер." -ForegroundColor Yellow
    Write-Host "Войдите через GitHub, затем вернитесь сюда." -ForegroundColor Yellow
    flyctl auth login
    $whoami = flyctl auth whoami 2>&1
}
Write-Host "Fly.io: $whoami" -ForegroundColor Green

# ── Шаг 5: Создаём приложение если нет ────────────────────────────────
$apps = flyctl apps list 2>&1
if ($apps -notmatch $APP_NAME) {
    Write-Host "Создаю приложение $APP_NAME..." -ForegroundColor Yellow
    flyctl apps create $APP_NAME --machines
}

# ── Шаг 6: Создаём volume если нет ────────────────────────────────────
$volumes = flyctl volumes list --app $APP_NAME 2>&1
if ($volumes -notmatch $VOLUME_NAME) {
    Write-Host "Создаю диск для базы данных..." -ForegroundColor Yellow
    flyctl volumes create $VOLUME_NAME --size 1 --region $REGION --app $APP_NAME --yes
}

# ── Шаг 7: Загружаем секреты из .env ──────────────────────────────────
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "ОШИБКА: нет файла .env!" -ForegroundColor Red
    Write-Host "Скопируйте .env.example -> .env и заполните токены." -ForegroundColor Red
    exit 1
}

Write-Host "Загружаю секреты из .env в Fly.io..." -ForegroundColor Yellow
$secrets = @()
Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line.Split("=", 2)
        if ($parts.Length -eq 2) {
            $val = $parts[1].Trim()
            if ($val -and $val -notmatch "your_.*_here") {
                $secrets += "$($parts[0].Trim())=$val"
            }
        }
    }
}
# Fly.io-специфичные переменные
$secrets += "PROD_DATABASE_PATH=/app/data/course_platform.db"
$secrets += "DATABASE_PATH=/app/data/course_platform.db"

$secretsArgs = $secrets -join " "
Invoke-Expression "flyctl secrets set $secretsArgs --app $APP_NAME" | Out-Null
Write-Host "Секреты загружены ($($secrets.Count) переменных)" -ForegroundColor Green

# ── Шаг 8: Деплой ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "Деплою на Fly.io..." -ForegroundColor Yellow
flyctl deploy --app $APP_NAME --remote-only

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Готово! Бот запущен на Fly.io" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Логи:    flyctl logs --app $APP_NAME" -ForegroundColor Cyan
Write-Host "Статус:  flyctl status --app $APP_NAME" -ForegroundColor Cyan
Write-Host ""

# ── Шаг 9: Токен для GitHub Actions ───────────────────────────────────
Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "Чтобы деплой шёл автоматически при push:" -ForegroundColor White
Write-Host "1. Скопируйте токен ниже" -ForegroundColor White
Write-Host "2. GitHub -> Settings -> Secrets -> New secret" -ForegroundColor White
Write-Host "3. Имя: FLY_API_TOKEN, значение: токен" -ForegroundColor White
Write-Host "─────────────────────────────────────────────" -ForegroundColor DarkGray
flyctl auth token
