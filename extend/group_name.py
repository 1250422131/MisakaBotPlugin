from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lunar_python import Solar


GROUP_NAME_PREFIX = "哔哩哔哩萌新交流社🇨🇳"
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")

SOLAR_FESTIVALS = {
    (1, 1): "元旦节",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (3, 12): "植树节",
    (5, 1): "劳动节",
    (5, 4): "青年节",
    (6, 1): "儿童节",
    (7, 1): "建党节",
    (8, 1): "建军节",
    (9, 10): "教师节",
    (10, 1): "国庆节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
}

LUNAR_FESTIVALS = {
    (1, 1): "春节",
    (1, 15): "元宵节",
    (2, 2): "龙抬头",
    (5, 5): "端午节",
    (7, 7): "七夕节",
    (7, 15): "中元节",
    (8, 15): "中秋节",
    (9, 9): "重阳节",
    (12, 8): "腊八节",
    (12, 23): "小年",
}

FESTIVAL_EMOJIS = {
    "元旦节": ("🎆", "✨"),
    "春节": ("🧨", "🧧"),
    "元宵节": ("🏮", "🥣"),
    "情人节": ("🌹", "💝"),
    "妇女节": ("🌷", "💐"),
    "植树节": ("🌳", "🌱"),
    "清明节": ("🌿", "🪁"),
    "劳动节": ("🛠️", "🎈"),
    "青年节": ("🌟", "🎈"),
    "儿童节": ("🧸", "🎈"),
    "端午节": ("🐉", "🍙"),
    "七夕节": ("🌌", "💫"),
    "中元节": ("🪷", "🕯️"),
    "中秋节": ("🌕", "🥮"),
    "教师节": ("📚", "🌷"),
    "重阳节": ("🍂", "🌼"),
    "国庆节": ("🇨🇳", "🎆"),
    "平安夜": ("🎄", "🔔"),
    "圣诞节": ("🎄", "🎁"),
    "腊八节": ("🥣", "❄️"),
    "小年": ("🧹", "🧧"),
    "除夕": ("🧨", "🏮"),
}

SOLAR_TERM_EMOJIS = {
    "立春": ("🌱", "🌸"),
    "雨水": ("🌧️", "💧"),
    "惊蛰": ("🐛", "⚡"),
    "春分": ("🌸", "🌷"),
    "清明": ("🌿", "🪁"),
    "谷雨": ("🌾", "🌧️"),
    "立夏": ("🌿", "🍉"),
    "小满": ("🌾", "🌿"),
    "芒种": ("🌾", "🌱"),
    "夏至": ("☀️", "🍉"),
    "小暑": ("🌤️", "🧊"),
    "大暑": ("☀️", "🍧"),
    "立秋": ("🍂", "🌾"),
    "处暑": ("🍂", "🍃"),
    "白露": ("🍂", "💧"),
    "秋分": ("🍁", "🌕"),
    "寒露": ("🍁", "💧"),
    "霜降": ("🍁", "❄️"),
    "立冬": ("❄️", "🍲"),
    "小雪": ("❄️", "🌨️"),
    "大雪": ("⛄", "🌨️"),
    "冬至": ("🥟", "❄️"),
    "小寒": ("🧣", "❄️"),
    "大寒": ("🧣", "🥶"),
}

SEASON_EMOJIS = {
    "spring": ("🌱", "🌸"),
    "summer": ("🌞", "🍉"),
    "autumn": ("🍂", "🌾"),
    "winter": ("❄️", "🧣"),
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def build_group_name(today: date | None = None) -> str:
    """生成当天群名，固定社名不参与变化。"""
    today = today or datetime.now(SHANGHAI_TIMEZONE).date()
    label, emojis = _day_label_and_emojis(today)
    name = (
        f"{emojis[0]}{GROUP_NAME_PREFIX}{today.month}月{today.day}日"
        f" {WEEKDAYS[today.weekday()]}"
    )
    return f"{name} {label}{emojis[1]}" if label else name


def get_special_day_label(today: date | None = None) -> str:
    """返回当天节日或节气名称；普通日期返回空字符串。"""
    today = today or datetime.now(SHANGHAI_TIMEZONE).date()
    label, _ = _day_label_and_emojis(today)
    return label


def _day_label_and_emojis(today: date) -> tuple[str, tuple[str, str]]:
    solar = Solar.fromYmd(today.year, today.month, today.day)
    lunar = solar.getLunar()
    festival = _festival_name(today, lunar)
    if festival:
        return festival, FESTIVAL_EMOJIS.get(festival, SEASON_EMOJIS[_season(today)])

    solar_term = lunar.getJieQi()
    if solar_term:
        return solar_term, SOLAR_TERM_EMOJIS.get(
            solar_term, SEASON_EMOJIS[_season(today)]
        )

    return "", SEASON_EMOJIS[_season(today)]


def _festival_name(today: date, lunar) -> str:
    solar_festival = SOLAR_FESTIVALS.get((today.month, today.day))
    if solar_festival:
        return solar_festival

    lunar_festival = LUNAR_FESTIVALS.get((abs(lunar.getMonth()), lunar.getDay()))
    if lunar_festival:
        return lunar_festival

    tomorrow_lunar = Solar.fromYmd(
        (today + timedelta(days=1)).year,
        (today + timedelta(days=1)).month,
        (today + timedelta(days=1)).day,
    ).getLunar()
    if tomorrow_lunar.getMonth() == 1 and tomorrow_lunar.getDay() == 1:
        return "除夕"

    return ""


def _season(today: date) -> str:
    if 3 <= today.month <= 5:
        return "spring"
    if 6 <= today.month <= 8:
        return "summer"
    if 9 <= today.month <= 11:
        return "autumn"
    return "winter"
