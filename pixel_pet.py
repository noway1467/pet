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

# 一帧的绘制状态：breath(呼吸量) eye_open(0~1) look/look_y(视线偏移) state yoff(竖直偏移) face
Ctx = namedtuple("Ctx", "breath eye_open look look_y state yoff face")


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
            ex = cx + s * eye_dx
            pupil_x = ex + ctx.look * 1.2
            pupil_y = eye_y + ctx.look_y * 0.9
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
                p.drawEllipse(QRectF(pupil_x - 1.8, pupil_y - eh / 2 + 0.4, 3.6, eh - 0.8))
                # 大高光（上方）
                p.setBrush(self.EYE_LIGHT)
                p.drawEllipse(QRectF(pupil_x - 1.2, pupil_y - eh / 2 + 0.8, 1.8, 2.2))
                # 小高光（右下）
                p.setBrush(QColor(255, 255, 255, 180))
                p.drawEllipse(QRectF(pupil_x + 0.4, pupil_y + 0.6, 1.0, 1.2))

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
#  角色：猫（黑猫剪影：大金眼、坐姿、卷尾）
# --------------------------------------------------------------------------- #
class CatCharacter:
    name = "cat"
    label = "小猫"
    canvas = (32, 32)

    FUR = QColor(4, 4, 5, 245)
    FUR_SOFT = QColor(16, 16, 18, 245)
    EYE_GOLD = QColor(255, 194, 45, 245)
    EYE_DARK = QColor(18, 16, 14, 255)
    WHISKER = QColor(238, 238, 238, 210)

    def draw(self, p, ctx):
        cx = 14.5
        y = ctx.yoff
        d = ctx.breath * 0.35

        # 影子
        p.setPen(Qt.NoPen)
        sh = 1.0 - max(0.0, -ctx.yoff) / 9.0 * 0.45
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(4.5, 28.0, 21.0 * sh, 3.2))

        # 卷尾放在身体后面，保留参考图里右侧高高翘起的轮廓。
        p.setPen(QPen(self.FUR, 4.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        tail = QPainterPath(QPointF(22.0, 25.5 + y))
        tail.cubicTo(QPointF(29.5, 22.0 + y), QPointF(29.0, 11.5 + y), QPointF(23.5, 13.0 + y))
        tail.cubicTo(QPointF(20.5, 14.0 + y), QPointF(21.0, 18.0 + y), QPointF(24.5, 17.5 + y))
        p.drawPath(tail)

        p.setPen(Qt.NoPen)
        p.setBrush(self.FUR)

        # 坐姿身体与前爪
        body = QPainterPath(QPointF(8.0, 27.8 + y))
        body.cubicTo(QPointF(7.0, 19.0 + y), QPointF(10.0, 13.0 + y), QPointF(15.0, 13.5 + y))
        body.cubicTo(QPointF(21.5, 14.0 + y), QPointF(24.0, 21.0 + y), QPointF(22.2, 28.2 + y))
        body.cubicTo(QPointF(18.6, 29.4 + y), QPointF(11.2, 29.4 + y), QPointF(8.0, 27.8 + y))
        p.drawPath(body)
        p.drawEllipse(QRectF(5.7, 18.5 + y, 7.2, 10.8))
        p.drawEllipse(QRectF(10.8, 17.4 + y, 7.8, 12.2))

        # 微弱内侧阴影让黑色身体不糊成一块，同时仍保持剪影感。
        p.setBrush(self.FUR_SOFT)
        p.drawEllipse(QRectF(12.0, 17.2 + y, 5.2, 9.8))

        # 头部与耳朵，略向左歪。
        _tri(p, [(7.1, 8.8 + y), (9.4, 1.9 + y), (13.4, 7.4 + y)], self.FUR)
        _tri(p, [(16.0, 7.0 + y), (23.2, 5.0 + y), (19.8, 12.0 + y)], self.FUR)
        head = QPainterPath(QPointF(6.8, 12.7 + y))
        head.cubicTo(QPointF(5.6, 8.0 + y), QPointF(9.2, 5.0 + y), QPointF(14.4, 5.7 + y))
        head.cubicTo(QPointF(20.4, 6.5 + y), QPointF(23.0, 11.0 + y), QPointF(20.5, 15.5 + y))
        head.cubicTo(QPointF(17.5, 20.6 + y), QPointF(9.2, 19.8 + y), QPointF(6.8, 12.7 + y))
        p.setBrush(self.FUR)
        p.drawPath(head)

        # 大金眼：眨眼时变成两条月牙，平时保留黑猫参考图的圆亮眼。
        eye_y = 11.0 + y
        eye_specs = ((11.0, eye_y, -0.6), (17.2, eye_y + 0.8, 0.6))
        open_amt = 1.0 if ctx.state == "grab" else ctx.eye_open
        for ex, ey, tilt in eye_specs:
            if open_amt < 0.35:
                p.setPen(QPen(self.EYE_GOLD, 1.2))
                p.setBrush(Qt.NoBrush)
                blink = QPainterPath(QPointF(ex - 2.0, ey))
                blink.quadTo(QPointF(ex, ey - 1.2), QPointF(ex + 2.0, ey))
                p.drawPath(blink)
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(self.EYE_GOLD)
            p.drawEllipse(QRectF(ex - 2.4, ey - 3.0, 4.8, 6.0))
            p.setBrush(self.EYE_DARK)
            pupil_x = ex + tilt * 0.2 + ctx.look * 0.55
            pupil_y = ey + ctx.look_y * 0.45
            p.drawEllipse(QRectF(pupil_x - 1.1, pupil_y - 2.0, 2.1, 4.1))
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(QRectF(pupil_x - 1.0, pupil_y - 2.4, 0.9, 1.1))

        # 白色胡须在黑色剪影上更清楚；保持短线，避免小尺寸糊成杂点。
        p.setPen(QPen(self.WHISKER, 0.9))
        for s in (-1, 1):
            bx = 14.2 + s * 3.0
            p.drawLine(QPointF(bx, 14.2 + y), QPointF(bx + s * 4.2, 13.2 + y))
            p.drawLine(QPointF(bx, 15.4 + y), QPointF(bx + s * 4.3, 15.8 + y))


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
    char.draw(p, Ctx(0.0, 1.0, 0.0, 0.0, "idle", 0.0, "normal"))
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
    ACTION_DUR = {"jump": 0.85, "hop": 0.6, "nod": 0.7, "wiggle": 0.75,
                  "tilt": 1.1, "lean": 0.8, "spin": 0.95, "dance": 1.7}

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
        self.look_y_f = 0.0
        self._look_target = 0
        self._look_target_y = 0.0
        self._follow_target_x = 0.0
        self._follow_target_y = 0.0
        self.follow = False
        self._next_look = random.uniform(2.0, 4.0)
        self.state = "idle"
        self.react_t = 0.0
        self.action = "idle"
        self.action_t = 0.0
        self.action_dur = 1.0
        self.face = "normal"
        self._last_sig = None
        self._content_inset_sig = None
        self._content_inset_cache = (0, 0, 0, 0)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 * self._dt))

    # --- 对外接口 ---
    def natural_size(self):
        cw, ch = self.char.canvas
        return QSize(cw * self.scale, ch * self.scale)

    def content_inset(self):
        """按当前像素帧的真实 alpha 计算透明留白，让气泡贴住本体而不是画布顶。"""
        ctx = self._make_ctx()
        sig = (ctx.breath, ctx.eye_open, ctx.look, ctx.look_y, ctx.state, ctx.yoff,
               ctx.face, self.action, round(self.action_t, 2), self.scale,
               self.style, self.char.name)
        if sig == self._content_inset_sig:
            return self._content_inset_cache

        img = self._render_logical_frame(ctx)
        cw, ch = img.width(), img.height()
        x0, y0, x1, y1 = cw, ch, -1, -1
        for yy in range(ch):
            for xx in range(cw):
                if img.pixelColor(xx, yy).alpha() <= 8:
                    continue
                x0 = min(x0, xx)
                y0 = min(y0, yy)
                x1 = max(x1, xx)
                y1 = max(y1, yy)
        if x1 < x0 or y1 < y0:
            inset = (0, 0, 0, 0)
        else:
            sx = self.width() / float(max(1, cw))
            sy = self.height() / float(max(1, ch))
            inset = (
                int(round(x0 * sx)),
                int(round(y0 * sy)),
                int(round((cw - x1 - 1) * sx)),
                int(round((ch - y1 - 1) * sy)),
            )
        self._content_inset_sig = sig
        self._content_inset_cache = inset
        return inset

    def set_scale(self, scale):
        self.scale = max(1, int(scale))
        cw, ch = self.char.canvas
        self.setFixedSize(cw * self.scale, ch * self.scale)
        self._content_inset_sig = None

    def set_style(self, style):
        if style in ("pixel", "smooth"):
            self.style = style
            self._last_sig = None
            self._content_inset_sig = None
            self.update()

    def set_follow(self, on):
        self.follow = bool(on)
        if not self.follow:
            self._follow_target_x = 0.0
            self._follow_target_y = 0.0
        self._last_sig = None

    def set_look(self, dx, dy):
        """dx/dy 为 [-1,1]，由主窗口按鼠标相对位置传入。"""
        if not self.follow:
            return
        self._follow_target_x = max(-1.0, min(1.0, float(dx)))
        self._follow_target_y = max(-1.0, min(1.0, float(dy)))

    def play(self, action):
        """播放像素宠物自身动作，避免用主窗口位移冒充本体动作。"""
        if action not in self.ACTION_DUR:
            return
        self.action = action
        self.action_t = 0.0
        self.action_dur = self.ACTION_DUR.get(action, 0.7)
        if self.state != "grab":
            self.state = "idle"
            self.face = "happy" if action in ("jump", "hop", "dance") else "normal"
        self._last_sig = None
        self._content_inset_sig = None
        self.update()

    def react(self, event):
        # touch_head 事件不触发动作
        if event == "touch_head":
            return
        if event == "grab":
            self.action = "idle"
            self.action_t = 0.0
            self.state, self.face = "grab", "surprised"
        elif event == "drop":
            self.state = "idle"
            self.play("hop")
        elif event == "click":
            self.state = "idle"
            self.play("jump")
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
        self._update_action(dt)

        if self.style == "pixel" and self.action == "idle":
            ctx = self._make_ctx()
            sig = (ctx.breath, ctx.eye_open, ctx.look, ctx.look_y, ctx.state, ctx.yoff, ctx.face)
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
        if self.follow:
            target_x = self._follow_target_x
            target_y = self._follow_target_y
        else:
            self._next_look -= dt
            if self._next_look <= 0:
                self._look_target = random.choice([-1, 0, 0, 1])
                self._look_target_y = random.choice([-0.6, 0.0, 0.0, 0.45])
                self._next_look = random.uniform(2.0, 4.5)
            target_x = self._look_target
            target_y = self._look_target_y
        self.look_f += (target_x - self.look_f) * min(1.0, dt * 5)
        self.look_y_f += (target_y - self.look_y_f) * min(1.0, dt * 5)

    def _update_action(self, dt):
        if self.action == "idle":
            if self.state != "grab" and self.face != "normal":
                self.face = "normal"
            return
        self.action_t += dt / max(0.1, self.action_dur)
        if self.action_t >= 1.0:
            self.action = "idle"
            self.action_t = 0.0
            if self.state != "grab":
                self.face = "normal"
            self._content_inset_sig = None

    def _make_ctx(self):
        b = (math.sin(self.t * 2 * math.pi / self.BREATH_PERIOD) * 0.5 + 0.5) * self.BREATH_AMP
        if self.state == "grab":
            yoff = -2.0
        else:
            yoff = self._action_yoff()
        eye, look, look_y = self.eye_open, self.look_f, self.look_y_f
        if self.style == "pixel":          # 量化为整数并分档 -> 离散步进
            b = float(round(b))
            yoff = float(round(yoff))
            look = float(round(look))
            look_y = float(round(look_y))
            eye = 1.0 if eye > 0.6 else (0.5 if eye > 0.15 else 0.0)
        return Ctx(b, eye, look, look_y, self.state, yoff, self.face)

    def _action_yoff(self):
        if self.action == "idle":
            return 0.0
        p = max(0.0, min(1.0, self.action_t))
        up = math.sin(math.pi * p)
        if self.action == "jump":
            return -self.JUMP_H * 1.15 * up
        if self.action == "hop":
            return -self.JUMP_H * 0.72 * up
        if self.action == "nod":
            return 1.6 * abs(math.sin(p * 2 * math.pi * 2)) * (1 - p)
        if self.action == "dance":
            return -1.8 * abs(math.sin(p * 2 * math.pi * 2))
        if self.action == "spin":
            return -2.0 * up
        return 0.0

    def _action_transform(self):
        if self.action == "idle":
            return 0.0, 0.0, 1.0, 1.0
        p = max(0.0, min(1.0, self.action_t))
        up = math.sin(math.pi * p)
        decay = max(0.0, 1.0 - p)
        dx = rot = 0.0
        sx = sy = 1.0
        if self.action in ("jump", "hop"):
            sx = 1.0 - (0.05 if self.action == "jump" else 0.03) * up
            sy = 1.0 + (0.08 if self.action == "jump" else 0.05) * up
        elif self.action == "wiggle":
            dx = 1.8 * math.sin(p * 2 * math.pi * 3) * decay
            rot = 4.0 * math.sin(p * 2 * math.pi * 3) * decay
        elif self.action == "nod":
            sy = 1.0 - 0.05 * abs(math.sin(p * 2 * math.pi * 2)) * decay
        elif self.action == "tilt":
            rot = 8.0 * math.sin(math.pi * p)
        elif self.action == "lean":
            dx = 2.0 * math.sin(math.pi * p)
            rot = 6.0 * math.sin(math.pi * p)
        elif self.action == "spin":
            sx = math.cos(2 * math.pi * p)
        elif self.action == "dance":
            dx = 1.8 * math.sin(p * 2 * math.pi * 2)
            rot = 6.0 * math.sin(p * 2 * math.pi * 2)
        if self.style == "pixel":
            dx = float(round(dx))
        return dx, rot, sx, sy

    def _draw_character(self, painter, ctx):
        dx, rot, sx, sy = self._action_transform()
        cw, ch = self.char.canvas
        painter.save()
        painter.translate(cw / 2.0 + dx, ch / 2.0)
        painter.rotate(rot)
        painter.scale(sx, sy)
        painter.translate(-cw / 2.0, -ch / 2.0)
        self.char.draw(painter, ctx)
        painter.restore()

    def paintEvent(self, ev):
        ctx = self._make_ctx()
        cw, ch = self.char.canvas
        if self.style == "smooth":         # 非像素：按比例 + 抗锯齿，直接画到控件
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.scale(self.width() / cw, self.height() / ch)
            self._draw_character(p, ctx)
            p.end()
        else:                              # 像素：32x32 小图 + 最近邻放大
            self._img.fill(Qt.transparent)
            ip = QPainter(self._img)
            ip.setRenderHint(QPainter.Antialiasing, False)
            self._draw_character(ip, ctx)
            ip.end()
            p = QPainter(self)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(self.rect(), self._img)
            p.end()

    def _render_logical_frame(self, ctx):
        cw, ch = self.char.canvas
        img = QImage(cw, ch, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, self.style == "smooth")
        self._draw_character(p, ctx)
        p.end()
        return img
