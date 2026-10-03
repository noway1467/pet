"""正式 exe 启动烟测：隔离配置，用延迟业务回调验证事件循环。"""
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


def run_case(executable, model=None):
    import config

    with tempfile.TemporaryDirectory(prefix='pet-frozen-smoke-') as temporary:
        home = Path(temporary)
        profile = home / '.desktop-pet'
        profile.mkdir()
        models = home / 'models'
        models.mkdir()
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg.update(character='live2d' if model else 'whale',
                   live2d_model=str(model) if model else '',
                   models_dir=str(models), nurture_mode=True,
                   chat_enabled=False, tts_enabled=False, voice_enabled=False,
                   holiday_greetings=False, edge_snap=False,
                   hotkey_toggle_pet='', hotkey_quick_panel='')
        config_path = profile / 'config.json'
        config_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        env = os.environ.copy()
        env.update(USERPROFILE=str(home), HOME=str(home),
                   APPDATA=str(home / 'AppData'), LOCALAPPDATA=str(home / 'LocalAppData'))
        env.pop('DESKTOP_PET_BUILD_DIAGNOSTICS', None)
        env.pop('DESKTOP_PET_DIAGNOSTIC_LOG', None)
        # 不借用开发环境 PATH 中的 Conda DLL，才能发现遗漏的 _ctypes/ffi 等依赖。
        windows = os.environ['SystemRoot']  # os.environ 在 Windows 下大小写不敏感，普通 dict 则不是。
        env['PATH'] = os.pathsep.join((str(Path(windows) / 'System32'), windows))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        start = time.monotonic()
        proc = subprocess.Popen([str(executable)], cwd=executable.parent, env=env,
                                startupinfo=startup, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            affinity_path = profile / 'affinity.json'
            deadline = start + 25
            while time.monotonic() < deadline:
                assert proc.poll() is None, f'exe 提前退出：{proc.returncode}'
                try:
                    affinity = json.loads(affinity_path.read_text(encoding='utf-8'))
                except (FileNotFoundError, json.JSONDecodeError):
                    affinity = {}
                # 创建文件不算成功，必须等启动后 4.2 秒的相见结算真正完成。
                if affinity.get('daily_counts', {}).get('daily_login', 0) > 0:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError('未观测到延迟业务回调：可能启动失败或事件循环阻塞')
            time.sleep(3)
            assert proc.poll() is None, f'回调后 exe 异常退出：{proc.returncode}'
            actual = json.loads(config_path.read_text(encoding='utf-8'))
            assert actual['character'] == cfg['character'], '启动后角色发生错误回退'
            if model:
                assert actual['live2d_model'] == str(model)
            return {'character': cfg['character'], 'model': model.name if model else None,
                    'event_loop_callback': True, 'seconds': round(time.monotonic() - start, 2)}
        finally:
            # 只结束本测试创建的进程，不按名称结束用户已有桌宠。
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=10)


def run():
    if len(sys.argv) < 2:
        raise SystemExit('用法：python tests/frozen_smoke.py exe路径 [v2模型路径 v3模型路径]')
    executable = Path(sys.argv[1]).resolve(strict=True)
    models = [Path(value).resolve(strict=True) for value in sys.argv[2:]]
    for model in [None, *models]:
        print('FROZEN_SMOKE=' + json.dumps(run_case(executable, model), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    run()
