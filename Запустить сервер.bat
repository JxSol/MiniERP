@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Запуск мини-ERP двойным щелчком: база в Docker, обновление базы, сервер, админка в браузере.
rem Для проверок: ERP_PORT задаёт другой порт, ERP_NO_BROWSER=1 не открывает браузер.
if not defined ERP_PORT set "ERP_PORT=8000"
set "ERP_URL=http://127.0.0.1:%ERP_PORT%/admin/"
title Мини-ERP: сервер на порту %ERP_PORT%. Закройте окно, чтобы остановить

echo.
echo  Мини-ERP
echo  ========
echo.

rem --- сервер уже работает? тогда просто открыть админку ---
netstat -ano | findstr /C:":%ERP_PORT% " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo  Сервер уже работает: %ERP_URL%
    if not defined ERP_NO_BROWSER start "" "%ERP_URL%"
    echo.
    echo  Это окно можно закрыть.
    timeout /t 6 >nul 2>&1
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo  Не найдено окружение .venv. Откройте терминал в этой папке и выполните:  uv sync
    goto :fail
)
if not exist ".env" (
    echo  Не найден файл настроек .env. Скопируйте .env.example в .env и задайте SECRET_KEY.
    goto :fail
)

rem --- база данных живёт в Docker ---
docker info >nul 2>&1
if errorlevel 1 (
    echo  Docker не запущен, запускаю Docker Desktop. Это может занять пару минут...
    if not exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
        echo  Docker Desktop не найден. Установите его или запустите вручную и повторите.
        goto :fail
    )
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    call :wait_docker
    if errorlevel 1 goto :fail
)

echo  Поднимаю базу данных...
docker compose up -d --wait
if errorlevel 1 (
    echo.
    echo  База данных не запустилась. Сообщение Docker выше.
    goto :fail
)

echo.
echo  Проверяю обновления базы...
".venv\Scripts\python.exe" manage.py migrate --no-input
if errorlevel 1 (
    echo.
    echo  Не удалось обновить базу данных. Сообщение об ошибке выше.
    goto :fail
)

echo.
echo  Запускаю сервер: %ERP_URL%
echo  Окно должно оставаться открытым. Чтобы остановить сервер, закройте его.
echo.
rem браузер откроется, как только сервер начнёт отвечать
if not defined ERP_NO_BROWSER start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%ERP_URL%login/' | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }; Start-Process '%ERP_URL%'"
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:%ERP_PORT%

echo.
echo  Сервер остановлен.
goto :fail

:wait_docker
set /a tries=0
:wait_docker_loop
docker info >nul 2>&1
if not errorlevel 1 exit /b 0
set /a tries+=1
if %tries% geq 60 (
    echo  Docker так и не запустился за 3 минуты. Запустите Docker Desktop вручную и повторите.
    exit /b 1
)
ping -n 4 127.0.0.1 >nul
goto :wait_docker_loop

:fail
echo.
pause
exit /b 1
