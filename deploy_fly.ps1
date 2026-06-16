# deploy_fly.ps1 — Автоматический деплой на Fly.io
# Запустите один раз: .\deploy_fly.ps1
# После этого деплой будет происходить автоматически при push в main (GitHub Actions)

$ErrorActionPreference = "Stop"
$APP_NAME = "botvot-prod"
$REGION = "waw"
$VOLUME_NAME = "botvot_data"

Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host " Деплой botvot на Fly.io" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# 1. Проверяем flyctl
if (-not (Get-Command flyctl -ErrorAction SilentlyContinue)) {
    Write-Host "Устанавливаю flyctl..." -ForegroundColor Yellow
    iwr https://fly.io/install.ps1 -useb | iex
}
Write-Host "flyctl найден: $(flyctl version)" -ForegroundColor Green

# 2. Проверяем авторизацию
$authStatus = flyctl auth whoami 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Нужна авторизация на Fly.io." -ForegroundColor Yellow
    Write-Host "Откроется браузер — войдите через GitHub." -ForegroundColor Yellow
    flyctl auth login
}
Write-Host "Авторизован: $authStatus" -ForegroundColor Green

# 3. Проверяем/создаём приложение
$apps = flyctl apps list 2>&1
if ($apps -notmatch $APP_NAME) {
    Write-Host "Создаю приложение $APP_NAME..." -ForegroundColor Yellow
    flyctl apps create $APP_NAME --machines
    Write-Host "Приложение создано" -ForegroundColor Green
} else {
    Write-Host "Приложение $APP_NAME уже существует" -ForegroundColor Green
}

# 4. Создаём volume если его нет
$volumes = flyctl volumes list --app $APP_NAME 2>&1
if ($volumes -notmatch $VOLUME_NAME) {
    Write-Host "Создаю постоянный диск для базы данных..." -ForegroundColor Yellow
    flyctl volumes create $VOLUME_NAME --size 1 --region $REGION --app $APP_NAME --yes
    Write-Host "Диск создан" -ForegroundColor Green
} else {
    Write-Host "Диск уже существует" -ForegroundColor Green
}

# 5. Читаем .env и загружаем секреты
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "ОШИБКА: файл .env не найден!" -ForegroundColor Red
    Write-Host "Создайте .env из .env.example и заполните реальные значения токенов." -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "Загружаю секреты из .env..." -ForegroundColor Yellow

# Собираем все переменные в одну команду
$secrets = @()
Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line.Split("=", 2)
        if ($parts.Length -eq 2 -and $parts[1] -and $parts[1] -notmatch "your_.*_here") {
            $secrets += "$($parts[0])=$($parts[1])"
        }
    }
}

# Добавляем критичные переменные для Fly.io
$secrets += "PROD_DATABASE_PATH=/app/data/course_platform.db"
$secrets += "DATABASE_PATH=/app/data/course_platform.db"

if ($secrets.Count -gt 0) {
    $secretsStr = $secrets -join " "
    Invoke-Expression "flyctl secrets set $secretsStr --app $APP_NAME"
    Write-Host "Секреты загружены ($($secrets.Count) переменных)" -ForegroundColor Green
}

# 6. Деплой
Write-Host ""
Write-Host "Деплою приложение..." -ForegroundColor Yellow
flyctl deploy --app $APP_NAME --remote-only

Write-Host ""
Write-Host "====================================" -ForegroundColor Green
Write-Host " Деплой завершён!" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Green
Write-Host ""
Write-Host "Полезные команды:" -ForegroundColor Cyan
Write-Host "  flyctl logs --app $APP_NAME      # смотреть логи"
Write-Host "  flyctl status --app $APP_NAME    # статус"
Write-Host "  flyctl ssh console --app $APP_NAME  # консоль"
Write-Host ""

# 7. Показываем токен для GitHub Actions
Write-Host "Токен для GitHub Actions (добавьте в Settings -> Secrets -> FLY_API_TOKEN):" -ForegroundColor Yellow
flyctl auth token
