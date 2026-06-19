"""模型语音播放（优先 pygame，按需初始化音频后端）。

很多 Live2D 模型（尤其 Cubism 2）在 model.json 里给每条动作配了 `"sound"`，
指向模型文件夹下的 `voice/*.wav` 或 `voice/*.mp3`。运行时库本身不会播放这些音频，
这里优先用 `pygame.mixer` 统一播放；若 pygame 不可用，再退回 `winsound` 播放 WAV。

音量：winsound 自身没有音量控制，所以音量 < 100% 时，用 `audioop` 把 PCM 采样
按比例缩小，在内存里重新封装成一段 WAV，再用 `SND_MEMORY` 播放（不落临时文件）。
pygame.mixer 支持直接设置音量。

特点：
- 异步播放（SND_ASYNC / pygame非阻塞），不阻塞界面；再调一次会顶掉正在放的那条（不会"多张嘴"）。
- pygame.mixer 延迟到首次播放/读 MP3 时长时才初始化，减少启动时音频设备占用。
- 没装 winsound（非 Windows）/ 文件缺失 / 非 PCM wav 时安静降级，绝不抛错影响主程序。
"""
import io
import os
import threading
import wave

try:
    import winsound
    _AVAILABLE = True
except Exception:  # noqa: BLE001  非 Windows / 裁剪环境
    winsound = None
    _AVAILABLE = False

try:
    import audioop  # 3.13 起标准库移除；3.12 仍在。缺了就只能整段满音量播放
    _HAS_AUDIOOP = True
except Exception:  # noqa: BLE001
    audioop = None
    _HAS_AUDIOOP = False

# 尝试导入 pygame.mixer 用于播放 MP3
try:
    import pygame.mixer as mixer
    _HAS_PYGAME = True
    _PYGAME_INITED = False  # 延迟初始化，避免在导入时占用音频设备
except Exception:  # noqa: BLE001
    mixer = None
    _HAS_PYGAME = False
    _PYGAME_INITED = False


