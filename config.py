"""配置读写：保存在用户目录下 ~/.desktop-pet/config.json，下次启动自动恢复。"""
import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".desktop-pet")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "character": "slime",      # slime | cat | image | live2d
    "scale": 5,                # 像素放大倍数（程序化角色）
    "style": "pixel",          # pixel | smooth（程序化角色画风）
    "always_on_top": False,    # 是否总在最前
    "avoid_taskbar": True,      # 不让宠物窗口盖住任务栏（把窗口限制在屏幕工作区内）
    "click_through": False,    # 鼠标穿透（开启后无法拖动）
    "pos": None,               # 上次位置 [x, y]（兼容旧配置；现按模型存于 model_memory）
    "edge_snap": True,         # 贴边自动隐藏：拖到屏幕左/右/上边缘自动缩回，鼠标移近再划出
    "edge_side": "",           # 当前吸附的边："" | "left" | "right" | "top"
    "edge_cross": 0,           # 吸附时另一轴坐标（left/right 存 y，top 存 x），重启后据此恢复
    "image_path": "",          # 图片宠物的图片路径（建议透明 PNG）
    "image_size": 240,         # 图片宠物高度(px)
    "facing": 1,               # 图片宠物朝向：1 正常 / -1 镜像
    "follow": True,            # 眼睛/头部跟随鼠标
    "gravity": True,           # 放手后重力掉落
    "regions": {},             # 各图片的五官位置 {路径: {eyeL,eyeR,mouth}}
    "live2d_model": "",        # Live2D 模型设置文件路径（Cubism2: model.json/*.model.json，Cubism3: *.model3.json）
    "live2d_size": 300,        # Live2D 窗口边长(px)
    "live2d_zoom": 1.0,        # 旧版全局缩放（保留以兼容旧配置；现按模型存于 live2d_views）
    "live2d_yoff": 0.0,        # 旧版全局竖直偏移（同上）
    "live2d_views": {},        # 按模型构图：{模型路径: {"zoom":float,"xoff":float,"yoff":float,"ratio":float,"canvas_scale":float}}
    "live2d_height_ratio": 1.4, # 新模型默认画布高/宽比例（越大越高，适合站姿立绘）
    "live2d_auto_expression": False,  # Live2D：是否每隔几秒自动随机切换一个表情
    "favorites": [],           # 常用宠物：[{"type":"image"|"live2d","path":..,"name":..}]
    "model_memory": {},        # 每个模型的记忆：{模型标识: {"pos":[x,y],"size":int,"edge_side":str,"edge_cross":int}}
    "chat_enabled": True,      # 聊天气泡：是否开启宠物自动说话功能
    "bubble_style": "cute",    # 气泡样式：simple | cute | pro | dark（之前未持久化，已修复）
    "companion_mode": False,   # 伴侣模式：开启后气泡只播放 520 条情侣语录（告白/情话/打情骂俏）
    "mesugaki_mode": False,    # 雌小鬼模式：开启后气泡只播放雌小鬼语气的角色扮演语录（与伴侣模式互斥）
    "nurture_mode": False,     # 养成模式：好感度系统+阶段化台词（伴侣模式的升级版，与上面两个互斥）
    "disabled_auto_motions": {},  # 禁止自动播放的动作：{模型规范路径: ["组名/索引", ...]}（手动触发不受限）
    "models_dir": "",          # Live2D 模型文件夹路径（自定义），为空时使用程序目录下的 live2d 文件夹
    "voice_enabled": True,     # Live2D 模型语音：开启后摸头/戳身体/换动作会播放模型自带的 voice/*.wav
    "voice_with_quote": False, # 气泡语录同步发声：宠物说话（弹气泡）时也随机放一条模型语音
    "voice_volume": 0.5,       # 模型语音音量 0.0~1.0（默认 50%，避免太吵）
    "chat_min_interval": 30,   # 气泡语录自动播放的最短间隔(秒)
    "chat_max_interval": 120,  # 气泡语录自动播放的最长间隔(秒)（也是"必播一次"的上限）
    "click_action_enabled": True,  # 点击宠物时是否触发内置窗口动作（关闭后点击不再"跳"）
    "click_action": "hop",     # 点击宠物触发的内置动作：hop/jump/nod/wiggle/tilt/lean/spin/dance
    "click_quote_enabled": True,  # 点击宠物时弹出语录（排除摸头和自动间隔）
    "tts_enabled": False,      # TTS 朗读气泡文字：开启后用 PySide6 QtTextToSpeech（Windows SAPI）把气泡文字读出来（无语音模型的兜底方案）
    "tts_volume": 0.7,         # TTS 音量 0.0~1.0（默认 70%，比模型语音稍小）
    "tts_rate": 0.0,           # TTS 语速：QtTextToSpeech 原生 -1.0~1.0（0=正常，旧版 wpm 会自动迁移）
    "tts_voice": "",           # TTS 嗓音名（空=自动挑选中文嗓音，如 Microsoft Huihui）
    "tts_engine": "auto",      # TTS 后端："auto"=系统语音(QtTextToSpeech) | "custom"=自定义命令/API
    "tts_custom_cmd": "",      # 自定义 TTS 命令模板（engine=custom 时用），支持 {text}/{out} 占位
    "holiday_greetings": True, # 节日/纪念日问候：春节/生日/自定义纪念日的专属问候与倒数
    "custom_holidays": [],     # 自定义纪念日：[{"name":"纪念日名称","date":"MM-DD","message":"问候语"}]
    "user_birthday": "",       # 用户生日（MM-DD 格式，如 "08-15"）
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
