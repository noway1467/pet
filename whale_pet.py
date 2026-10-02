"""鲸鱼娘精灵渲染器：保留全部公开动作，不依赖 Live2D 或新增运行库。"""
from collections import OrderedDict
import json
import math
from pathlib import Path
import random
import time

from PySide6.QtCore import Qt, QTimer, QSize, QRectF, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "whale"
ACTION_LABELS = {
    "IDLE": "待机", "BLINK": "眨眼", "GLANCE": "左右张望",
    "THINKING": "思考", "WALKING": "行走", "HAPPY": "开心",
    "HEAD_PAT": "摸头", "TALKING": "说话", "ANGRY": "生气",
    "POKE_REACT": "被戳", "TAIL_REACT": "甩尾", "EATING": "吃东西",
    "SWEEPING": "扫地", "SLEEPING": "睡觉", "DRAGGING": "被抓起",
    "FALLING": "下落", "LANDING": "落地", "DIZZY": "眩晕",
}
EXTRA_LABELS = {
    "idle_back": "背面", "walk_side_stand": "侧面站立",
    "walk_start_left": "向左起步", "walk_stop_left": "向左收步",
    "walk_start_right": "向右起步", "walk_stop_right": "向右收步",
    "sleep_enter": "入睡", "sleep_wake": "醒来", "released_airborne": "松手腾空",
}


