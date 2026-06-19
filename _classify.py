"""临时：逐个动作渲染素晴模型，判断哪些动作会把手摆到身前(穿模)。
存图后做成 contact sheet 肉眼确认。用完即删。"""
import os, sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
import live2d.v3 as l2d

MODEL = os.path.join("live2d", "model", "素晴-1104100", "1104100.model3.json")
DT = 1.0 / 30.0
_inited = []


class Ctl(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(360, 504)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.model = None
        self.motions = []

    def initializeGL(self):
        if not _inited:
            l2d.init(); _inited.append(1)
        l2d.glInit()
        self.model = l2d.LAppModel()
        self.model.LoadModelJson(MODEL, 4)
        try: self.model.CreateRenderer(4)
        except Exception: pass
        self.model.SetAutoBreathEnable(False)
        self.model.SetAutoBlinkEnable(False)
        self.model.Resize(self.width(), self.height())
        ms = self.model.GetMotions()         # {group:[{File:..}]}
        for g, lst in ms.items():
            for i, e in enumerate(lst):
                self.motions.append((g, i, os.path.basename(e.get("File", "")).replace(".motion3.json", "")))

    def paintGL(self):
        l2d.clearBuffer(0, 0, 0, 0)
        if self.model:
            self.model._model.Update(DT)
            self.model.Draw()


app = QApplication.instance() or QApplication(sys.argv)
w = Ctl(); w.show(); app.processEvents()

os.makedirs("_v2", exist_ok=True)
results = []
for (g, i, name) in w.motions:
    w.model.StopAllMotions()
    w.model.StartMotion(g, i, 3)
    for _ in range(45):                       # 推进到动作中段(抬手峰值附近)
        w.update(); app.processEvents()
    img = w.grabFramebuffer()
    p = f"_v2/m{i:02d}.png"; img.save(p)
    results.append((i, name, p))
    print(i, name)

# contact sheet
cols = 5; rows = (len(results) + cols - 1) // cols
tw, th = 180, 252
sheet = QImage(cols * tw, rows * th, QImage.Format_RGBA8888); sheet.fill(Qt.white)
pt = QPainter(sheet)
for n, (i, name, p) in enumerate(results):
    im = QImage(p).scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pt.drawImage((n % cols) * tw, (n // cols) * th, im)
    pt.drawText((n % cols) * tw + 3, (n // cols) * th + 12, f"{i}:{name}")
pt.end(); sheet.save("_v2/sheet.png")
print("SHEET _v2/sheet.png")
app.quit()
