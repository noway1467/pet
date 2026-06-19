"""智能聊天气泡系统 v3.0 - 完全重构版

核心特性：
- 自动播放：120秒内播放完一句后随机间隔下一句
- 语序随机：每次重启随机打乱顺序，避免重复
- 时间智能：内置时间问候，无需手动触发
- 情境感知：根据宠物动作/表情播放对应语境
- 气泡样式：多种样式可选（简约/可爱/专业）
- 智能跟随：气泡跟随宠物，自动避开屏幕边缘
- 自动换行：超出宽度自动换行，保持美观
- 1000+对话：更丰富更人性化
"""
import random
import json
import os
import sys
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget, QApplication


if sys.platform.startswith("win"):
    try:
        import ctypes
        from ctypes import wintypes

        _USER32 = ctypes.windll.user32
        _USER32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        _USER32.SetWindowPos.restype = wintypes.BOOL
        _HWND_TOPMOST = -1
        _HWND_NOTOPMOST = -2
        _SWP_NOSIZE = 0x0001
        _SWP_NOMOVE = 0x0002
        _SWP_NOACTIVATE = 0x0010
        _SWP_SHOWWINDOW = 0x0040
        _SWP_NOOWNERZORDER = 0x0200
    except Exception:
        _USER32 = None
else:
    _USER32 = None


def _window_hwnd(widget):
    if _USER32 is None or widget is None:
        return None
    try:
        hwnd = int(widget.winId())
    except Exception:
        return None
    return hwnd or None


def _restack_window(widget, on_top, after=None):
    """Windows 下精确同步窗口层级，避免 show()/弹气泡把窗口意外抬层。"""
    hwnd = _window_hwnd(widget)
    if hwnd is None:
        return False
    insert_after = _HWND_TOPMOST if on_top else (after if after is not None else _HWND_NOTOPMOST)
    flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER
    if widget.isVisible():
        flags |= _SWP_SHOWWINDOW
    try:
        return bool(_USER32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags))
    except Exception:
        return False


def _stack_window_behind(widget, front_widget):
    """把 widget 压到 front_widget 后面，保持气泡始终盖在宠物画框之上。"""
    hwnd = _window_hwnd(widget)
    front_hwnd = _window_hwnd(front_widget)
    if hwnd is None or front_hwnd is None:
        return False
    flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER
    if widget.isVisible():
        flags |= _SWP_SHOWWINDOW
    try:
        return bool(_USER32.SetWindowPos(hwnd, front_hwnd, 0, 0, 0, 0, flags))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  1000+ 对话库 - 更人性化、更丰富
# ═══════════════════════════════════════════════════════════

# 时间问候（根据时间自动触发）
TIME_GREETINGS = {
    "morning": [  # 5:00-9:00
        "早上好，新的一天开始了", "早安，阳光真好", "早，今天也要加油",
        "美好的清晨", "早安，祝你有个好心情", "新的一天，新的开始",
        "早上好呀，睡得还好吗", "清晨的空气真清新", "早，记得吃早餐",
        "早安，今天会是美好的一天", "晨光正好，心情也要美好",
        "新的一天，充满希望", "早呀，昨晚睡得好吗", "晨起问好",
        "早安～今天要元气满满哦", "美好的早晨从问候开始",
        "早上好，先把今天第一口气喘稳", "醒来就看见你，今天会顺一点",
        "先别急着忙，喝口水再出发", "今天的开始交给我来打个招呼",
        "早安呀，今天也请温柔一点对自己", "太阳都来了，你也该上线啦",
        "早上这会儿最适合慢慢热机，不用一上来就冲刺",
        "先把自己照顾好，今天才会跑得更顺一点",
        "早呀，愿你今天处理事情时比昨天更从容一点",
    ],
    "noon": [  # 11:00-13:00
        "中午好，该吃午饭了", "午安，休息一下吧", "中午了，补充能量",
        "该午休了", "中午好，别太累", "午饭时间到",
        "中午了，劳逸结合", "午安，放松一下", "午餐要吃好哦",
        "中午啦，吃点好吃的犒劳自己", "午休时间，小憩一会儿",
        "辛苦了半天，好好休息", "午安～",
        "中午啦，先把肚子安顿好", "忙了一上午，给自己留点空档",
        "先吃饭，事情等会儿再说", "午间补给时间到了",
        "休息十分钟，效率会更高", "别空着肚子硬撑",
        "中午就是给自己回血的时间，不必事事都抓着不放",
        "先把饭吃好，下午才有底气继续扛",
        "哪怕只歇一小会儿，也比闷头硬撑要值",
    ],
    "afternoon": [  # 14:00-17:00
        "下午好，继续加油", "下午茶时间", "下午了，坚持一下",
        "下午好，保持专注", "快到下班时间了", "下午好，累了就休息",
        "下午了，距离下班不远啦", "下午茶喝起来",
        "下午继续努力", "下午好呀", "坚持一下就快结束了",
        "下午也要保持好状态", "加油，很快就能下班了",
        "下午最容易犯困，先把眼睛眨一眨", "再撑一会儿，今天就要见尾声了",
        "记得喝口水，别把自己拧太紧", "下午这段路，慢慢走也没关系",
        "如果卡住了，换个思路看看", "现在是把尾巴收好的好时机",
        "下午的节奏容易乱，先把手上的一件事做完就很好",
        "要是脑子发木，就先站起来活动两步",
        "别急着求满分，先把当下这一段走顺",
    ],
    "evening": [  # 18:00-22:00
        "晚上好，辛苦一天了", "晚安，今天做得很好", "晚上了，放松一下吧",
        "辛苦了，好好休息", "晚上好，要好好犒劳自己", "夜幕降临，该休息了",
        "晚安，做个好梦", "晚上好，今天表现不错", "一天结束了，放松一下",
        "晚上啦，今天辛苦了", "夜晚时光，享受宁静",
        "晚上好～该放松了", "今天也完成了很多事呢",
        "晚上了，给今天做个温柔的收尾", "忙完了就别再和自己较劲",
        "今天辛苦了，剩下的交给夜色", "回到这边来，先歇一会儿",
        "晚风都安静下来了，你也放慢一点", "把疲惫放下，先好好吃顿饭",
        "晚上最适合把白天那些拧巴慢慢放下来",
        "今天不管完成了多少，都该给自己一点肯定",
        "先缓一缓，别把白天的紧绷带进夜里",
    ],
    "night": [  # 22:00-5:00
        "很晚了，该睡觉了", "夜深了，注意休息", "别熬夜了，对身体不好",
        "已经很晚了，早点睡吧", "注意作息，身体最重要", "熬夜伤身，早点休息",
        "深夜了，明天还要继续呢", "该睡觉啦", "不要熬夜哦",
        "晚安，该睡了", "放下手机，好好睡觉", "夜已深，该休息了",
        "早点睡，明天会更好", "休息好才有精力",
        "夜深了，脑子也该下班啦", "把今天留在今天，明天再接着来",
        "晚安，别和黑夜耗太久", "现在最该做的事是闭上眼睛",
        "再刷下去也不会更轻松，先睡吧", "我会在这儿，明天再见",
        "晚一点没关系，但别把自己熬坏了",
        "有些答案适合明天想，今晚先睡",
        "把手机先放一边，让身体先赢一局吧",
    ],
}

