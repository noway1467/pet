"""桌面宠物主程序：透明无边框置顶窗口 + 拖动 + 右键菜单 + 系统托盘。

运行：python main.py
"""
import json
import math
import os
import shutil
import sys
import threading
import gc

from PySide6.QtCore import Qt, QEvent, QTimer, Signal, QRect, QPoint, QRectF, QPointF
from PySide6.QtGui import QSurfaceFormat, QIcon, QAction, QPixmap, QCursor, QPainter, QColor, QPen, QFont, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication, QWidget, QMenu, QSystemTrayIcon, QFileDialog, QMessageBox,
    QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit, QSlider, QCheckBox, QProgressBar,
    QGridLayout, QFrame, QWidgetAction, QButtonGroup, QRadioButton,
    QComboBox,
)

import config
import system
from pixel_pet import PixelPet, render_icon, CHARACTERS
from image_pet import ImagePet
from chat_bubble import ChatBubble, ChatManager, _restack_window
from affinity import AffinitySystem

PIXEL_CHARS = [(name, cls.label) for name, cls in CHARACTERS.items()]
SCALES = [3, 4, 5, 6, 8]
IMAGE_SIZES = [120, 160, 220, 280, 360]
LIVE2D_SIZES = [120, 160, 200, 260, 320, 400, 500]
# 新模型（还没单独调过大小）的默认尺寸：用固定值，不沿用上一个模型的尺寸，
# 这样"每个模型各记各的大小、互不套用"（旧模型已有记忆的不受影响）。
DEFAULT_LIVE2D_SIZE = 200
DEFAULT_IMAGE_SIZE = 160
# 通用"窗口动作"：靠移动窗口实现，任何渲染器（图片/像素/Live2D）都能用
ACTION_ITEMS = [("jump", "起跳"), ("hop", "蹦跳"), ("nod", "点头"), ("wiggle", "摇头"),
                ("tilt", "歪头"), ("lean", "侧倾"), ("spin", "转身"), ("dance", "跳舞")]
ACTION_DUR = {"jump": 0.85, "hop": 0.6, "nod": 0.7, "wiggle": 0.75,
              "tilt": 1.1, "lean": 0.8, "spin": 0.95, "dance": 1.7, "pat": 0.9, "pet": 1.2}
# Live2D 模型常见动作组名 -> 中文显示名（让"动作"菜单更直观）；未列出的组用原名
MOTION_GROUP_LABELS = {
    "idle": "待机", "start": "开场", "tap_head": "摸头", "taphead": "摸头",
    "tap_body": "戳身体", "tapbody": "戳身体", "tap": "点击", "shake": "摇晃",
    "flick_head": "弹脑门", "random": "随机", "new_msg": "新消息",
}
# 气泡语录自动播放的间隔预设：(显示名, 最短秒, 最长秒)
CHAT_INTERVAL_PRESETS = [
    ("频繁（约 20–45 秒）", 20, 45),
    ("正常（约 30–120 秒）", 30, 120),
    ("较少（约 1–3 分钟）", 60, 180),
    ("很少（约 2–5 分钟）", 120, 300),
]
IMG_FILTER = "图片 (*.png *.jpg *.jpeg *.webp *.bmp)"
LIVE2D_FILTER = "Live2D 模型 (*.model3.json *.model.json model.json)"
# 项目自带的 Live2D 模型目录（启动桌面宠物的脚本会把工作目录设到项目根）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)        # 打包成 exe 后：exe 所在目录
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 默认 models 目录
DEFAULT_MODELS_DIR = os.path.join(BASE_DIR, "live2d")

def get_models_dir():
    """获取模型文件夹路径：优先使用配置，否则使用默认路径"""
    cfg = config.load()
    custom_dir = cfg.get("models_dir", "")
    if custom_dir and os.path.isdir(custom_dir):
        return custom_dir
    return DEFAULT_MODELS_DIR

MODELS_DIR = get_models_dir()
# "常用"模型文件夹：用户把常用宠物模型丢进这里，选择器会把它们置顶（⭐ 常用）。
FAV_DIRNAME = "常用"

def get_fav_dir():
    """获取常用模型文件夹"""
    return os.path.join(get_models_dir(), FAV_DIRNAME)

FAV_DIR = get_fav_dir()


def _canon_path(p):
    """规范化路径，作为"按模型记忆配置"的统一键。

    解析 junction/软链 + 统一大小写与分隔符——这样无论从源码目录直接运行，还是从
    exe 目录(其 live2d 是指向源码目录的 junction)运行，同一个模型都对应同一个键，
    位置/大小/显示区域等记忆不会因为路径写法不同而丢失或重复。
    """
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.realpath(p))
    except Exception:
        return os.path.normcase(os.path.normpath(p))


def _under_dir(path, folder):
    """path 是否位于 folder（含子文件夹）内——按规范化后的前缀判断。"""
    try:
        cp, cf = _canon_path(path), _canon_path(folder)
        return cp == cf or cp.startswith(cf + os.sep)
    except Exception:
        return False


def ensure_fav_dir():
    """确保"常用"模型文件夹存在，并放一个说明文件，方便用户往里丢模型。"""
    try:
        fav_dir = get_fav_dir()
        os.makedirs(fav_dir, exist_ok=True)
        readme = os.path.join(fav_dir, "把常用模型文件夹放进这里.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write(
                    "把你常用的 Live2D 模型文件夹整个拷进这个「常用」目录，\n"
                    "在「选择 Live2D 模型…（带预览）」里它们会自动置顶到 ⭐ 常用，\n"
                    "在一堆模型里一眼就能找到、随时切换。\n\n"
                    "每个模型一个子文件夹，里面要有 model.json / *.model.json（Cubism 2）\n"
                    "或 *.model3.json（Cubism 3）等模型设置文件。\n")
    except OSError:
        pass

# ──── 内置"常用宠物"：随软件分发，所有用户开箱即有 ────
# live2d 的 rel 相对 live2d/ 解析；image 的 rel 相对程序目录解析；文件缺失则该项自动跳过。
BUILTIN_FAVORITES = [
    {"type": "live2d", "name": "sagiri",       "rel": "model/sagiri/sagiri.model.json"},
    {"type": "image",  "name": "2-Photoroom",  "rel": "2-Photoroom.png"},
    {"type": "live2d", "name": "kurumi",       "rel": "model/kurumi/model.json"},
    {"type": "live2d", "name": "Pio",          "rel": "model/Pio/model.json"},
    {"type": "live2d", "name": "platelet",     "rel": "model/platelet/model.json"},
    {"type": "live2d", "name": "rem",          "rel": "model/rem/model.json"},
    {"type": "live2d", "name": "xiaomai",      "rel": "model/xiaomai/xiaomai.model.json"},
    {"type": "live2d", "name": "Doro",         "rel": "model/Doro/Doro.model3.json"},
    {"type": "live2d", "name": "huohuo",       "rel": "model/huohuo/huohuo.model3.json"},
    {"type": "live2d", "name": "素晴-1104100",  "rel": "model/素晴-1104100/1104100.model3.json"},
]
# 内置模型的推荐构图（按 rel 记），新用户没自己调过时就用它——开箱即取景得当。
BUILTIN_VIEWS = {
    "model/sagiri/sagiri.model.json":         {"zoom": 0.79, "xoff": 0.07,  "yoff": -0.09, "ratio": 1.714},
    "model/Pio/model.json":                   {"zoom": 1.0,  "xoff": 0.0,   "yoff": -0.08, "ratio": None},
    "model/platelet/model.json":              {"zoom": 2.15, "xoff": -0.02, "yoff": 0.02,  "ratio": 2.381},
    "model/Doro/Doro.model3.json":            {"zoom": 1.25, "xoff": 0.04,  "yoff": -0.03, "ratio": 0.962},
    "model/huohuo/huohuo.model3.json":        {"zoom": 1.0,  "xoff": 0.0,   "yoff": -0.08, "ratio": None},
    "model/素晴-1104100/1104100.model3.json":  {"zoom": 1.66, "xoff": -0.08, "yoff": -0.66, "ratio": 1.653},
}


def _builtin_fav_path(fav):
    """把内置常用宠物的相对路径解析成本机绝对路径。"""
    base = get_models_dir() if fav.get("type") == "live2d" else BASE_DIR
    return os.path.normpath(os.path.join(base, fav["rel"]))


def builtin_favorites():
    """解析内置常用宠物为 [{type,name,path}]，只保留文件确实存在的（缺哪个跳哪个）。"""
    out = []
    for f in BUILTIN_FAVORITES:
        p = _builtin_fav_path(f)
        if os.path.exists(p):
            out.append({"type": f["type"], "name": f["name"], "path": p})
    return out


APP_NAME = "桌面宠物"
APP_VERSION = "3.9.3"
ACCENT = "#5BB8F5"

# ──── 贴边自动隐藏参数 ────
EDGE_PEEK = 8            # 缩回后仍露在屏幕内的窄边宽度(px)，方便鼠标找到它
EDGE_SNAP_DIST = 12     # 模型本体离屏幕边 ≤ 此值才吸附（按可见内容算，不含透明画布）
EDGE_TRIGGER = 8        # 已缩回时：光标进入屏幕边这么宽的带子就划出
EDGE_HOVER_MARGIN = 14  # 光标在内容跨轴范围外扩这么多仍算"停在它上面"
EDGE_LEAVE_MS = 600     # 鼠标离开后多久自动缩回
EDGE_STARTUP_GRACE_MS = 2200  # 开机恢复贴边时先完整显示这么久，避免"开机找不到宠物"
EDGE_TICK_MS = 16       # 滑动动画帧间隔(~60fps，保证流畅)
EDGE_SLIDE_OUT_DUR = 0.34   # 划出时长(秒)：略过头再回弹到位，"蹦"出来更生动
EDGE_SLIDE_IN_DUR = 0.26    # 缩回时长(秒)：先微微探头蓄力，再利落收回

# 全局样式：右键 / 托盘菜单参考 Windows 右键菜单——更紧凑、更小巧（小圆角、小字号、低留白）
APP_QSS = """
QMenu {
    background-color: #fbfdff;
    border: 1px solid #d9e2ec;
    border-radius: 8px;
    padding: 4px;
    font-size: 12px;
}
QMenu::item {
    padding: 4px 22px 4px 24px;
    margin: 1px 4px;
    border-radius: 5px;
    color: #243b53;
}
QMenu::item:selected { background-color: #5BB8F5; color: #ffffff; }
QMenu::item:disabled { color: #9fb3c8; }
QMenu::separator { height: 1px; background: #e6edf3; margin: 4px 10px; }
QMenu::right-arrow { width: 8px; height: 8px; margin-right: 6px; }
QMenu::indicator { width: 14px; height: 14px; margin-left: 6px; }
QToolTip {
    background-color: #243b53; color: #ffffff;
    border: none; padding: 5px 8px; border-radius: 6px;
}
QDialog, QMessageBox { background-color: #fbfdff; }
QLabel { color: #243b53; }
QPushButton {
    background-color: #5BB8F5; color: #ffffff; border: none;
    border-radius: 8px; padding: 7px 18px; min-width: 64px;
}
QPushButton:hover  { background-color: #3f9fe0; }
QPushButton:pressed { background-color: #2e8bcc; }
"""


