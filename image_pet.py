"""图片宠物（2D 木偶 + 神态）：让一张静态 PNG 灵动起来，接近 Live2D 的感觉。

身体层（条带渲染）：
- 常驻：呼吸、摇摆、浮动、软体果冻抖动（脚下固定）。
- 看向鼠标：脖子以上条带朝目标横向错切 + 轻微竖直，整头转向。
- 动作：hop/jump/wiggle/nod/tilt/lean/spin/dance/land，随机/点击/手动触发。
- 镜像翻转 facing=±1。

神态层（叠加绘制，不挪像素、不露洞）：
- 眨眼：眼睛处叠肤色"眼皮"从上往下合 + 睫毛线。
- 张嘴：嘴处叠一个会开合的小暗色椭圆（subtle）。
五官位置可自动检测（绿色眼睛）或外部 set_regions 指定（归一化坐标）。
"""
import math
import random

from PySide6.QtCore import Qt, QTimer, QSize, QRect, QRectF, QPointF
from PySide6.QtGui import QPainter, QPixmap, QImage, QColor, QPen, QPainterPath
from PySide6.QtWidgets import QWidget

DEFAULT_REGIONS = {"eyeL": (0.40, 0.40), "eyeR": (0.60, 0.40), "mouth": (0.50, 0.52)}
SKIN_FALLBACK = QColor(0xF4, 0xCB, 0xAE)


