"""像素宠物：QPainter 程序化绘制，无需图片素材。

关键设计（解决"像素点一直抖"）：
- 动画驱动量（呼吸/眨眼/视线/跳跃）在像素模式下**量化为整数并保持**，
  且**只有当这一帧真的和上一帧不同时才重绘** -> 空闲时画面完全静止，只有动作才动。
- 同一套绘制代码支持两种画风：
  * pixel ：画到 32x32 小图 + 最近邻放大（硬像素）
  * smooth：直接按比例画到控件 + 抗锯齿（平滑/非像素）
"""
import math
import random
from collections import namedtuple

from PySide6.QtCore import Qt, QTimer, QSize, QRectF, QPointF
from PySide6.QtGui import QPainter, QImage, QColor, QPen, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

# 一帧的绘制状态：breath(呼吸量) eye_open(0~1) look(像素偏移) state yoff(竖直偏移) face
Ctx = namedtuple("Ctx", "breath eye_open look state yoff face")


# --------------------------------------------------------------------------- #
#  角色：史莱姆（二次元可爱版：更圆润、更Q弹、更萌）
# --------------------------------------------------------------------------- #
class SlimeCharacter:
    name = "slime"
    label = "史莱姆"
    canvas = (32, 32)

    # 渐变蓝色果冻：更鲜艳、更通透、更二次元
    BODY = QColor(0x6D, 0xD5, 0xFF, 220)      # 亮蓝色主体
    BODY_D = QColor(0x4D, 0xA8, 0xE8, 235)    # 深蓝色边缘
    BELLY = QColor(0xD5, 0xF4, 0xFF, 190)     # 浅蓝色肚子
    EYE = QColor(0x2A, 0x3B, 0x5C)            # 深蓝色眼睛
    EYE_LIGHT = QColor(255, 255, 255, 250)    # 眼睛高光
    BLUSH = QColor(0xFF, 0xA5, 0xC8, 180)     # 粉色腮红

    def draw(self, p, ctx):
        cx, ground = 16.0, 28.0
        d = ctx.breath
        if ctx.state == "grab":
            bw, bh = 18.0, 20.0
        else:
            bw, bh = 22.0 - d, 16.0 + d
        left, top = cx - bw / 2, ground - bh + ctx.yoff

        # 影子（固定在地面，跳起时缩小）
        p.setPen(Qt.NoPen)
        sh = 1.0 - max(0.0, -ctx.yoff) / 9.0 * 0.45
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(cx - bw * 0.52 * sh, ground - 1.0, bw * 1.04 * sh, 5.0))

        # 身体外轮廓（深色边缘）
        p.setBrush(self.BODY_D)
        p.drawEllipse(QRectF(left - 1.2, top - 1.2, bw + 2.4, bh + 2.4))

        # 身体主体（渐变效果通过多层叠加）
        p.setBrush(self.BODY)
        p.drawEllipse(QRectF(left, top, bw, bh))

        # 肚子高光区域（更大更明显）
        p.setBrush(self.BELLY)
        p.drawEllipse(QRectF(left + bw * 0.15, top + bh * 0.38, bw * 0.7, bh * 0.55))

        # 果冻光泽：三个高光点营造Q弹质感
        p.setBrush(QColor(255, 255, 255, 180))
        p.drawEllipse(QRectF(left + bw * 0.15, top + bh * 0.1, bw * 0.32, bh * 0.26))
        p.setBrush(QColor(255, 255, 255, 130))
        p.drawEllipse(QRectF(left + bw * 0.55, top + bh * 0.08, bw * 0.15, bh * 0.12))
        p.setBrush(QColor(255, 255, 255, 100))
        p.drawEllipse(QRectF(left + bw * 0.25, top + bh * 0.65, bw * 0.18, bh * 0.14))

        # 脸（竖直位置固定，不随呼吸晃）- 更大更萌的动漫眼睛
        eye_y = ground - 9.5 + ctx.yoff
        eye_dx = 4.8

        if ctx.state == "grab":
            # 被抓住：惊讶的大眼睛 + O型嘴
            for s in (-1, 1):
                ex = cx + s * eye_dx
                # 白色眼白
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 250))
                p.drawEllipse(QRectF(ex - 2.8, eye_y - 3.5, 5.6, 7.0))
                # 黑色瞳孔
                p.setBrush(self.EYE)
                p.drawEllipse(QRectF(ex - 1.6, eye_y - 1.6, 3.2, 3.6))
                # 高光
                p.setBrush(self.EYE_LIGHT)
                p.drawEllipse(QRectF(ex - 0.8, eye_y - 1.4, 1.4, 1.8))
            # O型嘴
            p.setBrush(self.EYE)
            p.drawEllipse(QRectF(cx - 1.8, eye_y + 5.0, 3.6, 4.0))
            p.setBrush(QColor(255, 200, 210, 200))
            p.drawEllipse(QRectF(cx - 1.2, eye_y + 5.4, 2.4, 2.8))
            return

        # 正常状态：二次元大眼睛
        for s in (-1, 1):
            ex = cx + s * eye_dx + ctx.look * 1.5
            eh = 6.0 * ctx.eye_open

            if eh < 1.5:  # 眨眼：弧线
                p.setPen(QPen(self.EYE, 1.8))
                p.setBrush(Qt.NoBrush)
                arc = QPainterPath(QPointF(ex - 2.2, eye_y))
                arc.quadTo(QPointF(ex, eye_y - 1.5), QPointF(ex + 2.2, eye_y))
                p.drawPath(arc)
            else:  # 睁眼：动漫风格大眼
                p.setPen(Qt.NoPen)
                # 眼白
                p.setBrush(QColor(255, 255, 255, 250))
                p.drawEllipse(QRectF(ex - 2.2, eye_y - eh / 2, 4.4, eh))
                # 瞳孔（椭圆形）
                p.setBrush(self.EYE)
                p.drawEllipse(QRectF(ex - 1.8, eye_y - eh / 2 + 0.4, 3.6, eh - 0.8))
                # 大高光（上方）
                p.setBrush(self.EYE_LIGHT)
                p.drawEllipse(QRectF(ex - 1.2, eye_y - eh / 2 + 0.8, 1.8, 2.2))
                # 小高光（右下）
                p.setBrush(QColor(255, 255, 255, 180))
                p.drawEllipse(QRectF(ex + 0.4, eye_y + 0.6, 1.0, 1.2))

        # 粉色腮红（更大更明显）
        p.setPen(Qt.NoPen)
        p.setBrush(self.BLUSH)
        p.drawEllipse(QRectF(cx - eye_dx - 4.0, eye_y + 1.8, 3.6, 2.4))
        p.drawEllipse(QRectF(cx + eye_dx + 0.4, eye_y + 1.8, 3.6, 2.4))

        # 嘴巴（可爱的微笑）
        p.setPen(QPen(self.EYE, 1.6))
        p.setBrush(Qt.NoBrush)
        if ctx.face == "happy":
            # 开心：大笑
            smile = QPainterPath(QPointF(cx - 2.8, eye_y + 4.0))
            smile.quadTo(QPointF(cx, eye_y + 7.2), QPointF(cx + 2.8, eye_y + 4.0))
        else:
            # 普通：小微笑
            smile = QPainterPath(QPointF(cx - 2.2, eye_y + 4.5))
            smile.quadTo(QPointF(cx, eye_y + 6.2), QPointF(cx + 2.2, eye_y + 4.5))
        p.drawPath(smile)


