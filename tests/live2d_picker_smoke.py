"""真实 v2/v3 管理界面预览：选择到首帧计时、快速切换、复用与销毁。"""
import argparse
import copy
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    paths = [str(Path(p).resolve()) for p in args.models]
    with tempfile.TemporaryDirectory(prefix='pet-picker-') as tmp:
        os.environ['USERPROFILE'] = os.environ['HOME'] = tmp
        import config
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(models_dir=tmp, live2d_texture_limit=2048,
                   hotkey_toggle_pet='', hotkey_quick_panel='')
        config.save(cfg)
        from PySide6.QtCore import Qt, QCoreApplication, QEvent
        from PySide6.QtGui import QSurfaceFormat, QImage
        from PySide6.QtWidgets import QApplication, QWidget
        from PySide6.QtTest import QTest
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        import main
        import live2d_pet
        app.setStyleSheet(main.APP_QSS)
        errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: errors.append(repr(value))
        host = QWidget()
        host.cfg = cfg
        entries = [(Path(p).parent.name, 'model', p) for p in paths]
        dialog = main.Live2DPicker(host, entries, '', {})
        def wait(ms):
            QTest.qWait(ms)
            assert not errors, errors
        def select(path):
            dialog._select_current(path)
        def ready(path):
            deadline = time.perf_counter() + 25
            while time.perf_counter() < deadline:
                wait(5)
                pv = dialog._preview
                if pv is not None and dialog._preview_path == path and pv._frame is not None:
                    import numpy as np
                    image = pv._frame.convertToFormat(QImage.Format_RGBA8888)
                    rgba = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
                    assert (rgba[:, :image.width()*4][:, 3::4] > 16).sum() > 100
                    return pv
            raise AssertionError('预览超时：' + path)
        try:
            dialog.show()
            wait(50)
            for repeat in range(2):
                for path in paths:
                    start = time.perf_counter()
                    select(path)
                    pv = ready(path)
                    print('PICKER_RESULT=' + json.dumps(dict(model=Path(path).name, repeat=repeat,
                          first_ms=round((time.perf_counter()-start)*1000),
                          texture_limit=pv._gl._texture_limit)), flush=True)
                    if not args.baseline:
                        assert dialog.apply_btn.isEnabled() and not dialog.hint.isVisible()
                        assert not pv._gl._prepare_warning
                        identifier = id(pv._gl.model)
                        dialog._load_preview(path)
                        wait(150)
                        assert dialog._preview is pv and id(pv._gl.model) == identifier
                        assert pv._render_timer.interval() == int(1000 / pv.fps)
            if not args.baseline:
                old = dialog._preview
                other = next(p for p in paths if p != dialog._preview_path)
                select(other)
                assert not dialog.apply_btn.isEnabled() and dialog._sel_path is None
                assert not old._render_timer.isActive()
                with patch.object(live2d_pet, 'create_live2d_pet', wraps=live2d_pet.create_live2d_pet) as create:
                    for path in paths * 6:
                        select(path)
                    ready(paths[-1])
                    assert create.call_count <= 1, create.call_count
                desktop_limit = cfg['live2d_texture_limit']
                dialog.preview_quality.setCurrentIndex(dialog.preview_quality.findData(2048))
                wait(150)
                pv = ready(paths[-1])
                assert pv._gl._texture_limit == 2048
                assert config.load()['live2d_preview_texture_limit'] == 2048
                assert cfg['live2d_texture_limit'] == desktop_limit
                output = os.environ.get('PICKER_QA_IMAGE')
                if output:
                    assert dialog.grab().save(output)
            dialog.done(0)
            wait(600)
            assert dialog._preview is None and not dialog._preview_timer.isActive()
            print('PICKER PASSED: real frames, rapid selection, no stale preview after close', flush=True)
        finally:
            dialog.done(0)
            dialog.deleteLater()
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            sys.excepthook = old_hook


if __name__ == '__main__':
    run()
