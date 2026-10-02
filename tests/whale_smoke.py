"""隔离用户目录的真实 Qt 主窗口烟测，包含可选 Cubism 2 / 3 切换。"""
import copy
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def memory_mb():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
             'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
             'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
    counter = Counters()
    counter.cb = ctypes.sizeof(counter)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return round(counter.PrivateUsage / 1024**2, 2)


def run():
    with tempfile.TemporaryDirectory(prefix='desktop-pet-smoke-') as temporary:
        # 必须先隔离再导入模块；真实配置、好感度和模型目录均不写入。
        os.environ['USERPROFILE'] = os.environ['HOME'] = temporary
        os.environ['APPDATA'] = str(Path(temporary) / 'AppData')
        os.environ['LOCALAPPDATA'] = str(Path(temporary) / 'LocalAppData')
        import config
        cfg = copy.deepcopy(config.DEFAULTS)
        models = Path(temporary) / 'models'
        models.mkdir()
        cfg.update(character='whale', whale_auto_actions=False, chat_enabled=False,
                   voice_enabled=False, tts_enabled=False, holiday_greetings=False,
                   models_dir=str(models), edge_snap=False)
        config.save(cfg)
        from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPoint, QTimer
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtWidgets import QApplication, QMessageBox, QMenu
        from PySide6.QtTest import QTest
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        import main
        from whale_pet import WhalePet
        from image_pet import ImagePet
        failures = []
        def exception_hook(kind, value, tb):
            failures.append(str(value))
            sys.__excepthook__(kind, value, tb)
        sys.excepthook = exception_hook
        QMessageBox.warning = lambda *args, **kwargs: failures.append(str(args[1:]))
        win = main.PetWindow(cfg)
        win.show()

        def wait(ms=120):
            QTest.qWait(ms)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not failures, failures

        try:
            wait(500)
            assert isinstance(win.renderer, WhalePet)
            win.renderer.grab().save(str(Path(temporary) / 'whale.png'))
            menu = QMenu()
            win._populate_menu(menu)
            assert any(a.text() == '鲸鱼娘动作（全部）' for a in menu.actions())
            for state in win.renderer.actions:
                win._play_whale_state(state)
                wait(50)
                assert not win.renderer.grab().isNull()
            win.renderer._start_state('IDLE', True)
            rect = win.renderer._draw_rect()
            head = QPoint(int(rect.center().x()), int(rect.y() + rect.height() * 0.24))
            QTest.mouseClick(win.renderer, Qt.LeftButton, pos=head)
            assert win.renderer.state == 'HEAD_PAT', win.renderer.state
            wait(80)
            win.renderer._start_state('IDLE', True)
            body = QPoint(int(rect.center().x()), int(rect.y() + rect.height() * 0.7))
            QTest.mouseClick(win.renderer, Qt.LeftButton, pos=body)
            assert win.renderer.state == 'POKE_REACT', win.renderer.state
            QTest.mouseDClick(win.renderer, Qt.LeftButton, pos=body)
            assert win.renderer.state == 'EATING'
            win.renderer._start_state('IDLE', True)
            win._set_whale_size(280)
            assert cfg['whale_size'] == 280 and win.renderer._target_h == 280
            win._toggle_whale_auto(True)
            assert config.load()['whale_auto_actions']
            win._toggle_whale_auto(False)
            win.move(300, 100)
            win._play_whale_state('WALKING', -1)
            wait(650)
            assert win.x() < 300, win.pos()
            win.renderer.react('grab')
            win.renderer.react('drop')
            win._start_fall()
            assert win.renderer.state == 'FALLING'
            wait(2000)
            assert not win._fall_timer.isActive()
            win._toggle_top(True)
            win._toggle_top(False)
            win.hide()
            assert not win.renderer.timer.isActive()
            win.show()
            wait()
            report = {'interaction': 'passed', 'switches': [], 'memory_mb': []}
            for i in range(12):
                win._set_character('slime')
                wait(80)
                win._set_character('whale')
                wait(80)
                assert isinstance(win.renderer, WhalePet)
                assert cfg['whale_size'] == 280
                assert len(win.findChildren(WhalePet)) == 1
                report['memory_mb'].append(memory_mb())
            # 与图片、真实 Cubism 模型交叉切换，运行时必须有非空帧。
            image = str(next((ROOT / 'assets/whale/frames/idle_front').glob('*.png')))
            cfg['image_path'] = image
            candidates = [('image', image)] + [('live2d', p) for p in sys.argv[1:]]
            for kind, path in candidates:
                if kind == 'live2d':
                    cfg['live2d_model'] = str(Path(path).resolve())
                start = time.perf_counter()
                win._set_character(kind)
                wait(1400)
                elapsed = time.perf_counter() - start
                assert cfg['character'] == kind, (path, cfg['character'])
                if kind == 'live2d':
                    for _ in range(30):
                        if win.renderer._frame is not None:
                            break
                        wait(100)
                    if win.renderer._frame is None:
                        r = win.renderer
                        raw = r._gl.grabFramebuffer()
                        print('FRAME_DIAGNOSTIC', r.size(), r._gl.size(),
                              r.devicePixelRatioF(), r._gl.devicePixelRatioF(),
                              raw.size(), r._expected_framebuffer_size(),
                              r._render_timer.isActive(), r._pending_resize_guard, flush=True)
                    assert win.renderer._frame is not None and not win.renderer._frame.isNull()
                    import numpy as np
                    from PySide6.QtGui import QImage
                    frame = win.renderer._frame.convertToFormat(QImage.Format_RGBA8888)
                    alpha = np.frombuffer(frame.constBits(), np.uint8).reshape(
                        frame.height(), frame.bytesPerLine())[:, :frame.width() * 4][:, 3::4]
                    assert int((alpha > 16).sum()) > 100, '模型首帧全透明'
                else:
                    assert isinstance(win.renderer, ImagePet)
                report['switches'].append({'kind': kind, 'model': Path(path).name,
                                          'seconds_including_wait': round(elapsed, 3),
                                          'private_mb': memory_mb()})
                win._set_character('whale')
                wait(400)
                assert isinstance(win.renderer, WhalePet)
                report['switches'][-1]['back_to_whale_private_mb'] = memory_mb()
            wait(3500)
            print('SMOKE_RESULT=' + json.dumps(report, ensure_ascii=False), flush=True)
        finally:
            win.renderer.shutdown()
            win.tray.hide()
            for timer in win.findChildren(QTimer):
                timer.stop()
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    run()