class PettingOverlay(QWidget):
    """叠在渲染器上方的透明覆盖层，只负责绘制摸头时的小手动画。"""

    def __init__(self, owner, parent):
        super().__init__(parent)
        self._owner = owner
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.show()
        self.setVisible(False)

    def sync_geometry(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        self.raise_()

    def paintEvent(self, _ev):
        if getattr(self._owner, "_petting_overlay_t", -1.0) < 0.0:
            return
        p = QPainter(self)
        self._owner._draw_petting_hand(
            p, self._owner._petting_overlay_rect(), self._owner._petting_overlay_t)
        p.end()


def _build_glove_path(unit=1.0):
    """返回一只更圆润的二次元白手套路径：不自相交、不留洞，适合粗描边卡通风。"""
    u = float(unit)
    hand = QPainterPath()
    hand.setFillRule(Qt.WindingFill)
    # 掌心略倾斜、偏椭圆，让姿势从“平放”变成轻微弯曲按压感。
    hand.addRoundedRect(QRectF(-11.0 * u, 0.8 * u, 21.5 * u, 15.4 * u), 8.7 * u, 8.7 * u)
    for x, y, w, h, rx in (
        (-11.0, -6.8, 4.7, 10.4, 2.5),
        (-6.0, -10.6, 5.2, 14.8, 2.8),
        (-0.6, -10.0, 5.0, 13.7, 2.7),
        (4.5, -7.5, 4.4, 10.7, 2.4),
    ):
        hand.addRoundedRect(QRectF(x * u, y * u, w * u, h * u), rx * u, rx * u)
    # 拇指做成更大、更外翻的椭圆，卡通味更强，也更像按下去的猫爪手套。
    hand.addEllipse(QRectF(-17.6 * u, 4.4 * u, 11.4 * u, 11.8 * u))
    hand.addRoundedRect(QRectF(-6.2 * u, 14.8 * u, 11.8 * u, 7.8 * u), 2.8 * u, 2.8 * u)
    return hand.simplified()


class PetWindow(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._migrate_view_keys()                   # 旧配置的 live2d_views 键统一成规范化路径
        self._builtin_favs = builtin_favorites()   # 内置常用宠物（所有用户都有）
        self._seed_builtin_views()                 # 给内置模型种入推荐构图（用户没调过时）
        ensure_fav_dir()                            # 确保"常用"模型文件夹存在
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.renderer = None
        self._drag_off = None
        self._drag_candidate_off = None
        self._l2d_models = None        # 扫描到的 Live2D 模型缓存（后台线程预扫，打开选择器即用，免每次重扫卡顿）
        self._l2d_feature_cache = {}   # 模型能力缓存：减少选择器反复解析 model json
        self._l2d_scanning = False
        self._start_bg_scan()          # 启动即在后台线程扫一遍，打开选择器时秒开
        self._pos_ready = False        # 位置就绪后，切换/缩放才按底部中心锚定
        self._press_global = None
        self._moved = False
        self._last_drag_x = None
        self._fall_vy = 0.0
        # 贴边隐藏状态
        self._edge = None              # 当前吸附的边：None|'left'|'right'|'top'
        self._edge_hidden = False      # 是否已缩回（只露窄边）
        self._slide_from = None        # 滑动动画起点 / 终点
        self._slide_to = None
        self._slide_t = 0.0
        self._slide_dur = EDGE_SLIDE_OUT_DUR
        self._slide_after_hidden = False
        self._leave_ms = 0             # 鼠标离开累计时长，到阈值就缩回
        self._layer_sync_pending = False
        self._input_mask_rect_cache = None

        self._build_renderer()
        self._apply_flags()
        self._restore_pos()
        QTimer.singleShot(0, self._sync_input_mask)
        QTimer.singleShot(240, self._sync_input_mask)

        # 好感度 / 养成系统：在建托盘菜单之前创建（菜单要显示当前层级/好感值）
        self._affinity = AffinitySystem(config.CONFIG_DIR)

        self.tray = self._build_tray()
        self.tray.show()

        # 鼠标跟随（眼睛/头部看向光标）
        self._look_timer = QTimer(self)
        self._look_timer.timeout.connect(self._update_look)
        self._look_timer.start(50)
        # 重力掉落
        self._fall_timer = QTimer(self)
        self._fall_timer.setTimerType(Qt.PreciseTimer)
        self._fall_timer.timeout.connect(self._fall_tick)
        # 窗口动作（起跳/点头/跳舞…）：移动整窗实现，Live2D/像素也能用
        self._act_timer = QTimer(self)
        self._act_timer.setTimerType(Qt.PreciseTimer)
        self._act_timer.timeout.connect(self._act_tick)
        self._act_name = None
        self._act_home = None
        self._act_t = 0.0
        self._act_dur = 0.7
        # 拖动节流：高频鼠标(125~1000Hz)每个 move 事件都 move() 半透明分层窗口会让 DWM
        # 反复重合成、拖不跟手。把实际 move() 合并到 ~125Hz，并在松手时落实最终位置。
        self._drag_target = None
        self._drag_applied = None
        self._drag_move_timer = QTimer(self)
        self._drag_move_timer.setTimerType(Qt.PreciseTimer)
        self._drag_move_timer.timeout.connect(self._drag_move_flush)
        # 摸头手势覆盖动画：只动画"手"，不再让宠物本体左右晃动
        self._petting_overlay_t = -1.0
        self._petting_overlay_dur = 0.9
        self._petting_timer = QTimer(self)
        self._petting_timer.setTimerType(Qt.PreciseTimer)
        self._petting_timer.timeout.connect(self._petting_tick)
        self._petting_overlay = PettingOverlay(self, self)
        self._petting_overlay.sync_geometry()
        self._pet_cursor_cache = None
        self._warm_petting_assets()
        # 贴边隐藏：滑动动画 + 监视光标决定划出/缩回
        self._slide_timer = QTimer(self)
        self._slide_timer.timeout.connect(self._slide_tick)
        self._edge_timer = QTimer(self)
        self._edge_timer.timeout.connect(self._edge_tick)
        self._edge_timer.start(80)

        # 聊天气泡系统
        self._chat_bubble = ChatBubble(self)
        self._chat_manager = ChatManager(self._chat_bubble, config_dir=config.CONFIG_DIR)
        # 加载保存的气泡样式
        bubble_style = self.cfg.get("bubble_style", "cute")
        self._chat_manager.set_bubble_style(bubble_style)
        # 加载伴侣模式 / 雌小鬼模式配置（两者互斥，雌小鬼优先）
        self._chat_manager.set_companion_mode(self.cfg.get("companion_mode", False))
        self._chat_manager.set_mesugaki_mode(self.cfg.get("mesugaki_mode", False))
        # 好感度 / 养成系统：实例已在前面创建，这里注入聊天管理器并应用养成模式开关
        self._chat_manager.set_affinity(self._affinity)
        self._chat_manager.set_nurture_mode(self.cfg.get("nurture_mode", False))
        # 加载点击弹语录开关
        self._chat_manager.set_click_quote_enabled(self.cfg.get("click_quote_enabled", True))
        # 应用气泡语录间隔配置
        self._chat_manager.set_intervals(self.cfg.get("chat_min_interval", 30),
                                         self.cfg.get("chat_max_interval", 120))
        # 配置 TTS 朗读（开关/音量/语速/嗓音/引擎/自定义命令，全部从配置恢复）
        self._apply_tts_settings()
        # 配置节日问候
        holiday_enabled = self.cfg.get("holiday_greetings", True)
        holiday_config = {
            "user_birthday": self.cfg.get("user_birthday", ""),
            "custom_holidays": self.cfg.get("custom_holidays", []),
        }
        self._chat_manager.set_holiday_enabled(holiday_enabled, holiday_config)
        # 同步置顶状态
        self._chat_bubble.set_always_on_top(self.cfg["always_on_top"])
        # 说话回调：仅用于"气泡语录同步发声"（放语音，不触发动作；动作仍由摸头等情境单独触发）
        self._chat_manager.on_speak = self._on_pet_voice
        if self.cfg.get("chat_enabled", True):
            self._chat_manager.start()
        # 养成模式：启动时结算连续登录 / 冷落惩罚 / 当日相见（延迟到问候之后再播，避免抢首问候）
        if self.cfg.get("nurture_mode", False):
            QTimer.singleShot(4200, self._nurture_on_start)

        self._hotkey_id = 1
        self._register_hotkey()

    # ------------------------------------------------------------------ #
    #  渲染器
    # ------------------------------------------------------------------ #
    def _model_key(self):
        """当前模型的唯一标识（用于记忆位置/大小/吸附状态）。"""
        c = self.cfg.get("character")
        if c == "live2d" and self.cfg.get("live2d_model"):
            return f"live2d:{_canon_path(self.cfg['live2d_model'])}"
        if c == "image" and self.cfg.get("image_path"):
            return f"image:{_canon_path(self.cfg['image_path'])}"
        if c in CHARACTERS:
            return f"pixel:{c}"
        return None

    def _get_model_memory(self, key=None):
        """获取某个模型的记忆（位置/大小/吸附状态），没有则返回空字典。"""
        if key is None:
            key = self._model_key()
        if not key:
            return {}
        mem = self.cfg.setdefault("model_memory", {})
        return mem.get(key) or {}

    def _save_model_memory(self, key=None, **updates):
        """保存当前模型的记忆（位置/大小/吸附状态）。"""
        if key is None:
            key = self._model_key()
        if not key:
            return
        mem = self.cfg.setdefault("model_memory", {})
        entry = dict(mem.get(key) or {})
        entry.update(updates)
        mem[key] = entry
        config.save(self.cfg)

    def _build_renderer(self):
        old = self.renderer
        if old is not None:
            try:
                clear = getattr(old, "clear_frame", None)
                if callable(clear):
                    clear()
            except Exception:
                pass
            try:
                old.hide()
            except Exception:
                pass
            old.shutdown()
            old.removeEventFilter(self)
            old.setParent(None)
            try:
                old.close()
            except Exception:
                pass
            try:
                old.deleteLater()
                QApplication.sendPostedEvents(None, 0)
            except Exception:
                pass
            self.renderer = None
            gc.collect()

        # 切换模型时隐藏并清除旧气泡，避免显示上一个模型的内容
        if hasattr(self, '_chat_bubble') and self._chat_bubble:
            self._chat_bubble.hide()
            # 停止当前正在播放的定时器，避免切换后立即显示旧内容
            if hasattr(self, '_chat_manager') and self._chat_manager:
                self._chat_manager._timer.stop()

        name = self.cfg["character"]
        if name == "live2d":
            try:
                from live2d_pet import Live2DPet
                v = self._l2d_view_of(self.cfg["live2d_model"])
                # 优先用这个模型自己的记忆，再用该模型保存过的构图尺寸；
                # 都没有（全新模型）才用固定默认值，绝不沿用上一个模型的尺寸（各记各的）。
                model_mem = self._get_model_memory()
                size = model_mem.get("size") or v["size"] or DEFAULT_LIVE2D_SIZE
                self.cfg["live2d_size"] = int(size)            # 让"大小"菜单反映当前模型尺寸
                self.renderer = Live2DPet(self.cfg["live2d_model"],
                                          size,
                                          v["zoom"], v["xoff"], v["yoff"], self,
                                          ratio=v["ratio"])
                self.renderer.on_error = self._on_live2d_render_error
                self.renderer.on_resized = self._fit_window_to_renderer
                self.renderer.on_voice_with_text = self._on_voice_with_text
                self.renderer.set_auto_expression(
                    self.cfg.get("live2d_auto_expression", False))
                if hasattr(self.renderer, "set_voice_enabled"):
                    self.renderer.set_voice_enabled(self.cfg.get("voice_enabled", True))
                if hasattr(self.renderer, "set_voice_volume"):
                    self.renderer.set_voice_volume(self.cfg.get("voice_volume", 0.5))
                # 应用该模型「禁止自动播放」的动作集合（手动触发不受限）
                self._apply_disabled_motions()
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Live2D 不可用", str(e))
                self.cfg["character"] = "slime"
                config.save(self.cfg)
        elif name == "image":
            try:
                # 图片宠物：优先用该图片自己记住的尺寸，全新图片才用固定默认值（不沿用上一个）
                model_mem = self._get_model_memory()
                size = model_mem.get("size") or DEFAULT_IMAGE_SIZE
                self.cfg["image_size"] = int(size)
                regions = self.cfg.get("regions", {}).get(self.cfg["image_path"])
                self.renderer = ImagePet(self.cfg["image_path"],
                                         int(size),
                                         self.cfg.get("facing", 1), regions, self)
                self.renderer.set_follow(self.cfg.get("follow", True))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "图片宠物不可用", str(e))
                self.cfg["character"] = "slime"
                config.save(self.cfg)

        if self.renderer is None:
            # 像素宠物：优先用该角色自己记住的缩放倍数
            model_mem = self._get_model_memory()
            scale = model_mem.get("size") or self.cfg["scale"]
            self.cfg["scale"] = int(scale)
            self.renderer = PixelPet(self.cfg["character"], int(scale),
                                     self.cfg["style"], self)

        self.renderer.move(0, 0)
        self.renderer.installEventFilter(self)
        self.renderer.setMouseTracking(True)   # 无按键也能收到 MouseMove，用于头部悬停换光标
        self.renderer.show()
        self._resize_keep_anchor(self.renderer.natural_size())
        QTimer.singleShot(0, self._sync_input_mask)
        QTimer.singleShot(240, self._sync_input_mask)
        # Live2D/OpenGL 首帧加载阶段可能还没把透明 alpha 写稳；稍后再同步窗口形状，
        # 避免 Windows 合成层缓存到黑色矩形或裁剪遮罩脏帧。
        QTimer.singleShot(900, self._refresh_live2d_alpha_mask)
        QTimer.singleShot(1800, self._refresh_live2d_alpha_mask)
        QTimer.singleShot(3200, self._refresh_live2d_alpha_mask)
        self._schedule_layer_sync()

    def _warm_petting_assets(self):
        """提前构建摸头资源，避免第一次点击时才创建导致一闪而过的首帧脏画面。"""
        try:
            self._pet_cursor()
        except Exception:
            pass
        try:
            clear = getattr(self.renderer, "clear_frame", None)
            if callable(clear):
                clear()
            self._petting_overlay.setVisible(False)
            self._petting_overlay.update()
        except Exception:
            pass

    def _refresh_live2d_alpha_mask(self):
        """强制重建 Live2D 控件自身 alpha mask，裁掉透明区黑色画框。"""
        try:
            fn = getattr(self.renderer, "sync_alpha_mask", None)
            if callable(fn):
                fn(force=True)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  鼠标：拖动 + 点击 + 右键菜单（通过事件过滤器统一处理）
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, ev):
        if obj is self.renderer:
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                if not self._point_hits_visible_content(ev.pos().x(), ev.pos().y()):
                    self._clear_head_cursor()
                    return False
            if t == QEvent.Leave:
                self._clear_head_cursor()
                return False
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._clear_head_cursor()
                gp = ev.globalPosition().toPoint()
                self._drag_candidate_off = gp - self.frameGeometry().topLeft()
                self._press_global = ev.globalPosition().toPoint()
                self._moved = False
                self._last_drag_x = ev.globalPosition().toPoint().x()
                self._fall_timer.stop()
                self._cancel_action()
                # 抓住贴边的宠物：停止缩回动画、当作已展开，方便直接拖走
                if self._slide_timer.isActive():
                    self._slide_timer.stop()
                self._edge_hidden = False
                self._leave_ms = 0
                return True
            if t == QEvent.MouseMove and self._drag_off is None and self._drag_candidate_off is not None:
                gp = ev.globalPosition().toPoint()
                if (ev.buttons() & Qt.LeftButton) and self._press_global is not None:
                    if (gp - self._press_global).manhattanLength() > 4:
                        self._begin_drag(gp)
                    else:
                        return True
                else:
                    self._drag_candidate_off = None
                    return False
            if t == QEvent.MouseMove and self._drag_off is None:
                if not self._point_hits_visible_content(ev.pos().x(), ev.pos().y()):
                    self._clear_head_cursor()
                    return False
                # 悬停在头部摸头范围：把光标换成"摸头小手"，离开恢复默认
                self._update_head_cursor(ev.pos().x(), ev.pos().y())
                return False
            if t == QEvent.MouseMove and self._drag_off is not None and (ev.buttons() & Qt.LeftButton):
                gp = ev.globalPosition().toPoint()
                # 走动自动转向（图片宠物）
                if isinstance(self.renderer, ImagePet) and self._last_drag_x is not None:
                    vx = gp.x() - self._last_drag_x
                    if vx > 3:
                        self.renderer.set_facing(1)
                    elif vx < -3:
                        self.renderer.set_facing(-1)
                self._last_drag_x = gp.x()
                tgt = gp - self._drag_off
                # 合并高频 move 事件：记录目标位置，首个事件立即落位、随后按 ~125Hz 节流，
                # 避免每个鼠标事件都触发分层窗口重合成导致拖动卡顿。
                self._drag_target = (tgt.x(), self._clamp_y(tgt.y()))
                if not self._drag_move_timer.isActive():
                    self._drag_applied = self._drag_target
                    self.move(*self._drag_target)
                    self._drag_move_timer.start(8)
                return True
            if t == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
                if self._drag_off is None and self._drag_candidate_off is None:
                    return False
                was_dragging = self._drag_off is not None
                self._drag_off = None
                self._drag_candidate_off = None
                if was_dragging:
                    # 落实拖动的最终位置，再停止节流定时器
                    self._drag_move_timer.stop()
                    if self._drag_target is not None and self._drag_target != self._drag_applied:
                        self.move(*self._drag_target)
                    self._drag_target = None
                    self._drag_applied = None
                    self._set_renderer_mask_updates(True)
                    self._set_renderer_render_active(True)
                moved = self._moved

                # 检测是否点击头部（未拖动时）
                head_clicked = False
                if not moved and self._in_head_region(ev.pos().x(), ev.pos().y()):
                    head_clicked = True
                    self._do_head_pat()
                    self._save_pos()
                    return True   # 直接返回，不执行后续任何动作

                # 非头部点击才执行通用 react 和语录。
                # Live2D 的"关闭（点击不动作）"应当真正屏蔽 click 类动作，而不是只关整窗位移动画。
                if not head_clicked:
                    # 养成模式下，点身体只记互动/弹反馈，不驱动 Live2D 点击动作，
                    # 否则会和好感度气泡、摸头手势感混在一起，看起来像“乱跳”。
                    live2d_click_enabled = (
                        not self.cfg.get("nurture_mode", False) and (
                            self.cfg.get("character") != "live2d"
                            or self.cfg.get("click_action_enabled", True)
                        )
                    )
                    if moved:
                        self.renderer.react("drop")
                    elif live2d_click_enabled:
                        self.renderer.react("click")
                    if self.cfg.get("nurture_mode", False):
                        # 养成模式：戳身体/陪玩加分并气泡提示收益（取代通用点击语录，避免双气泡）
                        self._nurture_quiet_action("drag_play" if moved else "body_poke")
                    elif not moved and self.cfg.get("click_quote_enabled", True):
                        # 通过 from_click 标记让 ChatManager 检查是否允许点击弹出语录
                        self._chat_manager.say(from_click=True)

                # 贴边状态下"单击"宠物 = 把它取下：解除吸附、留在原地不再自动隐藏
                if not moved and self._edge is not None:
                    shown, _h = self._dock_positions(self._edge)
                    self.move(shown)
                    self._undock()
                    self._save_pos()
                    return True

                # 拖动结束：靠边就吸附
                side = self._detect_edge() if moved else None
                if side is not None:
                    self._dock_to_edge(side)        # 吸附并准备缩回（鼠标移开后）
                    return True
                if self._edge is not None:
                    self._undock()                  # 被拖离边缘，解除吸附

                if isinstance(self.renderer, ImagePet) and self.cfg.get("gravity", True):
                    self._start_fall()
                else:
                    self._save_pos()

                # 点击让 Live2D 也明显动一下（可在"动作→内置动作→点击宠物时"里换动作或关闭）。
                # 头部点击已单独处理；这里只处理非头部点击。
                # 检查配置：只有当 click_action_enabled 为 True 时才触发动作
                click_quote_shown = (
                    not moved
                    and not head_clicked
                    and self.cfg.get("click_quote_enabled", True)
                    and not self.cfg.get("nurture_mode", False)
                )
                if (not moved and not head_clicked and not click_quote_shown
                        and not self.cfg.get("nurture_mode", False)
                        and self.cfg.get("click_action_enabled", True)):
                    # Live2D 角色才播放窗口动作（像素和图片宠物不需要额外动作）。
                    # 养成模式下点击是"戳身体"互动（加好感+气泡），不再让整窗"跳"一下。
                    if self.cfg.get("character") == "live2d":
                        self._play_window_action(self.cfg.get("click_action", "hop"))
                return True
            if t == QEvent.ContextMenu:
                if not self._point_hits_visible_content(ev.pos().x(), ev.pos().y()):
                    return False
                self._show_menu(ev.globalPos())
                return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------ #
    #  摸头交互：头部悬停换光标 + 点击播放摸头动画 + 好感度
    # ------------------------------------------------------------------ #
    def _begin_drag(self, gp):
        """鼠标确实移动超过阈值后才进入拖拽，避免普通点击触发拖拽副作用。"""
        if self._drag_off is not None:
            return
        self._drag_off = self._drag_candidate_off
        self._drag_candidate_off = None
        self._moved = True
        self._drag_target = None
        self._drag_applied = None
        self._clear_head_cursor()
        self._set_renderer_mask_updates(False)
        self._set_renderer_render_active(False)
        if hasattr(self, "_chat_bubble") and self._chat_bubble.isVisible():
            self._chat_bubble.hide()
        if hasattr(self.renderer, "react"):
            self.renderer.react("grab")
        tgt = gp - self._drag_off
        self._drag_target = (tgt.x(), self._clamp_y(tgt.y()))
        self._drag_applied = self._drag_target
        self.move(*self._drag_target)

    def _drag_move_flush(self):
        """拖动节流定时器：把最近一次鼠标目标落实到窗口；没有新位移就转入空闲（停表）。"""
        if self._drag_off is None:
            self._drag_move_timer.stop()
            return
        if self._drag_target is None or self._drag_target == self._drag_applied:
            self._drag_move_timer.stop()   # 本帧无新位移：停表，下个 move 事件再唤醒
            return
        self._drag_applied = self._drag_target
        self.move(*self._drag_target)

    def _in_head_region(self, click_x, click_y):
        """窗口内坐标 (click_x, click_y) 是否落在"头部摸头范围"：
        模型顶部偏上的较小区域，尽量只覆盖头顶，不吃到额头以下。"""
        try:
            l_in, top_inset, r_in, _ = self._content_inset()
        except Exception:
            l_in = r_in = 0
            top_inset = 0
        content_left = int(l_in)
        content_top = int(top_inset)
        content_w = max(1, self.width() - int(l_in) - int(r_in))
        content_h = max(1, self.height() - content_top)
        head_h = max(28, int(content_h * 0.18))
        head_bottom = content_top + head_h
        side_margin = int(content_w * 0.28)
        head_left = content_left + side_margin
        head_right = content_left + content_w - side_margin
        in_y = content_top <= click_y <= head_bottom
        in_x = head_left <= click_x <= head_right
        return in_y and in_x

    def _pet_cursor(self):
        """构造并缓存"摸头小手"光标。

        二次元白手套：与摸头覆盖动画同款手型，使用同一份路径数据，避免光标和覆盖层
        长得不一样。"""
        if getattr(self, "_pet_cursor_cache", None) is not None:
            return self._pet_cursor_cache
        cur = QCursor(Qt.PointingHandCursor)
        try:
            size = 40
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            # 把原点挪到手掌中心附近，随后整只手都在以原点为中心的坐标里绘制
            p.translate(21, 10)
            u = 1.0
            hand = _build_glove_path(u)

            # 阴影
            p.fillPath(QPainterPath(hand).translated(1.0, 1.6), QColor(0, 0, 0, 56))
            # 描边 + 白手套填充
            edge = QPen(QColor("#202124"))
            edge.setWidthF(1.9)
            edge.setJoinStyle(Qt.RoundJoin)
            edge.setCapStyle(Qt.RoundCap)
            p.setPen(edge)
            p.setBrush(QColor("#FFFFFF"))
            p.drawPath(hand)

            # 细节：掌心体积感 + 指尖高光 + 袖口分隔
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 214))
            p.drawEllipse(QRectF(-5.2 * u, 4.8 * u, 8.2 * u, 5.2 * u))
            p.setBrush(QColor(255, 255, 255, 190))
            for hx, hy, hw, hh in (
                (-10.1, -6.2, 2.1, 2.8),
                (-5.0, -8.8, 2.1, 3.0),
                (0.1, -8.0, 2.1, 2.9),
                (5.0, -5.5, 2.0, 2.5),
                (-14.8, 9.2, 2.2, 1.9),
            ):
                p.drawEllipse(QRectF(hx * u, hy * u, hw * u, hh * u))
            guide = QPen(QColor(176, 181, 194, 175))
            guide.setWidthF(0.95)
            guide.setCapStyle(Qt.RoundCap)
            p.setPen(guide)
            p.drawLine(QPointF(-5.8 * u, 1.2 * u), QPointF(-5.8 * u, -3.8 * u))
            p.drawLine(QPointF(-0.8 * u, 0.8 * u), QPointF(-0.8 * u, -5.2 * u))
            p.drawLine(QPointF(4.2 * u, 1.2 * u), QPointF(4.2 * u, -2.7 * u))
            p.drawLine(QPointF(-5.2 * u, 18.6 * u), QPointF(4.0 * u, 18.6 * u))

            p.end()

            # 热点在指尖与掌心之间（手"按"在头上的接触点）
            cur = QCursor(pm, 20, 14)
        except Exception:
            pass
        self._pet_cursor_cache = cur
        return cur

    def _update_head_cursor(self, x, y):
        """根据是否悬停在头部范围切换光标。"""
        try:
            if self._petting_overlay_t >= 0.0:
                self.renderer.setCursor(QCursor(Qt.BlankCursor))
                self._head_cursor_on = False
                return
            if (self._drag_off is not None or self._drag_candidate_off is not None
                    or self._fall_timer.isActive()
                    or (self._edge is not None and self._edge_hidden)):
                self._clear_head_cursor()
                return
            if self._in_head_region(x, y):
                if not getattr(self, "_head_cursor_on", False):
                    self.renderer.setCursor(self._pet_cursor())
                    self._head_cursor_on = True
            else:
                self._clear_head_cursor()
        except Exception:
            pass

    def _do_head_pat(self):
        """执行一次摸头：只播放覆盖在头顶的手势动画，宠物本体不跟着晃。"""
        self._start_petting_overlay()
        subtitle = None
        # 养成模式：记好感、按层级反馈（高优先级气泡，盖过间隔语录）
        if self.cfg.get("nurture_mode", False):
            self._head_pat_affinity_feedback(subtitle)
            return
        self._chat_manager.say_context("touch_head", allow_tts=True, interaction=True)

    def _start_petting_overlay(self):
        """启动摸头手势覆盖动画，只画手，不带动宠物窗口。"""
        self._petting_overlay_t = 0.0
        self._petting_overlay.sync_geometry()
        self._petting_overlay.setVisible(True)
        self._petting_overlay.raise_()
        self.renderer.setCursor(QCursor(Qt.BlankCursor))
        self._head_cursor_on = False
        if not self._petting_timer.isActive():
            self._petting_timer.start(33)
        self._petting_overlay.update()

    def _petting_tick(self):
        self._petting_overlay_t += 0.033 / max(0.1, self._petting_overlay_dur)
        if self._petting_overlay_t >= 1.0:
            self._petting_timer.stop()
            self._petting_overlay_t = -1.0
            self._petting_overlay.setVisible(False)
            try:
                pos = self.renderer.mapFromGlobal(QCursor.pos())
                self._update_head_cursor(pos.x(), pos.y())
            except Exception:
                self.renderer.unsetCursor()
        self._petting_overlay.update()

    def _petting_overlay_rect(self):
        """手势覆盖动画的手掌落点区域：比摸头热区略大一点，但仍只在头顶。"""
        try:
            l_in, top_inset, r_in, _ = self._content_inset()
        except Exception:
            l_in = r_in = 0
            top_inset = 0
        content_left = int(l_in)
        content_top = int(top_inset)
        content_w = max(1, self.width() - int(l_in) - int(r_in))
        content_h = max(1, self.height() - content_top)
        w = max(54, int(content_w * 0.30))
        h = max(60, int(content_h * 0.24))
        x = int(content_left + (content_w - w) / 2)
        y = max(0, int(top_inset) - int(h * 0.22))
        return QRectF(x, y, w, h)

    @staticmethod
    def _draw_petting_hand(p, rect, progress):
        """在给定区域画一只抚摸中的白手套。"""
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        arc = math.sin(progress * math.pi)
        pat = abs(math.sin(progress * 2 * math.pi * 1.2))
        sway = math.sin(progress * 2 * math.pi * 0.8) * rect.width() * 0.032
        press = pat * rect.height() * 0.11
        lift = (1.0 - arc) * rect.height() * 0.12
        squash_x = 0.94 + pat * 0.03
        squash_y = 1.02 - pat * 0.10
        p.translate(rect.center().x() + sway, rect.top() + rect.height() * 0.26 + press - lift)
        p.scale(squash_x, squash_y)
        p.rotate(math.sin(progress * 2 * math.pi * 0.8) * 4.5)

        edge = QPen(QColor("#202124"))
        edge.setWidthF(max(1.8, rect.width() * 0.024))
        edge.setJoinStyle(Qt.RoundJoin)
        edge.setCapStyle(Qt.RoundCap)
        p.setPen(edge)
        p.setBrush(QColor("#FFFFFF"))

        unit = rect.width() / 40.0
        hand = _build_glove_path(unit)

        p.fillPath(QPainterPath(hand).translated(1.0 * unit, 1.5 * unit), QColor(0, 0, 0, 54))
        p.drawPath(hand)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 214))
        p.drawEllipse(QRectF(-5.2 * unit, 4.8 * unit, 8.2 * unit, 5.2 * unit))
        p.setBrush(QColor(255, 255, 255, 190))
        for hx, hy, hw, hh in (
            (-10.1, -6.2, 2.1, 2.8),
            (-5.0, -8.8, 2.1, 3.0),
            (0.1, -8.0, 2.1, 2.9),
            (5.0, -5.5, 2.0, 2.5),
            (-14.8, 9.2, 2.2, 1.9),
        ):
            p.drawEllipse(QRectF(hx * unit, hy * unit, hw * unit, hh * unit))

        guide = QPen(QColor(176, 181, 194, 175))
        guide.setWidthF(max(0.8, rect.width() * 0.010))
        guide.setCapStyle(Qt.RoundCap)
        p.setPen(guide)
        p.drawLine(QPointF(-5.8 * unit, 1.2 * unit), QPointF(-5.8 * unit, -3.8 * unit))
        p.drawLine(QPointF(-0.8 * unit, 0.8 * unit), QPointF(-0.8 * unit, -5.2 * unit))
        p.drawLine(QPointF(4.2 * unit, 1.2 * unit), QPointF(4.2 * unit, -2.7 * unit))
        p.drawLine(QPointF(-5.2 * unit, 18.6 * unit), QPointF(4.0 * unit, 18.6 * unit))
        p.restore()

    def _nurture_quiet_action(self, action):
        """养成模式互动加分（戳身体/陪玩）：加分并在气泡提示本次收益；升级单独播报。"""
        try:
            r = self._affinity.register(action)
        except Exception:
            return
        if r.get("leveled_up"):
            self._announce_level_up(r["new_level"])
            self._refresh_tray()
            return
        self._affinity_gain_bubble(action, r)

    # 各行为获得好感时的轻台词（每次都在气泡提示一下收益）
    _GAIN_LINES = {
        "body_poke":  ["嗯？戳我做什么呀~", "诶嘿，被你戳到啦", "痒痒的~"],
        "drag_play":  ["陪你玩好开心~", "再来一局嘛！", "和你一起玩最快乐了"],
        "head_pat":   ["摸头好舒服~", "嘿嘿~", "喜欢被你摸头~"],
    }

    _MENU_SECTION_STYLE = """
        QWidget {
            color: #627d98;
            font-size: 11px;
            font-weight: 600;
            padding: 8px 12px 3px 14px;
            background: transparent;
        }
    """

    def _affinity_gain_bubble(self, action, r):
        """每次获得好感都在气泡提示：显示一句轻台词 + 「好感 +N ❤」；达上限则温柔提示。"""
        import random as _random
        line = ""
        try:
            import affinity_quotes
            lvl = self._affinity.level_index()
            addr = r.get("address") or self._affinity.address()
            if action == "body_poke":
                line = affinity_quotes.body_poke_line(lvl, addr)
            elif action == "drag_play":
                line = affinity_quotes.drag_play_line(lvl, addr)
            elif action == "head_pat":
                line = affinity_quotes.head_pat_line(lvl, addr)
        except Exception:
            line = ""
        if not line:
            line = _random.choice(self._GAIN_LINES.get(action, ["谢谢你陪我~"]))
        if r.get("gained"):
            tip = "　好感 +%d ❤" % r["gained"]
        else:
            tip = ""
        has_voice = (hasattr(self.renderer, "has_voice") and self.renderer.has_voice())
        # 戳身体 / 陪玩属于"点击宠物"互动：关掉"点击弹语录"开关后这里也应保持安静
        # （好感仍在 _nurture_quiet_action 里照常累计），并和普通点击共用同一套冷却闸。
        self._chat_manager.say(line + tip, allow_tts=not has_voice, from_click=True)

    def _affinity_action_state(self, action):
        """查看某个养成行为今天还能不能继续加分。"""
        try:
            panel = self._affinity.panel()
        except Exception:
            return "normal"
        total_cap = int(panel.get("daily_total_cap", 0) or 0)
        today_gains = int(panel.get("today_gains", 0) or 0)
        if total_cap > 0 and today_gains >= total_cap:
            return "total_full"
        for item in panel.get("actions", []):
            if item.get("key") != action:
                continue
            limit = int(item.get("limit", 0) or 0)
            used = int(item.get("used", 0) or 0)
            if limit > 0 and used >= limit:
                return "action_full"
            break
        return "normal"

    def _head_pat_affinity_feedback(self, subtitle):
        """养成模式下摸头的好感结算与气泡反馈。"""
        import affinity_quotes
        try:
            r = self._affinity.register("head_pat")
        except Exception:
            return
        # 升级优先播报
        if r.get("leveled_up"):
            self._announce_level_up(r["new_level"])
            self._refresh_tray()
            return
        addr = r.get("address", "你")
        lvl = self._affinity.level_index()
        line = affinity_quotes.head_pat_line(lvl, addr)
        if r.get("gained"):
            tip = "　好感 +%d ❤" % r["gained"]
        else:
            tip = ""
        # 模型自带语音时不再 TTS，避免和配音重复
        has_voice = (hasattr(self.renderer, "has_voice") and self.renderer.has_voice())
        # 摸头属于明确手势，不受"点击弹语录"开关限制，但仍走冷却闸防连摸刷屏
        self._chat_manager.say(line + tip, allow_tts=not has_voice, interaction=True)

    def _clear_head_cursor(self):
        """恢复默认光标，避免手型或空白光标残留。"""
        try:
            self.renderer.unsetCursor()
        except Exception:
            pass
        self._head_cursor_on = False

    def _center_window_on_screen(self, win):
        """让二级窗口弹到当前屏幕中央，别堵在宠物旁边。"""
        if win is None:
            return
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            win.ensurePolished()
            target = win.sizeHint().expandedTo(win.minimumSize())
            max_size = win.maximumSize()
            if 0 < max_size.width() < 16777215:
                target.setWidth(min(target.width(), max_size.width()))
            if 0 < max_size.height() < 16777215:
                target.setHeight(min(target.height(), max_size.height()))
            if target.width() > 0 and target.height() > 0:
                win.resize(target)
            geo = screen.availableGeometry()
            x = geo.left() + max(0, (geo.width() - win.width()) // 2)
            y = geo.top() + max(0, (geo.height() - win.height()) // 2)
            x = max(geo.left(), min(x, geo.right() - win.width() + 1))
            y = max(geo.top(), min(y, geo.bottom() - win.height() + 1))
            win.move(int(x), int(y))
        except Exception:
            pass

    def _exec_centered_dialog(self, dlg):
        self._center_window_on_screen(dlg)
        return dlg.exec()

    def _chat_settings_summary(self):
        mode = "普通"
        if self.cfg.get("nurture_mode", False):
            mode = "养成"
        elif self.cfg.get("mesugaki_mode", False):
            mode = "雌小鬼"
        elif self.cfg.get("companion_mode", False):
            mode = "伴侣"
        style = {"simple": "简约", "cute": "可爱", "pro": "专业", "dark": "深色"}.get(
            self.cfg.get("bubble_style", "cute"), "可爱")
        lo = int(self.cfg.get("chat_min_interval", 30))
        hi = int(self.cfg.get("chat_max_interval", 120))
        tts = "开" if self.cfg.get("tts_enabled", False) else "关"
        return mode, style, f"{lo}-{hi}s", tts

    def _open_chat_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("聊天气泡设置")
        dlg.setMinimumWidth(560)
        dlg.setStyleSheet(
            "QDialog{background:#f7fafc;color:#243b53;}"
            "QFrame#panel{background:#ffffff;border:1px solid #d9e2ec;border-radius:6px;}"
            "QFrame#chip{background:#eef4fa;border:1px solid #d9e2ec;border-radius:4px;}"
            "QLabel#title{color:#102a43;font-size:18px;font-weight:700;}"
            "QLabel#muted{color:#627d98;font-size:11px;}"
            "QPushButton{background:#5BB8F5;color:#fff;border:none;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#3f9fe0;}"
            "QPushButton:checked{background:#2e8bcc;}"
            "QRadioButton{spacing:6px;}"
        )

        root = QVBoxLayout(dlg)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        head = QVBoxLayout()
        title = QLabel("聊天气泡")
        title.setObjectName("title")
        head.addWidget(title)
        subtitle = QLabel("")
        subtitle.setObjectName("muted")
        head.addWidget(subtitle)
        root.addLayout(head)

        def refresh_summary():
            mode, style, interval, tts = self._chat_settings_summary()
            subtitle.setText(f"当前模式：{mode} · 样式：{style} · 间隔：{interval} · TTS：{tts}")

        summary = QFrame()
        summary.setObjectName("panel")
        summary_lay = QHBoxLayout(summary)
        summary_lay.setContentsMargins(10, 8, 10, 8)
        summary_lay.setSpacing(8)
        for text in (
            "养成模式的专属称呼只在满级后生效",
            "伴侣/雌小鬼/养成三种模式互斥",
            "语录频率会影响自动闲聊密度",
        ):
            chip = QLabel(text)
            chip.setObjectName("muted")
            chip.setStyleSheet("QLabel{background:#eef4fa;border:1px solid #d9e2ec;border-radius:4px;padding:5px 8px;}")
            summary_lay.addWidget(chip)
        summary_lay.addStretch(1)
        root.addWidget(summary)

        quick = QFrame()
        quick.setObjectName("panel")
        quick_lay = QHBoxLayout(quick)
        quick_lay.setContentsMargins(10, 8, 10, 8)
        quick_lay.setSpacing(8)
        for text, fn in (
            ("好感度面板", self._show_affinity_panel),
            ("管理语录", self._manage_quotes),
            ("TTS 试听", self._tts_preview),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            quick_lay.addWidget(b)
        quick_lay.addStretch(1)
        root.addWidget(quick)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        def add_section(col, title_text):
            box = QFrame()
            box.setObjectName("panel")
            lay = QVBoxLayout(box)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(6)
            head = QLabel(title_text)
            head.setStyleSheet("color:#102a43;font-size:12px;font-weight:700;")
            lay.addWidget(head)
            return box, lay

        general_box, general_lay = add_section(0, "常用")
        chat_toggle = QCheckBox("开启自动聊天")
        chat_toggle.setChecked(self.cfg.get("chat_enabled", True))
        chat_toggle.stateChanged.connect(lambda v: (self._toggle_chat(bool(v)), refresh_summary()))
        general_lay.addWidget(chat_toggle)
        click_toggle = QCheckBox("点击身体弹语录")
        click_toggle.setChecked(self.cfg.get("click_quote_enabled", True))
        click_toggle.stateChanged.connect(lambda v: self._toggle_click_quote(bool(v)))
        general_lay.addWidget(click_toggle)
        holiday_toggle = QCheckBox("节日 / 纪念日问候")
        holiday_toggle.setChecked(self.cfg.get("holiday_greetings", True))
        holiday_toggle.stateChanged.connect(lambda v: self._toggle_holiday_greetings(bool(v)))
        general_lay.addWidget(holiday_toggle)
        general_lay.addStretch(1)

        mode_box, mode_lay = add_section(0, "模式")
        mode_group = QButtonGroup(dlg)
        mode_group.setExclusive(True)

        def set_plain_mode():
            changed = any((
                self.cfg.get("companion_mode", False),
                self.cfg.get("mesugaki_mode", False),
                self.cfg.get("nurture_mode", False),
            ))
            self.cfg["companion_mode"] = False
            self.cfg["mesugaki_mode"] = False
            self.cfg["nurture_mode"] = False
            config.save(self.cfg)
            self._chat_manager.set_companion_mode(False)
            self._chat_manager.set_mesugaki_mode(False)
            self._chat_manager.set_nurture_mode(False)
            if changed:
                self._chat_manager.say("已切回普通聊天模式")
            self._refresh_tray()
            refresh_summary()

        plain = QRadioButton("普通模式")
        plain.setToolTip("恢复为日常聊天语录")
        plain.setChecked(not any((
            self.cfg.get("companion_mode", False),
            self.cfg.get("mesugaki_mode", False),
            self.cfg.get("nurture_mode", False),
        )))
        plain.toggled.connect(lambda checked: checked and set_plain_mode())
        mode_group.addButton(plain)
        mode_lay.addWidget(plain)

        for key, text, tip, fn in (
            ("companion", "伴侣模式", "气泡只播放情侣语录", self._toggle_companion_mode),
            ("mesugaki", "雌小鬼模式", "气泡改用傲娇调侃语气", self._toggle_mesugaki_mode),
            ("nurture", "养成模式", "好感度阶段台词与称呼", self._toggle_nurture_mode),
        ):
            row = QRadioButton(text)
            row.setChecked(self.cfg.get(f"{key}_mode", False))
            row.setToolTip(tip)
            row.toggled.connect(lambda checked, call=fn: checked and (call(True), refresh_summary()))
            mode_group.addButton(row)
            mode_lay.addWidget(row)
        mode_note = QLabel("养成模式里，专属称呼会在「灵魂羁绊」后正式启用。")
        mode_note.setObjectName("muted")
        mode_note.setWordWrap(True)
        mode_lay.addWidget(mode_note)
        mode_lay.addStretch(1)

        style_box, style_lay = add_section(1, "外观")
        for key, text in (("simple", "简约"), ("cute", "可爱"), ("pro", "专业"), ("dark", "深色")):
            row = QRadioButton(text)
            row.setChecked(self.cfg.get("bubble_style", "cute") == key)
            row.toggled.connect(lambda checked, s=key: checked and (self._set_bubble_style(s), refresh_summary()))
            style_lay.addWidget(row)
        style_lay.addStretch(1)

        freq_box, freq_lay = add_section(0, "频率")
        freq_group = QButtonGroup(dlg)
        for label, lo, hi in CHAT_INTERVAL_PRESETS:
            row = QRadioButton(label)
            row.setChecked(self.cfg.get("chat_min_interval", 30) == lo and self.cfg.get("chat_max_interval", 120) == hi)
            row.toggled.connect(lambda checked, lo=lo, hi=hi: checked and (self._set_chat_interval(lo, hi), refresh_summary()))
            freq_group.addButton(row)
            freq_lay.addWidget(row)
        freq_lay.addStretch(1)

        voice_box, voice_lay = add_section(1, "语音")
        voice_on = QRadioButton("朗读气泡文字")
        voice_on.setChecked(self.cfg.get("tts_enabled", False))
        voice_on.toggled.connect(lambda checked: checked and (self._toggle_tts(True), refresh_summary()))
        voice_off = QRadioButton("关闭朗读")
        voice_off.setChecked(not self.cfg.get("tts_enabled", False))
        voice_off.toggled.connect(lambda checked: checked and (self._toggle_tts(False), refresh_summary()))
        voice_lay.addWidget(voice_on)
        voice_lay.addWidget(voice_off)
        try:
            import tts_player as _tts
        except Exception:
            _tts = None
        if _tts is not None and _tts.tts_available():
            engine_row = QHBoxLayout()
            engine_label = QLabel("引擎")
            engine_label.setObjectName("muted")
            engine_row.addWidget(engine_label)
            engine_box = QComboBox()
            engine_box.addItem("系统语音", "auto")
            engine_box.addItem("自定义命令 / API", "custom")
            engine_box.setCurrentIndex(1 if self.cfg.get("tts_engine", "auto") == "custom" else 0)
            engine_box.currentIndexChanged.connect(
                lambda idx: self._set_tts_engine(engine_box.itemData(idx)))
            engine_row.addWidget(engine_box, 1)
            voice_lay.addLayout(engine_row)

            voice_names = _tts.list_voice_names()
            if voice_names:
                voice_row = QHBoxLayout()
                voice_label = QLabel("嗓音")
                voice_label.setObjectName("muted")
                voice_row.addWidget(voice_label)
                voice_box_sel = QComboBox()
                voice_box_sel.addItem("自动（中文优先）", "")
                cur_voice = self.cfg.get("tts_voice", "")
                current_idx = 0
                for i, nm in enumerate(voice_names, start=1):
                    voice_box_sel.addItem(nm, nm)
                    if nm == cur_voice:
                        current_idx = i
                voice_box_sel.setCurrentIndex(current_idx)
                voice_box_sel.currentIndexChanged.connect(
                    lambda idx: self._set_tts_voice(voice_box_sel.itemData(idx)))
                voice_row.addWidget(voice_box_sel, 1)
                voice_lay.addLayout(voice_row)

            vol_row = QHBoxLayout()
            vol_label = QLabel("音量")
            vol_label.setObjectName("muted")
            vol_row.addWidget(vol_label)
            vol_box = QComboBox()
            vol_items = [("静音", 0.0), ("30%", 0.3), ("50%", 0.5), ("70%", 0.7), ("100%", 1.0)]
            cur_tvol = float(self.cfg.get("tts_volume", 0.7))
            vol_idx = 0
            for i, (label, val) in enumerate(vol_items):
                vol_box.addItem(label, val)
                if abs(cur_tvol - val) < 0.01:
                    vol_idx = i
            vol_box.setCurrentIndex(vol_idx)
            vol_box.currentIndexChanged.connect(lambda idx: self._set_tts_volume(vol_box.itemData(idx)))
            vol_row.addWidget(vol_box, 1)
            voice_lay.addLayout(vol_row)

            rate_row = QHBoxLayout()
            rate_label = QLabel("语速")
            rate_label.setObjectName("muted")
            rate_row.addWidget(rate_label)
            rate_box = QComboBox()
            rate_items = [("很慢", -0.6), ("慢", -0.3), ("正常", 0.0), ("快", 0.3), ("很快", 0.6)]
            cur_rate = self._tts_rate_native()
            rate_idx = 2
            for i, (label, val) in enumerate(rate_items):
                rate_box.addItem(label, val)
                if abs(cur_rate - val) < 0.05:
                    rate_idx = i
            rate_box.setCurrentIndex(rate_idx)
            rate_box.currentIndexChanged.connect(lambda idx: self._set_tts_rate(rate_box.itemData(idx)))
            rate_row.addWidget(rate_box, 1)
            voice_lay.addLayout(rate_row)

            voice_tools = QHBoxLayout()
            for text, fn in (
                ("试听", self._tts_preview),
                ("自定义命令", self._set_tts_custom_cmd),
            ):
                b = QPushButton(text)
                b.clicked.connect(fn)
                voice_tools.addWidget(b)
            voice_tools.addStretch(1)
            voice_lay.addLayout(voice_tools)
        else:
            warn = QLabel("当前环境不支持 TTS 朗读。")
            warn.setObjectName("muted")
            voice_lay.addWidget(warn)
        voice_lay.addStretch(1)

        grid.addWidget(general_box, 0, 0)
        grid.addWidget(style_box, 0, 1)
        grid.addWidget(mode_box, 1, 0)
        grid.addWidget(freq_box, 1, 1)
        grid.addWidget(voice_box, 2, 0, 1, 2)
        root.addLayout(grid)

        close = QPushButton("关闭")
        close.clicked.connect(dlg.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close)
        root.addLayout(close_row)

        refresh_summary()
        self._exec_centered_dialog(dlg)

    def _can_show_chat_bubble(self):
        """贴边隐藏时不显示气泡；正在缩回也视为不可显示。"""
        if not self.isVisible():
            return False
        if self._drag_off is not None or self._drag_candidate_off is not None:
            return False
        if self._edge is None:
            return True
        if self._edge_hidden:
            return False
        if self._slide_timer.isActive() and getattr(self, "_slide_after_hidden", False):
            return False
        return True

    def _suspend_chat_bubble_follow(self):
        """窗口动作/摸头覆盖动画期间暂停气泡的“重测头部”跟随，避免抢刷新导致抽搐。"""
        if getattr(self, "_petting_overlay_t", -1.0) >= 0.0:
            return True
        return hasattr(self, "_act_timer") and self._act_timer.isActive()

    # ------------------------------------------------------------------ #
    #  菜单（右键 + 托盘共用）
    # ------------------------------------------------------------------ #
    def _add_menu_section(self, menu, text):
        action = QWidgetAction(menu)
        label = QLabel(text)
        label.setStyleSheet(self._MENU_SECTION_STYLE)
        action.setDefaultWidget(label)
        menu.addAction(action)
        return action

    def _populate_menu(self, m):
        # ──── 形象切换 ────
        self._add_menu_section(m, "形象")
        char_menu = m.addMenu("切换形象")
        for key, label in PIXEL_CHARS:
            a = char_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.cfg["character"] == key)
            a.triggered.connect(lambda _=False, k=key: self._set_character(k))
        if self.cfg["image_path"]:
            a = char_menu.addAction("图片宠物")
            a.setCheckable(True)
            a.setChecked(self.cfg["character"] == "image")
            a.triggered.connect(lambda _=False: self._set_character("image"))
        if self.cfg.get("live2d_model"):
            a = char_menu.addAction("Live2D：" + os.path.basename(os.path.dirname(self.cfg["live2d_model"])))
            a.setCheckable(True)
            a.setChecked(self.cfg["character"] == "live2d")
            a.triggered.connect(lambda _=False: self._set_character("live2d"))
        char_menu.addSeparator()
        char_menu.addAction("加载图片…").triggered.connect(self._choose_image)
        char_menu.addAction("选择 Live2D 模型…（带预览）").triggered.connect(self._open_live2d_picker)
        char_menu.addAction("从文件选择 Live2D…").triggered.connect(self._choose_live2d)
        self._populate_favorites_menu(char_menu)

        self._add_menu_section(m, "显示")
        size_menu = m.addMenu("大小")
        if self.cfg["character"] == "image":
            for hpx in IMAGE_SIZES:
                a = size_menu.addAction(f"{hpx}px")
                a.setCheckable(True)
                a.setChecked(self.cfg["image_size"] == hpx)
                a.triggered.connect(lambda _=False, hh=hpx: self._set_image_size(hh))
        elif self.cfg["character"] == "live2d":
            for spx in LIVE2D_SIZES:
                a = size_menu.addAction(f"{spx}px")
                a.setCheckable(True)
                a.setChecked(self.cfg["live2d_size"] == spx)
                a.triggered.connect(lambda _=False, ss=spx: self._set_live2d_size(ss))
        else:
            for s in SCALES:
                a = size_menu.addAction(f"{s}x")
                a.setCheckable(True)
                a.setChecked(self.cfg["scale"] == s)
                a.triggered.connect(lambda _=False, sc=s: self._set_scale(sc))

        # ──── 画风（仅像素角色）────
        if self.cfg["character"] not in ("image", "live2d"):
            style_menu = m.addMenu("画风（史莱姆/小猫）")
            for key, label in (("pixel", "像素"), ("smooth", "平滑（非像素）")):
                a = style_menu.addAction(label)
                a.setCheckable(True)
                a.setChecked(self.cfg["style"] == key)
                a.triggered.connect(lambda _=False, k=key: self._set_style(k))

        m.addSeparator()

        self._add_menu_section(m, "互动")
        if self.cfg["character"] == "live2d":
            a = m.addAction("看向鼠标")
            a.setCheckable(True)
            a.setChecked(self.cfg.get("follow", True))
            a.triggered.connect(self._toggle_follow)
            self._populate_live2d_action_menu(m)
            self._populate_live2d_expression_menu(m)
            self._populate_live2d_voice_menu(m)

        if self.cfg["character"] == "image":
            a = m.addAction("镜像翻转（左右朝向）")
            a.setCheckable(True)
            a.setChecked(self.cfg.get("facing", 1) < 0)
            a.triggered.connect(self._toggle_facing)
            a = m.addAction("跟随鼠标")
            a.setCheckable(True)
            a.setChecked(self.cfg.get("follow", True))
            a.triggered.connect(self._toggle_follow)
            a = m.addAction("重力掉落")
            a.setCheckable(True)
            a.setChecked(self.cfg.get("gravity", True))
            a.triggered.connect(self._toggle_gravity)
            act_menu = m.addMenu("动作")
            for key, label in (("hop", "蹦跳"), ("jump", "起跳"), ("nod", "点头"),
                               ("wiggle", "摇头"), ("tilt", "歪头"), ("lean", "侧倾"),
                               ("spin", "转身"), ("dance", "跳舞")):
                act_menu.addAction(label).triggered.connect(
                    lambda _=False, k=key: self._play_action(k))

        m.addSeparator()

        # ──── 陪它玩：轻量像素小游戏（养成模式下计入"陪它玩"好感）────
        play_menu = m.addMenu("陪它玩")
        play_menu.setToolTip("和宠物一起玩小游戏，互动升好感")
        play_menu.addAction("猜拳（石头剪刀布）…").triggered.connect(self._show_rps_game)
        play_menu.addAction("今日抽签（灵签 / 塔罗）…").triggered.connect(self._show_daily_fortune)

        # ──── 好感度面板：一级入口，养成模式核心功能 ────
        lvl_name = self._affinity.level_name()
        pts = self._affinity.data.get("points", 0)
        a = m.addAction(f"💕 好感度面板（{lvl_name} · {pts}）")
        a.setToolTip("查看好感值、今日收益、连续登录、累计摸头，并设置专属称呼")
        a.triggered.connect(self._show_affinity_panel)

        m.addSeparator()

        # ──── 聊天气泡设置 ────
        self._add_menu_section(m, "聊天与养成")
        chat_menu = m.addMenu("聊天气泡")
        a = chat_menu.addAction("打开完整设置…")
        a.setToolTip("集中管理模式、外观、频率、语音和语录")
        a.triggered.connect(self._open_chat_settings)
        chat_menu.addSeparator()
        a = chat_menu.addAction("总开关 · 开启聊天")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("chat_enabled", True))
        a.triggered.connect(self._toggle_chat)

        chat_menu.addSeparator()

        mode_menu = chat_menu.addMenu("模式")

        # 伴侣模式开关
        a = mode_menu.addAction("伴侣模式")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("companion_mode", False))
        a.setToolTip("开启后气泡只播放情侣语录（与雌小鬼模式互斥）")
        a.triggered.connect(self._toggle_companion_mode)

        # 雌小鬼模式开关（角色扮演：傲娇调侃语气，与伴侣模式互斥）
        a = mode_menu.addAction("雌小鬼模式")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("mesugaki_mode", False))
        a.setToolTip("开启后气泡用雌小鬼语气角色扮演（Ciallo～、杂鱼~ 等，与伴侣模式互斥）")
        a.triggered.connect(self._toggle_mesugaki_mode)

        # 养成模式开关（好感度系统：摸头/陪伴升好感，台词随层级变化，与上面两个互斥）
        a = mode_menu.addAction("养成模式")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("nurture_mode", False))
        a.setToolTip("好感度养成（伴侣模式升级版）：摸头/陪伴升好感，称呼与台词随 5 档层级变化")
        a.triggered.connect(self._toggle_nurture_mode)
        chat_menu.addAction("随机说一句").triggered.connect(lambda: self._chat_manager.say())
        chat_menu.addAction("管理语录...").triggered.connect(self._manage_quotes)

        m.addSeparator()

        self._add_menu_section(m, "窗口与系统")
        a = m.addAction("置于顶层")
        a.setCheckable(True)
        a.setChecked(self.cfg["always_on_top"])
        a.triggered.connect(self._toggle_top)

        a = m.addAction("不覆盖任务栏")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("avoid_taskbar", True))
        a.triggered.connect(self._toggle_avoid_taskbar)

        a = m.addAction("贴边隐藏（拖到屏幕边缘自动收起）")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("edge_snap", True))
        a.triggered.connect(self._toggle_edge_snap)

        a = m.addAction("鼠标穿透（不可拖动）")
        a.setCheckable(True)
        a.setChecked(self.cfg["click_through"])
        a.triggered.connect(self._toggle_click_through)

        m.addSeparator()

        # ──── 模型文件夹设置 ────
        m.addAction("设置模型文件夹…").triggered.connect(self._set_models_dir)

        current_dir = self.cfg.get("models_dir", "")
        if current_dir:
            dir_name = os.path.basename(current_dir)
            m.addAction(f"当前：{dir_name}").setEnabled(False)
        else:
            m.addAction("当前：默认（程序目录）").setEnabled(False)

        m.addSeparator()

        a = m.addAction("开机自启动")
        a.setCheckable(True)
        a.setChecked(system.is_autostart())
        a.triggered.connect(self._toggle_autostart)

        a = m.addAction("快捷键 Ctrl+Alt+P：显示/隐藏")
        a.setEnabled(False)

        m.addSeparator()
        m.addAction("关于 / 帮助…").triggered.connect(self._show_about)
        m.addAction("退出").triggered.connect(self._quit)

    def _populate_live2d_action_menu(self, m):
        """Live2D 动作菜单：按模型自己的动作组分子菜单列出（组内按文件去重，
        数量贴近模型真实动作数），不再把所有动作摊平成一长串。"""
        sub = m.addMenu("动作")

        groups = []
        if hasattr(self.renderer, "motion_menu"):
            try:
                groups = self.renderer.motion_menu()
            except Exception:
                groups = []

        if groups:
            sub.addAction("随机动作").triggered.connect(lambda: self._l2d_play("", None))
            sub.addSeparator()
            for group, items in groups:
                label = MOTION_GROUP_LABELS.get(group.lower(), group) if group else "默认"
                # 组内只有一条动作：直接放在顶级，点了就播放（含配音）
                if len(items) == 1:
                    idx = items[0]["index"]
                    sub.addAction(label).triggered.connect(
                        lambda _=False, g=group, i=idx: self._l2d_play(g, i))
                    continue
                gsub = sub.addMenu(f"{label}（{len(items)}）")
                gsub.addAction("随机播放").triggered.connect(
                    lambda _=False, g=group: self._l2d_play_group(g))
                gsub.addSeparator()
                for it in items:
                    gsub.addAction(it["label"]).triggered.connect(
                        lambda _=False, g=group, i=it["index"]: self._l2d_play(g, i))
        else:
            tip = sub.addAction("（该模型未提供动作）")
            tip.setEnabled(False)

        # ── 自动播放设置：勾选哪些动作参与待机自动循环（取消勾选=禁用其自动播放）──
        if groups:
            sub.addSeparator()
            auto_sub = sub.addMenu("自动播放设置")
            auto_sub.setToolTip("取消勾选的动作不再自动循环播放，但仍可在上方手动触发")
            disabled = self._disabled_motions_set()
            tip = auto_sub.addAction("勾选=参与自动循环；取消=禁止自动播放")
            tip.setEnabled(False)
            auto_sub.addSeparator()
            for group, items in groups:
                glabel = MOTION_GROUP_LABELS.get(group.lower(), group) if group else "默认"
                if len(items) > 1:
                    auto_sub.addSeparator()
                    htip = auto_sub.addAction("— %s —" % glabel)
                    htip.setEnabled(False)
                for it in items:
                    idx = it["index"]
                    mkey = f"{group}/{idx}"
                    label = it["label"] if len(items) > 1 else glabel
                    a = auto_sub.addAction(label)
                    a.setCheckable(True)
                    a.setChecked(mkey not in disabled)
                    a.triggered.connect(
                        lambda checked, g=group, i=idx: self._toggle_motion_auto(g, i, checked))

        # ── 分隔线 + 软件内置窗口动作 ──
        sub.addSeparator()
        win_sub = sub.addMenu("内置动作")

        # 点击宠物时触发的动作：可换成别的动作，或关闭（点击不再"跳"）
        click_sub = win_sub.addMenu("点击宠物时")
        enabled = (self.cfg.get("click_action_enabled", True)
                   and not self.cfg.get("nurture_mode", False))
        cur_click = self.cfg.get("click_action", "hop")
        off = click_sub.addAction("关闭（点击不动作）")
        off.setCheckable(True)
        off.setChecked(not enabled)
        if self.cfg.get("nurture_mode", False):
            off.setText("关闭（养成模式下点击不动作）")
        off.triggered.connect(lambda _=False: self._set_click_action(None))
        click_sub.addSeparator()
        for key, label in ACTION_ITEMS:
            a = click_sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(enabled and cur_click == key)
            if self.cfg.get("nurture_mode", False):
                a.setEnabled(False)
            a.triggered.connect(lambda _=False, k=key: self._set_click_action(k))

        win_sub.addSeparator()
        # 立即播放一次某个内置动作
        for key, label in ACTION_ITEMS:
            win_sub.addAction(label).triggered.connect(
                lambda _=False, k=key: self._play_action(k))

    def _l2d_play(self, group, index):
        if hasattr(self.renderer, "play_motion"):
            self.renderer.play_motion(group, index)

    def _l2d_play_group(self, group):
        if hasattr(self.renderer, "play_group_random"):
            self.renderer.play_group_random(group)

    def _set_click_action(self, name):
        """设置点击宠物时触发的内置动作；name=None 表示关闭（点击不再触发动作）。"""
        if name is None:
            self.cfg["click_action_enabled"] = False
        else:
            self.cfg["click_action_enabled"] = True
            self.cfg["click_action"] = name
        config.save(self.cfg)

    # ── 自动播放动作的禁用管理（按模型记） ──
    def _disabled_motions_set(self):
        """当前模型「禁止自动播放」的动作键集合（"组名/索引"）。"""
        path = self.cfg.get("live2d_model", "")
        if not path:
            return set()
        store = self.cfg.get("disabled_auto_motions") or {}
        return set(store.get(_canon_path(path), []))

    def _apply_disabled_motions(self):
        """把当前模型的禁用集合下发给渲染器。"""
        if hasattr(self.renderer, "set_disabled_motions"):
            try:
                self.renderer.set_disabled_motions(self._disabled_motions_set())
            except Exception:
                pass

    def _toggle_motion_auto(self, group, index, auto_enabled):
        """切换某条动作是否参与自动循环播放。auto_enabled=False 即禁用其自动播放。"""
        path = self.cfg.get("live2d_model", "")
        if not path:
            return
        key = _canon_path(path)
        store = self.cfg.setdefault("disabled_auto_motions", {})
        disabled = set(store.get(key, []))
        mkey = f"{group}/{index}"
        if auto_enabled:
            disabled.discard(mkey)
        else:
            disabled.add(mkey)
        if disabled:
            store[key] = sorted(disabled)
        elif key in store:
            del store[key]
        config.save(self.cfg)
        self._apply_disabled_motions()

    def _populate_live2d_expression_menu(self, m):
        """列出当前 Live2D 模型的表情（exp3/exp），供手动切换或自动轮播。"""
        sub = m.addMenu("表情")
        exprs = []
        if hasattr(self.renderer, "list_expressions"):
            try:
                exprs = self.renderer.list_expressions()
            except Exception:
                exprs = []
        if not exprs:
            tip = sub.addAction("（该模型没有表情文件）")
            tip.setEnabled(False)
            return
        sub.addAction("随机表情").triggered.connect(self._l2d_random_expression)
        sub.addAction("清除表情").triggered.connect(self._l2d_reset_expression)
        auto = sub.addAction("自动随机切换")
        auto.setCheckable(True)
        auto.setChecked(self.cfg.get("live2d_auto_expression", False))
        auto.triggered.connect(self._toggle_auto_expression)
        sub.addSeparator()
        for eid in exprs:
            label = eid
            for suf in (".exp3.json", ".exp.json", ".json"):
                if label.lower().endswith(suf):
                    label = label[: -len(suf)]
                    break
            sub.addAction(label).triggered.connect(
                lambda _=False, e=eid: self._l2d_set_expression(e))

    def _l2d_set_expression(self, eid):
        if hasattr(self.renderer, "set_expression"):
            self.renderer.set_expression(eid)

    def _l2d_random_expression(self):
        if hasattr(self.renderer, "set_random_expression"):
            self.renderer.set_random_expression()

    def _l2d_reset_expression(self):
        if hasattr(self.renderer, "reset_expression"):
            self.renderer.reset_expression()

    def _toggle_auto_expression(self, checked):
        self.cfg["live2d_auto_expression"] = bool(checked)
        config.save(self.cfg)
        if hasattr(self.renderer, "set_auto_expression"):
            self.renderer.set_auto_expression(bool(checked))

    def _populate_live2d_voice_menu(self, m):
        """模型语音菜单：仅当当前模型带有 voice/*.wav 时才显示。"""
        if not (hasattr(self.renderer, "has_voice") and self.renderer.has_voice()):
            return
        sub = m.addMenu("语音")
        a = sub.addAction("开启语音")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("voice_enabled", True))
        a.triggered.connect(self._toggle_voice)

        a = sub.addAction("气泡语录同步发声")
        a.setCheckable(True)
        a.setChecked(self.cfg.get("voice_with_quote", False))
        a.setToolTip("开启后，宠物弹出语录气泡时也会随机播放一句模型语音")
        a.triggered.connect(self._toggle_voice_with_quote)

        # 音量级别（winsound 没有连续音量，这里给几档常用值）
        vol_menu = sub.addMenu("音量")
        cur_vol = float(self.cfg.get("voice_volume", 0.5))
        for label, val in (("静音", 0.0), ("小（30%）", 0.3), ("中（50%）", 0.5),
                           ("大（75%）", 0.75), ("最大（100%）", 1.0)):
            a = vol_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(cur_vol - val) < 0.01)
            a.triggered.connect(lambda _=False, v=val: self._set_voice_volume(v))

        sub.addSeparator()
        sub.addAction("试听一句").triggered.connect(self._l2d_play_voice)

    def _toggle_voice(self, checked):
        self.cfg["voice_enabled"] = bool(checked)
        config.save(self.cfg)
        if hasattr(self.renderer, "set_voice_enabled"):
            self.renderer.set_voice_enabled(bool(checked))

    def _toggle_voice_with_quote(self, checked):
        self.cfg["voice_with_quote"] = bool(checked)
        config.save(self.cfg)

    def _set_voice_volume(self, v):
        self.cfg["voice_volume"] = float(v)
        config.save(self.cfg)
        if hasattr(self.renderer, "set_voice_volume"):
            self.renderer.set_voice_volume(float(v))
        # 调完音量顺手试听一下，给个反馈
        if v > 0 and hasattr(self.renderer, "play_voice_random"):
            self.renderer.play_voice_random()

    def _l2d_play_voice(self):
        if hasattr(self.renderer, "play_voice_random"):
            self.renderer.play_voice_random()

    def _on_pet_voice(self):
        """气泡语录同步发声：宠物说话时，若两个开关都开且是 Live2D 模型，随机放一句语音。"""
        if not (self.cfg.get("voice_enabled", True) and self.cfg.get("voice_with_quote", False)):
            return
        if self.cfg.get("character") != "live2d":
            return
        if hasattr(self.renderer, "play_voice_random"):
            try:
                self.renderer.play_voice_random()
            except Exception:
                pass

    def _on_voice_with_text(self, sound_path, text, duration_ms=None):
        """播放语音时显示对应的字幕：气泡停留时长尽量与语音长度对齐；
        同时刷新自动语录计时，避免紧接着又弹一条间隔语录与字幕打架。"""
        if not text:
            return
        if duration_ms and duration_ms > 0:
            dur = max(2000, min(8000, int(duration_ms) + 400))   # 语音时长 + 一点余量
        else:
            dur = max(3000, min(8000, 2500 + len(text) * 150))   # 拿不到时长就按字数估算
        # 使用聊天气泡显示字幕
        if hasattr(self, '_chat_bubble') and self._chat_bubble:
            try:
                self._chat_bubble.show_message(text, duration=dur)
            except Exception:
                pass
        # 语音字幕占用了气泡 → 顺延下一次间隔语录，避免冲突
        if hasattr(self, '_chat_manager') and self._chat_manager:
            try:
                self._chat_manager.notify_external_speak()
            except Exception:
                pass

    def _l2d_view_of(self, path):
        """取某模型已保存的构图/尺寸/位置；ratio 缺省 None 表示按模型画布自动定高。"""
        v = (self.cfg.get("live2d_views") or {}).get(_canon_path(path)) or {}
        r = v.get("ratio", None)
        pos = v.get("pos")
        return {"zoom": float(v.get("zoom", 1.0)),
                "xoff": float(v.get("xoff", 0.0)),
                "yoff": float(v.get("yoff", 0.0)),
                "ratio": (float(r) if r else None),
                "size": (int(v["size"]) if v.get("size") else None),
                "pos": ([int(pos[0]), int(pos[1])]
                        if isinstance(pos, (list, tuple)) and len(pos) == 2 else None)}

    def _default_live2d_view(self, path, size_hint=None):
        """新模型首次出现时的默认构图：各模型独立尺寸，构图归零，画布比例自动。"""
        model_mem = self._get_model_memory(f"live2d:{_canon_path(path)}")
        size = model_mem.get("size") or size_hint or DEFAULT_LIVE2D_SIZE
        return {"zoom": 1.0, "xoff": 0.0, "yoff": 0.0, "ratio": None,
                "size": int(size), "pos": None}

    def _resolve_live2d_state(self, path, size_hint=None):
        """合并模型记忆和构图配置，得到当前应使用的完整 Live2D 状态。"""
        v = self._l2d_view_of(path)
        base = self._default_live2d_view(path, size_hint=size_hint)
        for key in ("zoom", "xoff", "yoff", "ratio", "size", "pos"):
            if v.get(key) is not None:
                base[key] = v[key]
        return base

    def _l2d_save_view(self, path, zoom, xoff, yoff, ratio, size=None, pos=None):
        """按模型保存构图(zoom/xoff/yoff/ratio)；size/pos 仅在显式传入时更新、否则保留原值。"""
        if not path:
            return
        views = self.cfg.setdefault("live2d_views", {})
        key = _canon_path(path)
        entry = dict(views.get(key) or {})       # 合并而非覆盖：不丢已存的 size/pos
        entry.update({"zoom": round(float(zoom), 3),
                      "xoff": round(float(xoff), 3),
                      "yoff": round(float(yoff), 3),
                      "ratio": (round(float(ratio), 3) if ratio else None)})
        if size is not None:
            entry["size"] = int(size)
        if pos is not None:
            entry["pos"] = [int(pos[0]), int(pos[1])]
        views[key] = entry
        config.save(self.cfg)

    def _l2d_view(self, zoom_mul, dxoff, dyoff):
        path = self.cfg.get("live2d_model")
        v = self._l2d_view_of(path)
        v["zoom"] = max(0.2, min(5.0, v["zoom"] * zoom_mul))
        v["xoff"] = max(-2.0, min(2.0, v["xoff"] + dxoff))
        v["yoff"] = max(-2.0, min(2.0, v["yoff"] + dyoff))
        self._l2d_save_view(path, v["zoom"], v["xoff"], v["yoff"], v["ratio"])
        if hasattr(self.renderer, "set_view"):
            self.renderer.set_view(v["zoom"], v["xoff"], v["yoff"])

    def _l2d_view_reset(self):
        path = self.cfg.get("live2d_model")
        v = self._l2d_view_of(path)
        self._l2d_save_view(path, 1.0, 0.0, 0.0, v["ratio"])
        if hasattr(self.renderer, "set_view"):
            self.renderer.set_view(1.0, 0.0, 0.0)

    def _l2d_fit_region(self, top, bottom):
        """对当前 Live2D 模型贴合指定竖直区间（全身/半身/上半身/头肩），并按模型保存。"""
        if self.cfg.get("character") != "live2d":
            return
        r = self.renderer
        if not hasattr(r, "fit_to_content"):
            return
        if r.fit_to_content(top, bottom):
            z, x, y = r.get_view()
            self._l2d_save_view(self.cfg.get("live2d_model"), z, x, y, r.height_ratio())
            self._fit_window_to_renderer()

    def _l2d_set_ratio(self, ratio):
        """调整画布高度：ratio=None 表示按模型画布自动；否则手动比例（按模型记忆）。"""
        path = self.cfg.get("live2d_model")
        v = self._l2d_view_of(path)
        self._l2d_save_view(path, v["zoom"], v["xoff"], v["yoff"], ratio)
        if self.cfg.get("character") != "live2d":
            return
        if ratio is None:
            self._build_renderer()        # 回到自动需按画布重算
        elif hasattr(self.renderer, "set_height_ratio"):
            self.renderer.set_height_ratio(ratio)
            self._resize_keep_anchor(self.renderer.natural_size())

    def _fit_window_to_renderer(self):
        """渲染器自适应画布比例后，把窗口重新贴合（脚下位置不变）。"""
        if self.renderer is not None:
            natural_size = getattr(self.renderer, "natural_size", None)
            if not callable(natural_size):
                return
            self._resize_keep_anchor(natural_size())
            self._petting_overlay.sync_geometry()
            QTimer.singleShot(0, self._sync_input_mask)
            QTimer.singleShot(240, self._sync_input_mask)

    def _show_menu(self, global_pos):
        m = QMenu()
        self._populate_menu(m)
        m.exec(global_pos)

    def _tray_icon(self):
        c = self.cfg["character"]
        if c == "image" and self.cfg["image_path"]:
            pm = QPixmap(self.cfg["image_path"])
            if not pm.isNull():
                return QIcon(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        name = c if c in CHARACTERS else "slime"
        return QIcon(render_icon(name))

    def _build_tray(self):
        tray = QSystemTrayIcon(self)
        tray.setIcon(self._tray_icon())
        tray.setToolTip("桌面宠物")
        menu = QMenu()
        self._fill_tray_menu(menu)
        # 关键：每次托盘菜单弹出前都重新填充，保证勾选状态（尤其"鼠标穿透"）实时同步。
        # 否则托盘菜单是开机时建好的、勾选状态会滞后，导致开启穿透后必须点两次才能关闭。
        menu.aboutToShow.connect(lambda: self._fill_tray_menu(menu))
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        return tray

    def _fill_tray_menu(self, menu):
        """清空并重新填充托盘菜单（就地刷新，不销毁菜单本身）。"""
        menu.clear()
        menu.addAction("显示 / 隐藏").triggered.connect(self._toggle_visible)
        menu.addSeparator()
        self._populate_menu(menu)

    def _refresh_tray(self):
        # 重建托盘菜单以反映最新勾选状态/图标
        old = self.tray.contextMenu()
        if old is not None:
            old.deleteLater()
        menu = QMenu()
        self._fill_tray_menu(menu)
        menu.aboutToShow.connect(lambda: self._fill_tray_menu(menu))
        self.tray.setContextMenu(menu)
        self.tray.setIcon(self._tray_icon())

    # ------------------------------------------------------------------ #
    #  菜单动作
    # ------------------------------------------------------------------ #
    def _set_character(self, key):
        """切换角色：显示加载提示，异步加载模型避免卡顿。"""
        self.cfg["character"] = key
        config.save(self.cfg)

        # 显示"加载中"提示
        if hasattr(self, '_chat_bubble') and self._chat_bubble:
            self._chat_bubble.show_message("加载中...⏳", duration=5000)

        # 异步加载避免UI卡顿
        QTimer.singleShot(0, self._do_rebuild_and_restore)

    def _do_rebuild_and_restore(self):
        """真正执行重建和恢复（延迟执行避免UI卡顿）。"""
        if self.cfg.get("character") == "live2d" and self.cfg.get("live2d_model"):
            self._switch_character_fast("live2d", self.cfg.get("live2d_model"))
        else:
            self._rebuild_and_restore_pos()
        self._refresh_tray()

        # 加载完成后显示欢迎消息（之前误传 "greeting_morning"，会把这串原文显示出来）
        if hasattr(self, '_chat_manager') and self._chat_manager:
            QTimer.singleShot(500, lambda: self._chat_manager.say("你好呀，我换了个新形象~"))

    def _set_scale(self, scale):
        self.cfg["scale"] = scale
        config.save(self.cfg)
        if self.cfg["character"] in CHARACTERS:
            self.renderer.set_scale(scale)
            self._resize_keep_anchor(self.renderer.natural_size())
            # 按当前像素角色记住缩放倍数
            self._save_model_memory(size=scale)

    def _set_style(self, style):
        self.cfg["style"] = style
        config.save(self.cfg)
        if isinstance(self.renderer, PixelPet):
            self.renderer.set_style(style)
        self._refresh_tray()

    def _choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择图片（建议透明 PNG）", "", IMG_FILTER)
        if not path:
            return
        path = self._persist_image(path)      # 拷进 ~/.desktop-pet/images，防止原图丢失
        self.cfg["image_path"] = path
        self.cfg["character"] = "image"
        config.save(self.cfg)
        self._switch_character_fast("image")
        self._refresh_tray()

    def _persist_image(self, path):
        """把选中的图片复制到 ~/.desktop-pet/images/ 下，返回新路径（失败则原样返回）。"""
        try:
            dst_dir = os.path.join(config.CONFIG_DIR, "images")
            os.makedirs(dst_dir, exist_ok=True)
            base = os.path.basename(path)
            stem, ext = os.path.splitext(base)
            dst = os.path.join(dst_dir, base)
            # 已经在目录里就不重复拷；同名不同文件则改名避免覆盖
            if os.path.abspath(path) == os.path.abspath(dst):
                return path
            i = 1
            while os.path.exists(dst):
                dst = os.path.join(dst_dir, f"{stem}_{i}{ext}")
                i += 1
            shutil.copy2(path, dst)
            return dst
        except OSError:
            return path

    # ------------------------------------------------------------------ #
    #  常用 / 收藏
    # ------------------------------------------------------------------ #
    def _current_fav(self):
        """当前形象对应的收藏条目（不在收藏里也返回其描述），否则 None。"""
        c = self.cfg.get("character")
        if c == "image" and self.cfg.get("image_path"):
            p = self.cfg["image_path"]
            return {"type": "image", "path": p, "name": os.path.splitext(os.path.basename(p))[0]}
        if c == "live2d" and self.cfg.get("live2d_model"):
            p = self.cfg["live2d_model"]
            return {"type": "live2d", "path": p,
                    "name": os.path.basename(os.path.dirname(p)) or os.path.basename(p)}
        return None

    def _seed_builtin_views(self):
        """把内置模型的推荐构图种进 live2d_views（仅当用户没为该模型存过构图）。"""
        views = self.cfg.setdefault("live2d_views", {})
        changed = False
        for f in BUILTIN_FAVORITES:
            v = BUILTIN_VIEWS.get(f.get("rel"))
            if not v:
                continue
            p = _canon_path(_builtin_fav_path(f))
            if os.path.exists(p) and p not in views:
                views[p] = dict(v)
                changed = True
        if changed:
            config.save(self.cfg)

    def _migrate_view_keys(self):
        """把旧 live2d_views 的键统一成规范化路径（解析 junction/大小写/分隔符）。

        历史配置里同一个模型可能以 dist 的 junction 路径、源码路径、不同大小写各存了一份，
        互相不认导致"位置/大小/显示区域记不住"。这里一次性合并去重，键改成 _canon_path。
        """
        views = self.cfg.get("live2d_views")
        if not isinstance(views, dict) or not views:
            return
        new = {}
        changed = False
        for k, v in views.items():
            ck = _canon_path(k)
            if ck != k:
                changed = True
            if ck in new:                       # 撞键：保留信息更全的那份（含 ratio/size/pos）
                if len(v) > len(new[ck]):
                    new[ck] = v
                changed = True
            else:
                new[ck] = v
        if changed:
            self.cfg["live2d_views"] = new
            config.save(self.cfg)

    # ------------------------------------------------------------------ #
    #  Live2D 模型扫描缓存（后台预扫，打开选择器免卡顿）
    # ------------------------------------------------------------------ #
    def _start_bg_scan(self):
        """后台线程扫描 live2d/ 目录（纯文件 I/O，安全）。只更新一个普通属性，不碰 Qt 控件。"""
        if self._l2d_scanning:
            return
        self._l2d_scanning = True

        def work():
            try:
                from live2d_pet import discover_models, model_features
                models_dir = get_models_dir()
                models = discover_models(models_dir)
                for _group, _variant, path in models:
                    key = _canon_path(path)
                    try:
                        stat = os.stat(path)
                        stamp = (stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        stamp = None
                    if key in self._l2d_feature_cache and self._l2d_feature_cache[key].get("stamp") == stamp:
                        continue
                    try:
                        self._l2d_feature_cache[key] = {
                            "stamp": stamp,
                            "feat": dict(model_features(path)),
                        }
                    except Exception:
                        pass
            except Exception:
                models = []
            self._l2d_models = models
            self._l2d_scanning = False

        threading.Thread(target=work, daemon=True).start()

    def _get_model_features_cached(self, model_path):
        """缓存读取模型能力，避免模型选择器每次打开都重扫 JSON / 目录。"""
        key = _canon_path(model_path)
        try:
            stat = os.stat(model_path)
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        cached = self._l2d_feature_cache.get(key)
        if cached and cached.get("stamp") == stamp:
            return dict(cached.get("feat") or {})
        from live2d_pet import model_features
        feat = model_features(model_path)
        self._l2d_feature_cache[key] = {"stamp": stamp, "feat": dict(feat)}
        return feat

    def _get_models(self, force=False):
        """取模型列表：优先用缓存；缓存还没好(或强制)时同步扫一次并回填缓存。"""
        from live2d_pet import discover_models
        if force or self._l2d_models is None:
            models_dir = get_models_dir()
            models = discover_models(models_dir)
            self._l2d_models = models
            if force:
                self._l2d_feature_cache = {}
            return models
        return self._l2d_models if self._l2d_models else []
        return self._l2d_models

    def _pinned_model_paths(self, models=None):
        """选择器里要置顶的"常用"模型的规范化路径集合：收藏的 + 丢进"常用"文件夹的。"""
        pinned = set()
        for f in self._all_favorites():
            if f.get("type") == "live2d" and f.get("path"):
                pinned.add(_canon_path(f["path"]))
        # 物理放进"常用"文件夹里的模型也置顶——用户直接把模型文件夹拷进去即可，无需收藏。
        fav_dir = get_fav_dir()
        if models is None:
            models = self._l2d_models or []
        for m in models:
            try:
                path = m[2]            # discover_models 返回 (group, variant, path)
            except (IndexError, TypeError, KeyError):
                continue
            if path and _under_dir(path, fav_dir):
                pinned.add(_canon_path(path))
        return pinned

    @staticmethod
    def _fav_key(fav):
        """收藏去重键：解析 junction/软链 + 忽略大小写，避免同一模型(内置 vs 用户/不同写法)重复出现。"""
        p = fav.get("path", "") or ""
        try:
            p = os.path.realpath(p)
        except Exception:
            p = os.path.normpath(p)
        return (fav.get("type"), os.path.normcase(p))

    def _all_favorites(self):
        """内置常用宠物 + 用户自己收藏，按 (类型,路径) 去重（内置在前）。"""
        out = [dict(f) for f in self._builtin_favs]
        seen = {self._fav_key(f) for f in out}
        for f in (self.cfg.get("favorites") or []):
            k = self._fav_key(f)
            if k not in seen:
                seen.add(k)
                out.append(f)
        return out

    def _is_builtin_fav(self, fav):
        return fav is not None and self._fav_key(fav) in {self._fav_key(b) for b in self._builtin_favs}

    def _fav_index(self, fav):
        """在"用户自己收藏"里的下标（内置不算，内置删不掉）。"""
        favs = self.cfg.get("favorites") or []
        key = self._fav_key(fav)
        for i, f in enumerate(favs):
            if self._fav_key(f) == key:
                return i
        return -1

    def _populate_favorites_menu(self, parent):
        sub = parent.addMenu("常用宠物")
        cur = self._current_fav()
        allf = self._all_favorites()
        curkey = self._fav_key(cur) if cur else None
        for f in allf:
            tag = "🖼" if f.get("type") == "image" else "🎭"
            a = sub.addAction(f"{tag} {f.get('name','?')}")
            a.setCheckable(True)
            a.setChecked(curkey is not None and self._fav_key(f) == curkey)
            a.triggered.connect(lambda _=False, fav=f: self._switch_favorite(fav))
        sub.addSeparator()
        in_all = curkey is not None and curkey in {self._fav_key(f) for f in allf}
        add = sub.addAction("＋ 收藏当前形象")
        add.setEnabled(cur is not None and not in_all)
        add.triggered.connect(self._add_favorite)
        rm = sub.addAction("－ 取消收藏当前")
        rm.setEnabled(cur is not None and self._fav_index(cur) >= 0)   # 只能取消用户自己加的，内置删不掉
        rm.triggered.connect(self._remove_favorite)

    def _add_favorite(self):
        cur = self._current_fav()
        if not cur or self._fav_index(cur) >= 0:
            return
        self.cfg.setdefault("favorites", []).append(cur)
        config.save(self.cfg)
        self._refresh_tray()

    def _remove_favorite(self):
        cur = self._current_fav()
        if not cur:
            return
        i = self._fav_index(cur)
        if i >= 0:
            self.cfg["favorites"].pop(i)
            config.save(self.cfg)
            self._refresh_tray()

    def _switch_favorite(self, fav):
        if fav.get("type") == "image" and os.path.exists(fav["path"]):
            self.cfg["image_path"] = fav["path"]
            self.cfg["character"] = "image"
        elif fav.get("type") == "live2d" and os.path.exists(fav["path"]):
            self.cfg["live2d_model"] = fav["path"]
            self.cfg["character"] = "live2d"
        else:
            self.tray.showMessage("常用宠物", "文件已不存在：\n%s" % fav.get("path", ""),
                                  QSystemTrayIcon.Warning, 3000)
            return
        config.save(self.cfg)
        self._rebuild_and_restore_pos()
        self._refresh_tray()

    def _set_image_size(self, h):
        self.cfg["image_size"] = h
        config.save(self.cfg)
        if isinstance(self.renderer, ImagePet):
            self.renderer.set_image_size(h)
            self._resize_keep_anchor(self.renderer.natural_size())
            # 按当前图片宠物记住尺寸
            self._save_model_memory(size=h)

    def _toggle_facing(self, checked):
        self.cfg["facing"] = -1 if checked else 1
        config.save(self.cfg)
        if isinstance(self.renderer, ImagePet):
            self.renderer.set_facing(self.cfg["facing"])

    def _play_action(self, action):
        if isinstance(self.renderer, ImagePet):
            self.renderer.play(action)
        else:
            # Live2D / 像素：用移动整窗的方式做动作；Live2D 再顺带触发模型自带动作
            self._play_window_action(action)
            if hasattr(self.renderer, "play_motion"):
                try:
                    self.renderer.play_motion("", None)
                except Exception:
                    pass

    # --- 通用"窗口动作"：移动整窗实现 起跳/点头/跳舞…，任何渲染器都能用 ---
    def _play_window_action(self, name):
        if (self._drag_off is not None or self._drag_candidate_off is not None
                or self._fall_timer.isActive()):
            return
        if self._edge is not None:      # 吸在边上时不做"移动整窗"的动作，免得脱离边缘
            return
        if name not in ACTION_DUR:
            return
        # 若上一个动作还在跑，沿用它的起点，避免连点导致窗口越跳越偏
        if not (self._act_timer.isActive() and self._act_home is not None):
            self._act_home = self.pos()
            # 记录气泡初始位置，用于动画过程中的相对偏移
            if hasattr(self, "_chat_bubble") and self._chat_bubble.isVisible():
                self._chat_bubble._last_bubble_pos = (self._chat_bubble.x(), self._chat_bubble.y())
                # 暂停气泡的自动跟随，避免在动画期间调用 refresh_content_box 影响性能
                if hasattr(self._chat_bubble, "_follow_timer") and self._chat_bubble._follow_timer.isActive():
                    self._chat_bubble._follow_timer.stop()
                    self._chat_bubble._follow_paused = True
        self._act_name = name
        self._act_t = 0.0
        self._act_dur = ACTION_DUR.get(name, 0.7)
        if not self._act_timer.isActive():
            self._act_timer.start(33)

    def _cancel_action(self):
        if hasattr(self, "_act_timer") and self._act_timer.isActive():
            self._act_timer.stop()
        self._act_name = None
        self._act_home = None
        # 清理气泡位置缓存并恢复自动跟随
        if hasattr(self, "_chat_bubble"):
            if hasattr(self._chat_bubble, "_last_bubble_pos"):
                delattr(self._chat_bubble, "_last_bubble_pos")
            # 恢复气泡的自动跟随定时器
            if getattr(self._chat_bubble, "_follow_paused", False):
                self._chat_bubble._follow_paused = False
                if hasattr(self._chat_bubble, "_follow_timer") and self._chat_bubble.isVisible():
                    self._chat_bubble._follow_timer.start(self._chat_bubble._follow_interval)

    def _act_tick(self):
        if (self._act_name is None or self._act_home is None
                or self._drag_off is not None or self._drag_candidate_off is not None):
            self._cancel_action()
            return
        self._act_t += 0.033 / max(0.1, self._act_dur)
        hx, hy = self._act_home.x(), self._act_home.y()
        if self._act_t >= 1.0:
            self.move(hx, self._clamp_y(hy))      # 回到起点
            # 动画结束时更新气泡位置
            if hasattr(self, "_chat_bubble") and self._chat_bubble.isVisible():
                self._chat_bubble.update_position()
            self._cancel_action()
            self._save_pos()
            return
        dx, dy = self._action_offset(self._act_name, self._act_t)
        new_y = self._clamp_y(hy + int(round(dy)))
        self.move(hx + int(round(dx)), new_y)
        # 动画过程中使用轻量级的位置同步，只让气泡跟随宠物偏移，避免每帧重新计算。
        # 气泡若已锁定（猜拳），保持原位不随动作偏移，避免抽搐。
        if (hasattr(self, "_chat_bubble") and self._chat_bubble.isVisible()
                and not getattr(self._chat_bubble, "_follow_locked", False)):
            # 直接计算气泡应该移动的偏移量，不重新计算智能定位
            bubble_dx = int(round(dx))
            bubble_dy = new_y - hy  # 使用实际移动的y偏移（考虑了clamp）
            if hasattr(self._chat_bubble, "_last_bubble_pos"):
                base_x, base_y = self._chat_bubble._last_bubble_pos
            else:
                base_x, base_y = self._chat_bubble.x(), self._chat_bubble.y()
                self._chat_bubble._last_bubble_pos = (base_x, base_y)
            self._chat_bubble.move(base_x + bubble_dx, base_y + bubble_dy)

    def _action_offset(self, name, p):
        """动作在窗口像素上的 (dx, dy) 偏移；按模型大小等比缩放。"""
        k = max(0.6, min(2.2, self.height() / 240.0))
        up = math.sin(math.pi * p)
        dx = dy = 0.0
        if name == "jump":
            dy = -64 * up
        elif name == "hop":
            dy = -26 * up
        elif name == "nod":
            w = abs(math.sin(p * 2 * math.pi * 2)) * (1 - p)
            dy = -12 * w
        elif name == "wiggle":
            dx = 16 * math.sin(p * 2 * math.pi * 3) * (1 - p)
        elif name == "tilt":
            dx = 12 * math.sin(math.pi * p)
        elif name == "lean":
            dx = 18 * math.sin(math.pi * p)
        elif name == "spin":
            dx, dy = 22 * math.sin(2 * math.pi * p), -10 * up
        elif name == "dance":
            dx = 14 * math.sin(p * 2 * math.pi * 2)
            dy = -14 * abs(math.sin(p * 2 * math.pi * 2))
        elif name == "pat":
            # 被摸头：头部轻轻下沉几下（小幅高频，幅度随时间衰减），像被小手按了按
            w = math.sin(p * 2 * math.pi * 3) * (1 - p)
            dy = 7 * abs(w)
        elif name == "pet":
            # 抚摸动画：五指轻柔地左右抚摸（像撸猫），同时微微下沉
            # 左右摆动：sin 曲线，频率适中（2次完整循环），幅度随时间衰减
            dx = 8 * math.sin(p * 2 * math.pi * 2) * (1 - p * 0.5)
            # 微微下沉：模拟手掌轻轻按压的感觉，幅度很小
            dy = 3 * math.sin(p * math.pi)
        return dx * k, dy * k

    def _toggle_follow(self, checked):
        self.cfg["follow"] = bool(checked)
        config.save(self.cfg)
        if hasattr(self.renderer, "set_follow"):
            self.renderer.set_follow(bool(checked))

    def _toggle_gravity(self, checked):
        self.cfg["gravity"] = bool(checked)
        config.save(self.cfg)

    def _update_look(self):
        if not self.cfg.get("follow", True) or not hasattr(self.renderer, "set_look"):
            return
        if self._drag_off is not None or self._drag_candidate_off is not None or self._fall_timer.isActive():
            return
        c = QCursor.pos()
        cx = self.x() + self.width() / 2.0
        cy = self.y() + self.height() * 0.42
        self.renderer.set_look((c.x() - cx) / 320.0, (cy - c.y()) / 320.0)

    def _start_fall(self):
        geo = self._workarea_rect()
        self._floor = geo.bottom() - self.height() + 1
        self._landed = False
        if self.y() >= self._floor:
            self._save_pos()
            return
        self._fall_vy = 0.0
        self._fall_timer.start(16)

    def _fall_tick(self):
        self._fall_vy += 2.2
        ny = self.y() + int(self._fall_vy)
        if ny >= self._floor:
            self.move(self.x(), self._floor)
            if not self._landed:
                self._landed = True
                self.renderer.react("land")
            if self._fall_vy > 7:
                self._fall_vy = -self._fall_vy * 0.30
            else:
                self._fall_timer.stop()
                self._save_pos()
        else:
            self.move(self.x(), ny)

    # ------------------------------------------------------------------ #
    #  贴边自动隐藏：拖到屏幕边缘缩回（只露窄边），鼠标移近再划出
    # ------------------------------------------------------------------ #
    def _screen_geo(self):
        """当前所在屏幕的物理整屏矩形（隐藏要真的滑到屏幕外，所以用整屏而非工作区）。"""
        screen = self.screen() or QApplication.primaryScreen()
        return screen.geometry()

    def refresh_content_box(self):
        """让当前渲染器重测内容包围盒（若支持）。供气泡实时跟随头部高度调用。"""
        fn = getattr(self.renderer, "refresh_content_box", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _content_inset(self):
        """当前渲染器四周的透明留白 (左,上,右,下) 像素，用于"模型本体碰到边缘才贴边"。
        渲染器未提供则当作 0（内容铺满窗口，如像素宠物）。"""
        fn = getattr(self.renderer, "content_inset", None)
        if callable(fn):
            try:
                l, t, r, b = fn()
                w, h = self.width(), self.height()
                return (max(0, min(int(l), w // 2)), max(0, min(int(t), h // 2)),
                        max(0, min(int(r), w // 2)), max(0, min(int(b), h // 2)))
            except Exception:
                pass
        return (0, 0, 0, 0)

    def _visible_content_rect(self):
        """按当前内容留白收紧交互区域，避免透明画布挡鼠标。"""
        l_in, t_in, r_in, b_in = self._content_inset()
        pad = max(8, int(min(self.width(), self.height()) * 0.05))
        left = max(0, int(round(l_in)) - pad)
        top = max(0, int(round(t_in)) - pad)
        right = max(0, int(round(r_in)) - pad)
        bottom = max(0, int(round(b_in)) - pad)
        w = max(1, self.width() - left - right)
        h = max(1, self.height() - top - bottom)
        return QRect(left, top, w, h)

    def _point_hits_visible_content(self, x, y):
        rect = self._input_mask_rect_cache
        if rect is None:
            rect = self._visible_content_rect()
            self._input_mask_rect_cache = QRect(rect)
        return rect.contains(int(x), int(y))

    def _sync_input_mask(self):
        """同步窗口命中区域，让透明边尽量不拦住底下的软件。"""
        try:
            if self.cfg.get("click_through", False):
                self._input_mask_rect_cache = QRect(0, 0, self.width(), self.height())
                self.clearMask()
                return
            rect = self._visible_content_rect()
            self._input_mask_rect_cache = QRect(rect)
            if rect.width() >= self.width() - 2 and rect.height() >= self.height() - 2:
                self.clearMask()
            else:
                self.setMask(QRegion(rect))
        except Exception:
            pass

    def _set_renderer_mask_updates(self, enabled):
        fn = getattr(self.renderer, "set_mask_updates_enabled", None)
        if callable(fn):
            try:
                fn(bool(enabled))
            except Exception:
                pass

    def _set_renderer_render_active(self, enabled):
        """拖动时暂停渲染器每帧绘制，松手恢复，让拖动跟手、不掉帧。"""
        fn = getattr(self.renderer, "set_render_active", None)
        if callable(fn):
            try:
                fn(bool(enabled))
            except Exception:
                pass
        if enabled:
            QTimer.singleShot(120, self._refresh_live2d_alpha_mask)

    def _detect_edge(self):
        """以"模型可见内容"的边界判断是否贴边：内容碰到屏幕边(≤EDGE_SNAP_DIST)才吸附，
        透明画布靠近不算。返回 'left'/'right'/'top' 或 None（竖边优先）。"""
        if not self.cfg.get("edge_snap", True):
            return None
        r = self._screen_geo()
        l_in, t_in, r_in, _b = self._content_inset()
        x, y, w = self.x(), self.y(), self.width()
        if x + l_in <= r.left() + EDGE_SNAP_DIST:
            return "left"
        if x + w - r_in >= r.right() + 1 - EDGE_SNAP_DIST:
            return "right"
        if y + t_in <= r.top() + EDGE_SNAP_DIST:
            return "top"
        return None

    def _dock_positions(self, side):
        """返回该边的 (展开位置, 缩回位置)：展开时让模型本体贴住屏幕边（透明留白滑出屏外），
        缩回时露出模型本体那一侧 EDGE_PEEK 像素（露真身而非透明边）。"""
        r = self._screen_geo()
        w, h = self.width(), self.height()
        l_in, t_in, r_in, b_in = self._content_inset()
        if side == "left":
            sy = self._clamp_y(self.y())
            return (QPoint(r.left() - l_in, sy),
                    QPoint(r.left() + EDGE_PEEK - (w - r_in), sy))
        if side == "right":
            sy = self._clamp_y(self.y())
            return (QPoint(r.right() + 1 - (w - r_in), sy),
                    QPoint(r.right() - EDGE_PEEK - l_in, sy))
        # top
        sx = max(r.left() - l_in, min(self.x(), r.right() + 1 - (w - r_in)))
        return (QPoint(sx, r.top() - t_in),
                QPoint(sx, r.top() + EDGE_PEEK - (h - b_in)))

    def _remember_edge(self):
        """把当前吸附状态写入配置（按边 + 另一轴坐标），供重启恢复。"""
        if self._edge is None:
            return
        shown, _hidden = self._dock_positions(self._edge)
        self.cfg["edge_side"] = self._edge
        self.cfg["edge_cross"] = int(shown.y() if self._edge in ("left", "right") else shown.x())
        self.cfg["pos"] = [self.x(), self.y()]
        config.save(self.cfg)
        # 同时保存到当前模型的记忆里
        self._save_model_memory(edge_side=self._edge,
                                edge_cross=int(shown.y() if self._edge in ("left", "right") else shown.x()))

    def _dock_to_edge(self, side):
        """吸附到某条边：先贴到边完整显示，鼠标移开后由 _edge_tick 自动缩回。"""
        self._edge = side
        self._cancel_action()
        self._fall_timer.stop()
        if self._slide_timer.isActive():
            self._slide_timer.stop()
        shown, _hidden = self._dock_positions(side)
        self.move(shown)
        self._edge_hidden = False
        self._leave_ms = 0
        self._remember_edge()

    def _undock(self, save=True):
        """解除吸附（被拖离边缘 / 关闭功能时）。"""
        self._edge = None
        self._edge_hidden = False
        if self._slide_timer.isActive():
            self._slide_timer.stop()
        self.cfg["edge_side"] = ""
        if save:
            config.save(self.cfg)
        # 同时清除当前模型的吸附记忆
        self._save_model_memory(edge_side="", edge_cross=0)

    def _restore_edge(self):
        """已废弃：吸附状态恢复已集成到 _restore_pos() 中。
        保留此方法以防代码其他地方有调用，实际不做任何事。"""
        pass

    def _cursor_near_dock(self):
        """光标是否触发"划出"/维持显示——全部按"模型可见内容"的范围算，
        露出的窄边、感应的边带都对准真身，不会出现"找不到/划不出来"。"""
        if self._edge is None:
            return False
        c = QCursor.pos()
        r = self._screen_geo()
        l_in, t_in, r_in, b_in = self._content_inset()
        x, y, w, h = self.x(), self.y(), self.width(), self.height()
        m = EDGE_HOVER_MARGIN
        cl, cr = x + l_in, x + w - r_in          # 内容在屏幕上的左右/上下范围
        ct, cb = y + t_in, y + h - b_in
        if self._edge_hidden:
            t = max(EDGE_TRIGGER, EDGE_PEEK)
            if self._edge == "left":
                return c.x() <= r.left() + t and ct - m <= c.y() <= cb + m
            if self._edge == "right":
                return c.x() >= r.right() - t and ct - m <= c.y() <= cb + m
            return c.y() <= r.top() + t and cl - m <= c.x() <= cr + m
        return (cl - m <= c.x() <= cr + m) and (ct - m <= c.y() <= cb + m)

    def _edge_tick(self):
        """每 80ms 监视光标：缩回时靠近就划出，展开时离开够久就缩回。"""
        if (self._edge is None or self._drag_off is not None
                or self._drag_candidate_off is not None):
            return
        if (self._slide_timer.isActive() or self._fall_timer.isActive()
                or self._act_timer.isActive() or not self.isVisible()):
            return
        near = self._cursor_near_dock()
        if self._edge_hidden:
            self._leave_ms = 0
            if near:
                shown, _h = self._dock_positions(self._edge)
                self._slide_window_to(shown, hidden=False)
        elif near:
            self._leave_ms = 0
        else:
            self._leave_ms += self._edge_timer.interval()
            if self._leave_ms >= EDGE_LEAVE_MS:
                self._leave_ms = 0
                _s, hidden = self._dock_positions(self._edge)
                self._slide_window_to(hidden, hidden=True)

    def _slide_window_to(self, target, hidden):
        self._slide_from = self.pos()
        self._slide_to = target
        self._slide_after_hidden = hidden
        self._slide_dur = EDGE_SLIDE_IN_DUR if hidden else EDGE_SLIDE_OUT_DUR
        self._slide_t = 0.0
        if hidden:
            self._clear_head_cursor()
            if self._chat_bubble.isVisible():
                self._chat_bubble.hide()
        if not self._slide_timer.isActive():
            self._slide_timer.start(EDGE_TICK_MS)

    @staticmethod
    def _ease_slide(t, hidden):
        """带回弹的缓动，让贴边动画更生动：
        - 划出(hidden=False)：ease-out-back，蹦出来略过头再弹回贴齐；
        - 缩回(hidden=True) ：ease-in-back，先微微探头蓄力再利落收回。
        两者在 t=0→0、t=1→1，保证最终精确到位。"""
        if hidden:
            c1 = 1.05                       # 探头(蓄力)幅度
            return (c1 + 1.0) * t * t * t - c1 * t * t
        c1 = 1.7                            # 回弹幅度
        u = t - 1.0
        return 1.0 + (c1 + 1.0) * u * u * u + c1 * u * u

    def _slide_tick(self):
        if self._slide_to is None or self._slide_from is None:
            self._slide_timer.stop()
            return
        self._slide_t += (EDGE_TICK_MS / 1000.0) / max(0.05, self._slide_dur)
        if self._slide_t >= 1.0:
            self.move(self._slide_to)
            self._slide_timer.stop()
            self._edge_hidden = self._slide_after_hidden
            self._slide_from = self._slide_to = None
            if self._edge_hidden:
                self._clear_head_cursor()
                self._chat_bubble.hide()
            return
        e = self._ease_slide(self._slide_t, self._slide_after_hidden)
        fx = self._slide_from.x() + (self._slide_to.x() - self._slide_from.x()) * e
        fy = self._slide_from.y() + (self._slide_to.y() - self._slide_from.y()) * e
        self.move(int(round(fx)), int(round(fy)))

    def _toggle_edge_snap(self, checked):
        self.cfg["edge_snap"] = bool(checked)
        if checked:
            if self._edge is None:
                side = self._detect_edge()      # 已经靠边的话立刻吸附
                if side is not None:
                    self._dock_to_edge(side)
        elif self._edge is not None:
            shown, _h = self._dock_positions(self._edge)   # 关闭功能：彻底显示出来
            self._undock(save=False)
            self.move(shown)
            self._save_pos()
        config.save(self.cfg)

    # ------------------------------------------------------------------ #
    #  全局快捷键 + 开机自启
    # ------------------------------------------------------------------ #
    def _register_hotkey(self):
        try:
            import ctypes
            MOD_ALT, MOD_CONTROL, VK_P = 0x0001, 0x0002, 0x50
            ctypes.windll.user32.RegisterHotKey(
                int(self.winId()), self._hotkey_id, MOD_CONTROL | MOD_ALT, VK_P)
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        try:
            if eventType in ("windows_generic_MSG", b"windows_generic_MSG"):
                import ctypes.wintypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and msg.wParam == self._hotkey_id:  # WM_HOTKEY
                    self._toggle_visible()
                    return True, 0
        except Exception:
            pass
        return False, 0

    def _toggle_autostart(self, checked):
        system.set_autostart(bool(checked))

    def _set_models_dir(self):
        """设置模型文件夹路径"""
        current_dir = self.cfg.get("models_dir", "")
        if not current_dir:
            current_dir = DEFAULT_MODELS_DIR

        folder = QFileDialog.getExistingDirectory(
            self, "选择 Live2D 模型文件夹", current_dir)

        if folder:
            self.cfg["models_dir"] = folder
            config.save(self.cfg)
            # 清除模型缓存，强制重新扫描
            self._l2d_models = None
            self._start_bg_scan()
            QMessageBox.information(
                self, "设置成功",
                f"模型文件夹已设置为：\n{folder}\n\n重启程序后生效。"
            )

    def _toggle_chat(self, checked):
        """开关聊天气泡功能。"""
        self.cfg["chat_enabled"] = bool(checked)
        config.save(self.cfg)
        if checked:
            self._chat_manager.start()
        else:
            self._chat_manager.stop()

    def _toggle_companion_mode(self, checked):
        """开关伴侣模式。"""
        self.cfg["companion_mode"] = bool(checked)
        # 伴侣模式与雌小鬼/养成模式互斥
        if checked:
            if self.cfg.get("mesugaki_mode", False):
                self.cfg["mesugaki_mode"] = False
            if self.cfg.get("nurture_mode", False):
                self.cfg["nurture_mode"] = False
                self._chat_manager.set_nurture_mode(False)
        config.save(self.cfg)
        self._chat_manager.set_companion_mode(checked)
        # 切换后立即说一句，让用户感知模式变化
        if checked:
            self._chat_manager.say("伴侣模式已开启，我会更温柔地陪着你～")
        else:
            self._chat_manager.say("伴侣模式已关闭")
        # 刷新菜单状态
        self._refresh_tray()

    def _toggle_mesugaki_mode(self, checked):
        """开关雌小鬼模式。"""
        self.cfg["mesugaki_mode"] = bool(checked)
        # 雌小鬼模式与伴侣/养成模式互斥
        if checked:
            if self.cfg.get("companion_mode", False):
                self.cfg["companion_mode"] = False
            if self.cfg.get("nurture_mode", False):
                self.cfg["nurture_mode"] = False
                self._chat_manager.set_nurture_mode(False)
        config.save(self.cfg)
        self._chat_manager.set_mesugaki_mode(checked)
        # 切换后立即说一句，让用户感知模式变化
        if checked:
            self._chat_manager.say("Ciallo～(∠・ω< )⌒★ 杂鱼~")
        else:
            self._chat_manager.say("雌小鬼模式已关闭")
        # 刷新菜单状态
        self._refresh_tray()

    def _toggle_nurture_mode(self, checked):
        """开关养成模式（好感度阶段化台词 + 互动反馈）。与伴侣/雌小鬼互斥。"""
        self.cfg["nurture_mode"] = bool(checked)
        if checked:
            if self.cfg.get("companion_mode", False):
                self.cfg["companion_mode"] = False
                self._chat_manager.set_companion_mode(False)
            if self.cfg.get("mesugaki_mode", False):
                self.cfg["mesugaki_mode"] = False
                self._chat_manager.set_mesugaki_mode(False)
        config.save(self.cfg)
        self._chat_manager.set_nurture_mode(checked)
        if checked:
            lvl = self._affinity.level_name()
            self._chat_manager.say(
                "养成模式已开启～现在我们的关系是「%s」，往后请多关照哦。" % lvl)
            QTimer.singleShot(800, self._nurture_on_start)
        else:
            self._chat_manager.say("养成模式已关闭")
        self._refresh_tray()

    def _nurture_on_start(self):
        """养成模式启动结算：连续登录 / 冷落惩罚 / 当日相见，并按结果说一句。"""
        if not self.cfg.get("nurture_mode", False):
            return
        try:
            r = self._affinity.on_app_start()
        except Exception:
            return
        # 满级特殊剧情优先（只触发一次）
        if self._affinity.consume_max_story():
            self._play_max_story()
            return
        if not r.get("first_today"):
            return
        import affinity_quotes
        addr = self._affinity.address()
        if r.get("neglected"):
            msg = affinity_quotes.neglect_line(addr)
            msg += "（好感 -%d，连登归零）" % r["penalty"]
            self._chat_manager.say(msg)
        elif r.get("leveled_up"):
            self._announce_level_up(r["new_level"])
        else:
            streak = r.get("streak", 1)
            tail = ("　连续相见 %d 天～" % streak) if streak > 1 else ""
            gain = r.get("login_gain", 0)
            extra = ("（好感 +%d）" % gain) if gain else ""
            self._chat_manager.say(
                affinity_quotes.greet_line("morning", addr, self._affinity.level_index()) + tail + extra)

    def _announce_level_up(self, new_level):
        """升级播报：按新层级说升级台词。"""
        import affinity_quotes
        addr = self._affinity.address()
        lines = affinity_quotes.level_up_lines(new_level, addr)
        lvl_name = self._affinity.level_name()
        head = "好感升级 → 「%s」！" % lvl_name
        self._chat_manager.say(head + ("　" + lines[0] if lines else ""))
        # 满级则紧接着触发特殊剧情
        if self._affinity.consume_max_story():
            QTimer.singleShot(4000, self._play_max_story)

    def _play_max_story(self):
        """满级（灵魂羁绊）特殊剧情：多段台词依次播放。"""
        import affinity_quotes
        lines = affinity_quotes.max_story_lines(self._affinity.address())
        delay = 0
        for ln in lines:
            QTimer.singleShot(delay, lambda t=ln: self._chat_manager.say(t))
            delay += 4500

    def _show_affinity_panel(self):
        """好感度记录面板：紧凑 galgame 状态面板。"""
        from affinity import LEVELS
        dlg = QDialog(self)
        dlg.setWindowTitle("好感度 · 养成记录")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet(
            "QDialog{background:#191716;color:#f5eee4;}"
            "QFrame#panel{background:#241f20;border:1px solid #6f4e37;border-radius:6px;}"
            "QFrame#strip{background:#2d3030;border:1px solid #4f6260;border-radius:4px;}"
            "QLabel#caption{color:#c9b48b;font-size:11px;font-weight:600;}"
            "QLabel#hero{color:#fff4d6;font-size:20px;font-weight:700;}"
            "QLabel#muted{color:#b9aa98;font-size:12px;}"
            "QLabel#value{color:#fff4d6;font-size:15px;font-weight:700;}"
            "QLabel#small{color:#d3c4b2;font-size:11px;}"
            "QPushButton{background:#3a2926;color:#fff4d6;border:1px solid #9e7d4f;border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#4a3430;border-color:#d6b46d;}"
            "QProgressBar{border:1px solid #5f5148;border-radius:3px;background:#120f10;text-align:center;height:14px;color:#f5eee4;font-size:10px;}"
            "QProgressBar::chunk{background:#b78352;border-radius:2px;}"
        )
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        top = QFrame()
        top.setObjectName("panel")
        top_lay = QGridLayout(top)
        top_lay.setContentsMargins(12, 10, 12, 10)
        top_lay.setHorizontalSpacing(12)
        top_lay.setVerticalSpacing(5)
        cap = QLabel("RELATION")
        cap.setObjectName("caption")
        top_lay.addWidget(cap, 0, 0)
        title = QLabel("")
        title.setObjectName("hero")
        top_lay.addWidget(title, 1, 0)
        subtitle = QLabel("")
        subtitle.setObjectName("muted")
        top_lay.addWidget(subtitle, 2, 0)
        name_state = QLabel("")
        name_state.setObjectName("small")
        name_state.setWordWrap(True)
        top_lay.addWidget(name_state, 3, 0, 1, 2)
        bar = QProgressBar()
        bar.setTextVisible(True)
        top_lay.addWidget(bar, 1, 1)
        slbl = QLabel("")
        slbl.setObjectName("small")
        top_lay.addWidget(slbl, 2, 1)
        top_lay.setColumnStretch(1, 1)
        v.addWidget(top)

        mid = QFrame()
        mid.setObjectName("panel")
        mid_lay = QGridLayout(mid)
        mid_lay.setContentsMargins(10, 8, 10, 8)
        mid_lay.setHorizontalSpacing(10)
        mid_lay.setVerticalSpacing(6)
        metric_values = {}
        stats = ["好感值", "今日收益", "连续登录", "摸头", "陪玩", "抽签"]
        for idx, label_text in enumerate(stats):
            cell = QFrame()
            cell.setObjectName("strip")
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(8, 5, 8, 5)
            cell_lay.setSpacing(6)
            label = QLabel(label_text)
            label.setObjectName("small")
            cell_lay.addWidget(label)
            cell_lay.addStretch(1)
            value = QLabel("")
            value.setObjectName("value")
            cell_lay.addWidget(value)
            mid_lay.addWidget(cell, idx // 3, idx % 3)
            metric_values[label_text] = value
        v.addWidget(mid)

        acts = QFrame()
        acts.setObjectName("panel")
        acts_lay = QGridLayout(acts)
        acts_lay.setContentsMargins(10, 8, 10, 8)
        acts_lay.setHorizontalSpacing(8)
        acts_lay.setVerticalSpacing(5)
        actlbl = QLabel("TODAY ACTION")
        actlbl.setObjectName("caption")
        acts_lay.addWidget(actlbl, 0, 0, 1, 3)
        action_rows = {}
        for idx, a in enumerate(self._affinity.panel()["actions"], start=1):
            nm = QLabel("%s (+%d)" % (a["name"], a["per"]))
            nm.setObjectName("small")
            nm.setFixedWidth(86)
            acts_lay.addWidget(nm, idx, 0)
            pb = QProgressBar()
            pb.setFixedHeight(14)
            acts_lay.addWidget(pb, idx, 1)
            tx = QLabel("")
            tx.setObjectName("small")
            tx.setFixedWidth(46)
            tx.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            acts_lay.addWidget(tx, idx, 2)
            action_rows[a["key"]] = (pb, tx)
        acts_lay.setColumnStretch(1, 1)
        v.addWidget(acts)

        fortune_box = QFrame()
        fortune_box.setObjectName("panel")
        fortune_lay = QVBoxLayout(fortune_box)
        fortune_lay.setContentsMargins(10, 8, 10, 8)
        fortune_lay.setSpacing(2)
        fortune_title = QLabel("LAST FORTUNE")
        fortune_title.setObjectName("caption")
        fortune_lay.addWidget(fortune_title)
        fortune_text = QLabel("")
        fortune_text.setObjectName("muted")
        fortune_text.setWordWrap(True)
        fortune_lay.addWidget(fortune_text)
        v.addWidget(fortune_box)

        def refresh_panel():
            d = self._affinity.panel()
            title.setText(d["level_name"])
            subtitle.setText("称呼  %s" % d["address"])
            pet_name = (d.get("pet_name") or "").strip()
            if d.get("pet_name_active"):
                name_state.setText("专属称呼已生效：%s" % pet_name)
            elif pet_name:
                name_state.setText(
                    "已保存专属称呼：%s　将在「%s」阶段正式启用"
                    % (pet_name, d.get("pet_name_ready_level", "灵魂羁绊")))
            else:
                name_state.setText("还没有设置专属称呼。设置后会在满级阶段正式启用。")
            progress_span = max(1, d["progress_span"])
            bar.setRange(0, progress_span)
            bar.setValue(min(d["progress_got"], progress_span))
            if d["is_max"]:
                bar.setFormat("好感 %d / MAX" % d["points"])
            else:
                next_name = LEVELS[d["level_index"] + 1][0]
                bar.setFormat("%d · 距 %s %d" % (d["points"], next_name, d["to_next"]))
            stages = " → ".join(
                "[%s]" % LEVELS[i][0] if i == d["level_index"] else LEVELS[i][0]
                for i in range(len(LEVELS)))
            slbl.setText(stages)
            metric_values["好感值"].setText("%d" % d["points"])
            metric_values["今日收益"].setText("+%d / %d" % (d["today_gains"], d.get("daily_total_cap", 10)))
            metric_values["连续登录"].setText("%d天" % d["streak_days"])
            metric_values["摸头"].setText("%d" % d["total_head_pats"])
            metric_values["陪玩"].setText("%d" % d.get("total_drag_plays", 0))
            metric_values["抽签"].setText("%d" % d.get("total_fortunes", 0))
            for a in d["actions"]:
                row = action_rows.get(a["key"])
                if row is None:
                    continue
                pb, tx = row
                limit = max(1, a["limit"])
                pb.setRange(0, limit)
                pb.setValue(min(a["used"], limit))
                pb.setFormat("")
                tx.setText("%d/%d" % (a["used"], a["limit"]))
            last = d.get("last_fortune") or {}
            if last:
                fortune_text.setText("%s · %s · %s　%s" % (
                    last.get("date", ""),
                    last.get("mode_label", "抽签"),
                    last.get("grade", ""),
                    last.get("title", "")))
                color = last.get("accent") or "#b78352"
                fortune_box.setStyleSheet(
                    "QFrame#panel{background:#241f20;border:1px solid %s;border-radius:6px;}" % color)
            else:
                fortune_text.setText("今日还没有抽签。可以在「陪它玩」里抽一张。")

        refresh_panel()
        refresh_timer = QTimer(dlg)
        refresh_timer.timeout.connect(refresh_panel)
        refresh_timer.start(400)

        # 按钮：设置专属称呼（满级时生效）+ 关闭
        btns = QHBoxLayout()
        name_btn = QPushButton("设置专属称呼…")
        name_btn.setToolTip("灵魂羁绊层级会用这个昵称称呼你；其它层级用固定称呼")

        def _set_name():
            from PySide6.QtWidgets import QInputDialog
            cur = self._affinity.panel().get("pet_name", "")
            text, ok = QInputDialog.getText(dlg, "专属称呼", "TA 该怎么称呼你？", text=cur)
            if ok:
                self._affinity.set_pet_name(text.strip())
                refresh_panel()
        name_btn.clicked.connect(_set_name)
        btns.addWidget(name_btn)
        btns.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(dlg.accept)
        btns.addWidget(close)
        v.addLayout(btns)

        self._exec_centered_dialog(dlg)

    def _show_rps_game(self):
        """陪它玩 · 猜拳（石头剪刀布）：轻量像素风小游戏。

        每出一局即与宠物互动一次——养成模式下静默计入"陪它玩"好感（升级才播报），
        并让桌面上的宠物做个小动作呼应胜负，让"陪伴"有实感。"""
        import random
        # (key, 图标, 名称)；emoji 都是很早期的 Unicode，Win10 字体都支持
        MOVES = [("rock", "✊", "石头"), ("scissors", "✌", "剪刀"), ("paper", "✋", "布")]
        ICON = {k: e for k, e, _ in MOVES}
        BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}  # 键 克 值

        PET_WIN = ["哼哼，我赢啦~", "嘿嘿，这局是我的！", "略略略，你输咯~", "猜中你啦！"]
        PET_LOSE = ["呜…你好厉害", "被你赢走了啦…再来一局！", "可恶，下次不会输了！", "你是不是偷看了~"]
        PET_DRAW = ["平局！心有灵犀~", "想到一块儿去了呢", "再来一次决胜负！", "诶？一样的！"]

        dlg = QDialog(self)
        dlg.setWindowTitle("陪它玩 · 猜拳")
        dlg.setMinimumWidth(340)
        # 像素风：深色背景 + 等宽字体 + 方块按钮
        dlg.setStyleSheet(
            "QDialog{background:#1f2933;}"
            "QLabel{color:#e4e7eb;font-family:'Consolas','Courier New',monospace;}"
            "QPushButton{background:#323f4b;color:#f5f7fa;border:2px solid #7b8794;"
            "border-radius:0px;padding:8px;font-size:13px;"
            "font-family:'Consolas','Courier New',monospace;}"
            "QPushButton:hover{background:#3e4c59;border-color:#cbd2d9;}"
            "QPushButton:pressed{background:#52606d;}"
        )
        v = QVBoxLayout(dlg)
        v.setSpacing(10)

        title = QLabel("石头 · 剪刀 · 布")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:15px;font-weight:bold;color:#9fb3c8;letter-spacing:2px;")
        v.addWidget(title)

        # 出拳显示区：宠物 VS 你
        faces = QLabel("❔   VS   ❔")
        faces.setAlignment(Qt.AlignCenter)
        faces.setStyleSheet("font-size:40px;")
        v.addWidget(faces)
        vs_lbl = QLabel("它　　　　你")
        vs_lbl.setAlignment(Qt.AlignCenter)
        vs_lbl.setStyleSheet("font-size:11px;color:#7b8794;")
        v.addWidget(vs_lbl)

        result = QLabel("出拳吧！点下面的按钮～")
        result.setAlignment(Qt.AlignCenter)
        result.setWordWrap(True)
        result.setStyleSheet("font-size:13px;color:#ffd866;min-height:34px;")
        v.addWidget(result)

        state = {"win": 0, "lose": 0, "draw": 0}
        score = QLabel("胜 0　平 0　负 0")
        score.setAlignment(Qt.AlignCenter)
        score.setStyleSheet("font-size:12px;color:#9aa5b1;")
        v.addWidget(score)

        def play(player_move):
            pet_move = random.choice([m[0] for m in MOVES])
            faces.setText("%s   VS   %s" % (ICON[pet_move], ICON[player_move]))
            if player_move == pet_move:
                state["draw"] += 1
                line = random.choice(PET_DRAW)
                self._play_window_action("tilt")
            elif BEATS[player_move] == pet_move:
                # 玩家克制宠物 = 玩家赢、宠物输
                state["win"] += 1
                line = random.choice(PET_LOSE)
                self._play_window_action("nod")
            else:
                state["lose"] += 1
                line = random.choice(PET_WIN)
                self._play_window_action("hop")
            # 每局都记为一次"陪它玩"；养成模式下同步提示收益与升级。
            gain_tip = ""
            try:
                gain_state = self._affinity_action_state("drag_play")
                r = self._affinity.register("drag_play")
                self._refresh_tray()
                play_panel = self._affinity.panel()
                play_action = next((a for a in play_panel["actions"] if a["key"] == "drag_play"), None)
                if r.get("leveled_up"):
                    self._announce_level_up(r["new_level"])
                else:
                    if r.get("gained"):
                        gain_tip = "　好感 +%d ❤" % r["gained"]
                    else:
                        gain_tip = ""
                    # 猜拳气泡锁定位置：弹出后固定不动，避免每局 tilt/nod/hop 动作 +
                    # 待机重测让气泡上下左右抽搐（仅猜拳场景锁定）。
                    self._chat_manager.say(line + gain_tip, lock_position=True)
            except Exception:
                play_panel = None
                play_action = None
                pass
            result.setText(line + gain_tip)
            if play_panel and play_action:
                score.setText(
                    "胜 %d　平 %d　负 %d　|　今日陪玩 %d / %d　|　累计 %d 局"
                    % (state["win"], state["draw"], state["lose"],
                       play_action["used"], play_action["limit"], play_panel["total_drag_plays"]))
            else:
                score.setText("胜 %d　平 %d　负 %d" % (state["win"], state["draw"], state["lose"]))

        row = QHBoxLayout()
        row.setSpacing(8)
        for key, emoji, name in MOVES:
            b = QPushButton("%s\n%s" % (emoji, name))
            b.setMinimumHeight(60)
            b.clicked.connect(lambda _=False, k=key: play(k))
            row.addWidget(b)
        v.addLayout(row)

        close = QPushButton("不玩啦")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)

        self._exec_centered_dialog(dlg)

    def _show_daily_fortune(self):
        """陪它玩 · 今日抽签：中式灵签 / 塔罗占卜，抽取后计入好感 +1。"""
        import random
        import fortune_data

        dlg = QDialog(self)
        dlg.setWindowTitle("陪它玩 · 今日抽签")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(
            "QDialog{background:#161312;color:#f6ead8;}"
            "QLabel{color:#f6ead8;}"
            "QFrame#card{background:#241f20;border:1px solid #8b6f45;border-radius:8px;}"
            "QLabel#title{font-size:18px;font-weight:700;color:#fff3d1;}"
            "QLabel#grade{font-size:26px;font-weight:800;color:#d6b46d;}"
            "QLabel#muted{font-size:12px;color:#cbbba8;}"
            "QLabel#line{font-size:12px;color:#f1dfc8;}"
            "QPushButton{background:#322520;color:#fff3d1;border:1px solid #9e7d4f;border-radius:4px;padding:7px 10px;}"
            "QPushButton:hover{background:#46322b;border-color:#d6b46d;}"
            "QPushButton:disabled{background:#242020;color:#8c8176;border-color:#5f5148;}"
        )
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        head = QLabel("今日抽签")
        head.setObjectName("title")
        head.setAlignment(Qt.AlignCenter)
        v.addWidget(head)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(8)

        mode_lbl = QLabel("请选择规则")
        mode_lbl.setObjectName("muted")
        mode_lbl.setAlignment(Qt.AlignCenter)
        card_lay.addWidget(mode_lbl)

        grade = QLabel("未抽")
        grade.setObjectName("grade")
        grade.setAlignment(Qt.AlignCenter)
        card_lay.addWidget(grade)

        title = QLabel("灵签看今日，塔罗问心事")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        card_lay.addWidget(title)

        verse = QLabel("中式签重趋势与趋避，塔罗重牌意与选择。")
        verse.setObjectName("muted")
        verse.setAlignment(Qt.AlignCenter)
        verse.setWordWrap(True)
        card_lay.addWidget(verse)

        detail_state = self._affinity_action_state("daily_fortune")
        if detail_state == "action_full":
            detail_text = "今天已经抽过签啦，这次会照常展示结果，但不再重复增加好感。"
        elif detail_state == "total_full":
            detail_text = "今天的好感已经攒满啦，这次会记录抽签结果，但不再增加好感。"
        else:
            detail_text = "抽一次会记录今日抽签，并在养成模式下增加 1 点好感。"
        detail = QLabel(detail_text)
        detail.setObjectName("line")
        detail.setWordWrap(True)
        card_lay.addWidget(detail)
        v.addWidget(card)

        gain_lbl = QLabel("")
        gain_lbl.setObjectName("muted")
        gain_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(gain_lbl)

        row = QHBoxLayout()
        lot_btn = QPushButton("中式灵签")
        tarot_btn = QPushButton("塔罗占卜")
        row.addWidget(lot_btn)
        row.addWidget(tarot_btn)
        v.addLayout(row)

        close = QPushButton("收好签文")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)

        state = {"timer": None, "ticks": 0, "fortune": None, "busy": False}
        shuffle_marks = {
            "lot": ["一签入筒", "摇签中", "听签落地", "解签中"],
            "tarot": ["洗牌中", "切牌中", "牌背翻转", "读牌意"],
        }

        def finish(mode):
            f = fortune_data.draw_fortune(mode, random)
            state["fortune"] = f
            card.setStyleSheet(
                "QFrame#card{background:#241f20;border:1px solid %s;border-radius:8px;}" % f.get("accent", "#d6b46d"))
            mode_lbl.setText("%s · %s" % (f["mode_label"], f["id"].split("-")[-1]))
            grade.setText(f["grade"])
            title.setText("%s｜%s" % (f["title"], f["headline"]))
            verse.setText(f["verse"])
            detail.setText(
                "今日：%s\n感情：%s\n事业：%s\n建议：%s" %
                (f["summary"], f["love"], f["career"], f["advice"]))

            gain_tip = ""
            try:
                gain_state = self._affinity_action_state("daily_fortune")
                r = self._affinity.register("daily_fortune")
                self._affinity.record_fortune(f)
                self._refresh_tray()
                if r.get("leveled_up"):
                    self._announce_level_up(r["new_level"])
                elif r.get("gained"):
                    gain_tip = "好感 +%d" % r["gained"]
                else:
                    gain_tip = ""
            except Exception:
                pass
            gain_lbl.setText(gain_tip)
            pet_line = f.get("pet_line", "")
            if pet_line:
                suffix = ("　" + gain_tip) if gain_tip else ""
                self._chat_manager.say(pet_line + suffix)
            self._play_window_action("tilt" if f.get("tier") == "bad" else "hop")
            lot_btn.setEnabled(True)
            tarot_btn.setEnabled(True)
            state["busy"] = False

        def draw(mode):
            if state["busy"]:
                return
            state["busy"] = True
            lot_btn.setEnabled(False)
            tarot_btn.setEnabled(False)
            gain_lbl.setText("")
            card.setStyleSheet("")
            state["ticks"] = 0
            marks = shuffle_marks.get(mode) or shuffle_marks["lot"]

            def tick():
                state["ticks"] += 1
                mark = marks[(state["ticks"] - 1) % len(marks)]
                mode_lbl.setText(fortune_data.MODE_LABELS.get(mode, "抽签"))
                grade.setText("...")
                title.setText(mark)
                verse.setText("◇" * ((state["ticks"] % 4) + 1))
                detail.setText("正在听今日的风向。")
                if state["ticks"] >= 8:
                    state["timer"].stop()
                    finish(mode)

            timer = QTimer(dlg)
            timer.timeout.connect(tick)
            state["timer"] = timer
            timer.start(120)
            tick()

        lot_btn.clicked.connect(lambda: draw("lot"))
        tarot_btn.clicked.connect(lambda: draw("tarot"))

        self._exec_centered_dialog(dlg)

    def _toggle_click_quote(self, checked):
        """开关点击弹语录功能。"""
        self.cfg["click_quote_enabled"] = bool(checked)
        config.save(self.cfg)
        self._chat_manager.set_click_quote_enabled(checked)
        if checked:
            self._chat_manager.say("点击弹语录已开启～")
        else:
            self._chat_manager.say("点击弹语录已关闭")

    def _tts_rate_native(self):
        """读取配置里的 TTS 语速并规整到 QtTextToSpeech 原生 -1.0~1.0（兼容旧 wpm）。"""
        r = self.cfg.get("tts_rate", 0.0)
        try:
            r = float(r)
        except (TypeError, ValueError):
            return 0.0
        if r < -1.0 or r > 1.0:   # 旧配置里的 wpm
            r = (r - 150.0) / 150.0
        return max(-1.0, min(1.0, r))

    def _apply_tts_settings(self):
        """把当前配置里的 TTS 开关/音量/语速/嗓音/引擎/自定义命令一次性同步到聊天管理器。"""
        self._chat_manager.set_tts_enabled(
            self.cfg.get("tts_enabled", False),
            self.cfg.get("tts_volume", 0.7),
            self.cfg.get("tts_rate", 0.0),
            self.cfg.get("tts_voice", ""),
            self.cfg.get("tts_engine", "auto"),
            self.cfg.get("tts_custom_cmd", ""),
        )

    def _toggle_tts(self, checked):
        """开关 TTS 朗读功能。"""
        self.cfg["tts_enabled"] = bool(checked)
        config.save(self.cfg)
        self._apply_tts_settings()
        # 提示用户
        if checked:
            self._chat_manager.say("TTS 朗读已开启，我会把气泡文字读出来～")
        else:
            self._chat_manager.say("TTS 朗读已关闭")

    def _set_tts_voice(self, name):
        """选择 TTS 嗓音（空=自动中文优先），并立即试听。"""
        self.cfg["tts_voice"] = name or ""
        config.save(self.cfg)
        self._apply_tts_settings()
        self._tts_preview()

    def _set_tts_volume(self, v):
        """设置 TTS 音量并立即试听。"""
        self.cfg["tts_volume"] = float(v)
        config.save(self.cfg)
        self._apply_tts_settings()
        if v > 0:
            self._tts_preview()

    def _set_tts_rate(self, v):
        """设置 TTS 语速（-1.0~1.0）并立即试听。"""
        self.cfg["tts_rate"] = float(v)
        config.save(self.cfg)
        self._apply_tts_settings()
        self._tts_preview()

    def _tts_preview(self):
        """试听一句：即使 TTS 总开关是关的也临时朗读，给即时反馈。"""
        self._chat_manager.preview_tts(
            "你好呀，我是你的桌面宠物，这是语音试听～",
            self.cfg.get("tts_volume", 0.7),
            self.cfg.get("tts_rate", 0.0),
            self.cfg.get("tts_voice", ""),
            self.cfg.get("tts_engine", "auto"),
            self.cfg.get("tts_custom_cmd", ""),
        )

    def _set_tts_engine(self, mode):
        """切换 TTS 后端：auto=系统语音 / custom=自定义命令；自定义但未填命令时给出提示。"""
        mode = "custom" if mode == "custom" else "auto"
        self.cfg["tts_engine"] = mode
        config.save(self.cfg)
        self._apply_tts_settings()
        if mode == "custom" and not self.cfg.get("tts_custom_cmd", "").strip():
            # 还没填命令，引导用户去设置
            self._set_tts_custom_cmd()
        else:
            self._tts_preview()

    def _set_tts_custom_cmd(self):
        """弹窗设置自定义 TTS 命令模板。"""
        from PySide6.QtWidgets import QInputDialog
        cur = self.cfg.get("tts_custom_cmd", "")
        tip = (
            "输入自定义 TTS 命令模板（占位符：{text}=要朗读的文字，{out}=输出音频文件，由程序播放）：\n\n"
            "示例：\n"
            "  edge-tts --voice zh-CN-XiaoxiaoNeural --text \"{text}\" --write-media \"{out}\"\n"
            "  piper -m zh_CN.onnx -f \"{out}\"   （模板无 {text} 时，文字走标准输入）\n"
            "  curl -s \"http://127.0.0.1:5000/tts?text={text}\" -o \"{out}\"\n\n"
            "说明：命令不经 shell 执行（不支持管道/重定向）；含 {out} 则由程序播放合成音频，"
            "否则视为命令自行发声。"
        )
        text, ok = QInputDialog.getMultiLineText(self, "自定义 TTS 命令", tip, cur)
        if not ok:
            return
        self.cfg["tts_custom_cmd"] = text.strip()
        # 填了命令通常意味着想用自定义后端
        if text.strip():
            self.cfg["tts_engine"] = "custom"
        config.save(self.cfg)
        self._apply_tts_settings()
        self._tts_preview()

    def _toggle_holiday_greetings(self, checked):
        """开关节日问候功能。"""
        self.cfg["holiday_greetings"] = bool(checked)
        config.save(self.cfg)
        holiday_config = {
            "user_birthday": self.cfg.get("user_birthday", ""),
            "custom_holidays": self.cfg.get("custom_holidays", []),
        }
        self._chat_manager.set_holiday_enabled(checked, holiday_config)
        # 提示用户
        if checked:
            self._chat_manager.say("节日问候已开启，会在特殊日子给你惊喜～")
        else:
            self._chat_manager.say("节日问候已关闭")

    def _set_chat_interval(self, lo, hi):
        """设置气泡语录自动播放间隔(秒)。"""
        self.cfg["chat_min_interval"] = int(lo)
        self.cfg["chat_max_interval"] = int(hi)
        config.save(self.cfg)
        if self._chat_manager:
            self._chat_manager.set_intervals(lo, hi)

    def _on_pet_speak(self):
        """宠物说话时触发的动作。

        已禁用：根据用户反馈，气泡语录说话时不应自动触发动作（如点头）。
        如需要动作配合，应由具体情境（如摸头、点击）单独触发。
        """
        # 不再自动触发动作
        pass

    def _set_bubble_style(self, style_name):
        """设置气泡样式。"""
        self.cfg["bubble_style"] = style_name
        config.save(self.cfg)
        self._chat_manager.set_bubble_style(style_name)
        # 显示预览
        style_labels = {
            "simple": "简约",
            "cute": "可爱",
            "pro": "专业",
            "dark": "深色"
        }
        self._chat_manager.say(f"已切换到{style_labels.get(style_name, style_name)}风格")

    def _choose_live2d(self):
        models_dir = get_models_dir()
        start = models_dir if os.path.isdir(models_dir) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Live2D 模型设置（Cubism 2/3）", start, LIVE2D_FILTER)
        if not path:
            return
        self._set_live2d_model(path)

    def _set_live2d_model(self, path):
        self.cfg["live2d_model"] = path
        self.cfg["character"] = "live2d"
        state = self._resolve_live2d_state(path)
        self.cfg["live2d_size"] = int(state["size"])
        config.save(self.cfg)
        self._switch_character_fast("live2d", path)
        self._refresh_tray()

    def _open_live2d_picker(self):
        # 用后台预扫的缓存秒开选择器（首开若还没扫完则同步扫一次并回填缓存）；
        # 弹窗里的「🔄 刷新」会强制重扫并更新缓存，新放进 live2d/ 的模型立刻出现。
        models = self._get_models()

        def do_rescan():
            return self._get_models(force=True)

        # 显示加载提示
        if hasattr(self, '_chat_bubble') and self._chat_bubble:
            self._chat_bubble.show_message("准备模型选择器...⏳", duration=2000)

        dlg = Live2DPicker(self, models, self.cfg.get("live2d_model", ""),
                           self.cfg.get("live2d_views") or {}, rescan=do_rescan,
                           size_px=self.cfg.get("live2d_size", 300),
                           pinned=self._pinned_model_paths(models),
                           feature_provider=self._get_model_features_cached)
        if self._exec_centered_dialog(dlg) and dlg.selected_path:
            self.cfg["live2d_model"] = dlg.selected_path
            self.cfg["character"] = "live2d"
            self.cfg["live2d_size"] = int(dlg.size_px)
            # 选择器里调好的构图 + 显示尺寸，按这个模型一并记住
            self._l2d_save_view(dlg.selected_path, dlg.zoom, dlg.xoff, dlg.yoff,
                                dlg.ratio, size=int(dlg.size_px))
            config.save(self.cfg)
            self._switch_character_fast("live2d", dlg.selected_path)
            self._refresh_tray()

    def _on_live2d_render_error(self, path, err):
        """某个 Live2D 模型渲染崩了：安全切回像素宠物并提示。"""
        if self.cfg.get("character") != "live2d":
            return
        self.cfg["character"] = "slime"
        config.save(self.cfg)
        self._switch_character_fast("slime")
        self._refresh_tray()
        self.tray.showMessage("Live2D 渲染失败",
                              "该模型无法正常渲染，已切回像素宠物。\n可换一个模型再试。",
                              QSystemTrayIcon.Warning, 4000)

    def _switch_character_fast(self, character, model_path=None):
        """尽量原地切换形象，减少重建卡顿。"""
        if character == "live2d" and model_path:
            state = self._resolve_live2d_state(model_path)
            self.cfg["live2d_model"] = model_path
            self.cfg["character"] = "live2d"
            self.cfg["live2d_size"] = int(state["size"])
            config.save(self.cfg)
        self._rebuild_and_restore_pos()

    def _set_live2d_size(self, s):
        self.cfg["live2d_size"] = s
        if self.cfg["character"] == "live2d" and self.cfg.get("live2d_model"):
            v = self._l2d_view_of(self.cfg["live2d_model"])    # 按模型记住尺寸
            self._l2d_save_view(self.cfg["live2d_model"], v["zoom"], v["xoff"],
                                v["yoff"], v["ratio"], size=s)  # 内部已 config.save
            # 同时保存到新的 model_memory 结构里
            self._save_model_memory(size=s)
            # 尺寸变化改走完整重建路径，和“重启后正常”的冷启动保持一致，
            # 彻底规避运行中热改尺寸残留旧 FBO/旧模型状态的问题。
            self._rebuild_and_restore_pos()
        else:
            config.save(self.cfg)

    def _toggle_top(self, checked):
        self.cfg["always_on_top"] = bool(checked)
        config.save(self.cfg)
        self._apply_flags()
        try:
            self._chat_bubble.set_always_on_top(bool(checked))
        except Exception:
            pass
        self._schedule_layer_sync()

    def _toggle_avoid_taskbar(self, checked):
        self.cfg["avoid_taskbar"] = bool(checked)
        config.save(self.cfg)
        # 立刻把当前窗口拉回（或放出）工作区
        self.move(self.x(), self._clamp_y(self.y()))
        self._save_pos()

    def _workarea_rect(self):
        """当前所在屏幕的可用区域：开启"不覆盖任务栏"时排除任务栏，否则用整屏。"""
        screen = self.screen() or QApplication.primaryScreen()
        if self.cfg.get("avoid_taskbar", True):
            return screen.availableGeometry()
        return screen.geometry()

    def _clamp_y(self, y):
        """限制Y坐标：不覆盖任务栏时，让模型底部（而非画布底部）不超过工作区。"""
        r = self._workarea_rect()
        # 获取模型内容的底部留白
        _, _, _, b_in = self._content_inset()
        # 让模型底部（画布底部-底部留白）贴齐工作区底部
        max_y = r.bottom() - (self.height() - b_in) + 1
        return max(r.top(), min(int(y), max_y))

    def _bottom_right_pos(self):
        """右下角停靠坐标：让模型"可见本体"贴住工作区右缘与底缘（任务栏上方），
        透明留白滑出屏外，做到"不留空"。需在渲染器出图后调用才能拿到准确留白。"""
        geo = self._workarea_rect()
        full = self._screen_geo()
        w, h = self.width(), self.height()
        l_in, t_in, r_in, b_in = self._content_inset()
        nx = geo.right() + 1 - (w - r_in)    # 模型右缘贴工作区右缘（右侧透明留白滑出屏外）
        ny = geo.bottom() + 1 - (h - b_in)   # 模型底缘贴工作区底缘（恰在任务栏上方）
        nx = max(full.left() - l_in, nx)     # 极小模型/大留白时别整体跑出屏幕
        ny = max(full.top() - t_in, ny)
        return int(nx), int(ny)

    def _dock_bottom_right(self):
        """把宠物停到屏幕右下角（任务栏上方）。切换形象后调用，做到"不留空"。"""
        if (self.renderer is None or self._drag_off is not None
                or self._drag_candidate_off is not None):
            return                            # 正在拖动时不抢位置（避免延迟回贴打断拖动）
        if self._edge is not None:           # 先解除任何贴边状态
            self._undock(save=False)
        nx, ny = self._bottom_right_pos()
        self.move(nx, ny)
        self._pos_ready = True
        self._save_pos()


    def _toggle_click_through(self, checked):
        self.cfg["click_through"] = bool(checked)
        config.save(self.cfg)
        self._apply_flags()
        if checked:
            self.tray.showMessage(
                "桌面宠物", "已开启鼠标穿透，无法拖动。可在托盘菜单里关闭。",
                QSystemTrayIcon.Information, 3000)

    def _toggle_visible(self):
        vis = not self.isVisible()
        self.setVisible(vis)
        if vis:
            self._schedule_layer_sync()

    def _quit(self):
        self._save_pos()
        self.renderer.shutdown()
        self.tray.hide()
        QApplication.quit()

    def _show_about(self):
        from pixel_pet import render_icon
        dlg = QDialog(self)
        dlg.setWindowTitle("关于 " + APP_NAME)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        dlg.setFixedWidth(380)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(24, 24, 24, 20)
        v.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(render_icon("slime").scaled(
            56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(logo)
        head.addWidget(QLabel(
            f"<div style='font-size:18px;font-weight:700;color:#243b53'>{APP_NAME}</div>"
            f"<div style='color:#627d98'>版本 {APP_VERSION}</div>"), 1)
        v.addLayout(head)

        body = QLabel(
            "<style>p{margin:8px 0;line-height:1.6}</style>"
            "<p>🎨 <b>多种形象</b><br>"
            "像素宠物、图片、Live2D 模型随心切换</p>"

            "<p>💬 <b>智能陪伴</b><br>"
            "500+ 条对话，根据时间自动问候关心</p>"

            "<p>🎯 <b>简单易用</b><br>"
            "拖动移动、点击头部摸头、右键打开菜单<br>"
            "快捷键 <b>Ctrl+Alt+P</b> 显示/隐藏</p>"

            "<p>💾 <b>智能记忆</b><br>"
            "自动记住每个模型的位置和设置</p>")
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setStyleSheet("color:#243b53;")
        v.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("知道了")
        ok.clicked.connect(dlg.accept)
        row.addWidget(ok)
        v.addLayout(row)
        self._exec_centered_dialog(dlg)

    def _manage_quotes(self):
        """管理聊天语录：增删改查。"""
        try:
            dlg = QuoteManagerDialog(self._chat_manager, self)
            self._exec_centered_dialog(dlg)
        except Exception as e:
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "错误", f"无法打开语录管理：\n{e}")

    # ------------------------------------------------------------------ #
    #  窗口标志 / 位置
    # ------------------------------------------------------------------ #
    def _apply_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if self.cfg["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        if self.cfg["click_through"]:
            flags |= Qt.WindowTransparentForInput
        pos = self.pos()
        self.setWindowFlags(flags)
        self.move(pos)
        self.show()
        QTimer.singleShot(0, self._sync_input_mask)
        self._schedule_layer_sync()

    def _schedule_layer_sync(self):
        """把主窗口/气泡重新压回当前配置要求的层级，收敛 Qt 重建原生窗口带来的抖动。"""
        if self._layer_sync_pending:
            return
        self._layer_sync_pending = True
        QTimer.singleShot(0, self._sync_window_layers)

    def _sync_window_layers(self):
        self._layer_sync_pending = False
        try:
            _restack_window(self, bool(self.cfg.get("always_on_top", True)))
        except Exception:
            pass
        try:
            self._chat_bubble.set_always_on_top(bool(self.cfg.get("always_on_top", True)))
            if self._chat_bubble.isVisible():
                self._chat_bubble.sync_window_layer()
        except Exception:
            pass

    def _restore_pos(self):
        """恢复窗口位置：优先用当前模型自己的记忆，再用全局配置，最后用默认位置。"""
        model_mem = self._get_model_memory()
        # 先尝试恢复吸附状态（如果该模型上次停在某条边上）
        if self.cfg.get("edge_snap", True):
            edge_side = model_mem.get("edge_side") or self.cfg.get("edge_side", "")
            if edge_side in ("left", "right", "top"):
                self._edge = edge_side
                cross = int(model_mem.get("edge_cross") or self.cfg.get("edge_cross", 0) or 0)
                if edge_side in ("left", "right"):
                    self.move(self.x(), self._clamp_y(cross))
                else:
                    self.move(cross, self.y())
                shown, _hidden = self._dock_positions(edge_side)
                self.move(shown)
                self._edge_hidden = False
                self._leave_ms = -EDGE_STARTUP_GRACE_MS     # 先完整显示一会儿，别一开机就缩走
                self._pos_ready = True
                return
        # 没有吸附：恢复普通位置
        pos = model_mem.get("pos") or self.cfg.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), self._clamp_y(int(pos[1])))
        else:
            nx, ny = self._bottom_right_pos()     # 首次/无记忆：紧贴右下角，不留空
            self.move(nx, ny)
        self._pos_ready = True     # 之后切换/缩放都按底部中心锚定

    def _resize_keep_anchor(self, new_size):
        """换形象/改尺寸时让宠物"脚下"位置不动：保持窗口底部中心不变。"""
        if not self._pos_ready:
            self.resize(new_size)      # 启动首建：先不锚定，交给 _restore_pos
            QTimer.singleShot(0, self._sync_input_mask)
            return
        if self._edge is not None:     # 吸在边上：改尺寸后按当前(缩回/展开)重新贴边
            if self._slide_timer.isActive():
                self._slide_timer.stop()
            self.resize(new_size)
            shown, hidden = self._dock_positions(self._edge)
            self.move(hidden if self._edge_hidden else shown)
            self._remember_edge()
            QTimer.singleShot(0, self._sync_input_mask)
            QTimer.singleShot(240, self._sync_input_mask)
            return
        bcx = self.x() + self.width() / 2.0
        bottom = self.y() + self.height()
        self.resize(new_size)
        nx = round(bcx - new_size.width() / 2.0)
        ny = round(bottom - new_size.height())
        geo = self._workarea_rect()
        nx = max(geo.left(), min(nx, geo.right() - new_size.width()))
        ny = max(geo.top(), min(ny, geo.bottom() - new_size.height()))
        self.move(nx, ny)
        self._save_pos()
        QTimer.singleShot(0, self._sync_input_mask)
        QTimer.singleShot(240, self._sync_input_mask)

    def _save_pos(self):
        self.cfg["pos"] = [self.x(), self.y()]
        # 按模型记住位置：仅在未吸边时（吸边位置由 edge_side/edge_cross 负责恢复）
        if self._edge is None:
            self._save_model_memory(pos=[self.x(), self.y()])
            # Live2D 模型继续用旧的 live2d_views 存储（兼容）
            if self.cfg.get("character") == "live2d" and self.cfg.get("live2d_model"):
                v = self._l2d_view_of(self.cfg["live2d_model"])
                self._l2d_save_view(self.cfg["live2d_model"], v["zoom"], v["xoff"],
                                    v["yoff"], v["ratio"], pos=[self.x(), self.y()])
        else:
            config.save(self.cfg)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._input_mask_rect_cache = None
        if hasattr(self, "_petting_overlay"):
            self._petting_overlay.sync_geometry()
        QTimer.singleShot(0, self._sync_input_mask)
        self._schedule_layer_sync()

    def moveEvent(self, ev):
        super().moveEvent(ev)
        # 覆盖层是子控件，几何随父窗口本地坐标不变；仅摸头动画进行时才需重新对齐+置顶，
        # 拖动时每个 move 事件都同步是白费功夫，会拖慢跟手。
        if getattr(self, "_petting_overlay_t", -1.0) >= 0.0 and hasattr(self, "_petting_overlay"):
            self._petting_overlay.sync_geometry()
        # 动画播放期间跳过气泡更新，由 _act_tick 统一处理；避免每帧 moveEvent 触发重复的智能定位计算
        if (self._drag_off is None and self._drag_candidate_off is None
                and not (hasattr(self, "_act_timer") and self._act_timer.isActive())  # 动画期间不在这里更新
                and hasattr(self, "_chat_bubble") and self._chat_bubble.isVisible()):
            self._chat_bubble.update_position()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._schedule_layer_sync()

    def _rebuild_and_restore_pos(self):
        """切换形象时重建渲染器，并把新模型统一停到屏幕右下角（任务栏上方）、紧贴边角不留空。

        按需求：每次切换都回到右下角，不再沿用各模型上次的自由位置/贴边状态。"""
        self._build_renderer()
        # 立即先停到右下角；渲染器此刻还没出图、量不到透明留白，
        # 等它出一帧后再贴一次，让模型"本体"真正贴住边角。
        self._dock_bottom_right()
        QTimer.singleShot(500, self._dock_bottom_right)

        # 切换模型后重新启动聊天系统（延迟2秒，避免立即弹出气泡）
        if hasattr(self, '_chat_manager') and self._chat_manager and self.cfg.get("chat_enabled", True):
            QTimer.singleShot(2000, self._chat_manager._schedule_next)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._toggle_visible()


class RegionOverlay(QWidget):
    """拖拽框选覆盖层：叠在预览框上，让用户拖出矩形来指定模型的显示区域和画框大小。"""

    region_selected = Signal(float, float, float, float)   # 归一化 x0,y0,x1,y1

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._start = None
        self._cur = None
        self.resize(parent.size())
        self.raise_()
        self.show()
        self.setFocus()

    def _sel_rect(self):
        if self._start is None or self._cur is None:
            return None
        return QRect(self._start, self._cur).normalized()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 半透明暗色遮罩
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        r = self._sel_rect()
        if r and r.width() > 4 and r.height() > 4:
            # 镂空选区——清掉暗色，让预览透出
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            # 虚线选框
            pen = QPen(QColor(91, 184, 245), 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(0, 0, -1, -1))
            # 四角实心小方块
            p.setBrush(QColor(91, 184, 245))
            p.setPen(Qt.NoPen)
            sz = 7
            for cx2, cy2 in ((r.left(), r.top()), (r.right(), r.top()),
                             (r.left(), r.bottom()), (r.right(), r.bottom())):
                p.drawRect(cx2 - sz // 2, cy2 - sz // 2, sz, sz)
        else:
            # 还没开始拖，显示提示文字
            p.setPen(QColor(255, 255, 255, 210))
            f = p.font()
            f.setPointSize(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "拖拽鼠标选择要显示的区域\nESC 取消")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = e.position().toPoint()
            self._cur = self._start
            self.update()

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self._cur = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._start is not None:
            r = self._sel_rect()
            if r and r.width() > 10 and r.height() > 10:
                w, h = max(1, self.width()), max(1, self.height())
                self.region_selected.emit(
                    r.left() / w, r.top() / h,
                    r.right() / w, r.bottom() / h)
            self.hide()
            self.deleteLater()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            self.deleteLater()
        else:
            super().keyPressEvent(e)


class Live2DPicker(QDialog):
    """带实时预览的 Live2D 模型选择器：左侧可搜索列表，右侧实时预览 + 构图微调。"""

    SIZE_MIN, SIZE_MAX = 120, 520      # 显示尺寸(在桌面上的窗口边长 px)滑块范围
    PREVIEW_DEBOUNCE_MS = 420          # 选中后稍等再加载预览：连续翻列表不会每个都重载，杜绝卡死

    def __init__(self, parent, models, current, views, rescan=None, size_px=300,
                 pinned=None, feature_provider=None):
        super().__init__(parent)
        self.setWindowTitle("选择 Live2D 模型")
        self.setMinimumSize(720, 660)
        self._views = views or {}                 # {规范化路径: {zoom,xoff,yoff,ratio,size,pos}}
        self._pinned = set(pinned or [])           # 要置顶的"常用"模型(规范化路径集合)
        self._fav_dir = get_fav_dir()              # 物理"常用"文件夹：放进来的模型一律置顶（刷新后仍生效）
        self._rescan = rescan                      # 可选：重新扫描模型目录的回调，返回 [(group,variant,path)]
        self._feature_provider = feature_provider
        self._zoom, self._xoff, self._yoff = 1.0, 0.0, 0.0
        self._ratio = None                         # None=按模型画布自动
        self.zoom, self.xoff, self.yoff, self.ratio = 1.0, 0.0, 0.0, None
        self.size_px = int(size_px)                # 在桌面上的显示尺寸（点"应用"后生效）
        self.selected_path = None
        self._sel_path = None
        self._preview = None
        self._preview_path = None
        self._pending_path = None                  # 防抖：待加载预览的模型路径
        self._preview_timer = QTimer(self)         # 选择稳定后才真正加载预览
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._do_load_preview)

        # 模型列表整理成 [(显示名, 路径)]
        self._items = self._build_items(models, self._feature_provider)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # 左：搜索（+刷新）+ 列表
        left = QVBoxLayout()
        left.setSpacing(8)
        srow = QHBoxLayout()
        srow.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索模型名…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._rebuild)
        srow.addWidget(self.search, 1)
        if self._rescan is not None:
            refresh = QPushButton("🔄")
            refresh.setFixedWidth(40)
            refresh.setToolTip("重新扫描模型文件夹：把新模型放进 live2d/（含子文件夹）后点这里，无需重启")
            refresh.clicked.connect(self._refresh_models)
            srow.addWidget(refresh, 0)
        left.addLayout(srow)
        # 按能力筛选：只看带配套语音 / 带动作的模型
        frow = QHBoxLayout()
        frow.setSpacing(10)
        self.chk_sound = QCheckBox("🔊 有声音")
        self.chk_sound.setToolTip("只显示自带语音（voice/*.wav）的模型")
        self.chk_sound.stateChanged.connect(lambda _=0: self._rebuild())
        self.chk_motion = QCheckBox("🎬 有动作")
        self.chk_motion.setToolTip("只显示自带动作的模型")
        self.chk_motion.stateChanged.connect(lambda _=0: self._rebuild())
        frow.addWidget(self.chk_sound)
        frow.addWidget(self.chk_motion)
        frow.addStretch(1)
        left.addLayout(frow)
        self.listw = QListWidget()
        self.listw.setMinimumWidth(250)
        self.listw.setSelectionMode(QListWidget.ExtendedSelection)  # 支持多选
        self.listw.currentItemChanged.connect(self._on_select)
        left.addWidget(self.listw, 1)

        # 删除按钮
        delete_row = QHBoxLayout()
        delete_row.setSpacing(8)
        self.delete_btn = QPushButton("🗑️ 删除选中")
        self.delete_btn.setToolTip("删除选中的模型（支持多选）\n文件会移到回收站，可恢复")
        self.delete_btn.clicked.connect(self._delete_selected_models)
        delete_row.addWidget(self.delete_btn)
        delete_row.addStretch(1)
        left.addLayout(delete_row)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#627d98;")
        left.addWidget(self.count_lbl)
        root.addLayout(left, 0)

        # 右：预览 + 构图按钮 + 确定取消
        right = QVBoxLayout()
        right.setSpacing(10)
        self.preview_box = QWidget()
        self.preview_box.setFixedSize(360, 470)
        self.preview_box.setStyleSheet(
            "background:#eef4fa;border:1px solid #d9e2ec;border-radius:14px;")
        self.preview_lay = QVBoxLayout(self.preview_box)
        self.preview_lay.setContentsMargins(0, 0, 0, 0)
        self.preview_lay.setAlignment(Qt.AlignCenter)
        self.preview_view = QWidget(self.preview_box)
        self.preview_view.setAttribute(Qt.WA_StyledBackground, True)
        self.preview_view.setAutoFillBackground(True)
        self.preview_view.setStyleSheet(
            "background:#eef4fa;border:none;border-radius:12px;")
        self.preview_view.setFixedSize(316, 448)
        self.preview_view_lay = QVBoxLayout(self.preview_view)
        self.preview_view_lay.setContentsMargins(0, 0, 0, 0)
        self.preview_view_lay.setAlignment(Qt.AlignCenter)
        self.hint = QLabel("← 选个模型看看")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color:#9fb3c8;border:none;background:transparent;")
        self.preview_view_lay.addWidget(self.hint)
        self.preview_lay.addWidget(self.preview_view, 0, Qt.AlignCenter)
        right.addWidget(self.preview_box, 0, Qt.AlignHCenter)

        # 显示尺寸：贴到桌面后宠物有多大（直接对应窗口边长，点"应用"生效）
        srow2 = QHBoxLayout()
        srow2.setSpacing(8)
        slbl = QLabel("显示尺寸")
        slbl.setFixedWidth(64)
        srow2.addWidget(slbl)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(0, 100)
        self.size_slider.setToolTip("拖动改变这个宠物贴到桌面后的大小")
        self.size_slider.valueChanged.connect(self._on_size_slider)
        srow2.addWidget(self.size_slider, 1)
        self.size_val = QLabel("")
        self.size_val.setFixedWidth(48)
        self.size_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.size_val.setStyleSheet("color:#627d98;")
        srow2.addWidget(self.size_val)
        right.addLayout(srow2)
        self.size_slider.setValue(self._size_to_slider(self.size_px))
        self.size_val.setText("%d px" % self.size_px)

        # 模型缩放：模型在画框里占多大（手展开放不下就调小）
        zrow = QHBoxLayout()
        zrow.setSpacing(8)
        zlbl = QLabel("模型缩放")
        zlbl.setFixedWidth(64)
        zrow.addWidget(zlbl)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 100)
        self.zoom_slider.setToolTip("拖动改变模型在画框里的大小（手展开放不下就调小）")
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        zrow.addWidget(self.zoom_slider, 1)
        right.addLayout(zrow)
        # 画框形状：往左更宽（容下展开的手），往右更高（容下站姿全身）
        rrow = QHBoxLayout()
        rrow.setSpacing(8)
        rlbl = QLabel("画框 宽⟷高")
        rlbl.setFixedWidth(64)
        rrow.addWidget(rlbl)
        self.ratio_slider = QSlider(Qt.Horizontal)
        self.ratio_slider.setRange(0, 100)
        self.ratio_slider.setToolTip("拖动改变画框形状：往左更宽、往右更高")
        self.ratio_slider.valueChanged.connect(self._on_ratio_slider)
        rrow.addWidget(self.ratio_slider, 1)
        right.addLayout(rrow)
        # 位置 + 自动贴合 + 框选范围 + 复位 + 换动作
        tools = QHBoxLayout()
        tools.setSpacing(6)
        for txt, fn, tip in (
            ("↑", lambda: self._adj(1.0, 0.0, 0.08), "上移"),
            ("↓", lambda: self._adj(1.0, 0.0, -0.08), "下移"),
            ("←", lambda: self._adj(1.0, -0.08, 0.0), "左移"),
            ("→", lambda: self._adj(1.0, 0.08, 0.0), "右移"),
            ("框选范围", lambda: self._start_region_select(), "在预览上拖出矩形，自由选择模型显示区域和画框大小"),
            ("复位", lambda: self._reset(), "回到模型原始整体显示"),
            ("换动作", lambda: self._motion(), "随机播放一个动作"),
        ):
            b = QPushButton(txt)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tools.addWidget(b)
        right.addLayout(tools)
        tiph = QLabel("「显示尺寸」决定贴到桌面后多大；「模型缩放」调模型在画框里的占比；「画框 宽⟷高」改画框形状；↑↓←→挪位置；点「框选范围」后在预览上拖出矩形即可框住想显示的部位；「复位」回到默认整体显示。尺寸/构图都按每个模型各自记忆。")
        tiph.setWordWrap(True)
        tiph.setStyleSheet("color:#627d98;")
        right.addWidget(tiph)
        right.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setStyleSheet("background:#e2e8f0;color:#334e68;")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("应用")
        ok.clicked.connect(self._apply)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        right.addLayout(btns)
        root.addLayout(right, 1)

        self._rebuild("")
        # 延迟到对话框真正显示后再选中并(防抖)加载预览：点开菜单立刻弹窗，不再"卡一下才出来"
        QTimer.singleShot(0, lambda: self._select_current(current))

    def _view_for_path(self, path):
        """读取某模型的独立构图/尺寸；新模型返回干净默认值，不串用上一只模型。"""
        v = dict(self._views.get(_canon_path(path)) or {})
        r = v.get("ratio", None)
        pos = v.get("pos")
        return {
            "zoom": float(v.get("zoom", 1.0)),
            "xoff": float(v.get("xoff", 0.0)),
            "yoff": float(v.get("yoff", 0.0)),
            "ratio": (float(r) if r else None),
            "size": (int(v["size"]) if v.get("size") else None),
            "pos": ([int(pos[0]), int(pos[1])]
                    if isinstance(pos, (list, tuple)) and len(pos) == 2 else None),
        }

    def _apply_picker_state(self, path):
        """切到指定模型时，把选择器内部状态重置为该模型自己的独立参数。"""
        v = self._view_for_path(path)
        self._zoom = float(v.get("zoom", 1.0))
        self._xoff = float(v.get("xoff", 0.0))
        self._yoff = float(v.get("yoff", 0.0))
        self._ratio = v.get("ratio", None)
        self.size_px = int(v.get("size") or DEFAULT_LIVE2D_SIZE)
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(self._size_to_slider(self.size_px))
        self.size_slider.blockSignals(False)
        self.size_val.setText("%d px" % self.size_px)
        return v

    def _add_header(self, text):
        """往列表里加一行不可选中的分组标题。"""
        it = QListWidgetItem(text)
        it.setFlags(Qt.NoItemFlags)              # 不可选中/点击
        it.setForeground(QColor("#9fb3c8"))
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        self.listw.addItem(it)

    @staticmethod
    def _build_items(models, feature_provider=None):
        """[(group, variant, path), ...] -> [ {label, path, has_sound, has_motion, motions}, ... ]。"""
        items = []
        for group, variant, path in models:
            label = group if variant in ("default", "model") else f"{group} · {variant}"
            try:
                feat = feature_provider(path) if feature_provider else None
                if feat is None:
                    from live2d_pet import model_features
                    feat = model_features(path)
            except Exception:  # noqa: BLE001
                feat = {"has_sound": False, "has_motion": False, "motions": 0}
            items.append({"label": label, "path": path,
                          "has_sound": feat["has_sound"], "has_motion": feat["has_motion"],
                          "motions": feat["motions"]})
        return items

    def _refresh_models(self):
        """重新扫描模型目录并刷新列表，尽量保留当前选中项。"""
        if self._rescan is None:
            return
        cur = self.listw.currentItem()
        keep = cur.data(Qt.UserRole) if cur is not None else None
        try:
            self._items = self._build_items(self._rescan(), self._feature_provider)
        except Exception as e:  # noqa: BLE001
            self.count_lbl.setText("刷新失败：%s" % e)
            return
        self._rebuild(self.search.text())
        if keep:
            self._select_current(keep)

    def _delete_selected_models(self):
        """删除选中的模型到回收站（支持批量删除）。"""
        from PySide6.QtWidgets import QMessageBox

        selected = self.listw.selectedItems()
        if not selected:
            QMessageBox.information(self, "提示", "请先选择要删除的模型")
            return

        # 过滤掉分组标题（没有路径的项）
        valid_items = [it for it in selected if it.data(Qt.UserRole) is not None]

        if not valid_items:
            QMessageBox.information(self, "提示", "请选择具体的模型（不要选择分组标题）")
            return

        # 确认对话框
        count = len(valid_items)
        names = "\n".join(f"• {it.text()}" for it in valid_items[:5])
        if count > 5:
            names += f"\n... 等 {count} 个模型"

        msg = f"确定要删除以下 {count} 个模型吗？\n\n{names}\n\n文件将移到回收站，可以恢复。"
        reply = QMessageBox.question(
            self, "确认删除", msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        # 检查 send2trash 是否可用
        try:
            from send2trash import send2trash
        except ImportError:
            QMessageBox.warning(
                self, "错误",
                "删除功能需要 send2trash 库\n请运行：pip install send2trash"
            )
            return

        # 收集要删除的文件夹
        folders_to_delete = set()
        for it in valid_items:
            model_path = it.data(Qt.UserRole)
            if not model_path:
                continue

            # 获取模型文件夹（model.json 所在的文件夹）
            model_folder = os.path.dirname(os.path.abspath(model_path))

            # 检查文件夹是否存在
            if not os.path.exists(model_folder):
                continue

            folders_to_delete.add(model_folder)

        if not folders_to_delete:
            QMessageBox.warning(self, "错误", "未找到可删除的模型文件夹")
            return

        # 执行删除
        success_count = 0
        failed = []

        for folder in folders_to_delete:
            try:
                # 确保路径是绝对路径
                folder = os.path.abspath(folder)
                send2trash(folder)
                success_count += 1
            except Exception as e:
                folder_name = os.path.basename(folder)
                error_msg = str(e)
                failed.append((folder_name, error_msg))

        # 刷新列表
        if success_count > 0:
            try:
                self._refresh_models()
            except Exception:
                pass

        # 显示结果
        if success_count == 0 and failed:
            # 全部失败
            result_msg = f"删除失败！共 {len(failed)} 个\n\n"
            for name, err in failed[:5]:
                result_msg += f"• {name}:\n  {err}\n\n"
            if len(failed) > 5:
                result_msg += f"... 还有 {len(failed) - 5} 个失败"
            QMessageBox.critical(self, "删除失败", result_msg)
        elif failed:
            # 部分失败
            result_msg = f"成功删除 {success_count} 个模型\n\n"
            result_msg += f"失败 {len(failed)} 个："
            for name, err in failed[:3]:
                result_msg += f"\n• {name}: {err}"
            if len(failed) > 3:
                result_msg += f"\n... 还有 {len(failed) - 3} 个"
            QMessageBox.warning(self, "部分成功", result_msg)
        else:
            # 全部成功
            QMessageBox.information(self, "删除完成", f"成功删除 {success_count} 个模型到回收站")

    def _rebuild(self, _=None):
        f = self.search.text().strip().lower() if hasattr(self, "search") else ""
        only_sound = self.chk_sound.isChecked() if hasattr(self, "chk_sound") else False
        only_motion = self.chk_motion.isChecked() if hasattr(self, "chk_motion") else False
        self.listw.blockSignals(True)
        self.listw.clear()
        # 分四档：常用 → 有声音 → 有动作(无声音) → 其它，让"自带声音/动作"的模型优先靠前
        pinned, t_sound, t_motion, t_other = [], [], [], []
        for it in self._items:
            if f and f not in it["label"].lower():
                continue
            if only_sound and not it["has_sound"]:
                continue
            if only_motion and not it["has_motion"]:
                continue
            if _canon_path(it["path"]) in self._pinned or _under_dir(it["path"], self._fav_dir):
                pinned.append(it)
            elif it["has_sound"]:
                t_sound.append(it)
            elif it["has_motion"]:
                t_motion.append(it)
            else:
                t_other.append(it)

        def add_items(header, group, star=False):
            if not group:
                return
            self._add_header(header)
            for it in sorted(group, key=lambda x: x["label"].lower()):
                badge = ("🔊" if it["has_sound"] else "") + ("🎬" if it["has_motion"] else "")
                text = ("⭐ " if star else "") + it["label"] + (f"  {badge}" if badge else "")
                wi = QListWidgetItem(text)
                wi.setData(Qt.UserRole, it["path"])
                self.listw.addItem(wi)

        add_items("⭐ 常用", pinned, star=True)
        add_items("🔊 有声音", t_sound)
        add_items("🎬 有动作", t_motion)
        add_items("其它模型", t_other)

        self.listw.blockSignals(False)
        shown = len(pinned) + len(t_sound) + len(t_motion) + len(t_other)
        self.count_lbl.setText(
            f"共 {shown} / {len(self._items)} 个 · 🔊{len(t_sound)} 🎬{len(t_motion)}"
            + (f" · ⭐{len(pinned)}" if pinned else ""))

    def _select_current(self, current):
        want = _canon_path(current)
        if not want:
            return
        for i in range(self.listw.count()):
            p = self.listw.item(i).data(Qt.UserRole)
            if p and _canon_path(p) == want:
                self.listw.setCurrentRow(i)
                self.listw.scrollToItem(self.listw.item(i))
                return

    def _on_select(self, cur, _prev):
        """选中变化：立刻清掉旧预览并显示"加载中"，真正加载推迟到选择稳定后(防抖)。
        这样连续上下翻列表不会每个都去 new 一个 Live2D 渲染器，从根本上消除卡死。"""
        path = cur.data(Qt.UserRole) if cur is not None else None
        self._pending_path = path
        self._preview_timer.stop()
        if path is None:                 # 选到分组标题等无路径项：只清空
            self._clear_preview()
            return
        self.hint.setText("加载中…")
        self.hint.show()
        self._preview_timer.start(self.PREVIEW_DEBOUNCE_MS)

    def _do_load_preview(self):
        """防抖到期：仅当对话框还在、且选中项没再变时，才真正加载预览。"""
        if not self.isVisible():
            return
        cur = self.listw.currentItem()
        path = cur.data(Qt.UserRole) if cur is not None else None
        if path is None or path != self._pending_path:
            return
        self._load_preview(path)

    def _clear_preview(self):
        """清理预览渲染器，释放OpenGL资源和内存。"""
        if self._preview is not None:
            try:
                # 确保在OpenGL上下文中清理
                self._preview.makeCurrent()
                self._preview.shutdown()
                self._preview.doneCurrent()
            except Exception:
                pass
            try:
                # 从布局中移除
                self.preview_view_lay.removeWidget(self._preview)
                self._preview.setParent(None)
                self._preview.deleteLater()
            except Exception:
                pass
            self._preview = None
            self._preview_path = None

    def _load_preview(self, path):
        """加载预览：直接加载，不延迟。"""
        v = self._apply_picker_state(path)
        # 预览区比桌面实模更容易受旧 FBO/旧模型状态影响，统一走“清旧 -> 重建”，
        # 不再复用旧 preview 控件热切模型，避免出现侧边残影、镜像条、初始位置偏移。
        self._clear_preview()

        self._spawn_preview(path)
        self._sync_sliders()
        QTimer.singleShot(220, self._sync_sliders)

    def _fit_region(self, top, bottom):
        """让预览贴合指定竖直区间，并把结果读回选择器状态（供「应用」保存）。"""
        if self._preview is None:
            return
        try:
            if self._preview.fit_to_content(top, bottom):
                self._zoom, self._xoff, self._yoff = self._preview.get_view()
                self._ratio = self._preview.height_ratio()
        except Exception:
            pass
        self._sync_sliders()
        QTimer.singleShot(0, self._fit_preview)

    def _spawn_preview(self, path):
        """创建预览渲染器，使用较小的尺寸减少内存占用。"""
        from live2d_pet import Live2DPet
        try:
            # 使用较小的预览尺寸，减少内存占用
            preview_size = 220
            pv = Live2DPet(path, preview_size, self._zoom, self._xoff, self._yoff,
                           self.preview_view, ratio=self._ratio, preview_mode=True)
            pv.set_follow(False)
            pv.on_error = lambda *a: self._preview_failed()
            pv.on_resized = self._fit_preview
            self.preview_view_lay.addWidget(pv, 0, Qt.AlignCenter)
            pv.show()
            self._preview = pv
            self._preview_path = path
            self._sel_path = path
            self.hint.hide()
            # 预览新建后补一轮“复位态”同步，确保内部画布/外层 QWidget 从干净状态起步，
            # 避免某些模型首帧右侧挂出细条残影，只有点复位才恢复。
            QTimer.singleShot(0, lambda: self._preview and self._preview.set_view(self._zoom, self._xoff, self._yoff))
        except Exception as e:  # noqa: BLE001
            self._sel_path = None
            self._preview_path = None
            self.hint.setText("无法预览：%s" % e)
            self.hint.show()

    def _fit_preview(self):
        """预览随模型画布比例变化后，缩到不超过预览框。"""
        pv = self._preview
        if pv is None:
            return
        max_w = max(180, self.preview_view.width())
        max_h = max(220, self.preview_view.height())
        cur_w = max(1, pv.width())
        cur_h = max(1, pv.height())
        if cur_w <= max_w and cur_h <= max_h:
            return
        ratio = max(cur_w / float(max_w), cur_h / float(max_h))
        new_size = max(self.SIZE_MIN, int(round(pv.live2d_size() / ratio)))
        if new_size < pv.live2d_size():
            pv.set_live2d_size(new_size)

    def _preview_failed(self):
        self._clear_preview()
        self.hint.setText("⚠ 该模型无法渲染")
        self.hint.show()

    def _slider_to_zoom(self, v):
        return 0.25 + (v / 100.0) * (3.0 - 0.25)

    def _zoom_to_slider(self, z):
        return int(round((max(0.25, min(3.0, z)) - 0.25) / (3.0 - 0.25) * 100))

    def _slider_to_ratio(self, v):
        return 0.55 + (v / 100.0) * (2.8 - 0.55)

    def _ratio_to_slider(self, r):
        return int(round((max(0.55, min(2.8, r)) - 0.55) / (2.8 - 0.55) * 100))

    def _slider_to_size(self, v):
        return int(round(self.SIZE_MIN + (v / 100.0) * (self.SIZE_MAX - self.SIZE_MIN)))

    def _size_to_slider(self, s):
        s = max(self.SIZE_MIN, min(self.SIZE_MAX, int(s)))
        return int(round((s - self.SIZE_MIN) / (self.SIZE_MAX - self.SIZE_MIN) * 100))

    def _on_size_slider(self, v):
        self.size_px = self._slider_to_size(v)
        self.size_val.setText("%d px" % self.size_px)

    def _on_zoom_slider(self, v):
        self._zoom = max(0.2, min(5.0, self._slider_to_zoom(v)))
        if self._preview is not None:
            self._preview.set_view(self._zoom, self._xoff, self._yoff)

    def _on_ratio_slider(self, v):
        self._ratio = self._slider_to_ratio(v)
        if self._preview is not None and hasattr(self._preview, "set_height_ratio"):
            self._preview.set_height_ratio(self._ratio)
            QTimer.singleShot(0, self._fit_preview)

    def _sync_sliders(self):
        """把当前 zoom/ratio 反映到两个滑块（屏蔽信号，避免回环触发）。"""
        r = self._ratio
        if not r and self._preview is not None:
            r = self._preview.height_ratio()
        for sl, val in ((self.zoom_slider, self._zoom_to_slider(self._zoom)),
                        (self.ratio_slider, self._ratio_to_slider(r or 1.4))):
            sl.blockSignals(True)
            sl.setValue(val)
            sl.blockSignals(False)

    def _adj(self, zmul, dxoff, dyoff):
        self._zoom = max(0.2, min(5.0, self._zoom * zmul))
        self._xoff = max(-2.0, min(2.0, self._xoff + dxoff))
        self._yoff = max(-2.0, min(2.0, self._yoff + dyoff))
        if self._preview is not None:
            self._preview.set_view(self._zoom, self._xoff, self._yoff)
            QTimer.singleShot(0, self.preview_view.update)

    def _adj_ratio(self, d):
        base = self._preview.height_ratio() if self._preview is not None else (self._ratio or 1.4)
        self._ratio = max(0.55, min(2.8, base + d))   # 改为手动比例
        if self._sel_path:
            self._clear_preview()
            self._spawn_preview(self._sel_path)

    def _reset(self):
        # 复位：构图归位 + 画布回到自动
        self._zoom, self._xoff, self._yoff, self._ratio = 1.0, 0.0, 0.0, None
        if self._sel_path:
            self._clear_preview()
            self._spawn_preview(self._sel_path)
        self._sync_sliders()
        QTimer.singleShot(220, self._sync_sliders)

    def _motion(self):
        if self._preview is not None:
            self._preview.play_motion("", None)

    def _start_region_select(self):
        """在预览框上叠加框选覆盖层，用户拖拽来自由定义模型的显示区域。"""
        if self._preview is None:
            return
        overlay = RegionOverlay(self.preview_box)
        overlay.resize(self.preview_box.size())
        overlay.region_selected.connect(self._apply_region)
        overlay.show()
        overlay.raise_()
        overlay.setFocus()

    def _apply_region(self, x0, y0, x1, y1):
        """把归一化的框选矩形转换为 zoom/xoff/yoff/ratio，实时应用到预览并同步滑块。"""
        pv = self._preview
        if pv is None:
            return
        # 框选层铺满整个预览框，但模型画面是居中的一小块（四周有留白）。
        # 先把"相对预览框"的坐标换算成"相对模型画面"的坐标，框选定位才准。
        bw, bh = max(1, self.preview_box.width()), max(1, self.preview_box.height())
        gx, gy, gw, gh = pv.x(), pv.y(), max(1, pv.width()), max(1, pv.height())

        def _remap(fx, fy):
            return (max(0.0, min(1.0, (fx * bw - gx) / gw)),
                    max(0.0, min(1.0, (fy * bh - gy) / gh)))

        x0, y0 = _remap(x0, y0)
        x1, y1 = _remap(x1, y1)
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:      # 选区基本落在留白里，忽略
            return
        cw = max(0.01, x1 - x0)
        ch = max(0.01, y1 - y0)
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        target = 0.85
        # 居中：把选区中心平移到画布中心
        self._xoff = max(-2.0, min(2.0, self._xoff + (0.5 - cx) * 1.7))
        self._yoff = max(-2.0, min(2.0, self._yoff + (cy - 0.5) * 1.7))
        # 缩放：让选区铺满画布（留 target 余量，防止动画时出界）
        f = max(0.25, min(4.0, target / max(cw, ch)))
        self._zoom = max(0.2, min(5.0, self._zoom * f))
        # 画框比例：按选区高宽比调整
        base_ratio = self._ratio if self._ratio else (
            self._preview.height_ratio() if self._preview else 1.4)
        self._ratio = max(0.55, min(2.8, base_ratio * (ch / cw)))
        self._preview.set_view(self._zoom, self._xoff, self._yoff)
        if hasattr(self._preview, "set_height_ratio"):
            self._preview.set_height_ratio(self._ratio)
            QTimer.singleShot(0, self._fit_preview)
        self._sync_sliders()

    def _apply(self):
        if self._sel_path:
            self.selected_path = self._sel_path
            self.zoom, self.xoff, self.yoff = self._zoom, self._xoff, self._yoff
            self.ratio = self._ratio
            self.accept()

    def done(self, r):
        """关闭对话框时彻底清理资源。"""
        self._preview_timer.stop()
        self._clear_preview()
        super().done(r)


# ══════════════════════════════════════════════════════════════
#  语录管理对话框
# ══════════════════════════════════════════════════════════════

class QuoteManagerDialog(QDialog):
    """聊天语录管理对话框：增删改查自定义语录和内置语录。"""

    def __init__(self, chat_manager, parent=None):
        super().__init__(parent)
        self.chat_manager = chat_manager
        self.setWindowTitle("管理聊天语录")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(800, 600)

        # 加载所有语录（内置 + 自定义）
        self._builtin_category = {}      # {语录: 分类标签}，用于按分类分组显示
        self.builtin_quotes = self._load_builtin_quotes()
        self.custom_quotes = self._load_custom_quotes()

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 顶部说明
        tip = QLabel(
            "💬 <b>聊天语录管理</b><br>"
            "查看、编辑、删除现有语录，或添加新的自定义语录。语录按分类分组显示"
            "（日常 / 时间问候 / 互动回应 / 情侣语录等）。<br>"
            "🔵 蓝色 = 自定义语录　⚪ 黑色 = 内置语录　删除会在所有播放场景生效"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#334e68; background:#e0f2fe; padding:12px; border-radius:8px;")
        layout.addWidget(tip)

        # 搜索栏
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索语录...")
        self.search_input.textChanged.connect(self._filter_quotes)
        search_row.addWidget(self.search_input)
        layout.addLayout(search_row)

        # 语录列表
        self.quote_list = QListWidget()
        self.quote_list.setAlternatingRowColors(True)
        self.quote_list.itemDoubleClicked.connect(self._edit_quote)
        layout.addWidget(self.quote_list, 1)

        # 统计信息（必须在 _refresh_list 之前创建）
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("color:#627d98;")
        layout.addWidget(self.stats_label)

        # 刷新列表
        self._refresh_list()

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        add_btn = QPushButton("➕ 添加")
        add_btn.clicked.connect(self._add_quote)
        btn_row.addWidget(add_btn)

        edit_btn = QPushButton("✏️ 编辑")
        edit_btn.clicked.connect(self._edit_quote)
        btn_row.addWidget(edit_btn)

        delete_btn = QPushButton("🗑️ 删除")
        delete_btn.clicked.connect(self._delete_quote)
        btn_row.addWidget(delete_btn)

        btn_row.addStretch(1)

        test_btn = QPushButton("🧪 测试")
        test_btn.setToolTip("随机显示一条语录")
        test_btn.clicked.connect(self._test_quote)
        btn_row.addWidget(test_btn)

        layout.addLayout(btn_row)

        # 底部按钮
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)

        import_btn = QPushButton("导入...")
        import_btn.setToolTip("从文本文件导入语录（每行一条）")
        import_btn.clicked.connect(self._import_quotes)
        bottom_row.addWidget(import_btn)

        export_btn = QPushButton("导出...")
        export_btn.setToolTip("导出所有自定义语录到文件")
        export_btn.clicked.connect(self._export_quotes)
        bottom_row.addWidget(export_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_row.addWidget(close_btn)

        layout.addLayout(bottom_row)

    def _all_builtin_quotes(self):
        """所有内置语录，返回 (文本, 分类标签) 列表；按分类聚合并按文本去重（先出现的分类为准）。
        覆盖全部来源：日常语录 / 时间问候 / 互动回应 / 情侣语录。"""
        from chat_bubble import (
            INSPIRATIONAL, LIFE_CARE, LIFE_TIPS, CASUAL_CHAT, JOKES,
            TIME_GREETINGS, CONTEXT_MESSAGES,
        )
        groups = [
            ("励志鼓励", list(INSPIRATIONAL)),
            ("生活关心", list(LIFE_CARE)),
            ("生活小贴士", list(LIFE_TIPS)),
            ("闲聊", list(CASUAL_CHAT)),
            ("笑话", list(JOKES)),
            ("时间问候", [q for v in TIME_GREETINGS.values() for q in v]),
            ("互动回应", [q for v in CONTEXT_MESSAGES.values() for q in v]),
        ]
        try:
            from companion_quotes import COMPANION_LINES
            groups.append(("情侣语录（仅伴侣模式）", list(COMPANION_LINES)))
        except Exception:
            pass

        seen = set()
        out = []
        for label, items in groups:
            for q in items:
                if q and q not in seen:
                    seen.add(q)
                    out.append((q, label))
        return out

    def _load_deleted_set(self):
        """读取被删除的内置语录集合。"""
        f = os.path.join(config.CONFIG_DIR, "deleted_builtin_quotes.json")
        if os.path.exists(f):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    return set(data)
            except Exception:
                pass
        return set()

    def _load_builtin_quotes(self):
        """加载所有内置语录（排除已删除的），同时记录每条的分类用于分组显示。"""
        deleted = self._load_deleted_set()
        self._builtin_category = {}
        out = []
        for q, label in self._all_builtin_quotes():
            self._builtin_category[q] = label
            if q not in deleted:
                out.append(q)
        return out

    def _load_custom_quotes(self):
        """加载自定义语录（JSON文件）。"""
        quotes_file = os.path.join(config.CONFIG_DIR, "custom_quotes.json")
        if os.path.exists(quotes_file):
            try:
                with open(quotes_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_custom_quotes(self):
        """保存自定义语录和修改后的内置语录。"""
        quotes_file = os.path.join(config.CONFIG_DIR, "custom_quotes.json")
        deleted_builtin_file = os.path.join(config.CONFIG_DIR, "deleted_builtin_quotes.json")

        try:
            # 保存自定义语录
            with open(quotes_file, "w", encoding="utf-8") as f:
                json.dump(self.custom_quotes, f, ensure_ascii=False, indent=2)

            # 保存被删除的内置语录（与"全部内置"对比，确保历史删除也累积保留）
            all_builtin = [q for q, _ in self._all_builtin_quotes()]
            deleted = [q for q in all_builtin if q not in self.builtin_quotes]
            with open(deleted_builtin_file, "w", encoding="utf-8") as f:
                json.dump(deleted, f, ensure_ascii=False, indent=2)

            # 通知 ChatManager 重新加载
            if hasattr(self.chat_manager, 'reload_custom_quotes'):
                self.chat_manager.reload_custom_quotes()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存语录文件：{e}")

    def _refresh_list(self, filter_text=""):
        """刷新语录列表：内置语录按分类分组显示（每组一个标题行），自定义语录单独一组。"""
        self.quote_list.clear()
        ft = (filter_text or "").lower().strip()

        # 内置语录按分类分组（保持 _all_builtin_quotes 的分类先后顺序）
        cats = []           # [(分类, [语录...])]，保持插入顺序
        index = {}
        for q in self.builtin_quotes:
            c = self._builtin_category.get(q, "其他")
            if c not in index:
                index[c] = len(cats)
                cats.append((c, []))
            cats[index[c]][1].append(q)

        for label, items in cats:
            visible = [q for q in items if not ft or ft in q.lower()]
            if not visible:
                continue
            self._add_header(f"──  {label}（{len(visible)}）  ──")
            for q in visible:
                item = QListWidgetItem(q)
                item.setData(Qt.UserRole, "builtin")
                self.quote_list.addItem(item)

        # 自定义语录（蓝色）
        cust_visible = [q for q in self.custom_quotes if not ft or ft in q.lower()]
        if cust_visible:
            self._add_header(f"──  自定义（{len(cust_visible)}）  ──")
            for q in cust_visible:
                item = QListWidgetItem(q)
                item.setData(Qt.UserRole, "custom")
                item.setForeground(QColor("#2563eb"))  # 蓝色
                self.quote_list.addItem(item)

        self._update_stats()

    def _add_header(self, text):
        """往列表里加一个不可选中的分类标题行。"""
        h = QListWidgetItem(text)
        h.setData(Qt.UserRole, "header")
        h.setFlags(Qt.NoItemFlags)        # 不可选 / 不可交互
        font = h.font()
        font.setBold(True)
        h.setFont(font)
        h.setForeground(QColor("#94a3b8"))
        self.quote_list.addItem(h)

    def _filter_quotes(self, text):
        """根据搜索框过滤语录。"""
        self._refresh_list(text)

    def _update_stats(self):
        """更新统计信息（不计分类标题行）。"""
        total_builtin = len(self.builtin_quotes)
        total_custom = len(self.custom_quotes)
        total = total_builtin + total_custom
        shown = sum(1 for i in range(self.quote_list.count())
                    if self.quote_list.item(i).data(Qt.UserRole) != "header")

        if shown == total:
            self.stats_label.setText(
                f"共 {total} 条语录（内置 {total_builtin} 条，自定义 {total_custom} 条）"
            )
        else:
            self.stats_label.setText(f"显示 {shown} / {total} 条")

    def _add_quote(self):
        """添加新语录。"""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "添加语录", "请输入新的语录：",
            QLineEdit.Normal, ""
        )
        if ok and text.strip():
            quote = text.strip()
            if quote not in self.custom_quotes:
                self.custom_quotes.append(quote)
                self._save_custom_quotes()
                self._refresh_list(self.search_input.text())
            else:
                QMessageBox.information(self, "提示", "该语录已存在")

    def _edit_quote(self):
        """编辑选中的语录。"""
        current_item = self.quote_list.currentItem()
        if not current_item or current_item.data(Qt.UserRole) == "header":
            QMessageBox.information(self, "提示", "请先选择要编辑的语录")
            return

        old_text = current_item.text()
        quote_type = current_item.data(Qt.UserRole)

        from PySide6.QtWidgets import QInputDialog
        new_text, ok = QInputDialog.getText(
            self, "编辑语录", "编辑语录：",
            QLineEdit.Normal, old_text
        )
        if ok and new_text.strip():
            new_quote = new_text.strip()
            if new_quote != old_text:
                try:
                    if quote_type == "builtin":
                        # 编辑内置语录：从内置列表删除，添加到自定义列表
                        index = self.builtin_quotes.index(old_text)
                        self.builtin_quotes.pop(index)
                        if new_quote not in self.custom_quotes:
                            self.custom_quotes.append(new_quote)
                        self._save_custom_quotes()
                    else:
                        # 编辑自定义语录
                        index = self.custom_quotes.index(old_text)
                        self.custom_quotes[index] = new_quote
                        self._save_custom_quotes()
                    self._refresh_list(self.search_input.text())
                except ValueError:
                    pass

    def _delete_quote(self):
        """删除选中的语录。"""
        current_item = self.quote_list.currentItem()
        if not current_item or current_item.data(Qt.UserRole) == "header":
            QMessageBox.information(self, "提示", "请先选择要删除的语录")
            return

        quote_type = current_item.data(Qt.UserRole)
        quote_text = current_item.text()

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除这条语录吗？\n\n{quote_text}\n\n"
            f"类型：{'自定义语录' if quote_type == 'custom' else '内置语录'}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                if quote_type == "builtin":
                    self.builtin_quotes.remove(quote_text)
                else:
                    self.custom_quotes.remove(quote_text)
                self._save_custom_quotes()
                self._refresh_list(self.search_input.text())
            except ValueError:
                pass

    def _test_quote(self):
        """测试：随机显示一条语录。"""
        all_quotes = self.builtin_quotes + self.custom_quotes
        if not all_quotes:
            QMessageBox.information(self, "提示", "没有可用的语录")
            return
        import random
        quote = random.choice(all_quotes)
        self.chat_manager.say(quote)

    def _import_quotes(self):
        """从文本文件导入语录（每行一条）。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入语录", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            imported = 0
            for line in lines:
                quote = line.strip()
                if quote and quote not in self.custom_quotes:
                    self.custom_quotes.append(quote)
                    imported += 1

            if imported > 0:
                self._save_custom_quotes()
                self._refresh_list(self.search_input.text())
                QMessageBox.information(self, "导入成功", f"成功导入 {imported} 条新语录")
            else:
                QMessageBox.information(self, "导入完成", "没有新的语录被导入（可能已存在）")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法读取文件：{e}")

    def _export_quotes(self):
        """导出所有自定义语录到文件。"""
        if not self.custom_quotes:
            QMessageBox.information(self, "提示", "没有可导出的语录")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出语录", "custom_quotes.txt", "文本文件 (*.txt)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                for quote in self.custom_quotes:
                    f.write(quote + "\n")
            QMessageBox.information(self, "导出成功", f"已导出 {len(self.custom_quotes)} 条语录")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"无法写入文件：{e}")


def main():
    QApplication.setQuitOnLastWindowClosed(False)
    # 关键：让所有 OpenGL 上下文共享资源。否则切换 / 预览 Live2D 模型时，
    # 新建的 QOpenGLWidget 会拿到独立上下文，live2d 的着色器还绑在旧上下文上，
    # 导致第二个之后的模型渲染空白/错位（就是"部分模型不显示全身"的真正原因）。
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    # 单实例检测：通过 QSharedMemory 实现
    from PySide6.QtCore import QSharedMemory
    shared_memory = QSharedMemory("DesktopPet_SingleInstance_Key")

    # 尝试附加到已存在的共享内存
    if shared_memory.attach():
        # 已有实例运行，显示提示并退出
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "提示", "桌面宠物已经在运行了！\n请在系统托盘查看。")
        return

    # 创建新的共享内存（标记实例运行中）
    if not shared_memory.create(1):
        # 创建失败，可能有其他实例
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "警告", "无法启动，可能已有实例运行。")
        return

    app.setStyleSheet(APP_QSS)
    # 用更干净的中文字体（系统没有就回退）
    f = app.font()
    for fam in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        f.setFamily(fam)
        break
    f.setPointSize(10)
    app.setFont(f)
    app.setWindowIcon(QIcon(render_icon("slime")))

    cfg = config.load()
    win = PetWindow(cfg)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