class ImagePet(QWidget):
    STRIPS = 26
    M_TOP, M_SIDE, M_BOTTOM = 72, 44, 18

    ACTIONS = {"hop": 0.6, "jump": 0.85, "wiggle": 0.75, "nod": 0.7, "tilt": 1.1,
               "lean": 0.8, "spin": 0.95, "dance": 1.7, "land": 0.45}
    IDLE_ACTIONS = ["hop", "wiggle", "nod", "tilt", "lean", "spin", "dance"]
    CLICK_ACTIONS = ["jump", "spin", "wiggle", "dance", "hop"]

    def __init__(self, image_path, target_h=240, facing=1, regions=None, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.facing = -1 if facing < 0 else 1
        self._srcimg = None
        self._src = self._load_cropped(image_path)
        if self._src is None:
            raise RuntimeError("无法加载图片: %r" % (image_path,))
        self.set_regions(regions or self._auto_regions() or DEFAULT_REGIONS, _refresh=False)
        self._apply(target_h)
        self._srcimg = None        # 五官/肤色已采样完，释放整张源 QImage 省内存
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.fps = 30
        self._dt = 1.0 / self.fps
        self.t = 0.0
        self.action, self.action_t, self.action_dur = "idle", 0.0, 1.0
        self._next_action = random.uniform(2.5, 5.0)
        self.look_x = self.look_y = 0.0           # 目标 [-1,1]
        self._lx = self._ly = 0.0                 # 缓动当前值
        self.follow = True

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 * self._dt))

    # --- 加载 / 尺寸 ---
    def _load_cropped(self, path):
        img = QImage(path)
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format_ARGB32)
        rect = self._alpha_bbox(img)
        if rect is not None and rect.isValid():
            img = img.copy(rect)
        # 省内存：宠物最大也就显示 ~360px，源图按比例压到 ≤900px 高即可，
        # 既够清晰又能把动辄几千像素的大立绘从几十 MB 降到几 MB。
        CAP_H = 900
        if img.height() > CAP_H:
            img = img.scaledToHeight(CAP_H, Qt.SmoothTransformation)
        self._srcimg = img
        return QPixmap.fromImage(img)

    @staticmethod
    def _alpha_bbox(img):
        try:
            import numpy as np
            w, h = img.width(), img.height()
            a = np.frombuffer(img.bits(), np.uint8).reshape(h, w, 4)[..., 3]
            ys, xs = np.where(a > 16)
            if len(xs) == 0:
                return None
            pad = 2
            x0, y0 = max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad)
            x1, y1 = min(w - 1, int(xs.max()) + pad), min(h - 1, int(ys.max()) + pad)
            return QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        except Exception:
            return None

    def _auto_regions(self):
        """绿色眼睛自动检测：返回归一化 regions，失败返回 None。"""
        try:
            import numpy as np
            img = self._srcimg
            w, h = img.width(), img.height()
            a = np.frombuffer(img.bits(), np.uint8).reshape(h, w, 4)
            R, G, B, A = a[..., 2].astype(int), a[..., 1].astype(int), a[..., 0].astype(int), a[..., 3]
            green = (A > 160) & (G > 85) & (G > R + 18) & (G > B + 18)
            yy = np.arange(h)[:, None]
            xx = np.arange(w)[None, :]
            # 限定在脸部区域，排除胸前手持的绿色盆栽等
            green &= (yy > 0.24 * h) & (yy < 0.46 * h) & (xx > 0.28 * w) & (xx < 0.72 * w)
            ys, xs = np.where(green)
            if len(xs) < 40:
                return None
            mx = np.median(xs)
            left, right = xs < mx, xs >= mx
            if left.sum() < 15 or right.sum() < 15:
                return None
            exL = (float(xs[left].mean()) / w, float(ys[left].mean()) / h)
            exR = (float(xs[right].mean()) / w, float(ys[right].mean()) / h)
            eye_y = (exL[1] + exR[1]) / 2
            dist = abs(exR[0] - exL[0])
            mouth = ((exL[0] + exR[0]) / 2, min(0.92, eye_y + dist * 1.15))
            return {"eyeL": exL, "eyeR": exR, "mouth": mouth}
        except Exception:
            return None

    def set_regions(self, regions, _refresh=True):
        # 仅用于"脖子线/看向鼠标"的几何，不再做眨眼/张嘴等无图层硬盖。
        self.regions = {k: tuple(regions[k]) for k in ("eyeL", "eyeR", "mouth")}
        exL, exR = self.regions["eyeL"], self.regions["eyeR"]
        self.eye_dist = max(0.08, abs(exR[0] - exL[0]))
        if _refresh:
            self.update()

    def _apply(self, target_h):
        self._target_h = max(60, int(target_h))
        scale = self._target_h / self._src.height()
        w = max(1, int(self._src.width() * scale))
        self.pm = self._src.scaled(w, self._target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setFixedSize(self.pm.width() + self.M_SIDE * 2,
                          self.pm.height() + self.M_TOP + self.M_BOTTOM)

    # --- 对外接口 ---
    def natural_size(self):
        return QSize(self.width(), self.height())

    def content_inset(self):
        """窗口四周的透明留白(像素)：(左,上,右,下)。
        图片居中绘制：左右各留 M_SIDE，上留 M_TOP，下留 M_BOTTOM。
        贴边判定用它来"只在角色本体碰到屏幕边时才吸附"，而非透明画布一靠近就吸附。"""
        side = max(0, (self.width() - self.pm.width()) // 2)
        return (side, self.M_TOP, side, self.M_BOTTOM)

    def set_scale(self, *_):
        pass

    def set_image_size(self, h):
        self._apply(h)

    def toggle_facing(self):
        self.facing *= -1
        self.update()

    def set_facing(self, f):
        self.facing = -1 if f < 0 else 1
        self.update()

    def set_follow(self, on):
        self.follow = bool(on)
        if not on:
            self.look_x = self.look_y = 0.0

    def set_look(self, dx, dy):
        """dx,dy 为 [-1,1]，由窗口根据鼠标相对位置传入。"""
        if self.follow:
            self.look_x = max(-1.0, min(1.0, dx))
            self.look_y = max(-1.0, min(1.0, dy))

    def play(self, action):
        self._start(action)

    def react(self, event):
        # touch_head 事件不触发动作
        if event == "touch_head":
            return
        if event == "grab":
            self.action, self.action_t = "grab", 0.0
        elif event == "drop":
            self._start("hop")
        elif event == "land":
            self._start("land")
        elif event == "click":
            self._start(random.choice(self.CLICK_ACTIONS))

    def shutdown(self):
        self.timer.stop()

    def hideEvent(self, ev):
        self.timer.stop()
        super().hideEvent(ev)

    def showEvent(self, ev):
        if not self.timer.isActive():
            self.timer.start(int(1000 * self._dt))
        super().showEvent(ev)

    # --- 动画状态 ---
    def _start(self, name):
        self.action, self.action_t = name, 0.0
        self.action_dur = self.ACTIONS.get(name, 0.7)

    def _tick(self):
        dt = self._dt
        self.t += dt
        # 动作
        if self.action == "idle":
            pass
        elif self.action == "grab":
            pass
        else:
            self.action_t += dt / self.action_dur
            if self.action_t >= 1.0:
                self.action, self.action_t = "idle", 0.0
                self._next_action = random.uniform(3.5, 7.0)
        # 视线缓动
        self._lx += (self.look_x - self._lx) * min(1.0, dt * 6)
        self._ly += (self.look_y - self._ly) * min(1.0, dt * 6)
        self.update()

    def _idle(self):
        # 默认待机保持静态，避免图片宠物出现“液化/扭曲”的条带形变。
        return {"tx": 0.0, "ty": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0, "wob": 0.0}

    def _overlay(self):
        a, p = self.action, self.action_t
        o = {"tx": 0.0, "ty": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0, "wob": 0.0}
        if a in ("idle", "talk"):
            return o
        if a == "grab":
            o["sy"], o["sx"], o["ty"] = 1.06, 0.97, -4.0
            o["rot"] = 5.0 * math.sin(self.t * 6.0)
            return o
        up = math.sin(math.pi * p)
        if a == "hop":
            o["ty"], o["sy"], o["sx"], o["wob"] = -26 * up, 1 + 0.10 * up, 1 - 0.06 * up, 2.5 * up
        elif a == "jump":
            o["ty"], o["sy"], o["sx"] = -64 * up, 1 + 0.14 * up, 1 - 0.08 * up
            o["wob"] = 6.0 * max(0.0, p - 0.55) / 0.45
        elif a == "land":
            sq = math.sin(math.pi * min(1.0, p * 1.3))
            o["sy"], o["sx"], o["ty"] = 1 - 0.18 * sq, 1 + 0.12 * sq, 6 * sq
        elif a == "wiggle":
            o["rot"], o["wob"] = 15 * math.sin(p * 2 * math.pi * 3) * (1 - p), 3.0 * (1 - p)
        elif a == "nod":
            w = abs(math.sin(p * 2 * math.pi * 2)) * (1 - p)
            o["ty"], o["sy"] = -10 * w, 1 - 0.05 * w
        elif a == "tilt":
            o["rot"] = 13 * math.sin(math.pi * p) * self.facing
        elif a == "lean":
            o["tx"], o["rot"] = 14 * math.sin(math.pi * p) * self.facing, 7 * math.sin(math.pi * p) * self.facing
        elif a == "spin":
            o["sx"], o["ty"] = math.cos(2 * math.pi * p), -12 * up
        elif a == "dance":
            o["rot"], o["ty"], o["wob"] = 12 * math.sin(p * 2 * math.pi * 2), -14 * abs(math.sin(p * 2 * math.pi * 2)), 3.0
        return o

    def _shear_at(self, yn, neck):
        """归一化 y 处的看向横向错切量（像素，未乘 W 比例由调用方处理）。"""
        headness = max(0.0, min(1.0, (neck - yn) / max(0.05, neck * 0.7)))
        return headness

    def paintEvent(self, ev):
        b, o = self._idle(), self._overlay()
        tx, ty = b["tx"] + o["tx"], b["ty"] + o["ty"]
        rot = b["rot"] + o["rot"]
        sx = b["sx"] * o["sx"] * self.facing
        sy = b["sy"] * o["sy"]
        wob = b["wob"] + o["wob"]

        pm = self.pm
        W, H = pm.width(), pm.height()
        neck = min(0.95, self.regions["mouth"][1] + 0.10)
        max_shear = 0.06 * W
        max_vlook = 0.03 * H

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.translate(self.width() / 2 + tx, self.M_TOP + H + ty)
        p.rotate(rot)
        p.scale(sx, sy)

        n = self.STRIPS
        for i in range(n):
            y0 = i * H / n
            hh = H / n + 1
            yn = y0 / H
            fy = (H - y0) / H
            headn = self._shear_at(yn, neck)
            xoff = wob * fy * math.sin(self.t * 2.0 + fy * 3.2) + self._lx * max_shear * headn
            yextra = -self._ly * max_vlook * headn
            p.drawPixmap(QRectF(-W / 2 + xoff, -H + y0 + yextra, W, hh),
                         pm, QRectF(0, y0, W, hh))

        p.end()
