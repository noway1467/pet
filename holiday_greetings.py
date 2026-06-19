"""节日/纪念日问候系统：春节、生日、自定义纪念日的专属问候与倒数。

特点：
- 自动检测中国传统节日（春节、元宵、端午、中秋等）
- 支持用户生日倒数与祝福
- 支持自定义纪念日（恋爱纪念日、结婚纪念日等）
- 提前倒数提醒（节日前 7/3/1 天）
- 节日当天专属问候
"""
from datetime import datetime, timedelta
import json


# ══════════════════════════════════════════════════════════════
#  中国传统节日（农历）-> 2025-2030 年公历对照表
# ══════════════════════════════════════════════════════════════
# 数据来源：中国科学院紫金山天文台历算
LUNAR_HOLIDAYS = {
    "春节": {
        2025: "01-29", 2026: "02-17", 2027: "02-06", 2028: "01-26", 2029: "02-13", 2030: "02-02"
    },
    "元宵节": {
        2025: "02-12", 2026: "03-03", 2027: "02-20", 2028: "02-09", 2029: "02-27", 2030: "02-16"
    },
    "端午节": {
        2025: "05-31", 2026: "06-19", 2027: "06-09", 2028: "05-28", 2029: "06-16", 2030: "06-05"
    },
    "七夕节": {
        2025: "08-29", 2026: "08-19", 2027: "08-08", 2028: "08-26", 2029: "08-16", 2030: "08-05"
    },
    "中秋节": {
        2025: "10-06", 2026: "09-25", 2027: "09-15", 2028: "10-03", 2029: "09-22", 2030: "09-12"
    },
}

# 公历固定节日
SOLAR_HOLIDAYS = {
    "元旦": "01-01",
    "情人节": "02-14",
    "妇女节": "03-08",
    "劳动节": "05-01",
    "青年节": "05-04",
    "儿童节": "06-01",
    "建党节": "07-01",
    "建军节": "08-01",
    "教师节": "09-10",
    "国庆节": "10-01",
    "万圣节": "10-31",
    "感恩节": "11-28",  # 美国感恩节（第四个周四，这里简化）
    "圣诞节": "12-25",
}

# 节日问候语
HOLIDAY_MESSAGES = {
    "春节": [
        "新春快乐！祝你在新的一年里身体健康，万事如意！🧧",
        "过年好！愿你新的一年财源广进，事业顺利！🎊",
        "春节到啦！祝你阖家欢乐，幸福安康！🏮",
        "新年快乐！愿你龙年大吉，心想事成！🐉",
    ],
    "元宵节": [
        "元宵节快乐！愿你团团圆圆，甜甜蜜蜜！🏮",
        "正月十五闹元宵，祝你生活美满，笑口常开！",
        "元宵佳节，祝你花好月圆，幸福美满！",
    ],
    "情人节": [
        "情人节快乐！愿你被爱包围，幸福满满！❤️",
        "今天是情人节呀，祝你甜甜蜜蜜！💕",
        "情人节到了，愿你的爱情像巧克力一样甜～🍫",
    ],
    "端午节": [
        "端午安康！记得吃粽子哦～🍙",
        "端午节快乐！愿你平安喜乐，粽享美好！",
        "五月初五端午节，祝你身体健康，万事顺心！",
    ],
    "七夕节": [
        "七夕快乐！愿天下有情人终成眷属！💑",
        "今天是七夕呀，祝你甜蜜幸福！✨",
        "鹊桥相会日，祝你爱情美满！💕",
    ],
    "中秋节": [
        "中秋快乐！愿你月圆人圆事事圆满！🌕",
        "中秋佳节，祝你阖家团圆，幸福安康！🥮",
        "月到中秋分外明，祝你生活甜如蜜！",
    ],
    "国庆节": [
        "国庆快乐！祝祖国繁荣昌盛！🇨🇳",
        "十月一日国庆节，祝你假期愉快！",
        "国庆到了，祝你玩得开心，吃得开心！",
    ],
    "圣诞节": [
        "圣诞快乐！Merry Christmas！🎄",
        "圣诞节到啦，祝你收到满满的礼物！🎁",
        "平安夜，祝你平平安安，快快乐乐！",
    ],
    "元旦": [
        "元旦快乐！新的一年，新的开始！🎉",
        "元旦到了，祝你新年新气象，万事如意！",
        "Happy New Year！愿你新年快乐，梦想成真！✨",
    ],
}

# 倒数提醒（节日前 N 天）
COUNTDOWN_MESSAGES = {
    7: "距离{holiday}还有 7 天呢，好期待！",
    3: "距离{holiday}只剩 3 天啦！",
    1: "明天就是{holiday}了，好激动！",
}

# 生日问候
BIRTHDAY_MESSAGES = [
    "生日快乐！🎂 愿你年年有今日，岁岁有今朝！",
    "Happy Birthday！🎉 祝你生日快乐，心想事成！",
    "今天是你的生日呀，祝你开心快乐每一天！🎈",
    "生日快乐！愿你的每一个愿望都能实现！🌟",
    "祝你生日快乐！愿你被这个世界温柔以待！💕",
]

BIRTHDAY_COUNTDOWN = {
    30: "距离你的生日还有 30 天，提前准备惊喜吧！",
    15: "距离你的生日还有半个月啦！",
    7: "距离你的生日只有 7 天了！",
    3: "你的生日快到了，只剩 3 天！",
    1: "明天就是你的生日啦，好期待！🎂",
}


