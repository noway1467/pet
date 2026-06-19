"""TTS 语音播放器：把气泡文字读出来。

主后端用 PySide6 自带的 **QtTextToSpeech**（零额外依赖、随程序一起打包、与 Qt 事件
循环天然集成），底层调用 Windows 的 SAPI/WinRT 语音引擎。没有 QtTextToSpeech 时退回
pyttsx3（需单独 pip 安装）。两者都没有就安静降级，绝不抛错影响主程序。

作为"无语音模型"的兜底方案：当模型没有配套 voice/*.wav 时，把气泡文字朗读出来，
实现"语音与语录天然融洽"。

特点：
- 零额外依赖：QtTextToSpeech 随 PySide6 提供，开箱即用，也能被 PyInstaller 打进 exe
- 异步朗读，不阻塞界面（QtTextToSpeech 走 Qt 事件循环；pyttsx3 走独立工作线程）
- 中文语录默认用中文嗓音：自动把 locale 设为 zh_CN 并挑一个中文嗓音，
  避免英文引擎读不出中文（很多机器默认嗓音是英文）
- 支持音量 / 语速 / 嗓音选择
- 自定义后端：可接入外部 TTS 命令/API（edge-tts、piper、本地 HTTP 服务等），
  命令模板用 {text} 占位要朗读的文字、{out} 占位合成音频输出文件（本程序负责播放）；
  模板里没有 {text} 时，文字通过 stdin 传入（兼容 piper 等从标准输入读取的工具）。
- 失败时静默降级
"""
import os
import queue
import shlex
import subprocess
import tempfile
import threading

# 在 Windows 上启动子进程时隐藏控制台黑框（GUI 程序里很重要）
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ── 主后端：PySide6 自带的 QtTextToSpeech（首选） ────────────────────────────
try:
    from PySide6.QtTextToSpeech import QTextToSpeech
    from PySide6.QtCore import QLocale
    _QT_TTS = True
except Exception:  # noqa: BLE001  裁剪过的 PySide6 可能没有这个模块
    QTextToSpeech = None
    QLocale = None
    _QT_TTS = False

# ── 兜底后端：pyttsx3（需单独安装，未安装时不可用） ──────────────────────────
try:
    import pyttsx3
    _PYTTSX = True
except Exception:  # noqa: BLE001
    pyttsx3 = None
    _PYTTSX = False

_AVAILABLE = _QT_TTS or _PYTTSX


def tts_available():
    """当前环境是否支持 TTS（任一后端可用）。"""
    return _AVAILABLE


def _is_chinese_voice(name):
    """嗓音名是否像中文嗓音（用于自动挑选 / 排序）。"""
    n = (name or "").lower()
    return any(k in n for k in (
        "chinese", "mandarin", "huihui", "yaoyao", "kangkang",
        "xiaoxiao", "yunyang", "zh-", "zh_", "中文",
    ))


_voice_cache = None


def list_voice_names():
    """列出可选嗓音名（中文嗓音排前面），供设置菜单使用。结果缓存一次。

    需要 QApplication 已创建（菜单构建时一定满足）；拿不到就返回空列表。
    枚举前把 locale 设成 zh_CN，确保能列出中文嗓音（availableVoices 只返回当前
    locale 的嗓音）。"""
    global _voice_cache
    if _voice_cache is not None:
        return _voice_cache
    names = []
    if _QT_TTS:
        try:
            from PySide6.QtWidgets import QApplication
            if QApplication.instance() is not None:
                eng = QTextToSpeech()
                try:
                    eng.setLocale(QLocale(QLocale.Chinese, QLocale.China))
                except Exception:
                    pass
                seen = set()
                for v in eng.availableVoices():
                    nm = v.name()
                    if nm and nm not in seen:
                        seen.add(nm)
                        names.append(nm)
                del eng
        except Exception:  # noqa: BLE001
            names = []
    # 中文嗓音排前面，其余按名字排序
    names.sort(key=lambda n: (not _is_chinese_voice(n), n))
    _voice_cache = names
    return names


