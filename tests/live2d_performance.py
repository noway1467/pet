"""隔离配置的真实切换基准；可在修改前后用相同命令对照。"""
import copy
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from whale_smoke import memory_mb


def run():
    paths = [str(Path(p).resolve()) for p in sys.argv[1:]]
    with tempfile.TemporaryDirectory(prefix='desktop-pet-perf-') as tmp:
        os.environ['USERPROFILE'] = os.environ['HOME'] = tmp
        import config
        cfg = copy.deepcopy(config.DEFAULTS)
        models = Path(tmp) / 'models'
        models.mkdir()
        cfg.update(character='whale', models_dir=str(models), chat_enabled=False,
                   voice_enabled=False, tts_enabled=False, whale_auto_actions=False,
                   edge_snap=False, holiday_greetings=False)
        config.save(cfg)
        from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtTest import QTest
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        import main
        errors = []
        QMessageBox.warning = lambda *args: errors.append(str(args[1:]))
        win = main.PetWindow(cfg)
        win.show()
        QTest.qWait(700)
        profile = cProfile.Profile()
        beats, rows = [], []
        last = [time.perf_counter()]
        def beat():
            now = time.perf_counter()
            beats.append((now - last[0]) * 1000)
            last[0] = now
        timer = QTimer()
        timer.setTimerType(Qt.PreciseTimer)
        timer.timeout.connect(beat)
        timer.start(10)
        print('BASE_MB', memory_mb(), flush=True)
        try:
            profile.enable()
            for path in paths:
                for repeat in range(3):
                    beats.clear()
                    last[0] = time.perf_counter()
                    start = last[0]
                    cfg['live2d_model'] = path
                    win._set_character('live2d')
                    while time.perf_counter() - start < 15:
                        QTest.qWait(10)
                        if cfg['character'] == 'live2d' and getattr(win.renderer, '_frame', None) is not None:
                            break
                    assert not errors, errors
                    assert getattr(win.renderer, '_frame', None) is not None, '没有生成首帧'
                    first_ms = (time.perf_counter() - start) * 1000
                    QTest.qWait(400)
                    loaded = memory_mb()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    row = dict(model=Path(path).name, repeat=repeat, first_ms=round(first_ms),
                               ui_gap_ms=round(max(beats)), loaded_mb=loaded)
                    resize_start = time.perf_counter()
                    win._set_live2d_size(240 if repeat % 2 == 0 else 200)
                    QTest.qWait(100)
                    row['resize_ms'] = round((time.perf_counter() - resize_start) * 1000)
                    win._set_character('whale')
                    QTest.qWait(350)
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    row['released_mb'] = memory_mb()
                    rows.append(row)
                    print('PERF', json.dumps(row), flush=True)
            profile.disable()
            out = io.StringIO()
            pstats.Stats(profile, stream=out).sort_stats('cumulative').print_stats(24)
            print(out.getvalue())
            print('PERF_RESULT=' + json.dumps(rows), flush=True)
        finally:
            timer.stop()
            for t in win.findChildren(QTimer):
                t.stop()
            win.renderer.shutdown()
            win.tray.hide()
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    run()