# --------------------------------------------------------------------------- #
#  角色：猫（二次元萌猫：更大的眼睛、更柔和的配色、更可爱的表情）
# --------------------------------------------------------------------------- #
class CatCharacter:
    name = "cat"
    label = "小猫"
    canvas = (32, 32)

    # 柔和配色
    FUR = QColor(0xFF, 0xD7, 0xA8)          # 奶茶色毛发
    FUR_D = QColor(0xF0, 0xB8, 0x85)        # 深色边缘
    EAR_IN = QColor(0xFF, 0xD0, 0xDD)       # 粉色耳朵内侧
    MUZZLE = QColor(0xFF, 0xF5, 0xF0)       # 白色嘴巴区域
    EYE = QColor(0x3A, 0x2A, 0x28)          # 深棕色眼眶
    IRIS = QColor(0x7D, 0xD8, 0xF0)         # 青蓝色虹膜（更亮）
    PUPIL = QColor(0x2A, 0x22, 0x20)        # 瞳孔
    NOSE = QColor(0xF5, 0x9A, 0xA8)         # 粉色鼻子
    BLUSH = QColor(0xFF, 0xB0, 0xD0, 180)   # 粉色腮红

    def draw(self, p, ctx):
        cx = 16.0
        d = ctx.breath
        cy = (11.0 if ctx.state == "grab" else 15.5 - d) + ctx.yoff
        hw, hh = 19.5, 17.5

        # 影子
        p.setPen(Qt.NoPen)
        sh = 1.0 - max(0.0, -ctx.yoff) / 9.0 * 0.45
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(cx - 9.5 * sh, 28.5, 19 * sh, 4.5))

        # 身体（随跳跃 yoff 移动，不随呼吸）
        by = 19.5 + ctx.yoff
        p.setBrush(self.FUR_D)
        p.drawEllipse(QRectF(cx - 9.5, by, 19, 13.5))
        p.setBrush(self.FUR)
        p.drawEllipse(QRectF(cx - 8.5, by + 1, 17, 12.5))

        # 耳朵（更圆润）
        ey = cy - hh * 0.44
        for s in (-1, 1):
            # 外耳廓
            _tri(p, [(cx + s * hw * 0.46, ey + 0.5), (cx + s * hw * 0.14, ey - 1.2),
                     (cx + s * hw * 0.34, ey - 8.5)], self.FUR_D)
            _tri(p, [(cx + s * hw * 0.44, ey), (cx + s * hw * 0.18, ey - 1.2),
                     (cx + s * hw * 0.34, ey - 8.0)], self.FUR)
            # 内耳（粉色）
            _tri(p, [(cx + s * hw * 0.40, ey - 0.8), (cx + s * hw * 0.24, ey - 1.6),
                     (cx + s * hw * 0.34, ey - 5.5)], self.EAR_IN)

        # 头部外轮廓
        p.setBrush(self.FUR_D)
        p.drawEllipse(QRectF(cx - hw / 2 - 1.2, cy - hh / 2 - 1.2, hw + 2.4, hh + 2.4))
        # 头部主体
        p.setBrush(self.FUR)
        p.drawEllipse(QRectF(cx - hw / 2, cy - hh / 2, hw, hh))
        # 嘴巴区域（白色）
        p.setBrush(self.MUZZLE)
        p.drawEllipse(QRectF(cx - 6.5, cy + 0.8, 13, 9.5))

        # 眼睛（超大动漫眼）
        eye_y, eye_dx = cy - 1.2, 5.0
        for s in (-1, 1):
            ex = cx + s * eye_dx + ctx.look * 1.3
            open_amt = 1.0 if ctx.state == "grab" else ctx.eye_open
            eh = 8.0 * open_amt  # 更大的眼睛
            ew = 5.5

            if eh < 1.8:  # 眨眼：弧形笑眼
                p.setPen(QPen(self.EYE, 1.8))
                p.setBrush(Qt.NoBrush)
                arc = QPainterPath(QPointF(ex - 2.6, eye_y + 0.8))
                arc.quadTo(QPointF(ex, eye_y - 2.2), QPointF(ex + 2.6, eye_y + 0.8))
                p.drawPath(arc)
            else:
                top = eye_y - eh / 2
                p.setPen(Qt.NoPen)

                # 眼眶（深色边缘）
                p.setBrush(self.EYE)
                p.drawEllipse(QRectF(ex - ew / 2, top, ew, eh))

                # 白色眼白
                p.setBrush(QColor(255, 255, 255, 250))
                p.drawEllipse(QRectF(ex - ew / 2 + 0.4, top + 0.4, ew - 0.8, eh - 0.8))

                # 虹膜（亮蓝色）
                p.setBrush(self.IRIS)
                p.drawEllipse(QRectF(ex - ew / 2 + 0.9, top + eh * 0.2, ew - 1.8, eh * 0.68))

                # 瞳孔
                p.setBrush(self.PUPIL)
                p.drawEllipse(QRectF(ex - 1.2 + ctx.look * 0.4, eye_y - 1.6, 2.4, 3.4))

                # 大高光（上方，营造水汪汪的感觉）
                p.setBrush(QColor(255, 255, 255, 250))
                p.drawEllipse(QRectF(ex - 1.6 + ctx.look * 0.4, top + 1.2, 2.2, 2.8))

                # 小高光（右下）
                p.setBrush(QColor(255, 255, 255, 190))
                p.drawEllipse(QRectF(ex + 0.6, eye_y + 1.6, 1.2, 1.4))

                # 底部反光（增加水润感）
                p.setBrush(QColor(255, 255, 255, 120))
                p.drawEllipse(QRectF(ex - 1.8, eye_y + eh / 2 - 1.6, 3.6, 1.2))

        # 腮红（更明显）
        p.setPen(Qt.NoPen)
        p.setBrush(self.BLUSH)
        p.drawEllipse(QRectF(cx - eye_dx - 4.2, eye_y + 2.8, 3.8, 2.6))
        p.drawEllipse(QRectF(cx + eye_dx + 0.4, eye_y + 2.8, 3.8, 2.6))

        # 鼻子（小爱心形）
        p.setBrush(self.NOSE)
        _tri(p, [(cx - 1.8, cy + 3.2), (cx + 1.8, cy + 3.2), (cx, cy + 5.0)], self.NOSE)
        # 鼻子顶部两个圆角
        p.drawEllipse(QRectF(cx - 1.8, cy + 2.4, 1.6, 1.6))
        p.drawEllipse(QRectF(cx + 0.2, cy + 2.4, 1.6, 1.6))

        # 嘴巴（W形，更可爱）
        p.setPen(QPen(self.EYE, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(cx, cy + 5.0), QPointF(cx, cy + 5.8))
        for s in (-1, 1):
            mouth = QPainterPath(QPointF(cx, cy + 5.8))
            mouth.quadTo(QPointF(cx + s * 1.8, cy + 7.2), QPointF(cx + s * 3.2, cy + 6.0))
            p.drawPath(mouth)

        # 胡须
        p.setPen(QPen(QColor(110, 110, 110, 180), 1.0))
        for s in (-1, 1):
            bx = cx + s * 5.0
            p.drawLine(QPointF(bx, cy + 2.6), QPointF(bx + s * 5.8, cy + 1.4))
            p.drawLine(QPointF(bx, cy + 4.0), QPointF(bx + s * 5.8, cy + 4.4))

        # 腮红
        p.setPen(Qt.NoPen)
        p.setBrush(self.BLUSH)
        for s in (-1, 1):
            p.drawEllipse(QRectF(cx + s * eye_dx - 1.6, eye_y + 2.2, 3.2, 2.2))


def _tri(p, pts, color):
    path = QPainterPath(QPointF(*pts[0]))
    path.lineTo(QPointF(*pts[1]))
    path.lineTo(QPointF(*pts[2]))
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)


