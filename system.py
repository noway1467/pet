"""系统集成：开机自启动（写入当前用户的 Run 注册表项）。"""
import os
import sys

try:
    import winreg
except ImportError:                       # 非 Windows
    winreg = None

APP_NAME = "DesktopPet"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command():
    """返回开机启动时要执行的命令行。"""
    if getattr(sys, "frozen", False):     # 已打包成 exe
        return f'"{sys.executable}"'
    py = sys.executable
    pyw = py.replace("python.exe", "pythonw.exe")
    if os.path.exists(pyw):
        py = pyw
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{py}" "{main}"'


def is_autostart():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enable):
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
