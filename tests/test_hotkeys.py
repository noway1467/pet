"""快捷键解析、事务回滚、重复/交换、真实 Win32 消息队列验证。"""
import ctypes
from ctypes import wintypes
import sys
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import system


class FakeUser32:
    def __init__(self):
        self.keys = {}
        self.blocked = set()

    def RegisterHotKey(self, hwnd, identifier, mods, vk):
        signature = (mods & ~0x4000, vk)
        if signature in self.blocked or signature in self.keys.values():
            return False
        self.keys[identifier] = signature
        return True

    def UnregisterHotKey(self, hwnd, identifier):
        self.keys.pop(identifier, None)
        return True


class HotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.api = FakeUser32()
        self.fired = []
        self.manager = system.GlobalHotkeys(self.app, {'pet': lambda: self.fired.append('pet'),
                                                      'quick': lambda: self.fired.append('quick')}, self.api)

    def tearDown(self):
        self.manager.close()
        self.assertEqual(self.api.keys, {})

    def test_parse_disable_and_invalid(self):
        self.assertEqual(system.parse_hotkey('Ctrl+Alt+Space'), ('Ctrl+Alt+Space', 3, 32))
        self.assertEqual(system.parse_hotkey(''), ('', 0, 0))
        for text in ('A', 'Shift+A', 'Ctrl+F12', 'Ctrl+Num+1', 'Ctrl+K, Ctrl+C', 'not a shortcut', 123):
            with self.assertRaises(ValueError):
                system.parse_hotkey(text)

    def test_old_config_defaults_and_disabled_roundtrip(self):
        import config
        with tempfile.TemporaryDirectory(prefix='pet-hotkey-config-') as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'character': 'cat'}), encoding='utf-8')
            with patch.object(config, 'CONFIG_DIR', tmp), patch.object(config, 'CONFIG_PATH', str(path)):
                cfg = config.load()
                self.assertEqual(cfg['hotkey_toggle_pet'], 'Ctrl+Alt+P')
                self.assertEqual(cfg['hotkey_quick_panel'], 'Ctrl+Alt+Space')
                cfg['hotkey_quick_panel'] = ''
                config.save(cfg)
                self.assertEqual(config.load()['hotkey_quick_panel'], '')

    def test_duplicate_and_failed_apply_preserve_old(self):
        self.manager.apply({'pet': 'Ctrl+Alt+P', 'quick': ''})
        previous = dict(self.api.keys)
        with self.assertRaises(ValueError):
            self.manager.apply({'pet': 'Ctrl+A', 'quick': 'Ctrl+A'})
        self.assertEqual(self.api.keys, previous)
        self.api.blocked.add((2, ord('B')))
        with self.assertRaises(ValueError):
            self.manager.apply({'pet': 'Ctrl+A', 'quick': 'Ctrl+B'})
        self.assertEqual(self.api.keys, previous)

    def test_swap_disable_and_dispatch(self):
        self.manager.apply({'pet': 'Ctrl+Alt+P', 'quick': 'Ctrl+Alt+Space'})
        previous = dict(self.api.keys)
        self.manager.apply({'pet': 'Ctrl+Alt+Space', 'quick': 'Ctrl+Alt+P'})
        self.assertEqual(previous, self.api.keys)
        msg = wintypes.MSG()
        msg.message = 0x0312
        msg.wParam = next(i for i, a in self.manager._actions.items() if a == 'quick')
        self.manager.nativeEventFilter(b'windows_dispatcher_MSG', ctypes.addressof(msg))
        QTest.qWait(20)
        self.assertEqual(self.fired, ['quick'])
        self.manager.suspended = True
        captured = []
        self.manager.capture_sequence = captured.append
        self.manager.nativeEventFilter(b'windows_dispatcher_MSG', ctypes.addressof(msg))
        QTest.qWait(20)
        self.assertEqual(self.fired, ['quick'])
        self.assertEqual(captured, ['Ctrl+Alt+P'])
        self.manager.apply({'pet': '', 'quick': ''})
        self.assertEqual(self.api.keys, {})

    @unittest.skipUnless(sys.platform.startswith('win'), '需要 Windows')
    def test_real_registration_conflict_message_and_cleanup(self):
        fired = []
        owner = system.GlobalHotkeys(self.app, {'action': lambda: fired.append(True)})
        contender = system.GlobalHotkeys(self.app, {'action': lambda: None})
        try:
            owner.apply({'action': 'Ctrl+Alt+Shift+F9'})
            with self.assertRaises(ValueError):
                contender.apply({'action': 'Ctrl+Alt+Shift+F9'})
            identifier = next(iter(owner._actions))
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            thread = ctypes.windll.kernel32.GetCurrentThreadId()
            self.assertTrue(user32.PostThreadMessageW(thread, 0x0312, identifier, 0))
            QTest.qWait(50)
            self.assertEqual(fired, [True])
            owner.close()
            contender.apply({'action': 'Ctrl+Alt+Shift+F9'})
        finally:
            owner.close()
            contender.close()


if __name__ == '__main__':
    unittest.main()