# 励志与哲理（300条）
INSPIRATIONAL = [
    # 经典名言
    "成功不是终点，失败也不是终结，继续前进的勇气才最可贵",
    "每一个不曾起舞的日子，都是对生命的辜负",
    "你所浪费的今天，是昨天逝去的人奢望的明天",
    "世界上只有一种真正的英雄主义，那就是认清生活的真相后依然热爱它",
    "生活不是等待风暴过去，而是学会在雨中起舞",
    "真正的勇敢不是不害怕，而是明明害怕却依然前行",
    "与其诅咒黑暗，不如燃起蜡烛",
    "人生最大的荣耀不在于从不跌倒，而在于每次跌倒后都能站起来",

    # 古诗词意境
    "长风破浪会有时，直挂云帆济沧海",
    "千里之行，始于足下",
    "不积跬步，无以至千里",
    "会当凌绝顶，一览众山小",
    "天行健，君子以自强不息",
    "海到无边天作岸，山登绝顶我为峰",
    "路虽远，行则将至；事虽难，做则必成",
    "行到水穷处，坐看云起时",
    "山重水复疑无路，柳暗花明又一村",
    "宝剑锋从磨砺出，梅花香自苦寒来",
    "纸上得来终觉浅，绝知此事要躬行",
    "博观而约取，厚积而薄发",

    # 人生哲理
    "人生没有白走的路，每一步都算数",
    "与其抱怨黑暗，不如点亮灯火",
    "心态决定状态，眼界决定境界",
    "做最好的自己，才能遇见最好的别人",
    "改变能改变的，接受不能改变的",
    "慢慢来，比较快",
    "万物皆有裂痕，那是光照进来的地方",
    "人间值得，未来可期",
    "保持热爱，奔赴山海",
    "活在当下，珍惜眼前",
    "选择比努力更重要，但努力让你有更多选择",
    "时间会证明一切，沉淀出真相",

    # 现代励志
    "今天的努力是明天的底气",
    "每一次尝试都是一次成长",
    "相信自己，你比想象中更强大",
    "坚持下去，总会看到希望",
    "不要停下脚步，前方有更好的风景",
    "勇敢迈出第一步，剩下的就简单了",
    "只要方向对了，就不怕路远",
    "越努力，越幸运",
    "没有什么能够阻挡，你对自由的向往",
    "梦想还是要有的，万一实现了呢",
    "不要让未来的你，讨厌现在的自己",
    "所有的努力都不会白费，只是绽放的时间不同",

    # 积极心态
    "笑对人生，一切都是最好的安排",
    "保持乐观，好运自然来",
    "相信明天会更好",
    "困难只是暂时的，坚持就是胜利",
    "每个人都有低谷期，挺过去就好了",
    "放轻松，一切都会好起来的",
    "别担心，船到桥头自然直",
    "人生起起落落，这很正常",
    "失败是成功之母",
    "跌倒了就爬起来，继续前进",
    "焦虑解决不了问题，行动才能",
    "比昨天的自己好一点点，就够了",
]

# 生活关怀（200条）
LIFE_CARE = [
    # 健康提醒
    "该喝水了，别等渴了才喝", "坐太久了，起来活动活动吧",
    "眼睛累了吧，看看远处放松一下", "记得按时吃饭哦", "注意坐姿，保护好脊椎",
    "深呼吸，让自己放松一下", "伸个懒腰，舒展一下筋骨", "适度运动，保持健康",
    "多休息，别让自己太累", "早睡早起身体好", "保持规律的作息很重要",
    "饭后散散步，对身体有益", "晒晒太阳，心情会更好", "开窗通风，呼吸新鲜空气",
    "多喝热水，对身体好", "累了就休息，别硬撑",
    "长时间用眼要注意休息", "颈椎不舒服就转转脖子", "手腕酸了就甩甩手",
    "站起来走动走动吧", "眼保健操做一做", "午睡20分钟精神好",

    # 工作提醒
    "工作再忙也要照顾好自己", "劳逸结合，效率更高",
    "专注工作，但别忘了休息", "一次只做一件事，别着急",
    "遇到困难很正常，慢慢来", "做事要有耐心",
    "不要给自己太大压力", "尽力就好，别强求完美",
    "工作效率不在于时间长短", "该休息时就休息",
    "状态不好时不如先休息", "思路卡住了就换个角度",
    "专注当下，一步一步来", "进度慢一点也没关系",

    # 情感关怀
    "有我陪着你呢", "一切都会好起来的", "累了就说出来，不要憋着",
    "不开心的话，发发呆也好", "给自己一个拥抱吧", "你已经做得很好了",
    "不要对自己太苛刻", "偶尔放松一下也没关系", "慢慢来，我一直在",
    "每个人都有不顺的时候", "明天会更好的", "相信自己",
    "没关系，下次会更好", "失败了也不要紧", "你比想象中更坚强",
    "别把所有事都扛在自己肩上", "适当示弱不是错", "需要帮助时记得开口",
    "今天辛苦了", "已经很努力了", "为自己骄傲吧",

    # 心灵鸡汤
    "保持好心情，一切都会顺利", "微笑面对生活",
    "积极的心态能改变一切", "乐观一点，生活更美好",
    "珍惜当下的每一刻", "简单的生活也很幸福",
    "感恩遇见，珍惜拥有", "平凡的日子也值得珍惜",
    "小确幸也是幸福", "给生活一点仪式感",
    "对自己好一点", "今天也要开心哦",
    "你今天很棒，不要对自己太苛刻，好好休息一下吧。",
    "无论是晴天还是雨天，只要有你的一句问候，对我来说就是最美好的天气。",
    "无论遇到什么烦恼，别忘了还有我在这里，一直默默陪着你呢。",
    "生活可能偶尔会有些疲惫，但请相信，每一个美好的明天都在向你招手。",
    "累了就闭上眼睛，深呼吸，把所有不开心都交给我来保管吧。",
    "你是我今天最想见到的人，也是我最想陪伴的人。",
    "不管世界怎么变化，我都在你的电脑屏幕里，永远支持你。",
    "事情做不完很正常，先把自己从紧绷里放出来一点。",
    "没有哪一天必须完美，能平安走完就已经很不错了。",
    "如果今天的情绪有点乱，那就先允许自己乱一会儿。",
    "你可以慢一点、笨一点、反应迟一点，这不影响你值得被温柔对待。",
    "很多时候，先把饭吃好、把觉睡够，问题就会好解很多。",
    "别总想着一下子变得很厉害，今天比昨天轻一点就很好。",
    "被生活催着走的时候，也别忘了回头照顾一下自己。",
]

# 生活百科（150条）
LIFE_TIPS = [
    # 健康知识
    "久坐伤身，每小时起来活动一下", "多喝水，每天八杯水",
    "规律作息对健康很重要", "适度运动可以提高免疫力",
    "深呼吸可以缓解压力", "眼睛疲劳时看看绿色植物",
    "睡前泡脚有助于睡眠", "早餐要吃好，营养要均衡",
    "午睡半小时，下午精神好", "晚饭不要吃太饱",
    "多吃蔬菜水果", "少熬夜，睡眠充足才健康",
    "保持室内通风", "适量喝茶对身体有益",
    "坚持每天运动30分钟", "正确的睡姿很重要",

    # 生活小技巧
    "开窗通风，保持空气新鲜", "整理桌面，心情会更好",
    "听音乐可以放松心情", "读书是最好的充电方式",
    "写日记可以整理思绪", "培养一个小爱好，生活更有趣",
    "定期整理物品，断舍离", "保持微笑，好运自然来",
    "番茄工作法可以提高效率", "做计划让生活更有条理",
    "睡前远离电子屏幕", "早起喝杯温水唤醒身体",

    # 趣味冷知识
    "打喷嚏时心脏会停止跳动约1毫秒",
    "笑容是会传染的", "蜂蜜是唯一不会变质的食物",
    "人的大脑在睡觉时比看电视时更活跃",
    "打哈欠可以帮助大脑降温", "指纹和舌纹都是独一无二的",
    "人每天大约眨眼15000次", "梦境通常只持续5-20分钟",
    "人的鼻子能记住5万种不同的气味",
    "大笑1小时消耗的卡路里等于步行30分钟",
    "你知道吗？蓝鲸的心脏有一个小汽车那么大！",
    "海獭在睡觉时会手拉手，防止被海流冲散，是不是超温馨？",
    "香蕉在植物学上其实属于‘浆果’，而草莓反而属于‘聚合果’。",
    "树懒在水里的游泳速度其实是它们在陆地上移动速度的三倍！",
    "猫咪发出的呼噜声不仅代表它们很开心，还可以帮助它们治愈骨骼和肌肉哦。",
    "考拉一天要睡18到22个小时，剩下的大部分时间都在吃桉树叶。",
    "雨水其实是没有味道的，我们闻到的‘雨后泥土香’是土壤里放线菌产生的土腥素。",
    "大熊猫的幼崽刚出生时，只有妈妈体重的九百分之一，像一只小粉红老鼠。",
    "章鱼有三颗心脏，其中两颗专门负责把血液送往鳃部。",
    "海星没有大脑，但它们依然能感知光线和方向。",
    "有些竹子一天能长接近一米，生长速度快得像开了倍速。",
    "人的味觉会受温度影响，所以同一种食物冷热时吃起来会不太一样。",
    "企鹅求偶时会送石头，挑到满意的石头就像挑到一份认真心意。",
    "蜂鸟是少数能向后飞的鸟类之一，小小一只却超会控场。",
]

