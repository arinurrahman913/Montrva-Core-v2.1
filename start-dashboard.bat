@echo off
title Montrva Dashboard
cd /d "%~dp0"

echo ============================================
echo   Montrva v2 - Menjalankan dashboard...
echo ============================================
echo.

REM Jalankan backend (Flask). Mode produksi: Flask sekaligus menyajikan
REM frontend hasil build + API di http://localhost:5000 (cukup 1 proses).
start "Montrva Backend" cmd /k python backend\app.py

echo Menunggu backend siap...
REM Delay ~4 detik pakai ping (aman walau stdin di-redirect, beda dgn 'timeout').
ping -n 5 127.0.0.1 >nul

REM Buka dashboard di browser default.
start "" "http://localhost:5000"

echo.
echo  Dashboard: http://localhost:5000
echo.
echo  - Jendela "Montrva Backend" biarkan TERBUKA selama dipakai.
echo  - Untuk mematikan dashboard: tutup jendela backend itu.
echo  - Refresh data cukup lewat tombol "Generate" di dashboard.
echo.
ping -n 5 127.0.0.1 >nul
