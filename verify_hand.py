"""离屏渲染新版"摸头小手"光标 + 摸头覆盖手势，放大保存为 PNG 以肉眼检查拇指缺口。
路径代码与 main.py 中 _pet_cursor / _draw_petting_hand 保持一致。"""
import os, math
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPainterPath
from PySide6.QtCore import Qt, QRectF, QPointF

app = QApplication([])

S = 12  # 放大倍数


def draw_cursor(p):
    p.scale(S, S)
    edge = QPen(QColor("#000000")); edge.setWidthF(2.4)
    edge.setJoinStyle(Qt.RoundJoin); edge.setCapStyle(Qt.RoundCap)
    p.setPen(edge); p.setBrush(QColor("#FFFFFF"))
    hand = QPainterPath()
    hand.addRoundedRect(QRectF(11, 13, 18, 16), 7, 7)
    hand.addRoundedRect(QRectF(11.6, 5.6, 4.6, 13.0), 2.3, 2.3)
    hand.addRoundedRect(QRectF(16.0, 3.0, 4.8, 15.5), 2.4, 2.4)
    hand.addRoundedRect(QRectF(20.5, 4.3, 4.7, 14.2), 2.3, 2.3)
    hand.addRoundedRect(QRectF(24.7, 6.8, 4.2, 11.7), 2.1, 2.1)
    thumb = QPainterPath()
    thumb.moveTo(12.6, 15.0)
    thumb.quadTo(6.6, 14.4, 4.3, 19.0)
    thumb.quadTo(2.9, 22.8, 6.2, 24.8)
    thumb.quadTo(9.5, 26.2, 12.5, 22.0)
    thumb.quadTo(13.6, 19.8, 13.9, 16.4)
    thumb.closeSubpath()
    hand.addPath(thumb)
    hand.addRoundedRect(QRectF(13.5, 27.5, 11, 6), 2.2, 2.2)
    hand = hand.simplified()
    p.drawPath(hand)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 205)); p.drawEllipse(QRectF(15, 16, 9, 6))
    p.setBrush(QColor(255, 255, 255, 185))
    for r in (QRectF(12.3,6.9,2.1,3.0), QRectF(16.8,4.4,2.1,3.1), QRectF(21.3,5.6,2.1,3.0),
              QRectF(25.4,8.0,2.0,2.7), QRectF(5.6,19.6,2.4,2.0)):
        p.drawEllipse(r)
    g = QPen(QColor(180,180,180,170)); g.setWidthF(1.0); g.setCapStyle(Qt.RoundCap); p.setPen(g)
    p.drawLine(QPointF(16.0,14.0), QPointF(16.0,8.5))
    p.drawLine(QPointF(20.6,13.6), QPointF(20.6,7.5))
    p.drawLine(QPointF(24.7,14.0), QPointF(24.7,9.5))
    p.drawLine(QPointF(14.5,30.0), QPointF(23.5,30.0))


def draw_petting(p, rect, progress=0.5):
    p.setRenderHint(QPainter.Antialiasing, True)
    arc = math.sin(progress*math.pi); pat = abs(math.sin(progress*2*math.pi*1.2))
    sway = math.sin(progress*2*math.pi*0.8)*rect.width()*0.032
    press = pat*rect.height()*0.11; lift = (1.0-arc)*rect.height()*0.12
    p.translate(rect.center().x()+sway, rect.top()+rect.height()*0.26+press-lift)
    edge = QPen(QColor("#000000")); edge.setWidthF(max(1.8, rect.width()*0.024))
    edge.setJoinStyle(Qt.RoundJoin); edge.setCapStyle(Qt.RoundCap); p.setPen(edge); p.setBrush(QColor("#FFFFFF"))
    unit = rect.width()/40.0
    hand = QPainterPath()
    hand.addRoundedRect(QRectF(-11*unit,0*unit,20*unit,15*unit), 8*unit,8*unit)
    hand.addRoundedRect(QRectF(-10.7*unit,-7.5*unit,4.6*unit,10.5*unit),2.4*unit,2.4*unit)
    hand.addRoundedRect(QRectF(-6.0*unit,-10.0*unit,4.8*unit,12.6*unit),2.4*unit,2.4*unit)
    hand.addRoundedRect(QRectF(-1.0*unit,-9.2*unit,4.8*unit,11.6*unit),2.4*unit,2.4*unit)
    hand.addRoundedRect(QRectF(4.0*unit,-6.8*unit,4.6*unit,9.4*unit),2.4*unit,2.4*unit)
    thumb = QPainterPath()
    thumb.moveTo(-8.8*unit,3.4*unit)
    thumb.quadTo(-14.8*unit,2.6*unit,-16.4*unit,7.6*unit)
    thumb.quadTo(-17.6*unit,11.4*unit,-14.0*unit,13.6*unit)
    thumb.quadTo(-10.4*unit,15.0*unit,-7.4*unit,11.0*unit)
    thumb.quadTo(-6.3*unit,8.6*unit,-6.0*unit,4.6*unit)
    thumb.closeSubpath()
    hand.addPath(thumb)
    hand.addRoundedRect(QRectF(-5.5*unit,13.4*unit,9.4*unit,7.0*unit),2.1*unit,2.1*unit)
    hand = hand.simplified()
    p.drawPath(hand)


# 光标手：亮绿背景上画，便于看清白手轮廓有无缺口
img = QImage(40*S, 40*S, QImage.Format_RGBA8888); img.fill(QColor(40,160,80))
p = QPainter(img); p.setRenderHint(QPainter.Antialiasing, True); draw_cursor(p); p.end()
img.save("verify_hand_cursor.png")

# 摸头覆盖手：透明背景，放大
img2 = QImage(360, 420, QImage.Format_RGBA8888); img2.fill(QColor(40,160,80))
p2 = QPainter(img2); draw_petting(p2, QRectF(40, 30, 280, 320), 0.5); p2.end()
img2.save("verify_hand_petting.png")
print("saved verify_hand_cursor.png, verify_hand_petting.png")
