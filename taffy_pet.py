"""塔菲专用分层渲染器：提供真实可见的待机、动作、表情与鼠标跟随。"""
import json
import math
import os
import random

from PySide6.QtCore import Qt, QTimer, QSize, QRect, QRectF, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget


class TaffyPet(QWidget):
    CANVAS_W = 1024.0
    CANVAS_H = 1536.0
    MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 90, 95, 40
    ACTIONS = {"idle": 3.0, "jump": 3.0, "hop": 3.0, "nod": 3.0,
               "wiggle": 3.0, "tilt": 3.0, "lean": 3.0, "spin": 3.0,
               "dance": 3.0, "pet": 3.0, "grab": 3.0, "land": 3.0}
    ACTION_LABELS = {"idle": "待机站立", "jump": "开心起跳", "hop": "轻轻蹦跳",
                     "nod": "点头", "wiggle": "摇头", "tilt": "好奇歪头",
                     "lean": "闭眼陶醉摇摆", "spin": "转身", "dance": "开心抬腿舞步",
                     "pet": "摸头弯腰"}
    ACTION_SPRITES = {"dance": "dance", "lean": "sway", "tilt": "curious", "pet": "pet"}
    EXPRESSION_LABELS = {"happy": "闭眼开心", "shy": "害羞", "hard_cry": "嚎啕大哭",
                         "toothy": "眯眼露齿笑", "nervous": "汗滴尴尬",
                         "soft_cry": "委屈哭", "money": "金钱眼", "cheer": "好耶",
                         "surprised": "惊讶"}
    FACE_MOTION_MAP = {"face_happy": "happy", "face_shy": "shy",
                       "face_surprised": "surprised", "face_nervous": "nervous",
                       "face_sad": "soft_cry", "face_smug": "toothy",
                       "face_angry": "hard_cry", "face_wink": "cheer"}
    EYE_SPECS = {
        "Eye_L": ((426.0, 420.0), (428.0, 424.0)),
        "Eye_R": ((596.0, 420.0), (596.0, 424.0)),
    }
    EXPRESSION_BLEND_DURATION = 0.16

    def __init__(self, model_path, size=300, zoom=1.0, xoff=0.0, yoff=0.0,
                 parent=None, ratio=None, preview_mode=False, canvas_scale=1.0):
        super().__init__(parent)
        self.model_path = os.path.abspath(model_path)
        self.asset_dir = os.path.dirname(self.model_path)
        self._preview_mode = bool(preview_mode)
        self._size = max(80, int(size))
        self._zoom = max(0.35, min(3.0, float(zoom)))
        self._xoff = max(-2.0, min(2.0, float(xoff)))
        self._yoff = max(-2.0, min(2.0, float(yoff)))
        self._ratio = float(ratio) if ratio else self.CANVAS_H / self.CANVAS_W
        self._canvas_scale = max(0.7, min(2.5, float(canvas_scale or 1.0)))
        self._layers, self._expressions, self._action_images = {}, {}, {}
        self._render_buffer = None
        self._expression = ""
        self._expression_from = ""
        self._expression_mix = 1.0
        self._auto_expression = False
        self._next_expression_t = 0.0
        self._disabled_motions = set()
        self.follow = not self._preview_mode
        self.look_x = self.look_y = self._look_x = self._look_y = 0.0
        self.action, self.action_t, self.action_dur = "idle", 0.0, self.ACTIONS["idle"]
        self.t, self.fps = 0.0, 30
        self.on_error = self.on_resized = self.on_voice_with_text = None
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self._load_base_layers()
        self._apply_size()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(round(1000 / self.fps))

    def _asset_path(self, *parts):
        return os.path.join(self.asset_dir, *parts)

    @staticmethod
    def _load_cropped(path):
        image = QImage(path)
        if image.isNull():
            raise RuntimeError("无法加载塔菲图层: %s" % path)
        image = image.convertToFormat(QImage.Format_ARGB32)
        return TaffyPet._crop_image(image)

    @staticmethod
    def _crop_image(image):
        rect = None
        try:
            import numpy as np
            alpha = np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.width(), 4)[..., 3]
            ys, xs = np.where(alpha > 8)
            if len(xs):
                x0, y0 = int(xs.min()), int(ys.min())
                x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
                rect = QRect(x0, y0, x1 - x0, y1 - y0)
        except Exception:
            pass
        rect = rect or image.rect()
        return QPixmap.fromImage(image.copy(rect)), rect.x(), rect.y()

    @staticmethod
    def _ellipse_mask(size, center, radius):
        mask = QImage(size, QImage.Format_ARGB32)
        mask.fill(0)
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)
        painter.drawEllipse(QRectF(center[0] - radius[0], center[1] - radius[1],
                                   radius[0] * 2.0, radius[1] * 2.0))
        painter.end()
        return mask

    @staticmethod
    def _apply_mask(source, mask, keep_inside):
        image = source.copy()
        painter = QPainter(image)
        mode = (QPainter.CompositionMode_DestinationIn if keep_inside
                else QPainter.CompositionMode_DestinationOut)
        painter.setCompositionMode(mode)
        painter.drawImage(0, 0, mask)
        painter.end()
        return image

    def _load_split_eye(self, name):
        path = self._asset_path("live2d_layers", name + ".png")
        source = QImage(path).convertToFormat(QImage.Format_ARGB32)
        if source.isNull():
            raise RuntimeError("无法加载塔菲眼部图层: %s" % path)
        pivot, iris_center = self.EYE_SPECS[name]
        eye_mask = self._ellipse_mask(source.size(), pivot, (70.0, 84.0))
        # 切片边界放到眼白区域，虹膜平移后不会露出彩色区域的硬接缝。
        iris_mask = self._ellipse_mask(source.size(), iris_center, (48.0, 60.0))
        eye_dynamic = self._apply_mask(source, eye_mask, True)
        self._layers[name + "_Static"] = self._crop_image(
            self._apply_mask(source, eye_mask, False))
        self._layers[name + "_Frame"] = self._crop_image(
            self._apply_mask(eye_dynamic, iris_mask, False))
        self._layers[name + "_Iris"] = self._crop_image(
            self._apply_mask(eye_dynamic, iris_mask, True))

        socket = QImage(source.size(), QImage.Format_ARGB32)
        socket.fill(0)
        painter = QPainter(socket)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 250, 242))
        painter.drawEllipse(QRectF(iris_center[0] - 50.0, iris_center[1] - 62.0,
                                   100.0, 124.0))
        painter.end()
        self._layers[name + "_Socket"] = self._crop_image(socket)

    def _load_base_layers(self):
        master_path = self._asset_path("taffy_master.png")
        self._master = self._load_cropped(master_path)
        names = ("Body_Core", "Leg_L", "Leg_R", "Arm_L", "Arm_R", "Hair_L", "Hair_R",
                 "Head_Core", "Mouth_Default")
        for name in names:
            self._layers[name] = self._load_cropped(self._asset_path("live2d_layers", name + ".png"))
        self._load_split_eye("Eye_L")
        self._load_split_eye("Eye_R")
        base = QImage(master_path).convertToFormat(QImage.Format_ARGB32)
        painter = QPainter(base)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        for name in ("Arm_L", "Arm_R"):
            pixmap, x, y = self._layers[name]
            painter.drawPixmap(QPointF(float(x), float(y)), pixmap)
        painter.end()
        self._base_without_arms = self._crop_image(base)

    def _expression_layers(self, expression):
        cached = self._expressions.get(expression)
        if cached is not None:
            return cached
        path = self._asset_path("full_expressions", f"{expression}.png")
        cached = self._load_cropped(path) if os.path.isfile(path) else None
        self._expressions[expression] = cached
        return cached

    def _action_image(self, action):
        cached = self._action_images.get(action)
        if cached is not None:
            return cached
        filename = self.ACTION_SPRITES.get(action)
        path = self._asset_path("full_actions", f"{filename}.png") if filename else ""
        cached = self._load_cropped(path) if path and os.path.isfile(path) else None
        self._action_images[action] = cached
        return cached

    def _apply_size(self):
        scale = self._size / self.CANVAS_H
        width = round((self.CANVAS_W * self._zoom + self.MARGIN_X * 2) * scale * self._canvas_scale)
        height = round((self.CANVAS_H * self._zoom + self.MARGIN_TOP + self.MARGIN_BOTTOM) * scale)
        self.setFixedSize(max(80, width), max(100, height))

    def natural_size(self):
        return QSize(self.width(), self.height())

    def live2d_size(self):
        return self._size

    def set_live2d_size(self, size):
        self._size = max(80, int(size))
        self._apply_size()
        if callable(self.on_resized):
            QTimer.singleShot(0, self.on_resized)

    def set_scale(self, *_):
        pass

    def set_height_ratio(self, ratio):
        self._ratio = max(0.8, min(3.0, float(ratio)))

    def height_ratio(self):
        return self._ratio

    def set_canvas_scale(self, scale):
        self._canvas_scale = max(0.7, min(2.5, float(scale or 1.0)))
        self._apply_size()
        if callable(self.on_resized):
            QTimer.singleShot(0, self.on_resized)

    def canvas_scale(self):
        return self._canvas_scale

    def set_view(self, zoom, xoff, yoff):
        self._zoom = max(0.35, min(3.0, float(zoom)))
        self._xoff = max(-2.0, min(2.0, float(xoff)))
        self._yoff = max(-2.0, min(2.0, float(yoff)))
        self._apply_size()
        if callable(self.on_resized):
            QTimer.singleShot(0, self.on_resized)

    def get_view(self):
        return self._zoom, self._xoff, self._yoff

    def fit_to_content(self, *_args, **_kwargs):
        return True

    def content_inset(self):
        scale = self._size / self.CANVAS_H
        side = max(0, round(self.MARGIN_X * scale * self._canvas_scale))
        return side, max(0, round(self.MARGIN_TOP * scale)), side, max(0, round(self.MARGIN_BOTTOM * scale))

    def refresh_content_box(self):
        return None

    def set_mask_updates_enabled(self, _enabled):
        pass

    def set_render_active(self, enabled):
        if enabled:
            if not self.timer.isActive() and self.isVisible():
                self.timer.start(round(1000 / self.fps))
        else:
            self.timer.stop()

    def set_follow(self, enabled):
        self.follow = bool(enabled)
        if not self.follow:
            self.look_x = self.look_y = 0.0
            self._look_x = self._look_y = 0.0

    def set_look(self, dx, dy):
        if self.follow:
            self.look_x = max(-1.0, min(1.0, float(dx) * 1.35))
            self.look_y = max(-1.0, min(1.0, float(dy) * 1.35))

    def set_voice_enabled(self, _enabled):
        pass

    def set_voice_volume(self, _volume):
        pass

    def has_voice(self):
        return False

    def set_disabled_motions(self, keys):
        self._disabled_motions = set(keys or [])

    def motion_menu(self):
        return [(name, [{"index": 0, "label": label}])
                for name, label in self.ACTION_LABELS.items()]

    def play_motion(self, group, index=None, with_voice=True, with_subtitle=True):
        del index, with_voice, with_subtitle
        group = str(group or "").lower()
        if group in self.FACE_MOTION_MAP:
            self.set_expression(self.FACE_MOTION_MAP[group])
            self.play("nod")
            return None
        self.play(group if group in self.ACTIONS else random.choice(tuple(self.ACTION_LABELS)))
        return None

    def play_group_random(self, group):
        return self.play_motion(group)

    def play(self, action):
        action = str(action or "idle").lower()
        if action not in self.ACTIONS:
            action = "idle"
        self.action, self.action_t, self.action_dur = action, 0.0, self.ACTIONS[action]

    def react(self, event, with_voice=True, with_subtitle=True):
        del with_voice, with_subtitle
        if event == "touch_head":
            self.play("pet")
        elif event == "grab":
            self.play("idle")
        elif event == "drop":
            self.play("idle")
        elif event == "land":
            self.play("land")
        elif event == "click":
            self.set_random_expression()
            self.play(random.choice(("hop", "nod", "tilt", "dance")))
        return None

    def list_expressions(self):
        return list(self.EXPRESSION_LABELS)

    def set_expression(self, expression):
        expression = str(expression or "")
        for key, label in self.EXPRESSION_LABELS.items():
            if expression in (key, label):
                if key == self._expression and self._expression_mix >= 1.0:
                    return
                self._expression_from = self._expression
                self._expression = key
                self._expression_mix = 0.0
                self._expression_layers(key)
                self.update()
                return

    def set_random_expression(self):
        self.set_expression(random.choice(tuple(self.EXPRESSION_LABELS)))

    def reset_expression(self):
        self._expression_from = self._expression
        self._expression = ""
        self._expression_mix = 0.0
        self.update()

    def set_auto_expression(self, enabled):
        self._auto_expression = bool(enabled)
        self._next_expression_t = self.t + random.uniform(7.0, 12.0)

    def expression_label(self, expression):
        return self.EXPRESSION_LABELS.get(expression, expression)

    def _tick(self):
        dt = 1.0 / self.fps
        self.t += dt
        self._look_x += (self.look_x - self._look_x) * min(1.0, dt * 7.5)
        self._look_y += (self.look_y - self._look_y) * min(1.0, dt * 7.5)
        if self._expression_mix < 1.0 and self.action not in self.ACTION_SPRITES:
            self._expression_mix = min(
                1.0, self._expression_mix + dt / self.EXPRESSION_BLEND_DURATION)
            if self._expression_mix >= 1.0:
                self._expression_from = ""
        if self.action not in ("idle", "grab"):
            self.action_t += dt / max(0.01, self.action_dur)
            if self.action_t >= 1.0:
                self.action, self.action_t = "idle", 0.0
        if self._auto_expression and self.t >= self._next_expression_t:
            self.set_random_expression()
            self._next_expression_t = self.t + random.uniform(8.0, 14.0)
        self.update()

    def _action_pose(self):
        action = self.action
        progress = max(0.0, min(1.0, self.action_t))
        up = math.sin(math.pi * progress)
        pose = {"tx": 0.0, "ty": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0,
                "head_rot": 0.0, "head_y": 0.0, "arm_l": 0.0, "arm_r": 0.0,
                "leg_l": 0.0, "leg_r": 0.0}
        if action == "jump":
            pose.update(ty=-150.0 * up, sy=1.0 + 0.06 * up, sx=1.0 - 0.04 * up,
                        arm_l=28.0 * up, arm_r=-28.0 * up,
                        leg_l=-15.0 * up, leg_r=15.0 * up)
        elif action == "hop":
            pose.update(ty=-78.0 * up, rot=4.0 * math.sin(progress * math.pi * 2.0),
                        arm_l=12.0 * up, arm_r=-22.0 * up)
        elif action == "nod":
            wave = abs(math.sin(progress * math.pi * 3.0)) * (1.0 - progress * 0.35)
            pose.update(head_y=25.0 * wave, head_rot=2.0 * wave,
                        arm_l=-4.0 * up, arm_r=4.0 * up)
        elif action == "wiggle":
            arm_wave = math.sin(progress * math.pi * 4.0) * (1.0 - progress * 0.25)
            pose.update(head_rot=13.0 * math.sin(progress * math.pi * 6.0) * (1.0 - progress * 0.4),
                        rot=3.0 * math.sin(progress * math.pi * 4.0),
                        arm_l=10.0 * arm_wave, arm_r=7.0 * arm_wave)
        elif action == "tilt":
            pose.update(ty=-4.0 * up, rot=2.0 * up)
        elif action == "lean":
            pose.update(tx=8.0 * up, rot=2.0 * math.sin(progress * math.pi * 2.0))
        elif action == "spin":
            pose.update(sx=math.cos(progress * math.pi * 2.0), ty=-35.0 * up,
                        arm_l=20.0 * up, arm_r=-20.0 * up)
        elif action == "dance":
            beat = math.sin(progress * math.pi * 4.0)
            pose.update(ty=-12.0 * abs(beat), rot=2.0 * beat)
        elif action == "pet":
            pose.update(ty=8.0 * up, sy=1.0 + 0.02 * up)
        elif action == "land":
            squash = math.sin(math.pi * min(1.0, progress * 1.4))
            pose.update(ty=20.0 * squash, sx=1.0 + 0.07 * squash,
                        sy=1.0 - 0.10 * squash,
                        arm_l=-8.0 * squash, arm_r=8.0 * squash)
        return pose

    @staticmethod
    def _draw_asset(painter, asset, pivot=None, rotation=0.0, tx=0.0, ty=0.0,
                    sx=1.0, sy=1.0, opacity=1.0):
        if not asset:
            return
        pixmap, x, y = asset
        painter.save()
        painter.setOpacity(opacity)
        if pivot is not None:
            painter.translate(pivot[0] + tx, pivot[1] + ty)
            painter.rotate(rotation)
            painter.scale(sx, sy)
            painter.translate(-pivot[0], -pivot[1])
        else:
            painter.translate(tx, ty)
            painter.scale(sx, sy)
        painter.drawPixmap(QPointF(float(x), float(y)), pixmap)
        painter.restore()

    def _draw_face(self, painter, head_transform, blink):
        painter.save()
        px, py, rotation, tx, ty = head_transform
        painter.translate(px + tx, py + ty)
        painter.rotate(rotation)
        painter.translate(-px, -py)
        look_active = (self.action == "idle"
                       and not self._expression and not self._expression_from)
        if look_active:
            eye_dx = self._look_x * (6.0 if self._look_x >= 0.0 else 9.0)
            eye_dy = -self._look_y * 5.0
        else:
            eye_dx = eye_dy = 0.0
        self._draw_eye(painter, "Eye_L", blink, eye_dx, eye_dy)
        self._draw_eye(painter, "Eye_R", blink, eye_dx, eye_dy)
        self._draw_asset(painter, self._layers["Mouth_Default"])
        painter.restore()

    def _draw_eye(self, painter, name, blink, eye_dx, eye_dy):
        pivot, _iris_center = self.EYE_SPECS[name]
        self._draw_asset(painter, self._layers[name + "_Static"])
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(QRectF(pivot[0] - 70.0, pivot[1] - 84.0, 140.0, 168.0))
        painter.setClipPath(clip, Qt.IntersectClip)
        self._draw_asset(painter, self._layers[name + "_Socket"], pivot, sy=blink)
        self._draw_asset(painter, self._layers[name + "_Iris"], pivot,
                         tx=eye_dx, ty=eye_dy, sy=blink)
        self._draw_asset(painter, self._layers[name + "_Frame"], pivot, sy=blink)
        painter.restore()

    def _draw_default_pose(self, painter, pose, head_transform, blink):
        arm_l = max(-28.0, min(28.0, pose["arm_l"]))
        arm_r = max(-28.0, min(28.0, pose["arm_r"]))
        self._draw_asset(painter, self._base_without_arms)
        self._draw_asset(painter, self._layers["Arm_L"], (355.0, 650.0),
                         rotation=arm_l)
        self._draw_asset(painter, self._layers["Arm_R"], (669.0, 650.0),
                         rotation=arm_r)
        self._draw_asset(painter, self._layers["Head_Core"])
        self._draw_face(painter, head_transform, blink)

    def _draw_expression_state(self, painter, expression, pose, head_transform, blink):
        if expression:
            self._draw_asset(painter, self._expression_layers(expression))
        else:
            self._draw_default_pose(painter, pose, head_transform, blink)

    def _paint_scene(self, painter):
        pose = self._action_pose()
        blink_phase = self.t % 4.6
        blink = 1.0
        if blink_phase < 0.13:
            blink = max(0.08, abs(blink_phase - 0.065) / 0.065)
        head_rotation = 0.0
        head_tx = 0.0
        head_ty = 0.0
        head_transform = (512.0, 560.0, head_rotation, head_tx, head_ty)

        scale = self._size / self.CANVAS_H * self._zoom
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.translate(self.width() / 2.0, self.MARGIN_TOP * self._size / self.CANVAS_H)
        painter.translate(self._xoff * self._size * 0.25, -self._yoff * self._size * 0.25)
        painter.scale(scale * self._canvas_scale, scale)
        painter.translate(-self.CANVAS_W / 2.0, 0.0)
        painter.translate(512.0 + pose["tx"], 1410.0 + pose["ty"])
        painter.rotate(pose["rot"])
        painter.scale(pose["sx"], pose["sy"])
        painter.translate(-512.0, -1410.0)

        painter.save()
        current = self._action_image(self.action)
        if current is not None:
            self._draw_asset(painter, current)
        else:
            mix = max(0.0, min(1.0, self._expression_mix))
            if mix >= 1.0:
                self._draw_expression_state(
                    painter, self._expression, pose, head_transform, blink)
            else:
                eased = mix * mix * (3.0 - 2.0 * mix)
                reveal_y = self.CANVAS_H * eased
                painter.save()
                painter.setClipRect(QRectF(
                    0.0, reveal_y, self.CANVAS_W,
                    max(0.0, self.CANVAS_H - reveal_y)))
                self._draw_expression_state(
                    painter, self._expression_from, pose, head_transform, blink)
                painter.restore()

                painter.save()
                painter.setClipRect(QRectF(
                    0.0, 0.0, self.CANVAS_W, reveal_y))
                self._draw_expression_state(
                    painter, self._expression, pose, head_transform, blink)
                painter.restore()
        painter.restore()

    def paintEvent(self, _event):
        factor = 3 if self._size <= 500 else 2
        buffer_size = QSize(max(1, self.width() * factor), max(1, self.height() * factor))
        if self._render_buffer is None or self._render_buffer.size() != buffer_size:
            self._render_buffer = QImage(buffer_size, QImage.Format_ARGB32)
        self._render_buffer.fill(0)
        high = QPainter(self._render_buffer)
        high.scale(factor, factor)
        self._paint_scene(high)
        high.end()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(self.rect(), self._render_buffer)
        painter.end()

    def shutdown(self):
        self.timer.stop()
        self._layers.clear()
        self._expressions.clear()
        self._action_images.clear()
        self._render_buffer = None

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self.timer.isActive():
            self.timer.start(round(1000 / self.fps))
        super().showEvent(event)


def is_taffy_model(model_path):
    try:
        with open(model_path, encoding="utf-8") as file:
            data = json.load(file)
        return data.get("DesktopPetRenderer") == "taffy-layered"
    except Exception:
        return False
