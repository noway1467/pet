@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   清理临时文件和旧文档
echo ============================================
echo.

echo 正在清理以下文件：
echo.

REM 清理测试文件
echo [测试文件]
if exist test_bubble_position.py (del /f test_bubble_position.py && echo - test_bubble_position.py)
if exist test_fixes.py (del /f test_fixes.py && echo - test_fixes.py)
if exist test_guide_v3.6.2.sh (del /f test_guide_v3.6.2.sh && echo - test_guide_v3.6.2.sh)
if exist test_image.py (del /f test_image.py && echo - test_image.py)
if exist test_live2d_voice.py (del /f test_live2d_voice.py && echo - test_live2d_voice.py)
if exist test_optim.py (del /f test_optim.py && echo - test_optim.py)
if exist test_render.py (del /f test_render.py && echo - test_render.py)
if exist test_smoke.py (del /f test_smoke.py && echo - test_smoke.py)
if exist test_v3.4_fixes.py (del /f test_v3.4_fixes.py && echo - test_v3.4_fixes.py)
if exist test_v3.6_fixes.py (del /f test_v3.6_fixes.py && echo - test_v3.6_fixes.py)
if exist test_v3.6.2_fixes.py (del /f test_v3.6.2_fixes.py && echo - test_v3.6.2_fixes.py)
if exist test_voice_fix.py (del /f test_voice_fix.py && echo - test_voice_fix.py)
if exist test_voice_fix2.py (del /f test_voice_fix2.py && echo - test_voice_fix2.py)
if exist test_wav_volume.py (del /f test_wav_volume.py && echo - test_wav_volume.py)

echo.
echo [旧版本验证脚本]
if exist 验证v3.4功能.bat (del /f 验证v3.4功能.bat && echo - 验证v3.4功能.bat)
if exist 验证v3.6.2功能.bat (del /f 验证v3.6.2功能.bat && echo - 验证v3.6.2功能.bat)

echo.
echo [旧版本文档]
if exist README_v3.4.md (del /f README_v3.4.md && echo - README_v3.4.md)
if exist v3.6.2_修复总结.md (del /f v3.6.2_修复总结.md && echo - v3.6.2_修复总结.md)
if exist v3.6.2_更新说明.md (del /f v3.6.2_更新说明.md && echo - v3.6.2_更新说明.md)
if exist v3.6项目评估报告.md (del /f v3.6项目评估报告.md && echo - v3.6项目评估报告.md)
if exist 修复完成清单.md (del /f 修复完成清单.md && echo - 修复完成清单.md)

echo.
echo [临时文件]
if exist 完成打包配置.bat (del /f 完成打包配置.bat && echo - 完成打包配置.bat)

echo.
echo [临时目录]
if exist build (rmdir /s /q build && echo - build\)
if exist __pycache__ (rmdir /s /q __pycache__ && echo - __pycache__\)

echo.
echo ============================================
echo   清理完成！
echo ============================================
echo.
echo 保留的文件：
echo - README.md               (主文档)
echo - CHANGELOG.md            (更新日志)
echo - test_new_features.py    (v3.7 功能测试)
echo.
pause