class VoicePlayer:
    """简单的 .wav/.mp3 语音播放器，一次只放一条，支持 0~1 音量。"""

    def __init__(self, enabled=True, volume=0.5):
        self._enabled = bool(enabled)
        self._volume = self._clamp(volume)
        self._buf = None          # 持有正在异步播放的内存 WAV，防止被 GC（SND_MEMORY 要求）
        self._lock = threading.Lock()
        self._dur_cache = {}      # 音频时长缓存 {path: 秒/None}，避免每次戳都重新解码 MP3

    def _ensure_pygame_init(self):
        """确保 pygame.mixer 已初始化（延迟初始化，只做一次）"""
        global _PYGAME_INITED
        if _HAS_PYGAME and not _PYGAME_INITED:
            try:
                if not mixer.get_init():
                    mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
                mixer.music.set_volume(self._volume)
                _PYGAME_INITED = True
            except Exception:
                pass

    @staticmethod
    def _clamp(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, v))

    @property
    def available(self):
        return _AVAILABLE or _HAS_PYGAME

    def set_enabled(self, on):
        self._enabled = bool(on)
        if not self._enabled:
            self.stop()

    def is_enabled(self):
        return self._enabled

    def set_volume(self, v):
        """设置音量 0.0-1.0，立即应用到 pygame 和后续 winsound 播放。"""
        self._volume = self._clamp(v)
        # 若后端已经启动，则实时更新 pygame 音量；否则保留给首次播放时生效
        if _HAS_PYGAME and _PYGAME_INITED:
            try:
                mixer.music.set_volume(self._volume)
            except Exception:
                pass

    def get_volume(self):
        return self._volume

    def get_duration(self, path):
        """返回音频时长(秒)，拿不到返回 None。用于让字幕气泡显示时长与语音对齐。

        结果按路径缓存：同一段语音只解码一次，避免每次戳宠物都在 UI 线程整段解码 MP3。
        WAV 直接读头部算（帧数/采样率）；MP3 用 pygame.mixer.Sound 量一下长度
        （部分 SDL_mixer 不支持 mp3 的 Sound，失败就返回 None 交给调用方按字数估算）。"""
        if not path or not os.path.isfile(path):
            return None
        if path in self._dur_cache:
            return self._dur_cache[path]
        dur = None
        ext = path.lower()
        if ext.endswith('.wav'):
            try:
                with wave.open(path, 'rb') as w:
                    fr = w.getframerate()
                    n = w.getnframes()
                if fr:
                    dur = n / float(fr)
            except Exception:  # noqa: BLE001  非标准 PCM
                dur = None
        elif ext.endswith('.mp3') and _HAS_PYGAME:
            try:
                self._ensure_pygame_init()
                snd = mixer.Sound(path)
                d = snd.get_length()
                del snd
                dur = d if d and d > 0 else None
            except Exception:  # noqa: BLE001  该 SDL_mixer 不支持用 Sound 加载 mp3
                dur = None
        self._dur_cache[path] = dur
        return dur

    def play(self, path):
        """异步播放一个 .wav 或 .mp3，按当前音量缩放；关闭、静音、文件缺失或库不可用时跳过。"""
        if not (self._enabled and path):
            return
        if self._volume <= 0.0:
            return
        if not os.path.isfile(path):
            return

        # 统一使用pygame播放所有音频（WAV和MP3），避免winsound音量问题
        ext = path.lower()
        if ext.endswith('.mp3') or ext.endswith('.wav'):
            if _HAS_PYGAME:
                threading.Thread(target=self._play_pygame, args=(path, self._volume), daemon=True).start()
                return

        # 降级：如果pygame不可用且是WAV，尝试winsound（仅100%音量）
        if ext.endswith('.wav') and _AVAILABLE and self._volume >= 0.99:
            threading.Thread(target=self._play_wav, args=(path, self._volume), daemon=True).start()

    def _play_pygame(self, path, vol):
        """使用 pygame.mixer 播放音频（统一处理MP3和WAV）。"""
        try:
            self._ensure_pygame_init()
            # 停止当前播放
            mixer.music.stop()
            # 设置音量
            mixer.music.set_volume(vol)
            # 加载并播放
            mixer.music.load(path)
            mixer.music.play()
        except Exception as e:  # noqa: BLE001  播放失败不影响主程序
            # 某些文件可能损坏或格式不兼容，静默跳过
            pass

    def _play_mp3(self, path, vol):
        """使用 pygame.mixer 播放 MP3（已废弃，统一用_play_pygame）。"""
        self._play_pygame(path, vol)

    def _play_wav(self, path, vol):
        """使用 winsound 播放 WAV。"""
        try:
            if vol >= 0.99 or not _HAS_AUDIOOP:
                winsound.PlaySound(
                    path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return
            data = self._scaled_wav(path, vol)
            if data is None:   # 解析失败：退回满音量直接放文件，至少有声音
                winsound.PlaySound(
                    path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return
            with self._lock:
                self._buf = data    # 异步播放期间必须保持引用有效
                winsound.PlaySound(
                    data,
                    winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
        except Exception:  # noqa: BLE001  播放失败不影响主程序
            pass

    @staticmethod
    def _scaled_wav(path, vol):
        """读 wav -> 按 vol 缩放 PCM -> 在内存里重新封装成 WAV 字节；失败返回 None。"""
        try:
            with wave.open(path, "rb") as w:
                params = w.getparams()
                frames = w.readframes(w.getnframes())
            scaled = audioop.mul(frames, params.sampwidth, vol)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as o:
                o.setparams(params)
                o.writeframes(scaled)
            return buf.getvalue()
        except Exception:  # noqa: BLE001  非标准 PCM / 解析失败
            return None

    def stop(self):
        """停止当前播放的音频。"""
        # 停止 winsound
        if _AVAILABLE:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                pass
        # 停止 pygame
        if _HAS_PYGAME and _PYGAME_INITED:
            try:
                mixer.music.stop()
            except Exception:  # noqa: BLE001
                pass
        self._buf = None
