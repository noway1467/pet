@echo off
chcp 65001 >nul
echo ========================================
echo   桌面宠物 - 白手套版快速测试
echo ========================================
echo.
echo 正在启动程序...
echo.
echo 测试要点：
echo   1. 鼠标移到头部，看手势是否为白手套俯视视角
echo   2. 点击头部，看是否有五指抚摸动画（左右摆动）
echo   3. 右键取消"总在最前"，气泡是否跟随降层
echo.
echo 按任意键启动程序...
pause >nul

cd /d dist\DesktopPet
start DesktopPet.exe

echo.
echo 程序已启动！
echo 测试完成后可在任务栏托盘图标右键退出
echo.
pause
