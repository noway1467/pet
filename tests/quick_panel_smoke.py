"""主窗口快捷入口、真实 .lnk 启动和开关成本烟测。所有配置/辅助文件均在临时目录。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from whale_smoke import memory_mb


def run():
    with tempfile.TemporaryDirectory(prefix='pet-quick-smoke-') as tmp:
        home = Path(tmp)
        os.environ['USERPROFILE'] = os.environ['HOME'] = tmp
        import config
        cfg = copy.deepcopy(config.DEFAULTS)
        (home / 'models').mkdir()
        cfg.update(character='whale', whale_auto_actions=False, models_dir=str(home / 'models'),
                   chat_enabled=False, voice_enabled=False, tts_enabled=False, holiday_greetings=False,
                   hotkey_toggle_pet='', hotkey_quick_panel='')
        config.save(cfg)
        from PySide6.QtCore import Qt, QCoreApplication, QEvent, QTimer
        from PySide6.QtWidgets import QApplication, QMenu
        from PySide6.QtTest import QTest
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        import main
        from quick_actions import QuickPanel
        app.setStyleSheet(main.APP_QSS)
        errors = []
        def report_error(kind, value, tb):
            errors.append(repr(value))
            sys.__excepthook__(kind, value, tb)
        sys.excepthook = report_error
        win = main.PetWindow(cfg)
        win.show()
        def settle(ms=30):
            QTest.qWait(ms)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not errors, errors
        try:
            settle(250)
            menu = QMenu(win)
            win._populate_menu(menu)
            actions = menu.actions()
            entry = next(a for a in actions if a.text() == '快捷启动与搜索…')
            # 实际打开设置弹窗，保存后使用真实线程 WM_HOTKEY，不向用户桌面发送按键。
            from PySide6.QtGui import QKeySequence
            from PySide6.QtWidgets import QKeySequenceEdit, QDialogButtonBox
            import ctypes
            from ctypes import wintypes
            def configure_hotkeys():
                dialog = app.activeModalWidget()
                try:
                    dialog.findChild(QKeySequenceEdit, 'hotkey_toggle_pet').setKeySequence(QKeySequence('Ctrl+Alt+Shift+F7'))
                    dialog.findChild(QKeySequenceEdit, 'hotkey_quick_panel').setKeySequence(QKeySequence('Ctrl+Alt+Shift+F8'))
                    output = os.environ.get('HOTKEY_QA_IMAGE')
                    if output:
                        assert dialog.grab().save(output)
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
                    assert not dialog.isVisible(), '快捷键设置没有成功保存'
                except Exception as error:
                    errors.append(str(error))
                    dialog.reject()
            QTimer.singleShot(50, configure_hotkeys)
            win._show_hotkey_settings()
            settle()
            assert config.load()['hotkey_quick_panel'] == 'Ctrl+Alt+Shift+F8'
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            saved = config.load()
            def capture_and_cancel():
                dialog = app.activeModalWidget()
                try:
                    editor = dialog.findChild(QKeySequenceEdit, 'hotkey_quick_panel')
                    editor.setFocus()
                    QTest.qWait(10)
                    identifier = next(i for i, a in win._hotkeys._actions.items() if a == 'hotkey_toggle_pet')
                    assert user32.PostThreadMessageW(ctypes.windll.kernel32.GetCurrentThreadId(), 0x0312, identifier, 0)
                    QTest.qWait(30)
                    assert editor.keySequence().toString(QKeySequence.PortableText) == 'Ctrl+Alt+Shift+F7'
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
                    assert dialog.isVisible(), '重复按键不应保存成功'
                    assert config.load() == saved
                except Exception as error:
                    errors.append(str(error))
                finally:
                    dialog.reject()
            QTimer.singleShot(50, capture_and_cancel)
            win._show_hotkey_settings()
            settle()
            def hotkey(action):
                identifier = next(i for i, a in win._hotkeys._actions.items() if a == action)
                assert user32.PostThreadMessageW(ctypes.windll.kernel32.GetCurrentThreadId(), 0x0312, identifier, 0)
                settle(80)
            hotkey('hotkey_toggle_pet')
            assert not win.isVisible()
            hotkey('hotkey_quick_panel')
            assert win._quick_panel.isVisible()
            win._quick_panel.close()
            settle()
            hotkey('hotkey_toggle_pet')
            assert win.isVisible()
            win._toggle_top(True)
            win._toggle_top(False)
            hotkey('hotkey_quick_panel')
            assert win._quick_panel.isVisible()
            win._quick_panel.close()
            settle()
            quick_menu = next(a.menu() for a in actions if a.text() == '快速启动')
            win._fill_quick_launch_menu(quick_menu)
            assert len(quick_menu.actions()) == 5
            baseline = memory_mb()
            timings, memory = [], []
            for i in range(20):
                start = time.perf_counter()
                entry.trigger()
                app.processEvents()
                timings.append(round((time.perf_counter() - start) * 1000, 2))
                panel = win._quick_panel
                assert isinstance(panel, QuickPanel)
                win._show_quick_panel()
                assert win._quick_panel is panel
                assert len(win.findChildren(QuickPanel)) == 1
                assert not panel.findChildren(QTimer)
                QTest.keyClick(panel.search, Qt.Key_Escape)
                settle()
                assert win._quick_panel is None
                assert not win.findChildren(QuickPanel)
                memory.append(memory_mb())
            # 用已安装的 pythonw 作为真实程序，通过测试 .lnk 写完成标记并立即退出。
            import win32com.client
            script = home / '启动验证 & 中文.py'
            marker = home / 'launched.json'
            script.write_text('import json, os\nfrom pathlib import Path\n'
                              f'Path({str(marker)!r}).write_text(json.dumps({{"pid": os.getpid()}}))\n', encoding='utf-8')
            shortcut_path = home / '测试快捷方式 & 中文.lnk'
            shell = win32com.client.Dispatch('WScript.Shell')
            shortcut = shell.CreateShortcut(str(shortcut_path))
            shortcut.TargetPath = str(Path(sys.executable).with_name('pythonw.exe'))
            shortcut.Arguments = subprocess.list2cmdline([str(script)])
            shortcut.WorkingDirectory = str(home)
            shortcut.WindowStyle = 7
            shortcut.Save()
            del shortcut, shell
            win._show_quick_panel()
            panel = win._quick_panel
            assert panel.add_target(str(shortcut_path))
            panel.search.setText('测试快捷方式')
            QTest.keyClick(panel.search, Qt.Key_Return)
            deadline = time.monotonic() + 10
            while not marker.is_file() and time.monotonic() < deadline:
                settle(50)
            assert marker.is_file(), '真实快捷方式没有启动辅助程序'
            pid = json.loads(marker.read_text())['pid']
            # 等待测试辅助进程退出，不终止用户已有程序。
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 5000) == 0
                finally:
                    kernel.CloseHandle(handle)
            settle()
            assert win._quick_panel is None
            win._show_quick_panel()
            panel = win._quick_panel
            assert any(e['path'] == str(shortcut_path) for e in config.load()['quick_launch_items'])
            # 测到浏览器交接边界，不实际向外部搜索引擎发送测试词。
            urls = []
            panel._open_url = lambda url: urls.append(url.toString()) or True
            panel.search.setText('hello & 鲸鱼娘')
            panel.activateWindow()
            panel.search.setFocus()
            settle()
            QTest.keyClick(panel.search, Qt.Key_Return, Qt.ControlModifier)
            settle()
            assert len(urls) == 1, urls
            assert 'hello+%26+' in urls[0], urls
            assert win._quick_panel is None
            win._show_quick_panel()
            panel = win._quick_panel
            output = os.environ.get('QUICK_PANEL_QA')
            if output:
                panel.grab().save(output)
                panel.search.setText('桌面宠物')
                panel.grab().save(str(Path(output).with_stem(Path(output).stem + '-search')))
            panel.close()
            settle()
            print('QUICK_SMOKE=' + json.dumps({'status': 'passed', 'real_lnk_launch': True,
                'custom_hotkey_dialog_and_native_dispatch': True,
                'keyboard_web_handoff': True, 'open_ms': timings, 'baseline_private_mb': baseline,
                'closed_private_mb': memory, 'residual_panels': len(win.findChildren(QuickPanel))}), flush=True)
        finally:
            panel = getattr(win, '_quick_panel', None)
            if panel is not None:
                panel.close()
            win.renderer.shutdown()
            win.tray.hide()
            for timer in win.findChildren(QTimer):
                timer.stop()
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    run()