CHARACTERS = {c.name: c for c in (SlimeCharacter, CatCharacter)}


def make_character(name):
    return CHARACTERS.get(name, SlimeCharacter)()


def render_icon(name, px=64):
    """把角色静态渲染成 QPixmap，用作托盘图标。"""
    char = make_character(name)
    cw, ch = char.canvas
    img = QImage(cw, ch, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, False)
    char.draw(p, Ctx(0.0, 1.0, 0.0, "idle", 0.0, "normal"))
    p.end()
    return QPixmap.fromImage(img.scaled(px, px, Qt.IgnoreAspectRatio, Qt.FastTransformation))


# --------------------------------------------------------------------------- #
#  控件
# --------------------------------------------------------------------------- #
class PixelPet(QWidget):
    BREATH_PERIOD = 2.2     # 呼吸周期(秒)
    BREATH_AMP = 1.49       # 呼吸幅度(像素)；像素模式下 round 后在 {0,1} 间切换
    JUMP_H = 9.0
    JUMP_DUR = 0.5

    def __init__(self, character_name="slime", scale=5, style="pixel", parent=None):
        super().__init__(parent)
        self.char = make_character(character_name)
        self.scale = max(1, int(scale))
        self.style = style if style in ("pixel", "smooth") else "pixel"
        cw, ch = self.char.canvas
        self._img = QImage(cw, ch, QImage.Format_ARGB32_Premultiplied)
        self.setFixedSize(cw * self.scale, ch * self.scale)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.fps = 30
        self._dt = 1.0 / self.fps
        self.t = 0.0
        self.eye_open = 1.0
        self._blink = "open"
        self._next_blink = random.uniform(2.0, 4.0)
        self.look_f = 0.0
        self._look_target = 0
        self._next_look = random.uniform(2.0, 4.0)
        self.state = "idle"
        self.react_t = 0.0
        self.face = "normal"
        self._last_sig = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 * self._dt))

    # --- 对外接口 ---
    def natural_size(self):
        cw, ch = self.char.canvas
        return QSize(cw * self.scale, ch * self.scale)

    def set_scale(self, scale):
        self.scale = max(1, int(scale))
        cw, ch = self.char.canvas
        self.setFixedSize(cw * self.scale, ch * self.scale)

    def set_style(self, style):
        if style in ("pixel", "smooth"):
            self.style = style
            self._last_sig = None
            self.update()

    def react(self, event):
        # touch_head 事件不触发动作
        if event == "touch_head":
            return
        if event == "grab":
            self.state, self.face = "grab", "surprised"
        elif event in ("drop", "click"):
            self.state, self.react_t, self.face = "jump", 0.0, "happy"
        self._last_sig = None

    def shutdown(self):
        self.timer.stop()

    def hideEvent(self, ev):
        self.timer.stop()
        super().hideEvent(ev)

    def showEvent(self, ev):
        if not self.timer.isActive():
            self.timer.start(int(1000 * self._dt))
        super().showEvent(ev)

    # --- 动画 ---
    def _tick(self):
        dt = self._dt
        self.t += dt
        self._update_blink(dt)
        self._update_look(dt)
        if self.state == "jump":
            self.react_t += dt / self.JUMP_DUR
            if self.react_t >= 1.0:
                self.state, self.react_t, self.face = "idle", 0.0, "normal"

        if self.style == "pixel":
            ctx = self._make_ctx()
            sig = (ctx.breath, ctx.eye_open, ctx.look, ctx.state, ctx.yoff, ctx.face)
            if sig != self._last_sig:          # 只有真的变了才重绘 -> 不抖
                self._last_sig = sig
                self.update()
        else:
            self.update()

    def _update_blink(self, dt):
        if self.state == "grab":
            self.eye_open = 1.0
            return
        if self._blink == "open":
            self._next_blink -= dt
            if self._next_blink <= 0:
                self._blink = "closing"
        elif self._blink == "closing":
            self.eye_open -= dt / 0.09
            if self.eye_open <= 0:
                self.eye_open, self._blink = 0.0, "opening"
        else:
            self.eye_open += dt / 0.09
            if self.eye_open >= 1.0:
                self.eye_open, self._blink = 1.0, "open"
                self._next_blink = random.uniform(2.5, 5.0)

    def _update_look(self, dt):
        self._next_look -= dt
        if self._next_look <= 0:
            self._look_target = random.choice([-1, 0, 0, 1])
            self._next_look = random.uniform(2.0, 4.5)
        self.look_f += (self._look_target - self.look_f) * min(1.0, dt * 5)

    def _make_ctx(self):
        b = (math.sin(self.t * 2 * math.pi / self.BREATH_PERIOD) * 0.5 + 0.5) * self.BREATH_AMP
        if self.state == "jump":
            yoff = -self.JUMP_H * math.sin(math.pi * self.react_t)
        elif self.state == "grab":
            yoff = -2.0
        else:
            yoff = 0.0
        eye, look = self.eye_open, self.look_f
        if self.style == "pixel":          # 量化为整数并分档 -> 离散步进
            b = float(round(b))
            yoff = float(round(yoff))
            look = float(round(look))
            eye = 1.0 if eye > 0.6 else (0.5 if eye > 0.15 else 0.0)
        return Ctx(b, eye, look, self.state, yoff, self.face)

    def paintEvent(self, ev):
        ctx = self._make_ctx()
        cw, ch = self.char.canvas
        if self.style == "smooth":         # 非像素：按比例 + 抗锯齿，直接画到控件
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.scale(self.width() / cw, self.height() / ch)
            self.char.draw(p, ctx)
            p.end()
        else:                              # 像素：32x32 小图 + 最近邻放大
            self._img.fill(Qt.transparent)
            ip = QPainter(self._img)
            ip.setRenderHint(QPainter.Antialiasing, False)
            self.char.draw(ip, ctx)
            ip.end()
            p = QPainter(self)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(self.rect(), self._img)
            p.end()