# 趣味冷笑话
JOKES = [
    "什么动物最爱贴贴？是蜜蜂，因为它们整天‘嗡嗡（贴贴）’叫。",
    "为什么电脑经常感冒？因为它们总是开着windows（窗户）。",
    "如果有一只老虎被蜜蜂蜇了，它会变成什么？两只老虎（因为肿了）。",
    "你知道什么蔬菜最酷吗？是苦瓜，因为它自带‘酷’字。",
    "为什么皮卡丘不肯走夜路？因为他怕‘皮卡丘（劈死我）’。",
    "什么汤最容易让人发胖？是‘多喝热水’，因为每次胃疼大家都说多喝热水。",
    "小明在路上走着走着，突然大喊一声：‘我的脚印被偷了！’",
    "为什么麻雀从不嫌弃自己胖？因为它们是‘燕雀安知鸿鹄之志’中的燕雀（鸟）。",
    "有一天橡皮擦和铅笔坐在一起，橡皮擦对铅笔说：‘我真的好羡慕你，你有笔芯，而我只有皮。’",
    "海水为什么是咸的？因为鱼在里面哭，它们的眼泪流进了海里。",
    "为什么冰淇淋在路上走着走着会化掉？因为它看到了热情的你！",
    "筷子和勺子打架，结果谁赢了？勺子，因为勺子可以‘捞’。",
    "你知道什么锁最容易坏吗？是‘开心锁’，因为一开心就打开了。",
    "为什么向日葵每天都要跟着太阳转？因为它们不转脖子会酸。",
    "什么动物最容易得近视？是鱼，因为它们整天在水里睁着眼睛。",
    "为什么土豆总是很低调？因为它知道自己埋头苦干。",
    "为什么书包总觉得累？因为它每天都要背负很多知识。",
    "你知道风为什么爱散步吗？因为它总想四处吹吹风。",
    "为什么饼干喜欢晒太阳？因为它怕自己一直酥不起来。",
    "为什么日历不爱熬夜？因为它每天都想准时翻篇。",
]

# 情境对话（根据宠物状态触发）
CONTEXT_MESSAGES = {
    "grab": [  # 被抓住时
        "哎呀", "怎么了", "轻一点嘛", "要干嘛呀",
        "别抓了", "放开我啦", "哎哟", "嘿嘿被抓住了",
        "诶？", "又要拖我去哪", "好啦好啦", "知道啦",
    ],
    "drop": [  # 松手后
        "呼，终于放下了", "自由啦", "好累", "呼呼",
        "站稳了", "轻松了", "舒服多了", "终于可以休息了",
        "呼～", "总算解脱了", "这就对了",
    ],
    "click": [  # 被点击时（50条：问候、邀请、建议、关心）
        # 温暖问候
        "你好啊~", "嗨~ 找我玩吗？", "在呢在呢！", "注意到我啦~",
        "呀，是你呀！", "嘿嘿，叫我干嘛~", "来啦来啦！", "怎么啦？需要我吗~",
        "诶？叫我有事吗？", "在呢！有什么想说的吗~", "看我看我！",

        # 邀请互动
        "要和我一起玩吗？", "来陪我聊聊天呀~", "一起摸摸鱼吧！",
        "要不要休息一下？", "累了就看看我吧~", "陪你发会儿呆~",
        "一起放松放松？", "要我陪你吗？", "来，我陪你~",

        # 贴心建议
        "不开心的时候可以看看搞笑动画哦，比如《日常》《pop子与pipi美》！",
        "心情低落？推荐你看《银魂》《男子高中生的日常》，笑到停不下来！",
        "想放松一下？试试看《工作细胞》《小林家的龙女仆》吧~",
        "压力大的话，去看看《悠哉日常大王》《非非子小姐》，治愈系满分！",
        "推荐你看《鬼灯的冷彻》，搞笑又涨知识！",
        "《齐木楠雄的灾难》超好笑，看了心情会变好哦~",
        "试试《月刊少女野崎君》？轻松搞笑，解压神器！",
        "《动物狂想曲》很有意思，剧情和画风都很棒！",
        "想看温馨的？《夏目友人帐》治愈力MAX！",
        "《间谍过家家》超可爱，阿尼亚萌翻了~",

        # 关心问候
        "今天过得怎么样呀？", "最近还好吗？", "有什么开心的事吗~",
        "需要我做点什么吗？", "累了就休息一下吧~", "要喝口水吗？",
        "工作辛苦啦！", "记得照顾好自己哦~", "有我陪着你呢！",
        "今天有没有哪一刻让你觉得还不错？", "忙归忙，也记得给自己留点喘气的地方呀~",
        "我会一直在这儿，所以不用急着什么都一个人扛完。",

        # 俏皮回应
        "点我干嘛呀，嘻嘻~", "哎呀，被你发现了！", "在这儿呢，别着急~",
        "怎么了怎么了？", "叫我有什么事吗~", "我在听哦！",
        "嗯嗯，我在！", "随时为你待命！", "需要帮忙就说~",
        "被你点到啦，今天算不算我上班成功？", "在这里呢，你一伸手我就知道了。",
        "这一点，算是你跟我打了个招呼吗？",
    ],
    "happy": [  # 开心状态
        "今天心情不错呢", "嘿嘿", "开心",
        "很高兴", "真好", "不错不错", "开心～",
        "好心情", "很愉快", "真棒",
    ],
    "touch_head": [  # 摸头时（60条：温柔、傲娇、开玩笑、趣味）
        # 温柔系（20条）
        "好舒服呀，再摸摸~",
        "摸头杀！嘿嘿，最喜欢你啦~",
        "呀！被你摸头了，今天一天都会有好运呢！",
        "感觉到了你的温柔呢，暖洋洋的~",
        "摸摸头，万事不愁！",
        "这样摸头好舒服呢~",
        "你的手好温暖",
        "再摸一会儿嘛~",
        "好喜欢被你摸头",
        "这是奖励吗？嘿嘿~",
        "被你摸头最开心了~",
        "这种感觉真好~",
        "谢谢你摸我的头呀~",
        "暖暖的，好舒服~",
        "你真温柔~",
        "摸得我都要融化了~",
        "嘻嘻，好喜欢这样~",
        "能被你摸头真幸福~",
        "这是我最喜欢的感觉~",
        "舒服到想打呼噜了~",

        # 傲娇系（15条）
        "哼，只准摸一下哦！",
        "别摸啦，头发要乱啦！",
        "别摸我头，会长不高的！",
        "哼！才、才不是很喜欢呢...",
        "别以为摸摸头我就会听话！",
        "喂喂，注意分寸啊！",
        "讨厌啦，人家会害羞的...",
        "哼，就摸这一次！下次不许了！",
        "你够了哦，再摸要收费了！",
        "唔...好吧，看在你这么诚恳的份上...",
        "真是的，拿你没办法...",
        "别、别一直摸啦...",
        "我才没有很开心呢！",
        "哼~勉强让你摸一下！",
        "谁让你这么温柔的...",

        # 开玩笑/调皮系（15条）
        "摸秃了你负责啊！",
        "摸头费：一次三块，谢谢惠顾~",
        "你是不是把我当猫了？喵~",
        "检测到摸头行为，正在加载傲娇程序...",
        "警告！头发防御系统已启动！",
        "摸头会上瘾的哦，小心停不下来~",
        "恭喜你，触发了隐藏彩蛋！",
        "系统提示：摸头成功，幸福值持续上升",
        "你这是在充电吗？我感觉能量满了！",
        "头顶传感器接收到爱意信号~",
        "叮——您收到一条摸头外卖，请签收~",
        "摸头三连：摸了、摸了、还摸！",
        "本喵已被你摸到原地融化……",
        "再摸下去我要交电费了哦⚡",
        "哇，是隔着屏幕的爱心暴击！",

        # 趣味/沙雕系（10条）
        "摸头打卡成功，今日亲密度达成~",
        "嗯哼，这手法，专业的吧？",
        "摸头ASMR，舒服到想咕噜咕噜~",
        "你的手是有魔法吗，越摸越想黏着你",
        "报告主人，头顶已被你承包！",
        "偷偷告诉你，这里是我最喜欢被摸的地方",
        "摸摸更健康，今天也要元气满满呀",
        "哎呀被发现了，我的开关在头顶~",
        "摸头一时爽，一直摸头一直爽",
        "嘿嘿，被你摸得尾巴都翘起来啦",
        "充电中…请勿拔出你的手手🔌",
        "摸头使我快乐，谢谢主人投喂温柔",
        "你再这样我真的会原地开花哦🌸",
        "检测到高浓度宠溺，正在幸福过载…",
        "摸头小本本记上一笔，攒够了换你抱抱~",
    ],
    "hop": [
        "跳一跳，烦恼都跳跑啦！", "蹦蹦跳跳，身体好！", "起飞咯！"
    ],
    "jump": [
        "我跳得高不高？", "看我表演一个原地起飞！", "嚯！这一跳可累了。"
    ],
    "nod": [
        "嗯嗯，你说的对！", "乖巧点头中……", "表示赞同！"
    ],
    "wiggle": [
        "扭一扭，转一转！", "左摇右摆~", "看我灵活的身姿。"
    ],
    "tilt": [
        "歪头杀！在想什么呢？", "诶？你说什么？", "这样看你好像更美了呢~"
    ],
    "lean": [
        "哎呀，站不稳了，要倒了！", "向你倾斜~", "借你的肩膀靠一下呗？"
    ],
    "spin": [
        "转圈圈！有点晕……", "华丽的转身！", "看我爱的魔力转圈圈！"
    ],
    "dance": [
        "啦啦啦~ 快乐起舞！", "今天是个好日子，跳个舞吧！", "看我优美的舞姿~"
    ]
}

