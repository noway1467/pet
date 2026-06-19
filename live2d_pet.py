"""Live2D 渲染器（可选）。

同时支持 **Cubism 2**（`.moc` / `model.json`、`*.model.json`）和
**Cubism 3**（`.moc3` / `*.model3.json`）两种模型，按文件自动选择 v2 / v3 运行时。

依赖：pip install live2d-py。没装库或没模型时抛出明确的错误，
由主程序捕获并回退到像素模式。

注意：项目里那个装模型的 `live2d/` 文件夹和 pip 包 `live2d` 同名，
但 live2d-py 是带 __init__.py 的正式包，会优先于本地命名空间文件夹被导入，
所以两者并不冲突（实测 import live2d.v2 / live2d.v3 都能正确指向 site-packages）。
"""
import glob
import json
import os
import random
import time
import sys
import gc

from PySide6.QtCore import Qt, QTimer, QSize, QRect
from PySide6.QtGui import QImage, QRegion, QPainter
from PySide6.QtWidgets import QWidget
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from voice_player import VoicePlayer

# 加载语音翻译数据库
_VOICE_TRANSLATIONS = None

def _load_voice_translations():
    """加载语音翻译数据库"""
    global _VOICE_TRANSLATIONS
    if _VOICE_TRANSLATIONS is not None:
        return _VOICE_TRANSLATIONS

    # 查找翻译文件
    if getattr(sys, "frozen", False):
        # 打包后的exe环境
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发环境
        base_dir = os.path.dirname(os.path.abspath(__file__))

    translation_file = os.path.join(base_dir, "voice_translations.json")

    if not os.path.exists(translation_file):
        _VOICE_TRANSLATIONS = {}
        return _VOICE_TRANSLATIONS

    try:
        with open(translation_file, 'r', encoding='utf-8') as f:
            _VOICE_TRANSLATIONS = json.load(f)
    except Exception:
        _VOICE_TRANSLATIONS = {}

    return _VOICE_TRANSLATIONS

try:
    import live2d.v3 as live2d_v3
    try:
        import live2d.v2cpp as live2d_v2   # C++ 加速版：比纯 Python v2 更稳（不会因裁剪遮罩崩溃）也更省 CPU
        LIVE2D_V2_BACKEND = "v2cpp"
    except Exception:
        import live2d.v2 as live2d_v2       # 退化：纯 Python v2
        LIVE2D_V2_BACKEND = "v2"
    LIVE2D_AVAILABLE = True
    LIVE2D_IMPORT_ERROR = ""
except Exception as e:  # noqa: BLE001  导入失败时整体不可用
    live2d_v2 = live2d_v3 = None
    LIVE2D_AVAILABLE = False
    LIVE2D_IMPORT_ERROR = repr(e)
    LIVE2D_V2_BACKEND = ""

# 每个版本的运行时 init() 只需调用一次（全局）
_inited = set()

# Live2D 窗口的默认高/宽比例（立绘偏竖；可在右键/选择器里按模型调高）
L2D_RATIO = 1.4
# 画布高/宽比例允许范围（贴合内容时可能很宽/很方，所以下限给得比较低）
RATIO_MIN, RATIO_MAX = 0.55, 2.8
# Live2D 透明遮罩不要紧贴模型边缘，否则模型呼吸/物理轻动时 Windows 合成层会反复裁到边缘，
# 看起来像背后黑影在抽搐。给一圈余量，并降低刷新频率，让黑底保持透明但轮廓更稳定。
MASK_PADDING_MIN, MASK_PADDING_MAX = 8, 18
MASK_REFRESH_MS = 180
MASK_PREVIEW_REFRESH_MS = 260
CANVAS_MARGIN_MIN, CANVAS_MARGIN_MAX = 32, 96
CANVAS_MARGIN_RATIO = 0.22

# 可识别为 Live2D 模型设置的 JSON 文件后缀（小写）
MODEL_SUFFIXES = (".model3.json", ".model.json")


def is_model_json(filename):
    """文件名是否像一个 Live2D 模型设置 JSON。"""
    low = filename.lower()
    return low.endswith(MODEL_SUFFIXES) or low == "model.json"


