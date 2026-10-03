"""系统集成：开机自启动和可配置的 Windows 全局快捷键。"""
import os
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer
from PySide6.QtGui import QKeySequence

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


def parse_hotkey(text):
    """只允许带 Ctrl/Alt/Win 的单组组合键，避免误吞正常打字。"""
    if not isinstance(text, str):
        raise ValueError("快捷键必须是字符串")
    if not text.strip():
        return '', 0, 0
    sequence = QKeySequence.fromString(text, QKeySequence.PortableText)
    if sequence.count() != 1:
        raise ValueError("请输入一组组合键，不支持连续多组按键")
    combination = sequence[0]
    modifiers, key = combination.keyboardModifiers(), int(combination.key())
    if modifiers & Qt.KeypadModifier:
        raise ValueError("暂不支持数字小键盘专用组合，请使用普通数字键")
    mods = 0
    for qt_mod, native in ((Qt.AltModifier, 1), (Qt.ControlModifier, 2),
                           (Qt.ShiftModifier, 4), (Qt.MetaModifier, 8)):
        if modifiers & qt_mod:
            mods |= native
    if not mods & (1 | 2 | 8):
        raise ValueError("快捷键至少包含 Ctrl、Alt 或 Win 中的一个")
    special = {Qt.Key_Space: 0x20, Qt.Key_Tab: 0x09, Qt.Key_Return: 0x0D,
               Qt.Key_Enter: 0x0D, Qt.Key_Escape: 0x1B, Qt.Key_Backspace: 0x08,
               Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E, Qt.Key_Home: 0x24,
               Qt.Key_End: 0x23, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
               Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28}
    if ord('A') <= key <= ord('Z') or ord('0') <= key <= ord('9'):
        vk = key
    elif int(Qt.Key_F1) <= key <= int(Qt.Key_F24) and key != int(Qt.Key_F12):
        vk = 0x70 + key - int(Qt.Key_F1)
    else:
        vk = special.get(key)
    if vk is None:
        raise ValueError("支持字母、数字、功能键（F12 除外）、空格、方向和导航键")
    return sequence.toString(QKeySequence.PortableText), mods, vk


class GlobalHotkeys(QAbstractNativeEventFilter):
    """注册在线程消息队列上，避免置顶/穿透重建 HWND 后快捷键失效。"""
    _next_id = 0x5100

    def __init__(self, app, callbacks, user32=None):
        super().__init__()
        self.app, self.callbacks = app, callbacks
        self._registered = {}  # (modifiers, vk) -> id；交换两个动作时可复用已有注册
        self._actions = {}
        self._bindings = {}
        self._closed = False
        self.suspended = False
        self.capture_sequence = None
        if user32 is None and sys.platform.startswith('win'):
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32 = user32
        app.installNativeEventFilter(self)
        app.aboutToQuit.connect(self.close)

    def apply(self, bindings):
        """先注册全部新增按键，再提交；冲突时保留原快捷键，不保存半套设置。"""
        if self._closed:
            raise ValueError("快捷键服务已关闭")
        wanted, normalized = {}, {}
        for action, text in bindings.items():
            if action not in self.callbacks:
                raise ValueError("未知快捷键动作")
            canonical, mods, vk = parse_hotkey(text)
            normalized[action] = canonical
            if not vk:
                continue
            signature = (mods, vk)
            if signature in wanted:
                raise ValueError("两个动作不能使用相同的快捷键")
            wanted[signature] = action
        added = {}
        try:
            for signature, action in wanted.items():
                if signature in self._registered:
                    continue
                if self.user32 is None:
                    raise ValueError("当前系统不支持 Windows 全局快捷键")
                identifier = GlobalHotkeys._next_id
                GlobalHotkeys._next_id += 1
                if identifier > 0xBFFF:
                    raise ValueError("快捷键注册次数超出限制，请重启程序")
                mods, vk = signature
                if not self.user32.RegisterHotKey(None, identifier, mods | 0x4000, vk):
                    raise ValueError("快捷键 %s 已被占用或被系统保留，请更换组合" % normalized[action])
                added[signature] = identifier
        except Exception:
            for identifier in added.values():
                self.user32.UnregisterHotKey(None, identifier)
            raise
        for signature, identifier in self._registered.items():
            if signature not in wanted:
                self.user32.UnregisterHotKey(None, identifier)
        self._registered = {s: self._registered.get(s, added.get(s)) for s in wanted}
        self._actions = {self._registered[s]: action for s, action in wanted.items()}
        self._bindings = normalized
        return normalized

    def nativeEventFilter(self, event_type, message):
        if self._closed or event_type not in ('windows_generic_MSG', b'windows_generic_MSG',
                                              'windows_dispatcher_MSG', b'windows_dispatcher_MSG'):
            return False, 0
        import ctypes.wintypes
        msg = ctypes.wintypes.MSG.from_address(int(message))
        action = self._actions.get(int(msg.wParam)) if msg.message == 0x0312 else None
        if action is None:
            return False, 0
        if self.suspended and self.capture_sequence is not None:
            # 自己已注册的组合可能只产生 WM_HOTKEY；仍交给设置框录入，不能吞掉它。
            self.capture_sequence(self._bindings[action])
        elif not self.suspended:
            QTimer.singleShot(0, lambda a=action: (
                not self._closed and not self.suspended and self.callbacks[a]()))
        return True, 0

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.capture_sequence = None
        for identifier in self._registered.values():
            self.user32.UnregisterHotKey(None, identifier)
        self._registered.clear()
        self._actions.clear()
        self.app.removeNativeEventFilter(self)