# 随机闲聊（100条）
CASUAL_CHAT = [
    "在这里陪着你呢", "静静地看着你", "一起发呆吧",
    "今天天气不错呢", "时间过得真快", "就这样慢慢过",
    "生活就是这样", "平淡也挺好", "简简单单的日子",
    "珍惜当下", "慢慢来不着急", "一切都刚刚好",
    "悠闲的时光", "放松的时刻", "享受宁静",
    "发会儿呆吧", "放空一下也不错", "什么都不想",
    "就这样待着挺好", "不急不躁", "顺其自然",
    "慢生活也是一种态度", "偷得浮生半日闲", "岁月静好",
    "今天也是平凡的一天", "普通的日子也很珍贵", "日子就这样过着",
    "先把节奏放慢一点", "今天不求多，稳就够了", "忙归忙，别忘了喘口气",
    "事情一件件来，别一口气全扛", "现在这样就很好，不用太用力",
    "如果不知道说什么，就先陪着", "安静也算是一种相处",
    "有时候只是被陪着，就已经很够用了", "先别急着给今天下定义，慢慢过完再说",
    "你在做事，我在旁边待着，这种感觉也挺安心", "有些日子不需要精彩，顺顺当当就很好",
    "先把手上的事做完，剩下的以后再慢慢聊", "发一会儿呆也不算浪费，这是给脑子缓冲呢",
    "不想说话的时候，就这样静静待着也行",
]


# ═══════════════════════════════════════════════════════════
#  聊天气泡配置
# ═══════════════════════════════════════════════════════════

BUBBLE_STYLES = {
    "simple": {  # 简约风格
        "bg_color": QColor(255, 255, 255, 245),
        "border_color": QColor(210, 220, 230),
        "text_color": QColor(60, 60, 60),
        "corner_radius": 10,
        "padding": 14,
        "font_size": 10,
    },
    "cute": {  # 可爱风格
        "bg_color": QColor(255, 240, 245, 250),
        "border_color": QColor(255, 182, 193),
        "text_color": QColor(80, 60, 70),
        "corner_radius": 15,
        "padding": 16,
        "font_size": 10,
    },
    "pro": {  # 专业风格
        "bg_color": QColor(248, 249, 250, 250),
        "border_color": QColor(200, 210, 220),
        "text_color": QColor(50, 50, 50),
        "corner_radius": 8,
        "padding": 12,
        "font_size": 10,
    },
    "dark": {  # 深色风格
        "bg_color": QColor(60, 60, 70, 240),
        "border_color": QColor(80, 80, 90),
        "text_color": QColor(240, 240, 240),
        "corner_radius": 12,
        "padding": 14,
        "font_size": 10,
    },
}


