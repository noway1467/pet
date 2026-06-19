"""好感度 / 养成系统：记录玩家与宠物的情感互动，持久化到 ~/.desktop-pet/affinity.json。

设计要点（与「伴侣模式」的升级版）：
- 5 档好感层级：陌生人 → 熟悉 → 亲近 → 爱人 → 灵魂羁绊，对应称呼从「你」逐步变成专属称呼。
- 日常互动行为各有加点与每日限额（防刷分），并记录今日收益/连续登录/累计摸头等成长轨迹。
- 冷落惩罚：连续 3 天及以上未登录会扣分，增加情感羁绊的真实感。
- 满级（灵魂羁绊）首次达成触发一次特殊剧情。

本模块只管数据与规则，台词在 affinity_quotes.py，UI/触发在 main.py。
"""
import json
import os
from datetime import datetime, date


# ── 好感层级：(名称, 进入该档所需的最低好感值, 默认称呼) ──
# 称呼里 {name} 会替换成用户设置的专属昵称（未设置则用兜底）。
LEVELS = [
    ("陌生人",   0,    "你"),
    ("熟悉",     100,  "你呀"),
    ("亲近",     300,  "笨蛋"),
    ("爱人",     600,  "宝宝"),
    ("灵魂羁绊", 1000, "{name}"),
]
MAX_LEVEL = len(LEVELS) - 1

# ── 日常互动行为：key -> (显示名, 单次加点, 每日次数上限) ──
# 达到每日上限后仍可继续互动（播动画/语音），但不再加分——防刷分，又不打断陪伴体验。
# v3.7.8：好感慢养——每天总收益封顶 DAILY_TOTAL_CAP，单项也各有上限：
#   摸头最多 +3、陪它玩（猜拳/抽签）最多 +5、每日相见 +2，戳一戳共享总额度。
ACTIONS = {
    "head_pat":    ("摸头",     1, 3),
    "body_poke":   ("戳一戳",   1, 3),
    "drag_play":   ("陪它玩",   1, 5),
    "daily_fortune": ("今日抽签", 1, 1),
    "daily_login": ("每日相见", 2, 1),
}

# 每日好感总收益上限：所有行为加起来一天最多 +10（慢养，珍惜每一次陪伴）。
DAILY_TOTAL_CAP = 10

# 冷落惩罚参数
NEGLECT_GRACE_DAYS = 2     # 缺席 <=2 天不罚（出门几天可以理解）
NEGLECT_PER_DAY = 10       # 每多缺席一天扣的分
NEGLECT_CAP = 100          # 单次冷落最多扣这么多


def _today_str():
    return date.today().strftime("%Y-%m-%d")


