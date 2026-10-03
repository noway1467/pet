"""真实 Live2D 拖动回归与基准；隔离配置，不写入模型目录。"""
import argparse
import copy
import cProfile
import hashlib
import json
import math
import os
from pathlib import Path
import pstats
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from whale_smoke import memory_mb


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+', type=Path)
    parser.add_argument('--baseline', action='store_true', help='只记录旧实现的性能及已知回归')
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    paths = [path.resolve() for path in args.models]
    sources = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
               for model in paths for p in model.parent.rglob('*') if p.is_file()}
    with tempfile.TemporaryDirectory(prefix='pet-drag-') as tmp:
        os.environ['USERPROFILE'] = os.environ['HOME'] = tmp
        os.environ['APPDATA'] = str(Path(tmp) / 'AppData')
        os.environ['LOCALAPPDATA'] = str(Path(tmp) / 'LocalAppData')
        import config
        models = Path(tmp) / 'models'
        models.mkdir()
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(character='whale', models_dir=str(models), chat_enabled=False,
                   voice_enabled=False, tts_enabled=False, whale_auto_actions=False,
                   edge_snap=False, holiday_greetings=False, click_action_enabled=False,
                   hotkey_toggle_pet='', hotkey_quick_panel='')
        config.save(cfg)
        from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent, QPoint, QPointF
        from PySide6.QtGui import QSurfaceFormat, QMouseEvent
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
        old_hook = sys.excepthook
        def report_error(kind, value, tb):
            errors.append(repr(value))
            old_hook(kind, value, tb)
        sys.excepthook = report_error
        QMessageBox.warning = lambda *a: errors.append(str(a[1:]))
        win = main.PetWindow(cfg)
        win.show()
        original_render_error = win._on_live2d_render_error
        def render_error(path, error):
            errors.append((path, error))
            original_render_error(path, error)
        win._on_live2d_render_error = render_error
        profiler = cProfile.Profile()
        rows = []

        def wait(ms):
            QTest.qWait(ms)
            assert not errors, errors

        def ready():
            deadline = time.monotonic() + 30
            while getattr(win.renderer, '_frame', None) is None:
                assert time.monotonic() < deadline, '模型未生成首帧'
                wait(20)

        def mouse(kind, global_pos):
            button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
            buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
            event = QMouseEvent(kind, QPointF(win.renderer.mapFromGlobal(global_pos)),
                                QPointF(global_pos), button, buttons, Qt.NoModifier)
            QCoreApplication.sendEvent(win.renderer, event)

        try:
            wait(300)
            for path in paths:
                for limit in (0, 2048):
                    cfg.update(live2d_model=str(path), live2d_texture_limit=limit)
                    win._set_character('live2d')
                    wait(50)
                    ready()
                    win._set_live2d_size(400)
                    ready()
                    wait(3400)
                    renderer = win.renderer
                    # 不持有原生 model 强引用，否则切换后会延迟到错误 GL 上下文释放。
                    model_id = id(renderer._gl.model)
                    win.move(*win._clamp_pos(400, 180))
                    wait(50)
                    offset = QPoint(win.width() // 2, win.height() // 2)
                    origin = win.pos() + offset
                    mouse(QEvent.MouseButtonPress, origin)
                    assert renderer._render_timer.isActive()
                    mouse(QEvent.MouseMove, origin + QPoint(2, 0))
                    assert win._drag_off is None
                    assert renderer._render_timer.isActive()
                    with patch.object(win, '_clamp_pos', wraps=win._clamp_pos) as clamp, \
                         patch.object(win, '_win32_workarea_rect', wraps=win._win32_workarea_rect) as workarea, \
                         patch.object(renderer._gl, 'grabFramebuffer', wraps=renderer._gl.grabFramebuffer) as grab:
                        if args.profile:
                            profiler.enable()
                        start = time.perf_counter()
                        mouse(QEvent.MouseMove, origin + QPoint(10, 0))
                        begin_ms = (time.perf_counter() - start) * 1000
                        assert win._drag_off is not None
                        assert not renderer._render_timer.isActive()
                        frame_key = renderer._frame.cacheKey()
                        samples = []
                        for batch in range(120):
                            tick = time.perf_counter()
                            for step in range(16):
                                phase = (batch * 16 + step) / 160.0
                                point = origin + QPoint(round(90 * math.sin(phase)),
                                                        round(40 * math.cos(phase)))
                                mouse(QEvent.MouseMove, point)
                            samples.append((time.perf_counter() - tick) * 1000)
                            wait(8)
                        if args.profile:
                            profiler.disable()
                        assert renderer._frame.cacheKey() == frame_key
                        row = dict(model=path.name, texture_limit=limit, private_mb=memory_mb(),
                                   begin_ms=round(begin_ms, 3), mouse_events=1921,
                                   clamp_calls=clamp.call_count, workarea_calls=workarea.call_count,
                                   burst_p95_ms=round(sorted(samples)[int(len(samples)*.95)], 3),
                                   burst_max_ms=round(max(samples), 3), drag_grabs=grab.call_count)
                        release = origin + QPoint(123, 57)
                        expected = QPoint(*win._clamp_pos((release-offset).x(), (release-offset).y()))
                        mouse(QEvent.MouseButtonRelease, release)
                        row['release_exact'] = win.pos() == expected
                        assert renderer._render_timer.isActive()
                        assert not win._drag_move_timer.isActive()
                        wait(200)
                        assert renderer._frame.cacheKey() != frame_key
                        assert id(renderer._gl.model) == model_id
                        renderer.set_render_active(False)
                        grab.reset_mock()
                        renderer.hide()
                        renderer.show()
                        renderer._render_tick()
                        wait(90)
                        row['paused_grabs_after_show'] = grab.call_count
                        renderer.set_render_active(True)
                        wait(120)
                        print('DRAG_RESULT=' + json.dumps(row), flush=True)
                        rows.append(row)
                        if not args.baseline:
                            assert row['drag_grabs'] == 0, row
                            assert row['clamp_calls'] < row['mouse_events'] / 4, row
                            assert row['workarea_calls'] < row['mouse_events'] / 4, row
                            assert row['release_exact'], row
                            assert row['paused_grabs_after_show'] == 0, row
                    if not args.baseline:
                        for repeat in range(3):
                            origin = win.pos() + offset
                            mouse(QEvent.MouseButtonPress, origin)
                            mouse(QEvent.MouseMove, origin + QPoint(20, 10))
                            with patch.object(renderer._gl, '_measure_content',
                                              wraps=renderer._gl._measure_content) as measure:
                                # 上次松手留下的 120/180ms 回调不能打扰新一轮拖动。
                                win._refresh_live2d_alpha_mask()
                                win._refresh_live2d_input_region()
                                wait(210)
                                assert measure.call_count == 0
                            assert not win._drag_move_timer.isActive()
                            assert not renderer._render_timer.isActive()
                            release = origin + QPoint(32, 16)
                            mouse(QEvent.MouseButtonRelease, release)
                            assert renderer._render_timer.isActive()
                        # 按真实可见内容限制边界，而不是把透明画布整个塞进工作区。
                        for avoid in (False, True):
                            cfg['avoid_taskbar'] = avoid
                            area = win._workarea_rect()
                            origin = win.pos() + offset
                            mouse(QEvent.MouseButtonPress, origin)
                            mouse(QEvent.MouseMove, origin + QPoint(20, 10))
                            release = area.bottomRight() + QPoint(2000, 2000)
                            target = release - offset
                            expected = QPoint(*win._clamp_pos(target.x(), target.y()))
                            bottom = win._bottom_inset_for_workarea(win._content_inset()[3])
                            mouse(QEvent.MouseButtonRelease, release)
                            assert win.pos() == expected
                            assert win.y() + win.height() - bottom <= area.bottom() + 1
                            assert config.load()['pos'] == [win.x(), win.y()]
                        assert id(renderer._gl.model) == model_id
                        cfg['edge_snap'] = True
                        origin = win.pos() + offset
                        mouse(QEvent.MouseButtonPress, origin)
                        mouse(QEvent.MouseMove, origin + QPoint(20, 0))
                        left = win._screen_geo().left() - win._content_inset()[0]
                        release = QPoint(left, win.y()) + offset
                        mouse(QEvent.MouseButtonRelease, release)
                        assert win._edge == 'left'
                        assert renderer._render_timer.isActive()
                        cfg['edge_snap'] = False
                        win._undock(save=False)
            if not args.baseline:
                origin = win.pos() + QPoint(win.width() // 2, win.height() // 2)
                mouse(QEvent.MouseButtonPress, origin)
                mouse(QEvent.MouseMove, origin + QPoint(20, 10))
                win._set_character('slime')
                wait(100)
                assert win._drag_off is None and win._drag_target is None
                assert not win._drag_move_timer.isActive()
                position = win.pos()
                win._drag_move_flush()
                assert win.pos() == position
                # 共用拖动路径不能回归其他形象；关闭重力以单独检查鼠标落位。
                cfg['gravity'] = False
                cfg['image_path'] = str(next((ROOT / 'assets/whale/frames/idle_front').glob('*.png')))
                for character in ('slime', 'whale', 'image'):
                    win._set_character(character)
                    wait(150)
                    win._cancel_pending_switch_dock()
                    win.move(*win._clamp_pos(400, 180))
                    offset = win._visible_content_rect().center()
                    origin = win.pos() + offset
                    mouse(QEvent.MouseButtonPress, origin)
                    mouse(QEvent.MouseMove, origin + QPoint(30, 20))
                    assert win._drag_off is not None, character
                    release = origin + QPoint(70, 40)
                    expected = QPoint(*win._clamp_pos((release-offset).x(), (release-offset).y()))
                    mouse(QEvent.MouseButtonRelease, release)
                    assert win.pos() == expected, character
                    assert not win._drag_move_timer.isActive(), character
            if args.profile:
                pstats.Stats(profiler).sort_stats('cumulative').print_stats(22)
            if not args.baseline:
                print('DRAG_REGRESSION PASSED: click threshold, coalescing, precise release, '
                      'pause/show/resume, rapid regrab, workarea, edge snap, stale switch target, '
                      'pixel/whale/image drag', flush=True)
        finally:
            win.renderer.shutdown()
            win.tray.hide()
            for timer in win.findChildren(QTimer):
                timer.stop()
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            sys.excepthook = old_hook
    for path, expected in sources.items():
        assert (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) == expected, path
    print('SOURCE_UNCHANGED: %d files; %d drag cases completed' % (len(sources), len(rows)), flush=True)


if __name__ == '__main__':
    run()