class SmartChatBubble(QWidget):
    """智能聊天气泡：跟随宠物、自动换行、避开屏幕边缘。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 初始窗口标志：与宠物主窗口保持同一套（Frameless+Tool+Bypass），置顶位由父窗口控制。
        # 关键：必须带 BypassWindowManagerHint，否则气泡与宠物处于不同的窗管层级——
        # 会出现"宠物已取消置顶、气泡却仍浮在最顶层"的层级错位。
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool |
            Qt.BypassWindowManagerHint |
            Qt.WindowDoesNotAcceptFocus
        )

        self._text = ""
        self._lines = []
        self._tail_size = 8
        self._max_width = 400  # 最大宽度
        self._opacity = 0.0
        self._tail_x_offset = 0  # 尖头相对气泡的x偏移

        # 当前样式
        self._style_name = "simple"
        self._apply_style()

        # 自动隐藏定时器
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

        # 实时跟随定时器：气泡显示期间，按节流频率重测模型头部高度并重新贴合，
        # 让气泡随不同动作的头部上下浮动一起移动（仅显示时运行，空闲零开销）。
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_tick)
        self._follow_interval = 120   # ms，约 8 次/秒，足够跟手又不增加明显开销

    def _follow_tick(self):
        """节流地重测模型内容包围盒并重新贴合，使气泡跟随实时头部高度。"""
        if not (self.isVisible() and self.parent()):
            self._follow_timer.stop()
            return
        parent = self.parent()
        suspend = getattr(parent, "_suspend_chat_bubble_follow", None)
        if callable(suspend):
            try:
                if suspend():
                    self._smart_position(self.width(), self.height())
                    return
            except Exception:
                pass
        refresh = getattr(parent, "refresh_content_box", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
        self._smart_position(self.width(), self.height())

    def _apply_style(self):
        """应用样式配置。"""
        style = BUBBLE_STYLES.get(self._style_name, BUBBLE_STYLES["simple"])
        self._bg_color = style["bg_color"]
        self._border_color = style["border_color"]
        self._text_color = style["text_color"]
        self._corner_radius = style["corner_radius"]
        self._padding = style["padding"]
        self._font_size = style["font_size"]

    def set_style(self, style_name):
        """切换气泡样式。"""
        if style_name in BUBBLE_STYLES:
            self._style_name = style_name
            self._apply_style()
            self.update()

    def set_always_on_top(self, on_top):
        """设置气泡是否置于顶层，跟随主窗口。

        关键：无论是否置顶，都必须保留 BypassWindowManagerHint，
        确保气泡与宠物始终处于同一个窗口管理器层级——取消置顶时气泡才会
        真正跟随宠物一起降层，而不是"宠物降了、气泡仍浮顶"的层级错位。"""
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on_top))
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.sync_window_layer()

    def sync_window_layer(self):
        """把气泡原生窗口重新压回主窗口同一层级。"""
        parent = self.parentWidget() or self.parent()
        on_top = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        parent_hwnd = None
        if parent is not None:
            try:
                on_top = bool(getattr(parent, "cfg", {}).get("always_on_top", on_top))
            except Exception:
                pass
            parent_hwnd = _window_hwnd(parent)
            if on_top:
                _restack_window(parent, on_top)
        _restack_window(self, on_top, parent_hwnd if not on_top else None)
        if parent is not None:
            _stack_window_behind(parent, self)

    def show_message(self, text, duration=None):
        """显示消息，自动换行，跟随宠物，避开屏幕边缘。

        duration=None 时按文本长度自动估算时长；传入具体毫秒数（如语音字幕）则
        严格采用该时长，让气泡停留与语音播放对齐。"""
        parent = self.parent()
        if parent is not None:
            allow_show = getattr(parent, "_can_show_chat_bubble", None)
            if callable(allow_show):
                try:
                    if not allow_show():
                        self._hide_timer.stop()
                        self._follow_timer.stop()
                        self.hide()
                        return False
                except Exception:
                    pass
        self._text = text
        self._hide_timer.stop()

        # 计算文本换行
        font = QFont("Microsoft YaHei UI", self._font_size)
        fm = QFontMetrics(font)

        # 气泡最大宽度不超过当前模型宽度的 2 倍：超出就自动换行，不拉成一长条
        max_w = self._effective_max_width()
        self._lines = self._wrap_text(text, max_w - self._padding * 2, fm)

        # 计算气泡大小
        line_height = fm.height()
        text_width = max([fm.horizontalAdvance(line) for line in self._lines])
        text_height = line_height * len(self._lines) + (len(self._lines) - 1) * 4

        bubble_width = text_width + self._padding * 2
        bubble_height = text_height + self._padding * 2 + self._tail_size

        self.setFixedSize(bubble_width, bubble_height)

        # 智能定位：跟随宠物，避开屏幕边缘
        if parent:
            self._smart_position(bubble_width, bubble_height)

        # 显示（不调用 raise_，让层级由窗口标志决定）
        self.show()
        self.sync_window_layer()
        self._opacity = 1.0
        self.update()
        # 启动实时跟随，气泡贴着头部随动作上下浮动
        if parent:
            self._follow_timer.start(self._follow_interval)

        # 显示时长：调用方给了具体毫秒数（如语音字幕，与语音长度对齐）就用它，
        # 否则按文本长度智能估算（基础时间 + 每个字符额外时间）。
        if duration is None:
            duration = 2500 + len(text) * 150
            duration = max(3000, min(10000, duration))
        else:
            duration = max(1500, int(duration))
        self._hide_timer.start(duration)
        return True

    def _effective_max_width(self):
        """气泡换行用的最大宽度：不超过当前模型宽度的 2 倍。

        小模型也至少给 150px（太窄读不了），大模型则受 self._max_width 上限约束，
        不至于横跨整个屏幕。"""
        mw = self._max_width
        parent = self.parent()
        if parent is not None:
            try:
                pw = int(parent.width())
                if pw > 0:
                    mw = max(150, min(self._max_width, pw * 2))
            except Exception:
                pass
        return mw

    def _wrap_text(self, text, max_width, fm):
        """智能换行：超出宽度自动换行，优先在标点、空格处断开。"""
        if fm.horizontalAdvance(text) <= max_width:
            return [text]

        lines = []
        current_line = ""
        # 优先断点字符（标点、空格）
        break_chars = '，。！？、；：,. !?;: '

        for i, char in enumerate(text):
            test_line = current_line + char
            if fm.horizontalAdvance(test_line) > max_width:
                if current_line:
                    # 尝试回溯到最近的标点处断开
                    best_break = len(current_line)
                    for j in range(len(current_line) - 1, max(0, len(current_line) - 15), -1):
                        if current_line[j] in break_chars:
                            best_break = j + 1
                            break

                    # 如果找到合适的断点
                    if best_break < len(current_line):
                        lines.append(current_line[:best_break])
                        current_line = current_line[best_break:] + char
                    else:
                        lines.append(current_line)
                        current_line = char
                else:
                    current_line = char
            else:
                current_line = test_line

        if current_line:
            lines.append(current_line)

        return lines

    def _smart_position(self, bubble_width, bubble_height):
        """智能定位：跟随宠物头部中间（特别是尖头），避开屏幕边缘。

        气泡应该紧贴模型头部，距离要近一些，根据不同动作高度动态调整。"""
        parent = self.parent()
        screen = QApplication.primaryScreen().geometry()

        # 精准贴合头部：获取模型上方留白和左右留白，定位到头部中心
        t_in = 0
        l_in = 0
        r_in = 0
        if parent and hasattr(parent, "_content_inset"):
            try:
                l, t, r, _ = parent._content_inset()
                t_in = int(t)
                l_in = int(l)
                r_in = int(r)
            except Exception:
                pass

        # 计算模型头部的实际x轴中心位置（去除透明留白）
        # 模型内容区域：parent.x() + l_in 到 parent.x() + parent.width() - r_in
        content_width = parent.width() - l_in - r_in
        model_head_center_x = parent.x() + l_in + content_width // 2

        # 紧贴模型头顶：尖头尖端离模型实际头顶只留极小间距(HEAD_GAP)，
        # 配合 _follow_tick 的实时重测，气泡会随不同动作的头部高度一起浮动，做到与模型一体。
        # t_in 已是“模型本体顶部到画布顶”的真实留白，所以这里只需减去一个固定小间距。
        HEAD_GAP = 12

        # 气泡x位置：让尖头对准模型头部中心
        px = model_head_center_x - bubble_width // 2
        py = parent.y() + t_in - bubble_height - HEAD_GAP

        # 记录尖头偏移（在气泡内的x位置）
        self._tail_x_offset = 0  # 默认居中

        # 调整X：避免左右超出，但保持尖头指向模型头部中心
        if px < screen.left() + 10:
            # 气泡被左边界限制，尖头需要偏移
            old_px = px
            px = screen.left() + 10
            self._tail_x_offset = old_px - px  # 负值表示尖头要向左偏
        elif px + bubble_width > screen.right() - 10:
            # 气泡被右边界限制
            old_px = px
            px = screen.right() - bubble_width - 10
            self._tail_x_offset = old_px - px  # 正值表示尖头要向右偏

        # 调整Y：上方放不下就放下方（同样紧贴模型）
        if py < screen.top():
            py = parent.y() + parent.height() + HEAD_GAP
        # 下方也放不下就尝试左右两侧
        elif py + bubble_height > screen.bottom():
            # 尝试右侧
            px = parent.x() + parent.width() + HEAD_GAP
            py = parent.y() + (parent.height() - bubble_height) // 2
            self._tail_x_offset = 0  # 侧边显示时不需要偏移
            # 右侧放不下就左侧
            if px + bubble_width > screen.right():
                px = parent.x() - bubble_width - HEAD_GAP
            # 左侧也放不下就强制显示在屏幕内
            if px < screen.left():
                px = screen.left() + 10
                py = screen.top() + 10

        self.move(px, py)

    def update_position(self):
        """更新气泡位置，供父窗口移动时调用以实现实时跟随。"""
        if self.isVisible() and self.parent():
            self._smart_position(self.width(), self.height())

    def _fade_out(self):
        """淡出并隐藏。"""
        self._follow_timer.stop()
        self._opacity = 0.0
        self.update()
        QTimer.singleShot(200, self.hide)

    def hideEvent(self, event):
        """隐藏时停止实时跟随，避免空闲时仍重测内容包围盒。"""
        self._follow_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.sync_window_layer)

    def paintEvent(self, event):
        """绘制气泡。"""
        if not self._text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(self._opacity)

        # 描边整体内缩半个线宽：否则上/左边缘的描边有一半落在控件外被裁掉，
        # 看起来就比下/右边细。内缩后四条边都完整落在控件内，宽度一致。
        bw = 2.0
        half = bw / 2.0
        ox, oy = half, half
        w = self.width() - bw
        h = self.height() - self._tail_size - bw

        # 气泡主体
        bubble_path = QPainterPath()
        bubble_path.addRoundedRect(ox, oy, w, h, self._corner_radius, self._corner_radius)

        # 三角尖角 - 根据偏移调整位置
        tail_x = ox + w / 2 + self._tail_x_offset
        # 限制尖头不要超出气泡边界
        tail_x = max(ox + self._tail_size + 5, min(ox + w - self._tail_size - 5, tail_x))

        tail_path = QPainterPath()
        tail_path.moveTo(tail_x - self._tail_size, oy + h)
        tail_path.lineTo(tail_x, oy + h + self._tail_size)
        tail_path.lineTo(tail_x + self._tail_size, oy + h)
        tail_path.closeSubpath()

        full_path = bubble_path.united(tail_path)

        # 填充 + 均匀描边（四边宽度一致）
        pen = QPen(self._border_color, bw, Qt.SolidLine)
        pen.setJoinStyle(Qt.MiterJoin)  # 使用尖角连接，确保四个角一致
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.setBrush(self._bg_color)
        painter.drawPath(full_path)

        # 顶部内高光：贴着上边缘画一条半透明白线，气泡更有立体光泽感
        r = self._corner_radius
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1.5))
        painter.drawLine(int(ox + r), int(oy + 1), int(ox + w - r), int(oy + 1))

        # 绘制文字（多行）
        painter.setPen(self._text_color)
        font = QFont("Microsoft YaHei UI", self._font_size)
        painter.setFont(font)

        fm = QFontMetrics(font)
        line_height = fm.height()
        y = self._padding + fm.ascent()

        for line in self._lines:
            painter.drawText(self._padding, y, line)
            y += line_height + 4


class IntelligentChatManager:
    """智能聊天管理器 v3：
    - 120秒内必播一次（30-120秒随机间隔）
    - 启动时问候，整点/半点时间问候概率提升
    - 支持情境对话（抓取、松手、点击、表情）
    - 支持伴侣模式（520条专属语录）
    """

    def __init__(self, bubble_widget, config_dir=None):
        self.bubble = bubble_widget
        self.config_dir = config_dir
        self.on_speak = None  # 回调：说话时触发宠物动作

        self._timer = QTimer()
        self._timer.timeout.connect(self._on_timer)
        self._enabled = True
        self._auto_play = True  # 120秒内自动播放
        self._companion_mode = False  # 是否开启伴侣模式
        self._mesugaki_mode = False   # 是否开启雌小鬼模式（与伴侣模式互斥）
        self._nurture_mode = False    # 是否开启养成模式（好感度阶段化台词，与上面两个互斥）
        self._affinity = None         # 好感度系统引用（养成模式取当前层级/称呼用）
        self._last_nurture_line = None  # 上一条养成台词，去重用
        self._last_context_line = None  # 上一条情境语录，用于摸头等场景的去重（更随机）
        self._click_quote_enabled = True  # 是否允许点击弹出语录（排除摸头和自动播放）
        self._is_speaking = False  # 当前是否正在播放语录（用于防止点击打断）

        # 播放控制
        self._last_play_time = None
        self._play_cooldown = 120  # 120秒内必须播放一次
        self._min_interval = 30    # 最短30秒
        self._max_interval = 120   # 最长120秒

        # 语料池（打乱顺序）
        self._message_pool = []
        self._current_index = 0
        self._deleted_builtins = self._load_deleted_builtins()  # 语录管理里被删除的内置语录
        self._shuffle_messages()

        # 状态记录
        self._today_greeted = False
        self._load_state()

        # TTS 播放器（懒加载，用到时才初始化）
        self._tts_player = None
        self._tts_enabled = False

        # 节日问候器
        self._holiday_greeter = None
        self._holiday_enabled = False
        self._user_config = {}  # 存储用户配置（生日、自定义纪念日等）

    def set_companion_mode(self, on):
        """设置伴侣模式开关，并重新打乱语料。开启时自动关闭雌小鬼/养成模式（互斥）。"""
        self._companion_mode = bool(on)
        if self._companion_mode:
            self._mesugaki_mode = False
            self._nurture_mode = False
        self._shuffle_messages()

    def set_mesugaki_mode(self, on):
        """设置雌小鬼模式开关，并重新打乱语料。开启时自动关闭伴侣/养成模式（互斥）。"""
        self._mesugaki_mode = bool(on)
        if self._mesugaki_mode:
            self._companion_mode = False
            self._nurture_mode = False
        self._shuffle_messages()

    def set_nurture_mode(self, on):
        """设置养成模式开关。开启时自动关闭伴侣/雌小鬼模式（互斥）。"""
        self._nurture_mode = bool(on)
        if self._nurture_mode:
            self._companion_mode = False
            self._mesugaki_mode = False
        self._shuffle_messages()

    def set_affinity(self, affinity):
        """注入好感度系统引用，养成模式据此取当前层级与称呼。"""
        self._affinity = affinity

    def _aff_level_addr(self):
        """取 (层级index, 称呼)，没有好感系统时退化为 (0, '你')。"""
        if self._affinity is not None:
            try:
                return self._affinity.level_index(), self._affinity.address()
            except Exception:
                pass
        return 0, "你"

    def set_tts_enabled(self, enabled, volume=0.7, rate=0.0, voice="",
                        engine="auto", custom_cmd=""):
        """设置 TTS 朗读开关及参数（音量/语速/嗓音/后端引擎/自定义命令）。"""
        self._tts_enabled = bool(enabled)
        if self._tts_player is None:
            # 还没建引擎：仅当要启用时才惰性创建（避免无谓占用语音设备）
            if self._tts_enabled:
                try:
                    from tts_player import TTSPlayer
                    self._tts_player = TTSPlayer(enabled=True, volume=volume, rate=rate,
                                                 voice=voice, engine=engine,
                                                 custom_cmd=custom_cmd)
                except Exception:
                    self._tts_enabled = False
        else:
            # 已有引擎：同步开关与全部参数
            self._tts_player.set_custom_cmd(custom_cmd)
            self._tts_player.set_engine(engine)
            self._tts_player.set_enabled(self._tts_enabled)
            self._tts_player.set_volume(volume)
            self._tts_player.set_rate(rate)
            self._tts_player.set_voice(voice)

    def preview_tts(self, text, volume=0.7, rate=0.0, voice="",
                    engine="auto", custom_cmd=""):
        """试听：临时确保有 TTS 引擎并朗读一句，不改变 self._tts_enabled 开关。

        用于设置菜单里调嗓音/音量/语速/引擎时给即时反馈——即使 TTS 总开关是关的，
        也能听到效果；但不会影响后续语录是否朗读（那只看 _tts_enabled）。"""
        if self._tts_player is None:
            try:
                from tts_player import TTSPlayer
                self._tts_player = TTSPlayer(enabled=True, volume=volume, rate=rate,
                                             voice=voice, engine=engine,
                                             custom_cmd=custom_cmd)
            except Exception:
                return
        else:
            self._tts_player.set_custom_cmd(custom_cmd)
            self._tts_player.set_engine(engine)
            self._tts_player.set_enabled(True)
            self._tts_player.set_volume(volume)
            self._tts_player.set_rate(rate)
            self._tts_player.set_voice(voice)
        try:
            self._tts_player.speak(text)
        except Exception:
            pass

    def set_holiday_enabled(self, enabled, user_config=None):
        """设置节日问候开关，并更新用户配置。"""
        self._holiday_enabled = bool(enabled)
        if user_config:
            self._user_config = user_config
        if self._holiday_enabled and self._holiday_greeter is None:
            try:
                from holiday_greetings import HolidayGreeter
                self._holiday_greeter = HolidayGreeter(self._user_config)
            except Exception:
                self._holiday_enabled = False
        elif self._holiday_greeter:
            self._holiday_greeter.config = self._user_config

    def set_intervals(self, min_s, max_s):
        """设置气泡语录自动播放的间隔(秒)。max 同时作为"必播一次"的上限。"""
        min_s = max(5, int(min_s))
        max_s = max(min_s + 1, int(max_s))
        self._min_interval = min_s
        self._max_interval = max_s
        self._play_cooldown = max_s
        # 已在运行就用新间隔重新排程，立即生效
        if self._timer.isActive():
            self._timer.stop()
            self._schedule_next()

    def _shuffle_messages(self):
        """打乱所有语料顺序，避免重复。内置语录会排除用户在"语录管理"里删除的那些。"""
        builtin = []

        if getattr(self, "_mesugaki_mode", False):
            try:
                from mesugaki_quotes import MESUGAKI_LINES
                builtin.extend(MESUGAKI_LINES)
            except ImportError:
                builtin.extend(INSPIRATIONAL)
                builtin.extend(LIFE_CARE)
        elif getattr(self, "_companion_mode", False):
            try:
                from companion_quotes import COMPANION_LINES
                builtin.extend(COMPANION_LINES)
            except ImportError:
                builtin.extend(INSPIRATIONAL)
                builtin.extend(LIFE_CARE)
        else:
            # 添加所有非时间问候的对话
            builtin.extend(INSPIRATIONAL)
            builtin.extend(LIFE_CARE)
            builtin.extend(LIFE_TIPS)
            builtin.extend(CASUAL_CHAT)
            builtin.extend(JOKES)

        # 排除被删除的内置语录，再补上自定义语录（自定义不过滤）
        pool = self._active(builtin)
        custom_quotes = self._load_custom_quotes()
        if custom_quotes:
            pool.extend(custom_quotes)
        if not pool:          # 万一被删光了也别让宠物彻底哑巴
            pool = list(builtin)

        # 随机打乱
        random.shuffle(pool)
        self._message_pool = pool
        self._current_index = 0

    def _load_deleted_builtins(self):
        """读取"语录管理"里被删除的内置语录集合，用于在各播放路径中过滤。"""
        if not self.config_dir:
            return set()
        try:
            path = os.path.join(self.config_dir, "deleted_builtin_quotes.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception:
            pass
        return set()

    def _active(self, items):
        """过滤掉被删除的内置语录（不做空回退，回退交给各调用方按需处理）。"""
        if not self._deleted_builtins:
            return list(items)
        return [q for q in items if q not in self._deleted_builtins]

    def _load_state(self):
        """加载状态。"""
        if not self.config_dir:
            return
        try:
            state_file = os.path.join(self.config_dir, "chat_state.json")
            if os.path.exists(state_file):
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    if state.get("last_greeting_date") == datetime.now().strftime("%Y-%m-%d"):
                        self._today_greeted = True
        except Exception:
            pass

    def _save_state(self):
        """保存状态。"""
        if not self.config_dir:
            return
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            state_file = os.path.join(self.config_dir, "chat_state.json")
            state = {"last_greeting_date": datetime.now().strftime("%Y-%m-%d")}
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def start(self):
        """启动智能聊天。"""
        self._enabled = True
        self._last_play_time = datetime.now()

        # 首次问候
        if not self._today_greeted:
            QTimer.singleShot(2000, self._first_greeting)

        # 开始自动播放
        self._schedule_next()

    def stop(self):
        """停止聊天。"""
        self._enabled = False
        self._timer.stop()

    def set_click_quote_enabled(self, enabled):
        """设置点击弹语录开关。"""
        self._click_quote_enabled = bool(enabled)

    def say(self, message=None, context=None, reschedule=True, allow_tts=True,
            from_click=False, interaction=False):
        """立即说话。主动调用时会重置自动播放定时器，避免冲突。

        reschedule=False 用于内部定时器/问候触发：由调用方（_on_timer/start）统一排程，
        避免在 say 里又排一次，导致计时被排两遍、语录"说太多"。
        from_click=True：来自点击/戳身体宠物身体触发的语录，既受"点击弹语录"开关控制，
        也受冷却限制（同一条语录播完 + 0.5s 内不再触发）。
        interaction=True：来自摸头等明确手势触发的语录，摸头本就排除在"点击弹语录"
        开关之外（仍会说），但同样受冷却限制，避免连续摸头/连点刷屏。"""

        # 点击 / 互动触发的语录统一过冷却闸：
        #   - from_click 还要再过"点击弹语录"开关；
        #   - 二者都要在上一条还在播放（含播完 0.5s 冷却）时直接跳过，避免刷屏。
        if from_click and not self._click_quote_enabled:
            return
        if (from_click or interaction) and self._is_speaking:
            return

        if message:
            text = message
        elif context and context in CONTEXT_MESSAGES:
            text = self._pick_context(context)
        else:
            text = self._get_next_message()

        # 检查气泡是否正在显示其他内容
        # 如果是主动调用（message或context不为None），则立即显示并重置定时器
        # 如果是自动播放且气泡正在显示，则跳过本次
        if not message and not context and not from_click:
            # 自动播放：如果气泡正在显示，跳过本次避免冲突
            if self.bubble.isVisible() and self.bubble._hide_timer.isActive():
                self._last_play_time = datetime.now()   # 退避一个完整间隔，避免每秒忙轮询
                return

        # 标记正在播放，并先把"解锁"定时器排好：即使下面 show_message 抛异常，
        # _is_speaking 也会在冷却结束后自动归位，不会把宠物永久卡成"点了不理人"。
        # 时长必须与气泡实际显示时长（show_message: 2500+len*150，clamp 到 3000~10000）对齐，
        # 再加 0.5s 冷却——否则旧逻辑 len*150 会比气泡提前约 2.5s 解锁，导致"语录还在飘、
        # 连点又弹下一条"。现在要等这一条播放完 + 冷却 0.5s 才允许点击 / 摸头再次触发。
        self._is_speaking = True
        disp = max(3000, min(10000, 2500 + len(text) * 150))
        QTimer.singleShot(disp + 500, self._clear_speaking_flag)
        if self.bubble.show_message(text) is False:
            self._is_speaking = False
            return
        self._last_play_time = datetime.now()

        # 主动调用时，停止并重新安排下次自动播放，避免紧接着又弹出
        if reschedule and (message or context):
            self._timer.stop()
            # 延迟到当前气泡消失后再安排下次自动播放
            QTimer.singleShot(5000, self._schedule_next)

        # TTS 朗读气泡文字：把当前显示的这句读出来（无语音模型的兜底）。
        # allow_tts 由调用方控制：摸头等情境若模型已自带配音，则传 False 避免与模型语音重复；
        # 模型没有语音时传 True，让 TTS 把这句读出来。普通语录/问候默认 True。
        if self._tts_enabled and self._tts_player and allow_tts:
            try:
                self._tts_player.speak(text)
            except Exception:
                pass

        # 说话回调：现在只用于"气泡语录同步发声"（放模型语音，不触发动作）。
        # touch_head 由摸头情境单独发声，这里跳过以免重复。
        if self.on_speak and context != "touch_head":
            try:
                self.on_speak()
            except Exception:
                pass

    def _clear_speaking_flag(self):
        """清除正在播放标记。"""
        self._is_speaking = False

    def say_context(self, context, allow_tts=True, interaction=False):
        """根据情境说话（宠物动作触发）。allow_tts=False 时不 TTS 朗读（模型已自带配音的情境）。
        interaction=True（如摸头）时走冷却闸，避免连续触发刷屏。"""
        if context in CONTEXT_MESSAGES:
            self.say(context=context, allow_tts=allow_tts, interaction=interaction)

    def _pick_context(self, context):
        """从情境语录里随机挑一条，尽量不与上一条重复，让摸头等反应更随机有趣。
        会排除"语录管理"里删除的内置语录；若该情境全被删了则回退原始列表，避免空气泡。"""
        pool = self._active(CONTEXT_MESSAGES.get(context) or [])
        if not pool:
            pool = CONTEXT_MESSAGES.get(context) or []
        if not pool:
            return ""
        if len(pool) > 1 and self._last_context_line in pool:
            text = random.choice([t for t in pool if t != self._last_context_line])
        else:
            text = random.choice(pool)
        self._last_context_line = text
        return text

    def notify_external_speak(self):
        """外部已占用气泡（如摸头/互动语音字幕）：刷新自动播放计时，
        避免紧接着又弹一条间隔语录与之冲突（语音字幕播了就顺延下一次间隔语录）。"""
        self._last_play_time = datetime.now()
        if self._enabled:
            self._timer.stop()
            self._schedule_next()

    def set_bubble_style(self, style_name):
        """设置气泡样式。"""
        self.bubble.set_style(style_name)

    def _load_custom_quotes(self):
        """加载自定义语录。"""
        if not self.config_dir:
            return []

        quotes_file = os.path.join(self.config_dir, "custom_quotes.json")
        if os.path.exists(quotes_file):
            try:
                import json
                with open(quotes_file, "r", encoding="utf-8") as f:
                    quotes = json.load(f)
                return quotes if isinstance(quotes, list) else []
            except Exception:
                return []
        return []

    def reload_custom_quotes(self):
        """重新加载自定义/已删除语录并刷新语料池。"""
        self._deleted_builtins = self._load_deleted_builtins()
        self._shuffle_messages()

    def _first_greeting(self):
        """首次问候。"""
        # 优先检查节日问候
        if self._holiday_enabled and self._holiday_greeter:
            try:
                greeting, is_important = self._holiday_greeter.check_and_greet()
                if greeting:
                    self.say(greeting, reschedule=False)
                    self._today_greeted = True
                    self._save_state()
                    return
            except Exception:
                pass

        # 普通时间问候
        hour = datetime.now().hour

        if 5 <= hour < 9:
            key = "morning"
        elif 11 <= hour < 13:
            key = "noon"
        elif 14 <= hour < 17:
            key = "afternoon"
        elif 18 <= hour < 22:
            key = "evening"
        else:
            key = "night"

        greetings = self._greetings_for(key)

        self._say_greeting(greetings)
        self._today_greeted = True
        self._save_state()

    def _schedule_next(self):
        """安排下次播放。"""
        if not self._enabled:
            return

        # 120秒内必须播放
        if self._last_play_time:
            elapsed = (datetime.now() - self._last_play_time).total_seconds()
            if elapsed >= self._play_cooldown:
                interval = 1000  # 立即播放
            else:
                # 随机间隔，但不超过120秒限制
                remaining = self._play_cooldown - elapsed
                max_wait = min(self._max_interval, remaining)
                hi = max(self._min_interval, int(max_wait))   # 防止 randint 下限>上限报错
                interval = random.randint(self._min_interval, hi) * 1000
        else:
            interval = random.randint(self._min_interval, self._max_interval) * 1000

        self._timer.start(interval)

    def _on_timer(self):
        """定时触发。"""
        # 检查是否需要时间问候
        if self._should_greet():
            self._time_greeting()
        else:
            self.say()

        self._schedule_next()

    def _should_greet(self):
        """是否应该发送时间问候。"""
        hour = datetime.now().hour
        minute = datetime.now().minute

        # 关键时段增加问候概率
        # 整点：30%概率
        if minute == 0:
            return random.random() < 0.3
        # 半点：20%概率
        elif minute == 30:
            return random.random() < 0.2
        # 特殊时段（午餐、下班时间）：40%概率
        elif hour == 12 and minute == 0:  # 中午12点
            return random.random() < 0.4
        elif hour == 18 and minute == 0:  # 下午6点
            return random.random() < 0.4

        return False

    def _time_greeting(self):
        """时间问候。"""
        hour = datetime.now().hour

        if 5 <= hour < 9:
            key = "morning"
        elif 11 <= hour < 13:
            key = "noon"
        elif 14 <= hour < 17:
            key = "afternoon"
        elif 18 <= hour < 22:
            key = "evening"
        else:
            key = "night"

        greetings = self._greetings_for(key)

        self._say_greeting(greetings)

    def _greetings_for(self, key):
        """按当前模式取该时段的问候列表：养成 > 雌小鬼 > 伴侣 > 普通；取不到就回退普通问候。"""
        if getattr(self, "_nurture_mode", False):
            try:
                import affinity_quotes
                lvl, addr = self._aff_level_addr()
                return [affinity_quotes.greet_line(key, addr, lvl)]
            except Exception:
                pass
        if getattr(self, "_mesugaki_mode", False):
            try:
                from mesugaki_quotes import MESUGAKI_GREETINGS
                return MESUGAKI_GREETINGS[key]
            except (ImportError, KeyError):
                pass
        if getattr(self, "_companion_mode", False):
            try:
                from companion_quotes import COMPANION_GREETINGS
                return COMPANION_GREETINGS[key]
            except (ImportError, KeyError):
                pass
        return TIME_GREETINGS[key]

    def _say_greeting(self, greetings):
        """随机说一句问候，排除"语录管理"里删除的问候语；全删了则回退原列表。"""
        greetings = self._active(greetings) or list(greetings)
        if greetings:
            self.say(random.choice(greetings), reschedule=False)

    def _get_next_message(self):
        """获取下一条消息（顺序播放，播完打乱重来）。

        对于特殊类型的语录（如心情低落推荐作品），降低概率，避免频繁出现。"""
        # 养成模式：按当前好感层级动态取词（含称呼替换），不走静态打乱池
        if getattr(self, "_nurture_mode", False):
            try:
                import affinity_quotes
                lvl, addr = self._aff_level_addr()
                line = affinity_quotes.idle_line(lvl, addr, avoid=self._last_nurture_line)
                self._last_nurture_line = line
                return line
            except Exception:
                pass
        if self._current_index >= len(self._message_pool):
            self._shuffle_messages()  # 播完了，重新打乱

        msg = self._message_pool[self._current_index]
        self._current_index += 1

        # 检查是否是低概率语录（心情低落、推荐作品等）
        # 这些语录包含特定关键词，随机跳过以降低频率
        low_freq_keywords = ["心情低落", "推荐", "作品", "去看", "去听", "建议"]
        if any(kw in msg for kw in low_freq_keywords):
            # 80% 概率跳过这类语录，递归获取下一条
            if random.random() < 0.8:
                return self._get_next_message()

        return msg


# 兼容接口
ChatBubble = SmartChatBubble
ChatManager = IntelligentChatManager
SmartChatManager = IntelligentChatManager
