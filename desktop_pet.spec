# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本：生成 dist/DesktopPet/DesktopPet.exe（onedir，无控制台窗口）。

构建：  .venv\\Scripts\\python.exe -m PyInstaller desktop_pet.spec --noconfirm
版本：  v3.9.6
说明：  build_exe.bat 只更新 dist/DesktopPet/_internal 和 DesktopPet.exe；模型目录由用户维护。
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# live2d-py 带原生 DLL / .pyd（Cubism Core），必须整体收集
_d, _b, _h = collect_all("live2d")
datas += _d
binaries += _b
hiddenimports += _h

# send2trash 必须完整收集（包含所有子模块）
_d2, _b2, _h2 = collect_all("send2trash")
datas += _d2
binaries += _b2
hiddenimports += _h2
hiddenimports += collect_submodules("send2trash")

# 添加语音翻译数据库
datas += [('voice_translations.json', '.')]
hiddenimports += [
    "live2d.v2", "live2d.v2cpp", "live2d.v3", "live2d_pet", "image_pet",
    "pixel_pet", "config", "system", "OpenGL", "OpenGL.GL", "numpy",
    "chat_bubble", "companion_quotes", "mesugaki_quotes", "voice_player", "audioop", "wave",
    "pygame", "pygame.mixer",
    # 运气抽签数据（main.py 里函数内 import fortune_data，显式列出更稳妥）
    "fortune_data",
    # 养成 / 好感度系统
    "affinity", "affinity_quotes",
    # v3.7 新增模块
    "tts_player", "holiday_greetings",
    # 模型删除到回收站 - 使用 collect_all 已经包含，这里保留作为备份
    "send2trash", "send2trash.win", "send2trash.exceptions", "send2trash.util",
    # v3.7.2：TTS 改用 PySide6 自带的 QtTextToSpeech（不再依赖 pyttsx3/pywin32）
    "PySide6.QtTextToSpeech",
]

# QtTextToSpeech 随 PySide6 提供，但 PyInstaller 默认会漏掉它的运行时语音插件
# （Qt 在运行时按需加载 plugins/texttospeech/*.dll，不是链接依赖）。这里显式收集
# 模块 DLL + 全部 texttospeech 插件，确保打包后的 exe 也能朗读气泡文字。
import os as _os
import PySide6 as _pyside6
_ps_root = _os.path.dirname(_pyside6.__file__)
_tts_dll = _os.path.join(_ps_root, "Qt6TextToSpeech.dll")
if _os.path.isfile(_tts_dll):
    binaries += [(_tts_dll, "PySide6")]
_tts_plugins = _os.path.join(_ps_root, "plugins", "texttospeech")
if _os.path.isdir(_tts_plugins):
    for _f in _os.listdir(_tts_plugins):
        if _f.lower().endswith(".dll"):
            binaries += [(_os.path.join(_tts_plugins, _f),
                          _os.path.join("PySide6", "plugins", "texttospeech"))]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 本程序只用到 QtCore/QtGui/QtWidgets/QtOpenGLWidgets/QtTextToSpeech，
        # 其余 Qt 大模块整体排除，显著减小打包体积（每个都会带一个几 MB 的 DLL）。
        "tkinter", "matplotlib", "scipy", "pandas", "numpy.testing",
        "PIL", "Pillow",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtWebChannel", "PySide6.QtWebSockets",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
        "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
        "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
        "PySide6.QtQuickControls2", "PySide6.QtQml",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
        "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtPrintSupport", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtDBus",
        "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtDesigner",
        "PySide6.QtUiTools", "PySide6.QtHelp", "PySide6.QtScxml",
        "PySide6.QtRemoteObjects", "PySide6.QtStateMachine", "PySide6.QtVirtualKeyboard",
        "PySide6.QtConcurrent", "PySide6.QtWebView",
    ],
    noarchive=False,
)

# ── 体积优化：PySide6 的 PyInstaller hook 会把整目录的 Qt6 DLL/插件都收进来，
# 即使在 excludes 里排除了对应 Python 模块也不会删 DLL。这里直接在 a.binaries /
# a.datas 上按目标路径过滤，剔除本程序用不到的大块 Qt（QtQuick/QtQml/QtPdf 等）。
# 保守起见保留 QtNetwork / QtMultimedia（TTS 后端可能间接依赖）与全部平台/图片/TTS 插件。
def _qt_keep(dest):
    d = str(dest).replace("\\", "/").lower()
    # 必须保留的插件目录：没有它们程序起不来或图片加载失败
    for keep_dir in ("/plugins/platforms", "/plugins/imageformats",
                     "/plugins/iconengines", "/plugins/styles",
                     "/plugins/platforminputcontexts", "/plugins/generic"):
        if keep_dir in d:
            return True
    if "texttospeech" in d:        # 朗读后端：保留 sapi/winrt，丢掉用不到的 mock
        return "mock" not in d
    # 明确丢弃的插件目录（QML 工具链、PDF、虚拟键盘、数据库驱动等）
    for drop_dir in ("/plugins/qmltooling", "/plugins/scenegraph",
                     "/plugins/virtualkeyboard", "/plugins/pdf",
                     "/plugins/sqldrivers", "/plugins/position",
                     "/plugins/sensors", "/plugins/designer",
                     "/plugins/printsupport", "/plugins/webview",
                     "/plugins/sceneparsers", "/plugins/renderplugins",
                     "/plugins/geometryloaders", "/plugins/networkinformation"):
        if drop_dir in d:
            return False
    # 明确丢弃的大块 Qt DLL（按文件名前缀匹配，连带 *QmlModels/*QuickWidgets 等）
    base = d.rsplit("/", 1)[-1]
    drop_dll = (
        "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qt6svg",
        "qt6sql", "qt6test", "qt6websockets", "qt6charts",
        "qt6datavisualization", "qt6printsupport", "qt6serialport",
        "qt6bluetooth", "qt6nfc", "qt6designer", "qt6help", "qt6scxml",
        "qt6remoteobjects", "qt6statemachine", "qt6concurrent",
        "qt6webview", "qt6dbus", "qt6labs",
    )
    if base.startswith(drop_dll):
        return False
    return True

_before = (len(a.binaries), len(a.datas))
a.binaries = [b for b in a.binaries if _qt_keep(b[0])]
a.datas = [d for d in a.datas if _qt_keep(d[0])]
print("[spec] Qt 体积优化：binaries %d->%d, datas %d->%d"
      % (_before[0], len(a.binaries), _before[1], len(a.datas)))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DesktopPet",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="app_icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DesktopPet",
)
