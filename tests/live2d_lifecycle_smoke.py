"""真实 v2/v3 生命周期、热尺寸、画质切换与失效回调回归测试。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    paths = [Path(p).resolve() for p in sys.argv[1:]]
    assert len(paths) >= 2, '需要实际 Cubism 2 / Cubism 3 模型路径'
    source_snapshot = {}
    for model in paths:
        for path in model.parent.rglob('*'):
            if path.is_file():
                source_snapshot[path] = (path.stat().st_mtime_ns,
                                         hashlib.sha256(path.read_bytes()).hexdigest())
    with tempfile.TemporaryDirectory(prefix='pet-lifecycle-') as tmp:
        os.environ['USERPROFILE'] = os.environ['HOME'] = tmp
        import config
        models = Path(tmp) / 'models'
        models.mkdir()
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(character='whale', chat_enabled=False, voice_enabled=False,
                   tts_enabled=False, models_dir=str(models), whale_auto_actions=False,
                   edge_snap=False, holiday_greetings=False,
                   hotkey_toggle_pet='', hotkey_quick_panel='')
        config.save(cfg)
        from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent
        from PySide6.QtGui import QSurfaceFormat, QImage, QPainter, QColor
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtTest import QTest
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        import main
        import live2d_pet
        from whale_pet import WhalePet
        errors = []
        def report_error(kind, value, tb):
            errors.append(repr(value))
            sys.__excepthook__(kind, value, tb)
        sys.excepthook = report_error
        QMessageBox.warning = lambda *args: errors.append(str(args[1:]))
        win = main.PetWindow(cfg)
        win.show()
        captured = []
        def wait(ms):
            QTest.qWait(ms)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not errors, errors
        def ready():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                wait(30)
                if isinstance(win.renderer, live2d_pet.Live2DPet) and win.renderer._frame is not None:
                    import numpy as np
                    image = win.renderer._frame.convertToFormat(QImage.Format_RGBA8888)
                    rgba = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
                    assert (rgba[:, :image.width()*4][:, 3::4] > 16).sum() > 100
                    return
            raise AssertionError('没有收到有效模型首帧')
        try:
            wait(300)
            # 同一事件循环提交 15 次，只应重建最后一次。
            with patch.object(win, '_build_renderer', wraps=win._build_renderer) as rebuild:
                for character in ['slime', 'cat', 'whale'] * 5:
                    win._set_character(character)
                wait(120)
                assert rebuild.call_count == 1, rebuild.call_count
            for path in paths:
                cfg['live2d_model'] = str(path)
                for limit in (0, 2048):
                    cfg['live2d_texture_limit'] = limit
                    win._set_character('live2d')
                    ready()
                    renderer = win.renderer
                    model = renderer._gl.model
                    original_callback = renderer.on_error
                    assert not renderer._gl._prepare_warning, renderer._gl._prepare_warning
                    assert renderer._gl.model_path == str(path)
                    if limit == 0:
                        assert renderer._gl._prepared_path == str(path)
                    source_data = json.loads(path.read_text(encoding='utf-8-sig'))
                    references = source_data.get('FileReferences', source_data)
                    declared_motions = references.get('Motions', references.get('motions', {}))
                    for group, motions in declared_motions.items():
                        assert len(renderer._gl._ensure_motion_data().get(group, [])) == len(motions)
                        if motions:
                            renderer.play_motion(group, 0, with_voice=False, with_subtitle=False)
                            wait(60)
                    declared_expressions = references.get('Expressions', references.get('expressions', []))
                    for expression in declared_expressions:
                        name = expression.get('Name', expression.get('name'))
                        assert name in renderer.list_expressions()
                        renderer.set_expression(name)
                        wait(30)
                    renderer.reset_expression()
                    for size in (120, 400, 200, 320, 160, 260):
                        win._set_live2d_size(size)
                        ready()
                        assert win.renderer is renderer and renderer._gl.model is model
                        frame = renderer._frame
                        expected = renderer._expected_framebuffer_size()
                        assert abs(frame.width() - expected[0]) <= 2 and abs(frame.height() - expected[1]) <= 2
                        bounds = renderer._gl._measure_content(frame)
                        assert bounds and all(0 <= value <= 1 for value in bounds)
                    captured.append((path.name, limit, renderer.grab().toImage()))
                    # 纯边界查询绝不能再抓取 framebuffer。
                    with patch.object(renderer._gl, 'grabFramebuffer', side_effect=AssertionError('额外抓帧')):
                        renderer.content_inset()
                        renderer.refresh_content_box()
                        renderer.sync_alpha_mask(force=True)
                    win._set_character('whale')
                    wait(150)
                    original_callback(str(path), 'delayed error from destroyed model')
                    wait(50)
                    assert isinstance(win.renderer, WhalePet)
            # 快速选择/关闭正在准备纹理的预览，旧回调不能误伤新预览或主角色。
            entries = [(path.parent.name, path.stem, str(path)) for path in paths]
            dialog = main.Live2DPicker(win, entries, str(paths[0]), {}, size_px=200)
            dialog.show()
            dialog._load_preview(str(paths[-1]))
            wait(20)
            dialog._load_preview(str(paths[0]))
            wait(700)
            dialog.done(0)
            wait(1500)
            assert dialog._preview is None
            assert isinstance(win.renderer, WhalePet)
            dialog.deleteLater()
            wait(50)
            # 损坏模型必须回退且清除旧回调，不产生 Python 异常或崩溃。
            win._set_character('live2d')
            ready()
            old = win.renderer
            old._gl._handle_render_error(RuntimeError('injected renderer failure'))
            win._set_character('whale')
            wait(200)
            assert isinstance(win.renderer, WhalePet)
            assert not errors, errors
            output = os.environ.get('LIVE2D_QA_IMAGE')
            if output:
                sheet = QImage(440 * len(captured), max(image.height() for _, _, image in captured) + 90,
                               QImage.Format_ARGB32)
                sheet.fill(QColor('#222634'))
                painter = QPainter(sheet)
                for i, (name, limit, image) in enumerate(captured):
                    painter.setPen(Qt.white)
                    painter.drawText(i*440 + 12, 24, name + ' / ' + str(limit or 'original'))
                    painter.drawImage(i*440 + 12, 60, image)
                painter.end()
                assert sheet.save(output)
            print('LIFECYCLE PASSED: %d models × original/balanced; %d hot sizes; '
                  'declared motions/expressions retained; 15 requests coalesced; '
                  'preview disposal/stale errors guarded; frame geometry/alpha valid'
                  % (len(paths), len(paths)*12), flush=True)
        finally:
            win.renderer.shutdown()
            win.tray.hide()
            for timer in win.findChildren(QTimer):
                timer.stop()
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    for path, expected in source_snapshot.items():
        actual = path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, str(path)
    print('SOURCE_UNCHANGED: %d files verified by SHA-256 and mtime' % len(source_snapshot), flush=True)


if __name__ == '__main__':
    run()