class AffinitySystem:
    """好感度数据与规则。所有改动都会即时落盘。"""

    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.path = os.path.join(config_dir, "affinity.json") if config_dir else None
        self.data = {
            "points": 0,              # 好感值
            "total_head_pats": 0,     # 累计摸头次数
            "total_drag_plays": 0,    # 累计陪玩次数（如猜拳）
            "total_fortunes": 0,      # 累计抽签次数
            "last_fortune": None,     # 最近一次抽签结果（用于面板展示）
            "streak_days": 0,         # 连续登录天数
            "last_login_date": "",    # 上次登录日期(YYYY-MM-DD)
            "today_date": "",         # 今日收益对应的日期
            "today_gains": 0,         # 今日已获得好感
            "daily_counts": {},       # 今日各行为已用次数 {action: count}
            "max_story_seen": False,  # 满级特殊剧情是否已播过
            "pet_name": "",           # 专属称呼（满级时用；为空用兜底）
        }
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update({k: v for k, v in d.items() if k in self.data})
                self._normalize_loaded_state()
        except (OSError, ValueError):
            pass

    def _rewarded_today_from_counts(self):
        """按当前规则从 daily_counts 反推"今日已计入收益"。

        用于兼容旧版本数据：早期 today_gains 可能高于当前的每日总上限，
        或 daily_counts 里没有把"总上限已满后"的互动次数记出来。"""
        total = 0
        daily = self.data.get("daily_counts") or {}
        if not isinstance(daily, dict):
            return 0
        for action, used in daily.items():
            try:
                used = max(0, int(used))
            except (TypeError, ValueError):
                used = 0
            rule = ACTIONS.get(action)
            if not rule:
                continue
            _name, pts, limit = rule
            effective = used if limit <= 0 else min(used, limit)
            total += effective * pts
        return min(DAILY_TOTAL_CAP, total)

    def _normalize_loaded_state(self):
        """兼容旧版存档，把当天收益状态规整到当前规则下。"""
        try:
            self.data["points"] = max(0, int(self.data.get("points", 0)))
        except (TypeError, ValueError):
            self.data["points"] = 0
        if self.data.get("today_date") != _today_str():
            return
        try:
            stored = int(self.data.get("today_gains", 0))
        except (TypeError, ValueError):
            stored = 0
        if stored > DAILY_TOTAL_CAP:
            self.data["today_gains"] = self._rewarded_today_from_counts()
        else:
            self.data["today_gains"] = max(0, min(DAILY_TOTAL_CAP, stored))

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------- 每日翻篇 ----------
    def _roll_day_if_needed(self):
        """跨天则清零今日收益与各行为次数。"""
        today = _today_str()
        if self.data.get("today_date") != today:
            self.data["today_date"] = today
            self.data["today_gains"] = 0
            self.data["daily_counts"] = {}

    # ---------- 等级 / 称呼 ----------
    def level_index(self):
        pts = self.data["points"]
        lvl = 0
        for i, (_name, threshold, _addr) in enumerate(LEVELS):
            if pts >= threshold:
                lvl = i
        return lvl

    def level_name(self):
        return LEVELS[self.level_index()][0]

    def address(self):
        """当前层级对应的称呼，{name} 用专属昵称替换（未设则兜底）。"""
        addr = LEVELS[self.level_index()][2]
        name = (self.data.get("pet_name") or "").strip()
        if "{name}" in addr:
            return name or "小宝"
        return addr

    def set_pet_name(self, name):
        self.data["pet_name"] = (name or "").strip()
        self.save()

    def level_progress(self):
        """返回 (当前档内已得, 当前档跨度, 比例0~1, 距下一档还差)。满级时跨度按已超出算。"""
        pts = self.data["points"]
        idx = self.level_index()
        cur_thr = LEVELS[idx][1]
        if idx >= MAX_LEVEL:
            return (pts - cur_thr, max(1, pts - cur_thr), 1.0, 0)
        next_thr = LEVELS[idx + 1][1]
        span = max(1, next_thr - cur_thr)
        got = pts - cur_thr
        ratio = max(0.0, min(1.0, got / span))
        return (got, span, ratio, max(0, next_thr - pts))

    def is_max_level(self):
        return self.level_index() >= MAX_LEVEL

    # ---------- 登录 / 连续登录 / 冷落 ----------
    def on_app_start(self):
        """启动时结算：连续登录、冷落惩罚、当日首次相见加分。

        返回 dict：
          {"first_today":bool, "missed_days":int, "neglected":bool,
           "penalty":int, "streak":int, "login_gain":int,
           "leveled_up":bool, "old_level":int, "new_level":int}
        """
        self._roll_day_if_needed()
        old_level = self.level_index()
        today = date.today()
        last_str = self.data.get("last_login_date") or ""
        result = {"first_today": False, "missed_days": 0, "neglected": False,
                  "penalty": 0, "streak": self.data["streak_days"], "login_gain": 0,
                  "leveled_up": False, "old_level": old_level, "new_level": old_level}

        if last_str == today.strftime("%Y-%m-%d"):
            # 今天已经登录过，不重复结算
            return result

        result["first_today"] = True
        gap = None
        if last_str:
            try:
                last = datetime.strptime(last_str, "%Y-%m-%d").date()
                gap = (today - last).days
            except ValueError:
                gap = None

        if gap is None:
            # 第一次使用
            self.data["streak_days"] = 1
        elif gap <= 0:
            pass  # 理论上不会（已在上面拦截同一天）
        elif gap == 1:
            self.data["streak_days"] = self.data["streak_days"] + 1
        else:
            # 中断了：连续天数重置，并按冷落规则扣分
            self.data["streak_days"] = 1
            missed = gap - 1
            result["missed_days"] = missed
            if missed > NEGLECT_GRACE_DAYS:
                penalty = min(NEGLECT_CAP, (missed - NEGLECT_GRACE_DAYS) * NEGLECT_PER_DAY)
                self.data["points"] = max(0, self.data["points"] - penalty)
                result["penalty"] = penalty
                result["neglected"] = True

        self.data["last_login_date"] = today.strftime("%Y-%m-%d")
        result["streak"] = self.data["streak_days"]

        # 当日首次相见加分（受每日限额：1 次）
        gained = self._add_action_points("daily_login")
        result["login_gain"] = gained

        new_level = self.level_index()
        result["new_level"] = new_level
        result["leveled_up"] = new_level > old_level
        self.save()
        return result

    # ---------- 行为加点 ----------
    def _add_action_points(self, action):
        """内部：按每日限额加分，返回实际加到的分（达上限则 0）。不负责升级判断。

        两道闸门：① 该行为自身的每日次数上限；② 全天好感总收益上限 DAILY_TOTAL_CAP。
        接近总上限时只补足到 10（如相见 +2 但只剩 1，则 +1）。"""
        if action not in ACTIONS:
            return 0
        self._roll_day_if_needed()
        _name, pts, limit = ACTIONS[action]
        used = self.data["daily_counts"].get(action, 0)
        if limit > 0 and used >= limit:
            return 0
        # 即使今天总收益已经封顶，也把本次互动次数记下来；
        # 这样好感面板仍能如实显示"今天陪它玩了几次/摸了几次"。
        self.data["daily_counts"][action] = used + 1
        remaining = DAILY_TOTAL_CAP - self.data["today_gains"]
        if remaining <= 0:
            return 0
        grant = min(pts, remaining)
        self.data["points"] += grant
        self.data["today_gains"] += grant
        return grant

    def register(self, action):
        """记录一次互动行为，返回结果 dict：
          {"action":str, "gained":int, "capped":bool, "points":int,
           "today_gains":int, "leveled_up":bool, "old_level":int,
           "new_level":int, "address":str, "daily_used":int, "daily_limit":int}
        capped=True 表示今日该行为已达上限（gained=0）。
        """
        self._roll_day_if_needed()
        old_level = self.level_index()
        if action == "head_pat":
            self.data["total_head_pats"] = self.data.get("total_head_pats", 0) + 1
        elif action == "drag_play":
            self.data["total_drag_plays"] = self.data.get("total_drag_plays", 0) + 1
        elif action == "daily_fortune":
            self.data["total_fortunes"] = self.data.get("total_fortunes", 0) + 1
        gained = self._add_action_points(action)
        _name, _pts, limit = ACTIONS.get(action, ("", 0, 0))
        used = self.data["daily_counts"].get(action, 0)
        new_level = self.level_index()
        self.save()
        return {
            "action": action, "gained": gained, "capped": gained == 0 and limit > 0,
            "points": self.data["points"], "today_gains": self.data["today_gains"],
            "leveled_up": new_level > old_level, "old_level": old_level,
            "new_level": new_level, "address": self.address(),
            "daily_used": used, "daily_limit": limit,
        }

    def consume_max_story(self):
        """满级剧情是否该播：未播且已满级时返回 True 并打标记（只触发一次）。"""
        if self.is_max_level() and not self.data.get("max_story_seen"):
            self.data["max_story_seen"] = True
            self.save()
            return True
        return False

    def record_fortune(self, fortune):
        """保存最近一次抽签结果，便于好感面板展示。"""
        if isinstance(fortune, dict):
            keep = {}
            for key in ("mode", "mode_label", "grade", "title", "headline", "summary", "advice", "accent"):
                if key in fortune:
                    keep[key] = fortune[key]
            keep["date"] = _today_str()
            self.data["last_fortune"] = keep
            self.save()

    # ---------- 面板数据 ----------
    def panel(self):
        self._roll_day_if_needed()
        got, span, ratio, to_next = self.level_progress()
        pet_name = self.data.get("pet_name", "")
        actions = []
        for key, (name, pts, limit) in ACTIONS.items():
            used = self.data["daily_counts"].get(key, 0)
            actions.append({"key": key, "name": name, "per": pts,
                            "used": used, "limit": limit})
        return {
            "points": self.data["points"],
            "level_index": self.level_index(),
            "level_name": self.level_name(),
            "address": self.address(),
            "pet_name": pet_name,
            "pet_name_active": bool(self.is_max_level() and (pet_name or "").strip()),
            "pet_name_ready_level": LEVELS[MAX_LEVEL][0],
            "progress_got": got, "progress_span": span, "progress_ratio": ratio,
            "to_next": to_next, "is_max": self.is_max_level(),
            "today_gains": self.data["today_gains"],
            "daily_total_cap": DAILY_TOTAL_CAP,
            "streak_days": self.data["streak_days"],
            "total_head_pats": self.data.get("total_head_pats", 0),
            "total_drag_plays": self.data.get("total_drag_plays", 0),
            "total_fortunes": self.data.get("total_fortunes", 0),
            "last_fortune": self.data.get("last_fortune"),
            "actions": actions,
        }