def detect_version(model_path):
    """返回 'v3' 或 'v2'：优先看扩展名，拿不准再读 JSON 内容。"""
    low = model_path.lower()
    if low.endswith(".model3.json") or low.endswith(".moc3"):
        return "v3"
    if low.endswith(".model.json") or os.path.basename(low) == "model.json":
        # 绝大多数是 Cubism 2，但仍读内容兜底（个别打包把 moc3 塞进 model.json）
        try:
            with open(model_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "FileReferences" in data or str(data.get("Version", "")).startswith("3"):
                return "v3"
            ref = data.get("FileReferences") or {}
            moc = data.get("model") or ref.get("Moc") or ""
            if isinstance(moc, str) and moc.lower().endswith(".moc3"):
                return "v3"
        except (OSError, ValueError):
            pass
    return "v2"


def _module_for(version):
    return live2d_v3 if version == "v3" else live2d_v2


def discover_models(root, limit=5000):
    """递归扫描 root，找出所有 Live2D 模型设置 JSON。

    返回 [(group, variant, path), ...]：
      group   = 模型所在文件夹名（用于菜单分组）
      variant = 该 JSON 的简短名字（一个文件夹有多个 json 时区分用）

    扫描是**实时**的：往 root（含任意层级子文件夹）里放新模型后，重新调用本函数
    即可列出，无需固定结构。limit 只是个防失控的上限；达到时会打印提示而非静默截断。
    """
    found = []
    if not root or not os.path.isdir(root):
        return found
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not is_model_json(fn):
                continue
            group = os.path.basename(dirpath) or os.path.basename(root)
            variant = fn
            for suf in (".model3.json", ".model.json", ".json"):
                if variant.lower().endswith(suf):
                    variant = variant[: -len(suf)]
                    break
            # 去掉和文件夹同名的前缀，让 variant 更短，例如 22/model.default -> default
            if variant.lower().startswith(group.lower() + "."):
                variant = variant[len(group) + 1:]
            for pre in ("model.", "model"):
                if variant.lower().startswith(pre) and variant.lower() != pre.rstrip("."):
                    variant = variant[len(pre):]
            variant = variant.strip(". ") or "default"
            found.append((group, variant, os.path.join(dirpath, fn)))
            if len(found) >= limit:
                print("[live2d] discover_models 达到上限 %d，其余模型未列出；"
                      "如确实有这么多模型，请调高 discover_models 的 limit 参数。" % limit)
                found.sort(key=lambda x: (x[0].lower(), x[1].lower()))
                return found
    found.sort(key=lambda x: (x[0].lower(), x[1].lower()))
    return found


def model_features(model_path):
    """快速读取模型设置 JSON（不加载 GL / 不开渲染），判断它是否带动作、是否带配套语音。

    供模型选择器分组/筛选用。返回 {"has_motion": bool, "has_sound": bool, "motions": int}。
    """
    feat = {"has_motion": False, "has_sound": False, "motions": 0}
    if not model_path or not os.path.isfile(model_path):
        return feat
    folder = os.path.dirname(model_path)
    try:
        with open(model_path, encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        return feat
    motions = j.get("motions")
    if not isinstance(motions, dict):
        motions = (j.get("FileReferences") or {}).get("Motions") or {}
    count, has_sound = 0, False
    if isinstance(motions, dict):
        for items in motions.values():
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    motion_file = it.get("file") or it.get("File") or ""
                    motion_path = os.path.normpath(os.path.join(folder, motion_file)) if motion_file else ""
                    if motion_path and os.path.isfile(motion_path):
                        count += 1
                    if it.get("sound") or it.get("Sound"):
                        has_sound = True
    feat["motions"] = count
    feat["has_motion"] = count > 0
    # 声音：动作里引用了 sound，或文件夹下有常见的语音子目录
    if not has_sound:
        for sub in ("voice", "voices", "sound", "sounds", "snd", "se", "audio"):
            if os.path.isdir(os.path.join(folder, sub)):
                has_sound = True
                break
    feat["has_sound"] = has_sound
    # v3：JSON 没声明动作但文件夹里散落着 *.motion3.json，也算"带动作"（浅扫一层，避免卡顿）
    if not feat["has_motion"] and model_path.lower().endswith(".model3.json"):
        try:
            extra_count = 0
            for root, dirs, filenames in os.walk(folder):
                depth = root[len(folder):].count(os.sep)
                if depth > 2:
                    dirs[:] = []
                    continue
                for fn in filenames:
                    if fn.lower().endswith(".motion3.json"):
                        extra_count += 1
                if extra_count:
                    break
            if extra_count:
                feat["motions"] += extra_count
                feat["has_motion"] = True
        except OSError:
            pass
    return feat


class _Live2DGL(QOpenGLWidget):
    """离屏 Live2D GL 渲染器。**永远不会被 show()**，由外层 `Live2DPet` 用
    grabFramebuffer 按帧驱动渲染、取出带 alpha 的画面。

    之所以不直接把这个 QOpenGLWidget 放进半透明顶层窗口显示：在 Windows 上
    QOpenGLWidget 合成进半透明分层窗口时，透明区会被 DWM 填成不透明黑色（黑色画框）。
    实测它产出的 framebuffer 本身 alpha 完全正确（角落 rgba=0,0,0,0），问题只出在
    "把 GL 表面合成进窗口"这一步。所以改由普通 QWidget 用 QPainter 画它的 framebuffer，
    走普通控件逐像素 alpha 合成，桌面上就只剩宠物本体、没有任何黑框。"""
    def __init__(self, model_path, size=300, zoom=1.0, xoff=0.0, yoff=0.0,
                 parent=None, ratio=None, preview_mode=False):
        super().__init__(parent)
        if not LIVE2D_AVAILABLE:
            raise RuntimeError(
                "未安装 live2d-py，请运行：\n"
                "  .venv\\Scripts\\python.exe -m pip install live2d-py\n" + LIVE2D_IMPORT_ERROR)
        if not model_path or not os.path.exists(model_path):
            raise RuntimeError(
                "找不到 Live2D 模型: %r\n请在右键菜单里选择模型设置文件"
                "（Cubism 2 的 model.json / *.model.json 或 Cubism 3 的 *.model3.json）"
                % (model_path,))
        self.model_path = model_path
        self._preview_mode = bool(preview_mode)
        self.version = detect_version(model_path)
        self.l2d = _module_for(self.version)
        # Live2D 立绘多为竖图：用竖向窗口（高=宽×比例），配合库的等比适配，
        # 整身都能显示不被裁。比例可按模型调高，解决"模型太高被切"。
        self._w = int(size)
        self._auto_ratio = ratio is None       # None=按模型画布自动定高/宽比
        self._ratio = L2D_RATIO if ratio is None else max(RATIO_MIN, min(RATIO_MAX, float(ratio)))
        self._h = round(self._w * self._ratio)
        self._canvas_margin = 0
        self._canvas_w = self._w
        self._canvas_h = self._h
        self._zoom = max(0.2, min(5.0, float(zoom)))    # 画面缩放（构图）
        self._xoff = max(-2.0, min(2.0, float(xoff)))   # 水平偏移（+右 -左）
        self._yoff = max(-2.0, min(2.0, float(yoff)))   # 竖直偏移（+上 -下）
        self.model = None
        self._last_t = None
        self._look = (0.0, 0.0)        # 归一化视线方向 -1..1
        self._idle_group = None        # 用于循环播放的待机动作组
        self._auto_motion = not self._preview_mode
        self._disabled_motions = set()  # 禁止"自动循环"播放的动作键集合 {"组名/索引"}（手动触发不受限）
        self._extra_motions = {}       # 我们额外登记的散落动作 {组名: 数量}（模型没在 json 里声明动作时用）
        self._declared_motion_groups = set()  # model3/json 明确声明的动作组；散落动作不自动当待机
        self._motion_data = None       # 懒加载：{组名:[{index,file,sound}]}（含每条动作配套的语音绝对路径）
        self._voice = VoicePlayer(enabled=not self._preview_mode)  # 预览模式不预热语音路径
        self._expressions = None       # 懒加载：本模型可用的表情 id 列表
        self._expression_params = {}    # {expr_id: [(param_id, value, blend), ...]}：用于表情回退
        self._expression_manual = None  # 手工表情状态：{"expr_id", "base", "params"}
        self._expression_active = ""    # 当前生效的表情 id（用于重置/缓存）
        self._expr_rr = -1             # 兜底随机表情的轮询下标
        self._auto_expr = False        # 是否自动随机切换表情
        self._next_expr_t = 0.0        # 下次自动换表情的时间(perf_counter 秒)
        self._expr_interval = 12.0     # 自动换表情基础间隔(秒)，实际在 1~1.8 倍间随机，切太快模型会卡
        self._motion_cooldown = 0.0    # 待机动作冷却到期时间(perf_counter 秒)
        self._last_motion_t = 0.0      # 最近一次启动动作的时间，用来防止 model3 高频重启动作抖动
        self._motion_cooldown_duration = 2.0  # 一个待机动作放完后，至少隔这么久再换下一个（太频繁会卡顿/闪烁）
        self._errored = False          # 渲染出错后只回调一次
        self._content_box = None       # 缓存的内容包围盒(归一化 x0,y0,x1,y1)，供"模型碰到边缘"判定
        self._mask_region = None       # 按 alpha 生成的控件形状，裁掉 OpenGL 透明区黑底
        self._mask_stamp = 0.0
        self._last_opaque_black_bg = False
        self.on_error = None           # 渲染崩溃时的回调(主程序设置)：cb(model_path, err)
        self.on_resized = None         # 画布比例自适应后通知主程序重新贴合窗口
        self.on_voice_with_text = None # 播放语音时的回调(用于显示字幕)：cb(sound_path, text)
        self._update_canvas_size()
        self.setFixedSize(self._canvas_w, self._canvas_h)
        # QOpenGLWidget 在 Windows 上想真正合成到透明顶层窗口里，不能只靠 clear(0,0,0,0)：
        # 还要尽早声明自己没有系统背景、不要自动填底色，否则加载/首帧阶段容易闪出黑块。
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        # self.setAttribute(Qt.WA_AlwaysStackOnTop, True)  # 透明区会变黑或内容消失，改用离屏渲染方案
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        fmt = self.format()
        fmt.setAlphaBufferSize(8)
        fmt.setRedBufferSize(8)
        fmt.setGreenBufferSize(8)
        fmt.setBlueBufferSize(8)
        self.setFormat(fmt)
        self.setUpdateBehavior(QOpenGLWidget.NoPartialUpdate)
        self.fps = 12 if self._preview_mode else 30
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(int(1000 / self.fps))
        self._mask_timer = QTimer(self)
        self._mask_timer.timeout.connect(lambda: self.sync_alpha_mask(force=False))
        # 预览控件同样是 QOpenGLWidget；在 Windows 上透明区有时会被合成为黑色矩形。
        # 遮罩带余量且低频刷新，避免透明裁剪边界贴着模型抖。
        self._mask_timer.start(MASK_PREVIEW_REFRESH_MS if self._preview_mode else MASK_REFRESH_MS)

    def _reset_model_runtime(self):
        """清理当前模型的运行态缓存，但不销毁 Qt 控件本身。"""
        self._expressions = None
        self._extra_motions = {}
        self._declared_motion_groups = set()
        self._motion_data = None
        self._expression_params = {}
        self._expression_manual = None
        self._expression_active = ""
        self._content_box = None
        self._idle_group = None
        self._last_t = None
        self._motion_cooldown = 0.0
        self._last_motion_t = 0.0
        self._next_expr_t = 0.0
        self._expr_rr = -1
        self._errored = False
        self._auto_ratio = True if getattr(self, "_auto_ratio", True) else False

    def _release_model(self):
        """释放当前模型资源。"""
        try:
            try:
                self._voice.stop()
            except Exception:
                pass
            if self.model is not None:
                if self.version == "v3":
                    try:
                        if hasattr(self.model, "StopAllMotions"):
                            self.model.StopAllMotions()
                    except Exception:
                        pass
                    try:
                        self.model.DestroyRenderer()
                    except Exception:
                        pass
                try:
                    if hasattr(self.model, "Release"):
                        self.model.Release()
                except Exception:
                    pass
        except Exception:
            pass
        self.model = None
        self._reset_model_runtime()
        gc.collect()

    def _load_current_model(self):
        """在当前 OpenGL 上下文里加载 self.model_path 指向的模型。"""
        if self.version not in _inited:
            self.l2d.init()
            _inited.add(self.version)
        self.l2d.glInit()
        self.model = self.l2d.LAppModel()
        if self.version == "v3":
            try:
                self.model.LoadModelJson(self.model_path, 4)
            except TypeError:
                self.model.LoadModelJson(self.model_path)
            try:
                self.model.CreateRenderer(2)
            except Exception:
                try:
                    self.model.CreateRenderer()
                except Exception:
                    pass
        else:
            self.model.LoadModelJson(self.model_path)
        for setter in ("SetAutoBreathEnable", "SetAutoBlinkEnable"):
            try:
                getattr(self.model, setter)(True)
            except Exception:
                pass
        self.model.Resize(self._canvas_w, self._canvas_h)
        self._apply_view()
        if self._auto_ratio:
            try:
                cw, ch = self.model.GetCanvasSize()
                if cw and ch:
                    self._ratio = max(RATIO_MIN, min(RATIO_MAX, float(ch) / float(cw)))
                    self._resize_to_ratio(notify=True)
            except Exception:
                pass
        self._ensure_motion_data()
        if not self._preview_mode:
            self._register_loose_motions()
        try:
            groups = self.model.GetMotionGroups()
            names = list(groups.keys()) if isinstance(groups, dict) else list(groups)
            for g in names:
                if g in self._declared_motion_groups and "idle" in str(g).lower():
                    self._idle_group = g
                    break
        except Exception:
            pass
        if not self._preview_mode:
            self._start_auto_motion()

    # --- 对外接口（与 PixelPet / ImagePet 保持一致，供 main.py 调用）---
    def _update_canvas_size(self):
        self._canvas_margin = max(
            MASK_PADDING_MIN,
            min(CANVAS_MARGIN_MAX,
                max(CANVAS_MARGIN_MIN, int(round(self._w * CANVAS_MARGIN_RATIO)))))
        self._canvas_w = int(self._w + self._canvas_margin * 2)
        self._canvas_h = int(self._h + self._canvas_margin * 2)

    def natural_size(self):
        return QSize(self._canvas_w, self._canvas_h)

    def set_scale(self, *_):
        pass

    def set_live2d_size(self, s):
        self._w = int(s)
        self._h = round(self._w * self._ratio)
        self._update_canvas_size()
        self.setFixedSize(self._canvas_w, self._canvas_h)
        if self.model:
            self.model.Resize(self._canvas_w, self._canvas_h)
            self._apply_view()
        self._content_box = None

    def _resize_to_ratio(self, notify):
        self._h = round(self._w * self._ratio)
        self._update_canvas_size()
        self.setFixedSize(self._canvas_w, self._canvas_h)
        if self.model:
            self.model.Resize(self._canvas_w, self._canvas_h)
            self._apply_view()
        self._content_box = None
        if notify and callable(self.on_resized):
            QTimer.singleShot(0, self.on_resized)

    def set_height_ratio(self, r):
        self._auto_ratio = False
        self._ratio = max(RATIO_MIN, min(RATIO_MAX, float(r)))
        self._resize_to_ratio(notify=True)

    def height_ratio(self):
        return self._ratio

    def is_auto_ratio(self):
        return self._auto_ratio

    def set_follow(self, on):
        if not on:
            self._look = (0.0, 0.0)
        if self._preview_mode:
            self._auto_motion = False

    def set_mask_updates_enabled(self, enabled):
        """暂停/恢复 alpha 遮罩刷新；拖动窗口时暂停可避免 grabFramebuffer 抢占。"""
        tm = getattr(self, "_mask_timer", None)
        if tm is None:
            return
        if enabled:
            if not tm.isActive():
                tm.start(MASK_PREVIEW_REFRESH_MS if self._preview_mode else MASK_REFRESH_MS)
        else:
            tm.stop()

    def set_render_active(self, enabled):
        """暂停/恢复每帧渲染。拖动窗口时暂停：透明 + OpenGL 的无边框窗口每次
        present 都要让 DWM 重新合成，和 move() 抢 GUI 线程，导致鼠标拖不动/掉帧。
        拖动时模型静止一两帧没人会注意，松手即恢复。"""
        tm = getattr(self, "timer", None)
        if tm is None:
            return
        if enabled:
            self._last_t = None        # 复位计时，避免暂停期间累积的 dt 让动作瞬间跳一大步
            if not tm.isActive() and self.isVisible():
                tm.start(int(1000 / self.fps))
        else:
            tm.stop()

    def set_look(self, dx, dy):
        """归一化视线方向（dx 右正，dy 上正），让模型头/眼看向鼠标。"""
        self._look = (max(-1.0, min(1.0, dx)), max(-1.0, min(1.0, dy)))

    def react(self, event):
        if not self.model:
            return None
        # 摸头：只走主程序的手势覆盖动画，不再驱动模型本体动作。
        # 这样“手在摸”，但宠物不会跟着左右晃动；气泡语录由主程序统一处理。
        if event == "touch_head":
            return None
        # 按下（grab，也可能是拖动的开始）：只做个动作，不放语音/字幕。
        # 否则"按下 grab + 松开 click"会各放一次戳身体语音，形成"双次语录/双次语音"。
        if event == "grab":
            self._start_motion(priority=3)
            return None
        if event in ("click", "drop", "land"):
            # 真正的点击/拖动松手/落地：优先播放"戳身体"动作组（带配音），没有就普通高优先级随机动作
            played, _ = self.play_interaction("body")
            if not played:
                self._start_motion(priority=3)     # 用户互动用高优先级，明显打断待机
        if event in ("click", "land"):
            self.set_random_expression()       # 点击/落地顺手换个表情，反应更明显
        return None

    def hit_test(self, area_name, px, py):
        """传入窗口内像素坐标 px, py，判断是否在指定的 HitArea 区域。"""
        if not self.model:
            return False
        
        names = [area_name, area_name.lower(), area_name.capitalize()]
        if area_name.lower() == "head":
            names.extend(["HitAreaHead", "HitArea_Head"])
        elif area_name.lower() == "body":
            names.extend(["HitAreaBody", "HitArea_Body"])
            
        for name in names:
            try:
                if self.model.HitTest(name, float(px), float(py)):
                    return True
            except Exception:
                pass
        return False

    def _start_motion(self, group=None, priority=2):
        """播放一个动作；group=None 时优先用待机组，再退化到随机。
        priority：待机循环用 2，用户触发用 3（FORCE，能打断当前动作）。"""
        if not self.model:
            return
        now = time.perf_counter()
        if priority <= 2 and now - self._last_motion_t < 0.35:
            return
        g = group if group is not None else self._idle_group
        try:
            if g:
                self.model.StartRandomMotion(g, priority)
            else:
                self.model.StartRandomMotion("", priority)   # "" = 该组/全部
            self._last_motion_t = now
        except Exception:
            try:
                self.model.StartRandomMotion()
                self._last_motion_t = now
            except Exception:
                pass

    # --- 自动循环：禁用动作支持 ---
    def set_disabled_motions(self, keys):
        """设置禁止自动播放的动作键集合（"组名/索引" 字符串）。手动触发不受影响。"""
        self._disabled_motions = set(keys or [])

    def _auto_motion_pool(self):
        """自动循环可选的 (组名, 索引) 列表：优先待机组，去掉被用户禁用的；
        待机组全被禁用时退化到全部动作（同样去禁用）。返回 [] 表示没有可自动播放的动作。"""
        data = self._ensure_motion_data()
        if not data:
            return []
        groups_allowed = self._declared_motion_groups or set(data.keys())

        def collect(groups):
            pool = []
            for g in groups:
                if g not in groups_allowed:
                    continue
                for it in data.get(g, []):
                    if f"{g}/{it['index']}" not in self._disabled_motions:
                        pool.append((g, it["index"]))
            return pool

        if self._idle_group and self._idle_group in data:
            pool = collect([self._idle_group])
            if pool:
                return pool
        return collect(list(data.keys()))

    def _start_auto_motion(self):
        """待机循环用：从"未被禁用"的动作里随机挑一个播放。没有可播的就保持当前姿态。"""
        if not self.model:
            return
        pool = self._auto_motion_pool()
        if not pool:
            # 用户把动作全禁了：不强行播放，让模型停在当前姿态（仍有眨眼/呼吸等空闲动画）
            return
        now = time.perf_counter()
        if now - self._last_motion_t < 0.6:
            return
        g, idx = random.choice(pool)
        try:
            self.model.StartMotion(g, int(idx), 2)
            self._last_motion_t = now
        except Exception:
            self._start_motion()

    # --- 构图（缩放 / 竖直偏移）：解决部分模型上半身显示不全 ---
    def _apply_view(self):
        if not self.model:
            return
        scale = self._zoom * min(self._w / max(1, self._canvas_w),
                                 self._h / max(1, self._canvas_h))
        for fn, arg in (("SetScale", (scale,)), ("SetOffset", (self._xoff, self._yoff))):
            try:
                getattr(self.model, fn)(*arg)
            except Exception:
                pass

    def set_view(self, zoom, xoff, yoff):
        self._zoom = max(0.2, min(5.0, float(zoom)))
        self._xoff = max(-2.0, min(2.0, float(xoff)))
        self._yoff = max(-2.0, min(2.0, float(yoff)))
        self._content_box = None
        self._apply_view()

    def get_view(self):
        return self._zoom, self._xoff, self._yoff

    def refresh_content_box(self):
        """丢弃缓存的内容包围盒，强制下次 content_inset() 重新抓帧测量。
        供气泡“实时跟随头部高度”用：不同动作头部高低不同，重测后气泡才贴得准。"""
        self._content_box = None
        self._mask_region = None

    def _visible_pixels_from_image(self, img, alpha_threshold=8):
        """返回 framebuffer 中真实模型像素 mask，排除透明背景。"""
        import numpy as np
        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        if w < 4 or h < 4:
            return None, w, h
        rgba = np.frombuffer(img.bits(), np.uint8).reshape(h, w, 4)
        alpha = rgba[..., 3]
        rgb = rgba[..., :3].astype(np.int16)
        alpha_visible = alpha > int(alpha_threshold)
        visible = alpha_visible.copy()

        # Windows 透明窗口会把低 alpha 的抗锯齿边缘放大成一圈发亮或发黑的晕边。
        # 从透明背景向内扩两步，不再依赖具体的颜色深浅，无差别剔除外轮廓的半透明过渡带，
        # 保留高 alpha 的真实描边/衣物，从而彻底消除白边和黑影，且不会误伤内部衣服。
        transparent = alpha <= 4
        near_transparent = transparent.copy()
        for _ in range(2):
            expanded = near_transparent.copy()
            expanded[1:, :] |= near_transparent[:-1, :]
            expanded[:-1, :] |= near_transparent[1:, :]
            expanded[:, 1:] |= near_transparent[:, :-1]
            expanded[:, :-1] |= near_transparent[:, 1:]
            near_transparent = expanded
            
        edge_halo = (
            visible
            & near_transparent
            & (alpha <= 180)
        )
        visible &= ~edge_halo

        # 某些 Qt/OpenGL/显卡组合会把透明清屏读回成 alpha=255 的纯黑背景。
        # 只剔除“从画面边缘连通进来的近黑色”，避免误删模型自身的黑色衣物/描边。
        opaque_black_bg = False
        if int(alpha_visible.sum()) > (w * h * 0.85):
            near_black = (rgb[..., 0] <= 10) & (rgb[..., 1] <= 10) & (rgb[..., 2] <= 10)
            border_black = np.zeros((h, w), dtype=bool)
            border_black[0, :] = near_black[0, :]
            border_black[-1, :] = near_black[-1, :]
            border_black[:, 0] |= near_black[:, 0]
            border_black[:, -1] |= near_black[:, -1]
            if border_black.any():
                from collections import deque
                bg = np.zeros((h, w), dtype=bool)
                q = deque((int(y), int(x)) for y, x in np.argwhere(border_black))
                while q:
                    y, x = q.popleft()
                    if bg[y, x] or not near_black[y, x]:
                        continue
                    bg[y, x] = True
                    if y > 0:
                        q.append((y - 1, x))
                    if y + 1 < h:
                        q.append((y + 1, x))
                    if x > 0:
                        q.append((y, x - 1))
                    if x + 1 < w:
                        q.append((y, x + 1))
                visible &= ~bg
                opaque_black_bg = int(bg.sum()) > (w * h * 0.20)
        if self is not None:
            try:
                self._last_opaque_black_bg = opaque_black_bg
            except Exception:
                pass
        if int(visible.sum()) < 16:
            return None, w, h
        return visible, w, h

    def _expanded_visible_mask(self, visible, w, h, tight=False):
        """给透明裁剪区留一圈余量，避免遮罩边缘贴着模型动作抖动。"""
        import numpy as np
        if visible is None:
            return None
        if tight:
            pad = 0
        else:
            pad = max(MASK_PADDING_MIN, min(MASK_PADDING_MAX, int(round(min(w, h) * 0.035))))
        ys, xs = np.where(visible)
        if len(xs) < 16:
            return visible
        padded = np.pad(visible.astype(np.uint8), ((pad, pad), (pad, pad)), mode="constant")
        integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
        win = pad * 2 + 1
        sums = (
            integral[win:, win:]
            - integral[:-win, win:]
            - integral[win:, :-win]
            + integral[:-win, :-win]
        )
        return sums > 0

    @staticmethod
    def _region_from_visible_mask(visible, src_w, src_h, target_w, target_h):
        """把 bool mask 转成控件坐标 QRegion，按行合并矩形，兼容高 DPI framebuffer。"""
        import numpy as np
        if visible is None or src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
            return None
        region = QRegion()
        rows = np.where(visible.any(axis=1))[0]
        for y in rows:
            row = visible[y]
            padded = np.concatenate(([False], row, [False]))
            changes = np.flatnonzero(padded[1:] != padded[:-1])
            ty0 = int(y) * target_h // src_h
            ty1 = (int(y) + 1) * target_h
            ty1 = (ty1 + src_h - 1) // src_h
            rh = max(1, ty1 - ty0)
            for i in range(0, len(changes), 2):
                x0 = int(changes[i])
                x1 = int(changes[i + 1])
                tx0 = x0 * target_w // src_w
                tx1 = (x1 * target_w + src_w - 1) // src_w
                region = region.united(QRegion(QRect(tx0, ty0, max(1, tx1 - tx0), rh)))
        return None if region.isEmpty() else region

    def content_inset(self):
        """窗口四周的透明留白(像素)：(左,上,右,下)，按真实渲染内容测量并缓存。
        让主程序"只在模型本体碰到屏幕边时才贴边"，而不是透明画布一靠近就触发。"""
        box = self._content_box
        if box is None:
            box = self._measure_content()
            if box is not None:
                self._content_box = box
        if not box:
            return (0, 0, 0, 0)
        x0, y0, x1, y1 = box
        cw, ch = self.width(), self.height()
        return (max(0.0, x0) * cw, max(0.0, y0) * ch,
                max(0.0, 1.0 - x1) * cw, max(0.0, 1.0 - y1) * ch)

    def sync_alpha_mask(self, force=False):
        """按真实模型像素裁掉 OpenGL 透明区，避免透明失效时露出黑色画框。"""
        if not force:
            return True
        now = time.perf_counter()
        try:
            img = self.grabFramebuffer()
        except Exception:
            return False
        if img is None or img.isNull():
            return False
        try:
            visible, w, h = self._visible_pixels_from_image(img, alpha_threshold=48)
            if visible is None:
                return False
            # 显示遮罩必须紧贴模型本体：透明余量和半透明污染边缘在部分
            # Windows/OpenGL 组合上会变成黑/白边框。定位/贴边仍用 content_inset。
            visible = self._expanded_visible_mask(visible, w, h, tight=False)
            region = self._region_from_visible_mask(visible, w, h, self.width(), self.height())
        except Exception:
            return False
        if region is None or region.isEmpty():
            return False
        self._mask_region = QRegion(region)
        self._mask_stamp = now
        # self.setMask(region)
        return True

    # --- 贴合内容：测量真实渲染范围，让画布严格包住模型（去掉大片空白）---
    def _measure_content(self):
        """抓当前帧，返回非透明内容的归一化包围盒 (x0,y0,x1,y1)；失败返回 None。"""
        try:
            img = self.grabFramebuffer()
        except Exception:
            return None
        if img is None or img.isNull():
            return None
        try:
            import numpy as np
            img = img.convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            if w < 4 or h < 4:
                return None
            visible, w, h = self._visible_pixels_from_image(img)
            if visible is None:
                return None
            ys, xs = np.where(visible)
            if len(xs) < 16:
                return None
            return (float(xs.min()) / w, float(ys.min()) / h,
                    float(xs.max() + 1) / w, float(ys.max() + 1) / h)
        except Exception:
            return None

    def fit_to_content(self, top=0.0, bottom=1.0, target=0.80, iters=None):
        """让模型紧贴窗口、去掉空白；target<1 留出余量，模型动起来(手展开)也不易出界。

        top/bottom 预留：相对完整内容高度的可见区间，默认整只。靠"抓帧测量→修正"
        迭代收敛，对不同宽高的模型自适应。返回 True 表示测到内容并完成贴合。
        """
        if not self.model:
            return False
        if iters is None:
            iters = 3 if self._preview_mode else 6
        ok = False
        for _ in range(int(iters)):
            b = self._measure_content()
            if b is None:
                break
            nx0, ny0, nx1, ny1 = b
            cw = nx1 - nx0
            full_h = ny1 - ny0
            if cw < 0.01 or full_h < 0.01:
                break
            ok = True
            by0 = ny0 + max(0.0, top) * full_h          # 目标可见区间（屏幕归一化，0=上）
            by1 = ny0 + min(1.0, bottom) * full_h
            ch = max(0.01, by1 - by0)
            cx = (nx0 + nx1) / 2.0
            cy = (by0 + by1) / 2.0
            # 1) 居中：把目标区间中心移到窗口中心（限幅，避免来回震荡）
            self._xoff = max(-2.0, min(2.0, self._xoff + max(-0.5, min(0.5, (0.5 - cx) * 1.7))))
            self._yoff = max(-2.0, min(2.0, self._yoff + max(-0.5, min(0.5, (cy - 0.5) * 1.7))))
            # 2) 缩放：让较大的一维填到 target（取大维，避免裁掉内容）
            f = max(0.7, min(1.6, target / max(cw, ch)))
            self._zoom = max(0.2, min(5.0, self._zoom * f))
            # 3) 画布比例贴合内容（窗口高/宽 = 目标区间像素高/宽）
            self._ratio = max(RATIO_MIN, min(RATIO_MAX, self._ratio * (ch / cw)))
            self._auto_ratio = False
            self._apply_view()
            self._resize_to_ratio(notify=False)
            if abs(cx - 0.5) < 0.02 and abs(cy - 0.5) < 0.02 and (target - 0.08) < max(cw, ch) <= (target + 0.06):
                break
        if ok:
            self._resize_to_ratio(notify=True)
        return ok

    # --- 动作清单 / 手动播放（供菜单调用）---
    def list_motions(self):
        """返回 [(group, count), ...]，group 可能是空串。
        扫描散落的 motion3.json 文件（类似表情的处理方式）。"""
        return [(g, len(items)) for g, items in self.motion_menu()]

    def motion_menu(self):
        """供右键菜单使用：按动作组返回去重后的动作清单。

        返回 [(group, [ {"index":int, "sound":路径|None, "label":str}, ... ]), ...]。
        组内同一个动作文件被多条 msg 复用时只列一次，让数量贴近模型真实动作数
        （xiaomai 这类模型不会再出现一长串重复的"tap_head 1..15"摊平项）。

        优化：过滤掉无法播放的动作（文件不存在），避免菜单中出现无效项。"""
        groups = []
        data = self._ensure_motion_data()
        model_dir = os.path.dirname(self.model_path)
        for g, items in data.items():
            seen, lst = set(), []
            for it in items:
                key = it["file"] or it["index"]
                if key in seen:
                    continue
                motion_file = it["file"] or ""
                if motion_file:
                    motion_path = os.path.normpath(os.path.join(model_dir, motion_file))
                    if not os.path.isfile(motion_path):
                        continue
                    label = os.path.splitext(os.path.basename(motion_file))[0]
                else:
                    continue
                seen.add(key)
                lst.append({"index": it["index"], "sound": it["sound"], "label": label})
            if lst:
                groups.append((g, lst))
        if groups:
            return groups

        # 退化：仅在真实存在散落 motion3.json 时，才把它们列为可点动作
        for g, count in sorted(self._extra_motions.items(), key=lambda x: x[0].lower()):
            if count <= 0:
                continue
            label = g or "动作"
            lst = [{"index": i, "sound": None,
                    "label": f"{label} {i + 1}" if count > 1 else label} for i in range(count)]
            groups.append((g, lst))
        return groups

    def play_motion(self, group, index=None):
        """播放某条动作并配上语音；返回已显示的语音字幕文本（无字幕则 None）。"""
        if not self.model:
            return None
        if index is None:
            self._start_motion(group if group else "", priority=3)
            return None
        try:
            self.model.StartMotion(group, int(index), 3)
        except Exception:
            try:
                self.model.StartRandomMotion()
            except Exception:
                pass

        # 播放配套语音
        text_shown = None
        sound_path = self._sound_for(group, int(index))
        if sound_path:
            self._voice.play(sound_path)
            # 提取语音对应的字幕文字，并把语音时长一并带出，让气泡停留与语音播放对齐
            if callable(self.on_voice_with_text):
                try:
                    text = self._extract_voice_text(sound_path, group, index)
                    if text:
                        dur = self._voice.get_duration(sound_path)
                        self.on_voice_with_text(sound_path, text,
                                                int(dur * 1000) if dur else None)
                        text_shown = text
                except Exception:
                    pass
        return text_shown

    def _extract_voice_text(self, sound_path, group, index):
        """从语音文件路径提取对应的文本信息（语音字幕）"""
        if not sound_path or not os.path.isfile(sound_path):
            return None

        # 1. 从翻译数据库查找
        translations = _load_voice_translations()
        if translations:
            # 获取模型名称（从model_path提取）
            try:
                model_folder = os.path.basename(os.path.dirname(os.path.dirname(self.model_path)))
                model_voices = translations.get(model_folder, [])

                # 计算相对路径用于匹配
                model_dir = os.path.dirname(self.model_path)
                relative_sound = os.path.relpath(sound_path, model_dir).replace('\\', '/')

                # 在数据库中查找匹配的语音
                for voice_info in model_voices:
                    if voice_info['group'] == group and voice_info['index'] == index:
                        translation = voice_info.get('translation')
                        if translation:
                            return translation

                    # 也尝试通过文件名匹配
                    if voice_info['sound_file'].replace('\\', '/') == relative_sound:
                        translation = voice_info.get('translation')
                        if translation:
                            return translation
            except Exception:
                pass

        # 2. 尝试读取同名的txt文件
        txt_path = os.path.splitext(sound_path)[0] + '.txt'
        if os.path.isfile(txt_path):
            try:
                with open(txt_path, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
                    if text:
                        return text
            except Exception:
                pass

        # 3. 根据动作组名生成默认字幕（与 voice_translations 的风格一致，尽量贴合语音情绪）
        default_texts = {
            'tap_head': ['摸头好舒服~', '嘿嘿~', '再摸摸~', '喜欢被摸头~'],
            'taphead': ['摸头好舒服~', '嘿嘿~', '再摸摸~', '喜欢被摸头~'],
            'flick_head': ['哎哟~', '疼疼疼~', '别弹了~', '轻一点嘛~'],
            'tap_body': ['嗯？', '怎么了？', '找我吗？', '在呢~'],
            'tapbody': ['嗯？', '怎么了？', '找我吗？', '在呢~'],
            'tap': ['嗯？', '怎么了呀~', '在听呢~', '叫我吗？'],
            'shake': ['哎呀~', '别晃啦~', '好晕~', '要倒了~'],
            'drag': ['哎呀~', '要去哪里？', '别拉我~'],
            'hello': ['你好~', '嗨~', '很高兴见到你~', '欢迎~'],
            'greet': ['你好呀~', '早上好~', '见到你真开心~'],
            'happy': ['好开心~', '耶~', '真棒~', '太好了~'],
            'joy': ['好开心啊~', '开心~', '嘿嘿~'],
            'smile': ['笑一个~', '开心~', '嘻嘻~'],
            'surprise': ['哇~', '诶？', '好厉害~', '太惊讶了~'],
            'shock': ['啊！', '吓一跳~', '没想到~'],
            'angry': ['哼~', '生气了~', '不开心~', '讨厌~'],
            'sad': ['呜呜~', '好难过~', '不开心~'],
            'cry': ['要哭了~', '呜呜呜~', '难过~'],
            'shy': ['好害羞~', '不好意思~', '嘿嘿~'],
            'idle': ['...', '嗯~', '呼~'],
            'wait': ['等等我~', '稍等一下~'],
            'bye': ['再见~', '拜拜~', '下次见~'],
        }
        if group:
            g = group.lower()
            if g in default_texts:                 # 先精确匹配
                return random.choice(default_texts[g])
            for key, texts in default_texts.items():  # 再按子串匹配（如 tap_head_01）
                if key in g:
                    return random.choice(texts)

        # 返回None表示没有文本
        return None

    # --- 动作配套语音（model.json 里每条动作的 "sound"）---
    def _ensure_motion_data(self):
        """懒加载并缓存：解析模型设置 JSON 的动作组，返回
        {组名: [ {"index":int, "file":str, "sound":绝对路径或None}, ... ]}。

        同时支持 Cubism 2（顶层 "motions"）和 Cubism 3（FileReferences.Motions）。
        sound 用于播放语音、菜单也据此列出真实动作。"""
        if self._motion_data is not None:
            return self._motion_data
        out = {}
        try:
            with open(self.model_path, encoding="utf-8") as f:
                j = json.load(f)
        except Exception:
            self._motion_data = out
            return out
        motions = j.get("motions")
        if not isinstance(motions, dict):
            motions = (j.get("FileReferences") or {}).get("Motions") or {}
        self._declared_motion_groups = set(motions.keys()) if isinstance(motions, dict) else set()
        model_dir = os.path.dirname(self.model_path)
        for group, items in motions.items():
            if not isinstance(items, list):
                continue
            lst = []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                fpath = it.get("file") or it.get("File") or ""
                snd = it.get("sound") or it.get("Sound") or ""
                snd_abs = os.path.normpath(os.path.join(model_dir, snd)) if snd else None
                lst.append({"index": i, "file": fpath, "sound": snd_abs})
            if lst:
                out[group] = lst
        self._motion_data = out
        return out

    def _sound_for(self, group, index):
        """获取指定动作的配套语音文件路径，文件不存在则返回 None。"""
        for it in self._ensure_motion_data().get(group, []):
            if it["index"] == index:
                sound_path = it["sound"]
                # 验证文件是否存在（过滤无效语音）
                if sound_path and os.path.isfile(sound_path):
                    return sound_path
                return None
        return None

    # 互动类型 -> 候选动作组名（先精确，再按关键词模糊匹配；大小写不敏感）
    _INTERACTION_GROUPS = {
        "head": (["tap_head", "taphead", "tap_face", "flick_head", "head"],
                 ["head", "face"]),
        "body": (["tap_body", "tapbody", "tap", "body"],
                 ["body", "tap", "touch"]),
        "shake": (["shake", "drag", "tap_body"],
                  ["shake", "drag"]),
    }

    def _match_group(self, kind):
        """按互动类型在模型已声明的动作组里找一个合适的组名，找不到返回 None。"""
        data = self._ensure_motion_data()
        if not data:
            return None
        lower = {g.lower(): g for g in data}
        exact, keywords = self._INTERACTION_GROUPS.get(kind, ([], []))
        for cand in exact:
            if cand in lower:
                return lower[cand]
        for kw in keywords:
            for lg, g in lower.items():
                if kw in lg:
                    return g
        return None

    def play_group_random(self, group):
        """从某动作组里随机挑一条播放，并配上它的语音；返回语音字幕文本（无则 None）。
        自己选 index（而非交给库随机）以便语音与动作严格对应。"""
        items = self._ensure_motion_data().get(group)
        if items:
            return self.play_motion(group, random.choice(items)["index"])
        self._start_motion(group if group else "", priority=3)
        return None

    def play_interaction(self, kind):
        """播放某类互动（head/body/shake）对应的模型动作组（含配音）。
        返回 (played, subtitle_text)：played 表示模型有对应动作组并已播放；
        subtitle_text 为已显示的语音字幕（无语音/无翻译则 None）。"""
        if not self.model:
            return (False, None)
        group = self._match_group(kind)
        if group:
            return (True, self.play_group_random(group))
        return (False, None)

    def play_voice_random(self):
        """随机放一条模型语音（不触发动作）——供"气泡语录同步发声"使用。

        优化：只从实际存在的语音文件中选择。"""
        if not self.model:
            return
        sounds = [it["sound"] for items in self._ensure_motion_data().values()
                  for it in items if it["sound"] and os.path.isfile(it["sound"])]
        if sounds:
            self._voice.play(random.choice(sounds))

    def set_voice_enabled(self, on):
        self._voice.set_enabled(bool(on))

    def set_voice_volume(self, v):
        self._voice.set_volume(v)

    def has_voice(self):
        """该模型是否带有可播放的语音文件（用于菜单是否显示语音项）。

        优化：只检查实际存在的语音文件。"""
        return any(it["sound"] and os.path.isfile(it["sound"])
                   for items in self._ensure_motion_data().values()
                   for it in items)

    # --- 表情（Cubism3 的 *.exp3.json / Cubism2 model.json 里的 expressions）---
    def _ensure_expressions(self):
        """懒加载并返回本模型可用的表情 id 列表（缓存）。

        v3：很多模型没把表情登记进 *.model3.json，这里额外扫描模型文件夹下散落的
            *.exp3.json，用 LoadExtraExpression 逐个登记，Set/SetRandomExpression 才认。
        v2：运行时按 model.json 的 expressions 自动加载，直接读名字即可。
        优化：限制扫描深度和数量，减少卡顿。
        """
        if self._expressions is not None:
            return self._expressions
        ids = []
        self._expression_params = {}
        if not self.model:
            return ids
        try:                                  # 模型已登记的表情（v3 有此接口，v2cpp 没有）
            got = self.model.GetExpressionIds()
            if got:
                ids.extend(str(x) for x in got)
        except Exception:
            pass
        if self.version == "v3":
            folder = os.path.dirname(self.model_path)
            # 优化：限制扫描深度和数量
            exp_files = []
            try:
                for root, dirs, filenames in os.walk(folder):
                    # 限制扫描深度：最多2层
                    depth = root[len(folder):].count(os.sep)
                    if depth > 2:
                        dirs[:] = []
                        continue
                    for fn in filenames:
                        if fn.lower().endswith('.exp3.json'):
                            exp_files.append(os.path.join(root, fn))
                            if len(exp_files) >= 50:  # 最多50个表情
                                break
                    if len(exp_files) >= 50:
                        break
            except Exception:
                pass

            for p in sorted(exp_files):
                eid = os.path.basename(p)[: -len(".exp3.json")]
                if eid in ids:
                    continue
                try:
                    self.model.LoadExtraExpression(eid, p)
                    ids.append(eid)
                    self._expression_params[eid] = self._parse_expression_file(p)
                except Exception:
                    pass
        else:
            try:
                with open(self.model_path, encoding="utf-8") as f:
                    data = json.load(f)
                for e in (data.get("expressions") or []):
                    nm = e.get("name") or e.get("file") or ""
                    if nm and nm not in ids:
                        ids.append(nm)
                        fp = e.get("file") or e.get("File") or ""
                        if fp:
                            expr_path = os.path.join(os.path.dirname(self.model_path), fp)
                            if os.path.isfile(expr_path):
                                self._expression_params[nm] = self._parse_expression_file(expr_path)
            except Exception:
                pass
        self._expressions = ids
        return ids

    def list_expressions(self):
        return list(self._ensure_expressions())

    def _parse_expression_file(self, path):
        """读取 exp3/json 里的参数表，供 SetExpression 失效时手动回退。"""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        params = []
        for it in (data.get("Parameters") or []):
            if not isinstance(it, dict):
                continue
            pid = it.get("Id") or it.get("id") or ""
            if not pid:
                continue
            try:
                val = float(it.get("Value", 0.0))
            except Exception:
                val = 0.0
            blend = str(it.get("Blend") or "Add").lower()
            params.append((str(pid), val, blend))
        return params

    def _capture_expression_manual(self, expr_id):
        """捕获当前参数基线，并构造可逐帧重放的手工表情状态。"""
        params = self._expression_params.get(expr_id) or []
        if not params or not self.model:
            return None
        base = {}
        try:
            ids = list(self.model.GetParamIds())
        except Exception:
            ids = []
        for pid, _val, _blend in params:
            if pid in base:
                continue
            try:
                idx = ids.index(pid)
                base[pid] = float(self.model.GetParameterValue(idx))
            except Exception:
                base[pid] = 0.0
        return {"expr_id": expr_id, "base": base, "params": params}

    def _restore_expression_manual(self):
        """把手工表情恢复到进入前的参数基线。"""
        state = self._expression_manual
        if not state or not self.model:
            return
        for pid, base_val in (state.get("base") or {}).items():
            try:
                self.model.SetParameterValue(pid, base_val)
            except Exception:
                pass
        self._expression_manual = None

    def _apply_expression_manual(self):
        """对不支持 SetExpression 的模型，逐帧重放 exp3 参数。"""
        state = self._expression_manual
        if not state or not self.model:
            return False
        base = state.get("base") or {}
        params = state.get("params") or []
        for pid, val, blend in params:
            try:
                cur = float(base.get(pid, 0.0))
                if blend == "mult":
                    target = cur * val
                elif blend == "overwrite":
                    target = val
                else:
                    target = cur + val
                self.model.SetParameterValue(pid, target)
            except Exception:
                pass
        self._expression_active = state.get("expr_id", "")
        return True

    def set_expression(self, expr_id):
        if not self.model:
            return
        self._ensure_expressions()
        self._restore_expression_manual()
        expr_id = str(expr_id)
        params = self._expression_params.get(expr_id) or []
        if params:
            for fn in ("ResetExpression", "ResetExpressions"):
                try:
                    getattr(self.model, fn)()
                    break
                except Exception:
                    pass
            self._expression_manual = self._capture_expression_manual(expr_id)
            self._expression_active = expr_id
            self._apply_expression_manual()
            return
        try:
            self.model.SetExpression(expr_id)
            self._expression_active = expr_id
        except Exception:
            pass

    def set_random_expression(self):
        if not self.model:
            return
        ids = self._ensure_expressions()
        if not ids:
            return
        manual_ids = [eid for eid in ids if self._expression_params.get(eid)]
        if manual_ids:
            self._expr_rr = (self._expr_rr + 1) % len(manual_ids)
            self.set_expression(manual_ids[self._expr_rr])
            return
        self._restore_expression_manual()
        try:
            self.model.SetRandomExpression()    # 库自带随机
            self._expression_active = ""
            return
        except Exception:
            pass
        # 兜底：库没有随机接口/失败时，自己轮着挑一个
        self._expr_rr = (self._expr_rr + 1) % len(ids)
        try:
            self.set_expression(ids[self._expr_rr])
        except Exception:
            pass

    def reset_expression(self):
        if not self.model:
            return
        self._restore_expression_manual()
        self._expression_active = ""
        for fn in ("ResetExpression", "ResetExpressions"):
            try:
                getattr(self.model, fn)()
                return
            except Exception:
                pass

    def set_auto_expression(self, on):
        """开/关：每隔 _expr_interval 秒自动随机换一个表情，让模型更活。"""
        self._auto_expr = bool(on)
        self._next_expr_t = 0.0

    def _apply_manual_expression_overrides(self):
        """每帧补写手工表情参数，避免被 Update/其它逻辑冲掉。"""
        self._apply_expression_manual()

    def shutdown(self):
        """关闭并释放所有资源。"""
        self.timer.stop()
        if hasattr(self, "_mask_timer"):
            self._mask_timer.stop()
        try:
            self._voice.stop()                  # 停掉可能正在放的语音
        except Exception:
            pass
        # 释放渲染资源需要当前 GL 上下文
        try:
            self.makeCurrent()
            self._release_model()
        except Exception:
            pass
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass

    def hideEvent(self, ev):
        self.timer.stop()
        if hasattr(self, "_mask_timer"):
            self._mask_timer.stop()
        super().hideEvent(ev)

    def showEvent(self, ev):
        if not self.timer.isActive():
            self.timer.start(int(1000 / self.fps))
        if (hasattr(self, "_mask_timer")
                and not self._mask_timer.isActive()):
            self._mask_timer.start(MASK_PREVIEW_REFRESH_MS if self._preview_mode else MASK_REFRESH_MS)
        super().showEvent(ev)

    # --- OpenGL ---
    def initializeGL(self):
        self._clear_gl_background()
        self._prepare_transparent_blending()
        self._load_current_model()

    def _clear_gl_background(self):
        """尽早把 OpenGL 背景清成透明，避免模型加载首帧时露出默认黑底。"""
        try:
            f = self.context().functions()
            f.glClearColor(0.0, 0.0, 0.0, 0.0)
            f.glClear(f.GL_COLOR_BUFFER_BIT | f.GL_DEPTH_BUFFER_BIT)
        except Exception:
            pass

    def _prepare_transparent_blending(self):
        """让透明 framebuffer 用正确 alpha 混合，减少桌面合成时的黑色晕边。"""
        try:
            from OpenGL import GL
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFuncSeparate(
                GL.GL_SRC_ALPHA,
                GL.GL_ONE_MINUS_SRC_ALPHA,
                GL.GL_ONE,
                GL.GL_ONE_MINUS_SRC_ALPHA,
            )
        except Exception:
            pass

    def reload_model(self, model_path, size=None, zoom=None, xoff=None, yoff=None, ratio=None):
        """在不重建 QWidget 的情况下热切换 Live2D 模型。"""
        self.model_path = model_path
        self.version = detect_version(model_path)
        self.l2d = _module_for(self.version)
        if size is not None:
            self._w = int(size)
        if zoom is not None:
            self._zoom = max(0.2, min(5.0, float(zoom)))
        if xoff is not None:
            self._xoff = max(-2.0, min(2.0, float(xoff)))
        if yoff is not None:
            self._yoff = max(-2.0, min(2.0, float(yoff)))
        if ratio is not None:
            self._auto_ratio = False
            self._ratio = max(RATIO_MIN, min(RATIO_MAX, float(ratio)))
        self._h = round(self._w * self._ratio)
        self._update_canvas_size()
        self.setFixedSize(self._canvas_w, self._canvas_h)
        self._content_box = None
        self._mask_region = None
        try:
            self.makeCurrent()
        except Exception:
            pass
        try:
            self._clear_gl_background()
            self._release_model()
            self._load_current_model()
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass
        self.update()

    def _register_loose_motions(self, limit=40):
        """v3：模型未声明任何动作时，扫描其文件夹下散落的 *.motion3.json 并逐个登记。

        每个动作文件登记为一个同名组（菜单里直接显示动作名）。只在"模型自己一个动作都没
        声明"时才做，避免与已正常声明动作的模型重复。优化：限制扫描深度和数量。"""
        if self.version != "v3" or not self.model:
            return
        try:
            declared = self.model.GetMotions()
            if isinstance(declared, dict) and declared:
                return                      # 模型已自带动作，用它自己的
        except Exception:
            pass
        folder = os.path.dirname(self.model_path)
        try:
            # 限制扫描深度，避免过深的目录结构导致卡顿
            files = []
            for root, dirs, filenames in os.walk(folder):
                # 限制扫描深度：最多2层子目录
                depth = root[len(folder):].count(os.sep)
                if depth > 2:
                    dirs[:] = []  # 不再往下扫描
                    continue
                for fn in filenames:
                    if fn.lower().endswith(".motion3.json"):
                        files.append(os.path.join(root, fn))
                        if len(files) >= limit * 2:  # 提前退出
                            break
                if len(files) >= limit * 2:
                    break
            files = sorted(files)[:limit]  # 只取前limit个
        except Exception:
            files = []
        for p in files:
            if len(self._extra_motions) >= limit:
                break
            grp = os.path.basename(p)[: -len(".motion3.json")].strip() or "动作"
            if grp in self._extra_motions:
                continue
            try:
                self.model.LoadExtraMotion(grp, p)
                self._extra_motions[grp] = 1
            except Exception:
                pass
        if self._extra_motions:
            try:
                self.model._motions_cache = None    # 让 GetMotions 重新读取，纳入刚登记的动作
            except Exception:
                pass

    def resizeGL(self, w, h):
        if self.model:
            self.model.Resize(w, h)
            self._apply_view()


    def paintGL(self):
        self._clear_gl_background()
        self.l2d.clearBuffer(0.0, 0.0, 0.0, 0.0)   # 透明背景
        self._prepare_transparent_blending()
        if not self.model:
            return
        try:
            now = time.perf_counter()
            dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
            self._last_t = now
            # 看向鼠标：把归一化方向转成窗口像素坐标喂给 Drag
            try:
                px = (self._look[0] * 0.5 + 0.5) * self._canvas_w
                py = (0.5 - self._look[1] * 0.5) * self._canvas_h
                self.model.Drag(px, py)
            except Exception:
                pass
            if self.version == "v3":
                self.model.Update()
            else:
                self.model.Update()
            self._apply_manual_expression_overrides()
            # 待机循环：上一个动作放完就再来一个，模型持续有动作
            # 放完后留一段（带随机）空档再换，切太快会卡顿/闪烁
            if self._auto_motion:
                try:
                    if self.model.IsMotionFinished():
                        if now >= self._motion_cooldown:
                            self._start_auto_motion()
                            self._motion_cooldown = now + self._motion_cooldown_duration * random.uniform(0.9, 1.8)
                        # 如果冷却时间未到，但距离上次播放已经超过10秒（防止卡死不动），强制重新播放
                        elif self._last_motion_t and now - self._last_motion_t > 10.0:
                            self._start_auto_motion()
                            self._motion_cooldown = now + self._motion_cooldown_duration * random.uniform(0.9, 1.8)
                except Exception:
                    pass
            # 自动随机表情：开启后每隔一段（带随机）时间换一个表情，间隔够长才不卡
            if self._auto_expr:
                if not self._next_expr_t:
                    self._next_expr_t = now + self._expr_interval
                elif now >= self._next_expr_t:
                    self.set_random_expression()
                    self._next_expr_t = now + self._expr_interval * random.uniform(1.0, 1.8)
            self.model.Draw()
        except Exception as e:  # noqa: BLE001
            # 个别模型（如带特殊裁剪遮罩的）可能渲染报错——绝不让它崩掉整个程序
            self._handle_render_error(e)

    def _handle_render_error(self, e):
        if self._errored:
            return
        self._errored = True
        self.timer.stop()
        if hasattr(self, "_mask_timer"):
            self._mask_timer.stop()
        self.model = None
        if callable(self.on_error):
            QTimer.singleShot(0, lambda: self.on_error(self.model_path, repr(e)))


class Live2DPet(QWidget):
    """桌面显示层（main.py 持有的就是它）。

    本体是一个**普通的半透明 QWidget**，每帧把离屏 GL 渲染器(`_Live2DGL`)产出的
    framebuffer 用 QPainter 画上来。这样桌面合成完全走普通控件的逐像素 alpha，
    宠物真正贴在桌面上——没有黑色画框、没有黑底、没有画布矩形。

    模型加载、动作、表情、语音、命中测试、构图/尺寸等全部委托给 `_Live2DGL`；
    凡是会改变离屏画布尺寸的接口，这里都会把自身尺寸同步过去，保证命中坐标一致。
    其余未显式定义的方法/属性通过 __getattr__ 透明转发到离屏渲染器。"""

    def __init__(self, model_path, size=300, zoom=1.0, xoff=0.0, yoff=0.0,
                 parent=None, ratio=None, preview_mode=False):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self._preview_mode = bool(preview_mode)
        self._frame = None
        # main.py 会给这些回调赋值（赋到本控件上，再桥接到离屏渲染器）
        self.on_error = None
        self.on_resized = None
        self.on_voice_with_text = None
        # 离屏 GL 渲染器：parent=None、永不 show()，靠 grabFramebuffer 驱动
        self._gl = _Live2DGL(model_path, size, zoom, xoff, yoff,
                             None, ratio, preview_mode)
        # 离屏控件 update() 不会触发 paintGL，所以停掉它自带的刷新/遮罩定时器，
        # 改由本控件按帧 grabFramebuffer 主动驱动渲染。
        try:
            self._gl.timer.stop()
        except Exception:
            pass
        try:
            self._gl._mask_timer.stop()
        except Exception:
            pass
        self._gl.on_error = self._on_gl_error
        self._gl.on_resized = self._on_gl_resized
        self._gl.on_voice_with_text = self._on_gl_voice
        self.setFixedSize(self._gl.natural_size())
        self.setMouseTracking(True)
        self.fps = getattr(self._gl, "fps", 30)
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_tick)
        self._render_timer.start(int(1000 / max(1, self.fps)))

    # ---------- 每帧渲染：抓离屏 framebuffer → 重绘 ----------
    def _render_tick(self):
        gl = self._gl
        if gl is None:
            return
        try:
            img = gl.grabFramebuffer()
        except Exception:
            return
        if img is None or img.isNull():
            return
        w = max(1, self.width())
        dpr = img.width() / float(w)
        if dpr > 0:
            img.setDevicePixelRatio(dpr)
        self._frame = img
        self.update()

    def paintEvent(self, _ev):
        if self._frame is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(0, 0, self._frame)
        p.end()

    # ---------- 回调桥接 ----------
    def _on_gl_error(self, *a):
        if callable(self.on_error):
            self.on_error(*a)

    def _on_gl_voice(self, *a):
        if callable(self.on_voice_with_text):
            self.on_voice_with_text(*a)

    def _on_gl_resized(self, *a):
        self._sync_size_from_gl()
        if callable(self.on_resized):
            self.on_resized(*a)

    def _sync_size_from_gl(self):
        sz = self._gl.natural_size()
        if self.size() != sz:
            self.setFixedSize(sz)
        self.update()

    # ---------- 尺寸/构图：离屏画布变了要同步本控件 ----------
    def natural_size(self):
        return self._gl.natural_size()

    def set_live2d_size(self, s):
        self._gl.set_live2d_size(s)
        self._sync_size_from_gl()

    def set_height_ratio(self, r):
        self._gl.set_height_ratio(r)
        self._sync_size_from_gl()

    def fit_to_content(self, *a, **k):
        ok = self._gl.fit_to_content(*a, **k)
        self._sync_size_from_gl()
        return ok

    def reload_model(self, *a, **k):
        self._gl.reload_model(*a, **k)
        self._sync_size_from_gl()
        self.update()

    # ---------- 渲染开关（拖动时暂停以让拖动跟手） ----------
    def set_render_active(self, enabled):
        if enabled:
            try:
                self._gl.set_render_active(True)   # 仅复位 _last_t，不会启动离屏定时器
            except Exception:
                pass
            if not self._render_timer.isActive():
                self._render_timer.start(int(1000 / max(1, self.fps)))
        else:
            self._render_timer.stop()

    def set_mask_updates_enabled(self, enabled):
        # 新方案下显示完全靠 QPainter 逐像素 alpha，不再需要 GL 自身遮罩刷新。
        return

    # ---------- 生命周期 ----------
    def shutdown(self):
        try:
            self._render_timer.stop()
        except Exception:
            pass
        gl = self._gl
        self._gl = None
        try:
            if gl is not None:
                gl.shutdown()
        except Exception:
            pass
        # 离屏渲染器没有 Qt 父对象，需显式回收，避免切换模型/形象时累积泄漏。
        try:
            if gl is not None:
                gl.deleteLater()
        except Exception:
            pass

    def showEvent(self, ev):
        if not self._render_timer.isActive():
            self._render_timer.start(int(1000 / max(1, self.fps)))
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._render_timer.stop()
        super().hideEvent(ev)

    # ---------- 其余接口一律转发到离屏渲染器 ----------
    def __getattr__(self, name):
        # 仅当本类与 QWidget 都没有该属性时才会进来
        gl = self.__dict__.get("_gl")
        if gl is not None:
            return getattr(gl, name)
        raise AttributeError(name)