class WhalePet(QWidget):
    """固定画布 + 按需帧缓存，切换动作不重建窗口、不缓存全部原图。"""
    walk_step = Signal(float)
    content_changed = Signal()
    CACHE_LIMIT = 8 * 1024 * 1024
    AUTO_ACTIONS = ("BLINK", "GLANCE", "THINKING", "HAPPY", "SWEEPING")

    def __init__(self, target_h=240, facing=1, auto_actions=True, parent=None,
                 asset_dir=None):
        super().__init__(parent)
        self.asset_dir = Path(asset_dir or ASSET_DIR).resolve()
        with (self.asset_dir / "manifest.json").open(encoding="utf-8") as stream:
            self.manifest = json.load(stream)
        self.assets = self.manifest["assets"]
        self.actions = self.manifest["actions"]
        self.facing = -1 if facing < 0 else 1
        self.auto_actions = bool(auto_actions)
        self.follow = False
        self._closed = False
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._state, self._asset = "IDLE", "idle_front"
        self._phase, self._duration, self._elapsed = "body", None, 0.0
        self._pending_state = None
        self._manual = False
        self._frame = QPixmap()
        self._frame_key = None
        self._walk_elapsed = 0.0
        self._next_auto = random.uniform(3.0, 6.0)
        self._idle_elapsed = 0.0
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.set_image_size(target_h)
        self._set_clip("idle_front", None)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self._last_tick = time.monotonic()

    @property
    def state(self):
        return self._state

    @property
    def cache_bytes(self):
        return self._cache_bytes

    def natural_size(self):
        return QSize(self.width(), self.height())

    def set_image_size(self, height):
        self._target_h = max(100, min(600, int(height)))
        # 所有帧按同一比例缩放，睡姿不会被强行拉成立姿。
        self._scale = self._target_h / 334.0
        self.setFixedSize(round(370 * self._scale), round(390 * self._scale))
        self._cache.clear()
        self._cache_bytes = 0
        self._frame_key = None
        self._refresh_frame()
        self.content_changed.emit()

    def _path_for(self, relative):
        path = (self.asset_dir / relative).resolve()
        if not path.is_relative_to(self.asset_dir) or not path.is_file():
            raise ValueError("鲸鱼娘素材路径无效：%s" % relative)
        return path

    def _load_frame(self, relative):
        if relative in self._cache:
            self._cache.move_to_end(relative)
            return self._cache[relative][0]
        image = QImage(str(self._path_for(relative)))
        if image.isNull():
            raise RuntimeError("无法加载鲸鱼娘动作帧：%s" % relative)
        image = image.scaled(max(1, round(image.width() * self._scale)),
                             max(1, round(image.height() * self._scale)),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap = QPixmap.fromImage(image)
        size = pixmap.width() * pixmap.height() * 4
        while self._cache and self._cache_bytes + size > self.CACHE_LIMIT:
            _, (_, old_size) = self._cache.popitem(last=False)
            self._cache_bytes -= old_size
        self._cache[relative] = pixmap, size
        self._cache_bytes += size
        return pixmap

    def _refresh_frame(self):
        frames = self.assets[self._asset]["frames"]
        if self._duration:
            index = min(len(frames) - 1, int(self._elapsed / self._duration * len(frames)))
        else:
            index = int(self._elapsed * 8) % len(frames)
        key = frames[index]
        if key != self._frame_key:
            self._frame = self._load_frame(key)
            self._frame_key = key
            self.content_changed.emit()

    def _set_clip(self, asset, duration):
        self._asset, self._duration, self._elapsed = asset, duration, 0.0
        self._refresh_frame()
        self.update()

    def _directional(self, asset):
        if self.facing > 0:
            return {"walk_side": "walk_side_right", "walk_start_left": "walk_start_right",
                    "walk_stop_left": "walk_stop_right"}.get(asset, asset)
        return asset

    def play_state(self, state, *, manual=True):
        if self._closed:
            return
        if state not in self.actions:
            raise ValueError("未知鲸鱼娘动作：%s" % state)
        # 抓取/下落优先于过渡；其余状态保留起床与收步动画。
        transition = self.actions[self._state].get("transition", {})
        if (state != self._state and self._phase != "exit"
                and state not in ("DRAGGING", "FALLING", "LANDING")
                and "exit" in transition):
            self._pending_state = (state, manual)
            self._phase = "exit"
            clip = transition["exit"]
            self._set_clip(self._directional(clip["asset"]), clip["duration_ms"] / 1000)
            return
        self._start_state(state, manual)

    def _start_state(self, state, manual):
        self._pending_state = None
        self._state, self._manual = state, manual
        self._walk_elapsed = self._idle_elapsed = 0.0
        spec = self.actions[state]
        enter = spec.get("transition", {}).get("enter")
        if enter:
            self._phase = "enter"
            self._set_clip(self._directional(enter["asset"]), enter["duration_ms"] / 1000)
        else:
            self._start_body()

    def _start_body(self):
        self._phase = "body"
        spec = self.actions[self._state]
        duration = spec.get("duration_ms")
        self._set_clip(self._directional(spec["asset"]), duration / 1000 if duration else None)

    def play_asset(self, asset):
        if asset not in self.assets:
            raise ValueError("未知鲸鱼娘素材：%s" % asset)
        self._pending_state = None
        self._state, self._manual, self._phase = "IDLE", True, "preview"
        self._set_clip(asset, max(1.2, len(self.assets[asset]["frames"]) / 7.0))

    def play(self, action):
        aliases = {"idle": "IDLE", "pet": "HEAD_PAT", "pat": "HEAD_PAT",
                   "jump": "HAPPY", "hop": "HAPPY", "nod": "THINKING",
                   "wiggle": "GLANCE", "tilt": "THINKING", "lean": "HAPPY",
                   "spin": "TAIL_REACT", "dance": "HAPPY", "grab": "DRAGGING",
                   "land": "LANDING", "talk": "TALKING"}
        self.play_state(aliases.get(action, action))

    def react(self, event):
        state = {"grab": "DRAGGING", "drop": "IDLE", "fall": "FALLING",
                 "land": "LANDING", "touch_head": "HEAD_PAT", "click": "POKE_REACT",
                 "tail": "TAIL_REACT", "feed": "EATING"}.get(event)
        if state:
            self.play_state(state, manual=False)

    def set_facing(self, facing):
        facing = -1 if facing < 0 else 1
        if facing == self.facing:
            return
        self.facing = facing
        if self._state == "WALKING":
            self._start_state("WALKING", self._manual)
        self.update()

    def set_follow(self, on):
        self.follow = bool(on)

    def set_look(self, dx, dy):
        # 不对整张图做伪造眼球变形；保留主窗口统一调用接口。
        pass

    def set_auto_actions(self, on):
        self.auto_actions = bool(on)
        if not on and not self._manual:
            self.play_state("IDLE")

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self._last_tick))
        self._last_tick = now
        self.advance(dt)

    def advance(self, dt):
        """与墙钟分离，便于逐帧测试完整状态机。"""
        if self._closed:
            return
        self._elapsed += dt
        if self._duration is not None and self._elapsed >= self._duration:
            if self._phase == "exit" and self._pending_state:
                self._start_state(*self._pending_state)
            elif self._phase == "enter":
                self._start_body()
            else:
                self._start_state("IDLE", False)
                self._next_auto = random.uniform(3, 7)
        if self._state == "WALKING" and self._phase == "body":
            self._walk_elapsed += dt
            self.walk_step.emit(self.facing * 25 * self._scale * dt)
            if self._walk_elapsed > 5:
                self.play_state("IDLE", manual=False)
        if self.auto_actions and self._state == "IDLE" and self._phase == "body":
            self._idle_elapsed += dt
            if self._idle_elapsed >= self._next_auto:
                self.play_state(random.choice(self.AUTO_ACTIONS), manual=False)
        self._refresh_frame()
        self.update()

    def _draw_rect(self):
        bounds = self.manifest["files"][self._frame_key]["bounds"]
        bottom_pad = (self.manifest["files"][self._frame_key]["height"] - bounds[3]) * self._scale
        return QRectF((self.width() - self._frame.width()) / 2,
                      self.height() - 20 * self._scale - self._frame.height() + bottom_pad,
                      self._frame.width(), self._frame.height())

    def content_inset(self):
        rect = self._draw_rect()
        x0, y0, x1, y1 = self.manifest["files"][self._frame_key]["bounds"]
        return (round(rect.left() + x0 * self._scale), round(rect.top() + y0 * self._scale),
                round(self.width() - rect.left() - x1 * self._scale),
                round(self.height() - rect.top() - y1 * self._scale))

    def hit_region(self, x, y):
        r = self._draw_rect()
        if not r.contains(x, y):
            return ""
        nx, ny = (x - r.x()) / r.width(), (y - r.y()) / r.height()
        if 0.2 <= nx <= 0.8 and ny < 0.4:
            return "head"
        if nx > 0.76 and ny > 0.6:
            return "tail"
        return "body"

    def paintEvent(self, event):
        if self._frame.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self._draw_rect()
        dx, dy, sx, sy, angle = self._pose()
        # 整体变换绕脚底进行，避免呼吸缩放让脚穿过任务栏边界。
        anchor_x, anchor_y = rect.center().x(), self.height() - 20 * self._scale
        p.translate(anchor_x + dx * self._scale, anchor_y + dy * self._scale)
        p.rotate(angle)
        p.scale(sx, sy)
        rect.translate(-anchor_x, -anchor_y)
        p.drawPixmap(rect.toRect(), self._frame)
        p.end()

    def _pose(self):
        """关键帧之外的呼吸/重心变化，只做整体变换，不伪装成骨骼绑定。"""
        t = self._elapsed
        wave = math.sin(t * 2.35)
        dx = dy = angle = 0.0
        sx = sy = 1.0
        state = self._state
        if self._phase == 'enter' and state == 'WALKING':
            impulse = math.sin(min(1, t / 0.26) * math.pi)
            sx, sy = 1 + impulse * .018, 1 - impulse * .025
        elif self._phase == 'exit' and state == 'WALKING':
            impulse = math.exp(-t * 12) * math.cos(t * 24)
            sx, sy = 1 + impulse * .012, 1 - impulse * .018
        elif state in ('IDLE', 'SLEEPING'):
            sx, sy = 1 - wave * .006, 1 + wave * .012
        elif state in ('BLINK', 'GLANCE', 'HEAD_PAT', 'TAIL_REACT'):
            angle = math.exp(-t * 5) * math.sin(t * 10) * .7
        elif state == 'THINKING':
            dy, angle = -1.5 + wave, math.sin(t * 2.2) * 1.8
        elif state == 'HAPPY':
            phase = max(0, math.sin(t * math.pi * 2.15))
            dy, sx, sy = -phase * 10, 1 + phase * .018, 1 - phase * .024
        elif state == 'WALKING':
            dy = -abs(math.sin(t * math.tau * 2)) * .75
        elif state == 'TALKING':
            phase = math.sin(t * 7.6)
            dy, angle = -abs(phase) * 1.8, phase * .7
        elif state == 'ANGRY':
            phase = math.sin(t * 20)
            dx, angle = phase * 2.5, phase * 1.1
        elif state == 'POKE_REACT':
            impulse = math.exp(-t * 7)
            dx, angle = -impulse * 2, -impulse * .8
        elif state == 'EATING':
            phase = max(0, math.sin(t * 11))
            dy, sx, sy = -phase * 2.8, 1 + phase * .012, 1 - phase * .015
        elif state == 'SWEEPING':
            dx, angle = math.sin(t * 4.2) * 2.5, math.sin(t * 4.2) * 2.4
        elif state == 'DRAGGING':
            dy, angle = -abs(wave) * 2, math.sin(t * 4) * 2
        elif state == 'FALLING':
            angle, sy = math.sin(t * 8) * 1.4, 1.015
        elif state == 'LANDING':
            impulse = math.exp(-t * 7) * math.sin(t * 13)
            sx, sy = 1 + impulse * .035, 1 - impulse * .05
        elif state == 'DIZZY':
            angle = math.sin(t * 5) * 2.2
        return dx, dy, sx, sy, angle

    def shutdown(self):
        self._closed = True
        self.timer.stop()
        self._frame = QPixmap()
        self._cache.clear()
        self._cache_bytes = 0

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._closed:
            self._last_tick = time.monotonic()
            self.timer.start(33)
        super().showEvent(event)