def _to_native_rate(r):
    """把语速值规整到 QtTextToSpeech 的 -1.0~1.0（0=正常）。

    兼容旧配置里的 wpm（如 150）：|r|>1 视为 wpm，按 (r-150)/150 映射
    （150→0 正常，300→1 最快，0→-1 最慢）。"""
    try:
        r = float(r)
    except (TypeError, ValueError):
        return 0.0
    if r < -1.0 or r > 1.0:   # 旧配置的 wpm
        r = (r - 150.0) / 150.0
    return max(-1.0, min(1.0, r))


class TTSPlayer:
    """文本朗读器：优先用 QtTextToSpeech，没有则退回 pyttsx3，支持音量/语速/嗓音。"""

    def __init__(self, enabled=True, volume=0.7, rate=0.0, voice="",
                 engine="auto", custom_cmd=""):
        """初始化 TTS 播放器。

        Args:
            enabled: 是否启用
            volume: 音量 0.0~1.0（默认 0.7，比模型语音稍小，不抢戏）
            rate:   语速；QtTextToSpeech 原生 -1.0~1.0（0=正常），也兼容旧的 wpm
            voice:  嗓音名；空字符串=自动挑选中文嗓音
            engine: "auto"=QtTextToSpeech（默认）；"custom"=自定义外部命令/API
            custom_cmd: 自定义命令模板（engine="custom" 时使用），支持 {text}/{out} 占位
        """
        self._engine_mode = "custom" if engine == "custom" else "auto"
        self._custom_cmd = custom_cmd or ""
        self._enabled = bool(enabled) and (
            _AVAILABLE or (self._engine_mode == "custom" and bool(self._custom_cmd.strip()))
        )
        self._volume = self._clamp(volume, 0.0, 1.0)
        self._rate = _to_native_rate(rate)
        self._voice_name = voice or ""

        self._engine = None       # QTextToSpeech 实例或 pyttsx3 引擎
        self._backend = None      # 'qt' | 'pyttsx'
        self._voice_player = None  # 自定义命令合成出文件后，用它来播放

        # pyttsx3 兜底用的线程/队列（QtTextToSpeech 不需要）
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker_thread = None
        self._stop_flag = False

        # 自定义命令合成的临时输出文件（固定路径，每次覆盖，避免堆积）
        self._custom_out = os.path.join(tempfile.gettempdir(), "desktop_pet_tts_out.mp3")

        if self._enabled and self._engine_mode != "custom":
            self._ensure_engine()

    # ------------------------------------------------------------------ #
    #  基础属性
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clamp(v, lo, hi):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return (lo + hi) / 2
        return max(lo, min(hi, v))

    @property
    def available(self):
        """TTS 是否可用。"""
        return _AVAILABLE

    def is_enabled(self):
        return self._enabled

    def get_volume(self):
        return self._volume

    def get_rate(self):
        return self._rate

    def get_voice(self):
        return self._voice_name

    # ------------------------------------------------------------------ #
    #  引擎初始化
    # ------------------------------------------------------------------ #
    def _ensure_engine(self):
        """惰性创建语音引擎。QtTextToSpeech 必须在 GUI 线程、且 QApplication 存在时创建。"""
        if self._engine is not None or self._backend == 'pyttsx':
            return

        if _QT_TTS:
            try:
                from PySide6.QtWidgets import QApplication
                if QApplication.instance() is None:
                    return  # 还没有 QApplication，等下次（有事件循环时）再建
                self._engine = QTextToSpeech()
                self._backend = 'qt'
                # 默认中文 locale + 中文嗓音，保证读得出中文语录
                try:
                    self._engine.setLocale(QLocale(QLocale.Chinese, QLocale.China))
                except Exception:
                    pass
                self._apply_voice()
                self._engine.setVolume(self._volume)
                self._engine.setRate(self._rate)
                return
            except Exception:  # noqa: BLE001  创建失败则尝试兜底后端
                self._engine = None
                self._backend = None

        if _PYTTSX:
            self._backend = 'pyttsx'
            self._start_worker()

    def _apply_voice(self):
        """按 self._voice_name 设置 QtTextToSpeech 嗓音；为空则自动挑中文嗓音。"""
        if self._backend != 'qt' or not self._engine:
            return
        try:
            voices = self._engine.availableVoices()
            if not voices:
                return
            chosen = None
            if self._voice_name:
                for v in voices:
                    if v.name() == self._voice_name:
                        chosen = v
                        break
            if chosen is None:   # 自动：优先中文嗓音
                for v in voices:
                    if _is_chinese_voice(v.name()):
                        chosen = v
                        break
            if chosen is not None:
                self._engine.setVoice(chosen)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    #  开关 / 参数
    # ------------------------------------------------------------------ #
    def set_enabled(self, on):
        """启用/禁用 TTS。"""
        self._enabled = bool(on) and (
            _AVAILABLE or (self._engine_mode == "custom" and bool(self._custom_cmd.strip()))
        )
        if not self._enabled:
            self.stop()
        elif self._engine_mode != "custom":
            self._ensure_engine()

    def set_volume(self, v):
        """设置音量 0.0~1.0。"""
        self._volume = self._clamp(v, 0.0, 1.0)
        if self._backend == 'qt' and self._engine:
            try:
                self._engine.setVolume(self._volume)
            except Exception:
                pass
        elif self._backend == 'pyttsx' and self._engine:
            try:
                self._engine.setProperty('volume', self._volume)
            except Exception:
                pass
        if self._voice_player:   # 自定义命令的播放音量
            try:
                self._voice_player.set_volume(self._volume)
            except Exception:
                pass

    def set_rate(self, r):
        """设置语速（原生 -1.0~1.0，0=正常；也兼容旧 wpm）。"""
        self._rate = _to_native_rate(r)
        if self._backend == 'qt' and self._engine:
            try:
                self._engine.setRate(self._rate)
            except Exception:
                pass
        elif self._backend == 'pyttsx' and self._engine:
            try:   # pyttsx3 用 wpm：把 -1~1 映回大约 50~250
                self._engine.setProperty('rate', int(150 + self._rate * 100))
            except Exception:
                pass

    def set_voice(self, name):
        """设置嗓音名（空=自动挑中文嗓音）。"""
        self._voice_name = name or ""
        if self._backend == 'qt':
            self._apply_voice()
        # pyttsx3 兜底场景不强求切换嗓音

    def set_engine(self, mode):
        """切换后端："auto"=QtTextToSpeech；"custom"=自定义命令/API。"""
        self._engine_mode = "custom" if mode == "custom" else "auto"
        self.stop()
        # 重新计算可用性（自定义模式即使没有 QtTextToSpeech 也能用）
        self._enabled = self._enabled and (
            _AVAILABLE or (self._engine_mode == "custom" and bool(self._custom_cmd.strip()))
        )
        if self._enabled and self._engine_mode != "custom":
            self._ensure_engine()

    def set_custom_cmd(self, cmd):
        """设置自定义命令模板。"""
        self._custom_cmd = cmd or ""

    def get_engine(self):
        return self._engine_mode

    def get_custom_cmd(self):
        return self._custom_cmd

    # ------------------------------------------------------------------ #
    #  朗读 / 停止
    # ------------------------------------------------------------------ #
    def speak(self, text):
        """异步朗读文本。新的朗读会顶掉正在播放的，避免"多张嘴"。"""
        if not (self._enabled and text):
            return
        if self._volume <= 0.0:
            return

        # 自定义命令/API 后端：在子线程里合成并播放，不阻塞界面
        if self._engine_mode == "custom" and self._custom_cmd.strip():
            threading.Thread(target=self._speak_custom, args=(text,), daemon=True).start()
            return

        self._ensure_engine()

        if self._backend == 'qt' and self._engine:
            try:
                self._engine.stop()    # 顶掉当前，再说新的
                self._engine.say(text)
            except Exception:
                pass
            return

        if self._backend == 'pyttsx':
            # 清空队列、停掉当前，再放入新任务
            self._drain_queue()
            if self._engine:
                try:
                    self._engine.stop()
                except Exception:
                    pass
            self._queue.put(text)

    def stop(self):
        """停止当前朗读。"""
        if self._voice_player:   # 自定义命令合成的音频
            try:
                self._voice_player.stop()
            except Exception:
                pass
        if self._backend == 'qt' and self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass
            return
        if self._backend == 'pyttsx':
            self._drain_queue()
            if self._engine:
                try:
                    self._engine.stop()
                except Exception:
                    pass

    def shutdown(self):
        """关闭引擎，释放资源。"""
        self.stop()
        if self._backend == 'pyttsx':
            self._stop_flag = True
            if self._worker_thread:
                self._queue.put(None)
                self._worker_thread.join(timeout=2)
        self._engine = None

    # ------------------------------------------------------------------ #
    #  自定义命令 / API 后端
    # ------------------------------------------------------------------ #
    def _speak_custom(self, text):
        """用自定义命令/API 合成并朗读一句。

        - 模板含 {text}：替换为要朗读的文字（作为单个参数传入，不经 shell，避免注入/转义问题）。
          不含 {text}：文字通过 stdin 传给命令（兼容 piper 等从标准输入读取的工具）。
        - 模板含 {out}：本程序提供一个临时输出路径，命令把音频写进去，写完由本程序播放。
          不含 {out}：认为命令自己会发声（如自带播放的工具），本程序不再播放。
        出错时静默忽略，不影响主程序。"""
        cmd_t = self._custom_cmd.strip()
        if not cmd_t:
            return
        want_out = "{out}" in cmd_t
        out_path = self._custom_out if want_out else None
        use_stdin = "{text}" not in cmd_t
        try:
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            argv = self._build_argv(cmd_t, "" if use_stdin else text, out_path)
            if not argv:
                return
            self.stop()   # 顶掉正在放的自定义语音
            subprocess.run(
                argv,
                input=((text + "\n").encode("utf-8", "ignore") if use_stdin else None),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=_CREATE_NO_WINDOW,
            )
            if out_path and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                self._play_custom_file(out_path)
        except Exception:
            pass

    @staticmethod
    def _build_argv(template, text, out_path):
        """把命令模板解析成 argv（shell=False），再替换 {text}/{out} 占位。

        Windows 下用 posix=False 解析以保留路径里的反斜杠（如 C:\\tools\\piper.exe），
        再手动去掉外层引号；{text}/{out} 在分词之后才替换，所以其中的特殊字符不受影响。"""
        try:
            if os.name == "nt":
                parts = shlex.split(template, posix=False)
                parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p
                         for p in parts]
            else:
                parts = shlex.split(template, posix=True)
        except Exception:
            parts = template.split()
        argv = []
        for p in parts:
            p = p.replace("{text}", text)
            if out_path is not None:
                p = p.replace("{out}", out_path)
            argv.append(p)
        return argv

    def _ensure_voice_player(self):
        """惰性创建用于播放自定义合成音频的播放器（复用 voice_player）。"""
        if self._voice_player is None:
            try:
                from voice_player import VoicePlayer
                self._voice_player = VoicePlayer(enabled=True, volume=self._volume)
            except Exception:
                self._voice_player = None

    def _play_custom_file(self, path):
        """播放自定义命令合成出来的音频（按当前音量；wav/mp3 均可）。"""
        self._ensure_voice_player()
        if self._voice_player:
            try:
                self._voice_player.set_volume(self._volume)
                self._voice_player.play(path)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  pyttsx3 兜底：工作线程
    # ------------------------------------------------------------------ #
    def _drain_queue(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _start_worker(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_flag = False
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def _worker(self):
        """pyttsx3 工作线程：初始化引擎并串行播放队列里的文本。"""
        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty('volume', self._volume)
            self._engine.setProperty('rate', int(150 + self._rate * 100))
            while not self._stop_flag:
                try:
                    text = self._queue.get(timeout=1)
                    if text is None:
                        break
                    if text:
                        self._engine.say(text)
                        self._engine.runAndWait()
                except queue.Empty:
                    continue
                except Exception:
                    pass
        except Exception:
            self._enabled = False
        finally:
            if self._engine:
                try:
                    self._engine.stop()
                except Exception:
                    pass


# 全局单例（保留以兼容旧调用；主程序实际是直接 new TTSPlayer）
_tts_player = None


def get_tts_player():
    """获取全局 TTS 播放器单例。"""
    global _tts_player
    if _tts_player is None:
        _tts_player = TTSPlayer()
    return _tts_player