class HolidayGreeter:
    """节日问候管理器：检测节日、生成问候、倒数提醒。"""

    def __init__(self, config=None):
        """初始化节日问候器。

        Args:
            config: 配置字典，包含 user_birthday、custom_holidays
        """
        self.config = config or {}
        self._last_check_date = None
        self._today_greeted = set()  # 今天已问候的节日

    def check_and_greet(self):
        """检查今天是否有节日，返回问候语（如果有的话）。

        返回：(问候语, 是否是重要节日) 或 (None, False)
        """
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        # 新的一天，清空已问候记录
        if self._last_check_date != today_str:
            self._last_check_date = today_str
            self._today_greeted.clear()

        # 检查各类节日
        greeting = None

        # 1. 检查生日
        if not greeting:
            greeting = self._check_birthday(today)
            if greeting:
                self._today_greeted.add("birthday")
                return greeting, True

        # 2. 检查农历节日
        if not greeting:
            greeting = self._check_lunar_holidays(today)
            if greeting:
                return greeting, True

        # 3. 检查公历节日
        if not greeting:
            greeting = self._check_solar_holidays(today)
            if greeting:
                return greeting, True

        # 4. 检查自定义纪念日
        if not greeting:
            greeting = self._check_custom_holidays(today)
            if greeting:
                return greeting, True

        # 5. 检查倒数提醒
        if not greeting:
            greeting = self._check_countdown(today)
            if greeting:
                return greeting, False

        return None, False

    def _check_birthday(self, today):
        """检查今天是否是用户生日。"""
        birthday = self.config.get("user_birthday", "")
        if not birthday:
            return None

        try:
            month, day = map(int, birthday.split("-"))
            if today.month == month and today.day == day:
                if "birthday" not in self._today_greeted:
                    import random
                    return random.choice(BIRTHDAY_MESSAGES)
        except Exception:
            pass

        return None

    def _check_lunar_holidays(self, today):
        """检查今天是否是农历节日。"""
        today_mmdd = today.strftime("%m-%d")
        year = today.year

        for name, dates in LUNAR_HOLIDAYS.items():
            if year in dates and dates[year] == today_mmdd:
                if name not in self._today_greeted:
                    self._today_greeted.add(name)
                    import random
                    return random.choice(HOLIDAY_MESSAGES.get(name, [f"{name}快乐！"]))

        return None

    def _check_solar_holidays(self, today):
        """检查今天是否是公历节日。"""
        today_mmdd = today.strftime("%m-%d")

        for name, date in SOLAR_HOLIDAYS.items():
            if date == today_mmdd:
                if name not in self._today_greeted:
                    self._today_greeted.add(name)
                    import random
                    return random.choice(HOLIDAY_MESSAGES.get(name, [f"{name}快乐！"]))

        return None

    def _check_custom_holidays(self, today):
        """检查今天是否是自定义纪念日。"""
        custom_holidays = self.config.get("custom_holidays", [])
        today_mmdd = today.strftime("%m-%d")

        for holiday in custom_holidays:
            if not isinstance(holiday, dict):
                continue
            date = holiday.get("date", "")
            name = holiday.get("name", "")
            message = holiday.get("message", "")

            if date == today_mmdd:
                key = f"custom_{name}"
                if key not in self._today_greeted:
                    self._today_greeted.add(key)
                    return message or f"{name}快乐！"

        return None

    def _check_countdown(self, today):
        """检查是否有即将到来的节日（倒数提醒）。"""
        # 检查生日倒数
        birthday = self.config.get("user_birthday", "")
        if birthday:
            try:
                month, day = map(int, birthday.split("-"))
                birthday_date = datetime(today.year, month, day)
                if birthday_date < today:
                    birthday_date = datetime(today.year + 1, month, day)
                days_left = (birthday_date - today).days

                if days_left in BIRTHDAY_COUNTDOWN:
                    key = f"birthday_countdown_{days_left}"
                    if key not in self._today_greeted:
                        self._today_greeted.add(key)
                        return BIRTHDAY_COUNTDOWN[days_left]
            except Exception:
                pass

        # 检查节日倒数
        today_mmdd = today.strftime("%m-%d")
        year = today.year

        # 农历节日
        for name, dates in LUNAR_HOLIDAYS.items():
            if year in dates:
                try:
                    h_month, h_day = map(int, dates[year].split("-"))
                    holiday_date = datetime(year, h_month, h_day)
                    if holiday_date > today:
                        days_left = (holiday_date - today).days
                        if days_left in COUNTDOWN_MESSAGES:
                            key = f"{name}_countdown_{days_left}"
                            if key not in self._today_greeted:
                                self._today_greeted.add(key)
                                return COUNTDOWN_MESSAGES[days_left].format(holiday=name)
                except Exception:
                    pass

        # 公历节日
        for name, date in SOLAR_HOLIDAYS.items():
            try:
                h_month, h_day = map(int, date.split("-"))
                holiday_date = datetime(year, h_month, h_day)
                if holiday_date < today:
                    holiday_date = datetime(year + 1, h_month, h_day)
                days_left = (holiday_date - today).days

                if days_left in COUNTDOWN_MESSAGES:
                    key = f"{name}_countdown_{days_left}"
                    if key not in self._today_greeted:
                        self._today_greeted.add(key)
                        return COUNTDOWN_MESSAGES[days_left].format(holiday=name)
            except Exception:
                pass

        return None


def get_holiday_greeting(config=None):
    """便捷函数：获取今天的节日问候（如果有的话）。

    Returns:
        (问候语, 是否是重要节日) 或 (None, False)
    """
    greeter = HolidayGreeter(config)
    return greeter.check_and_greet()
