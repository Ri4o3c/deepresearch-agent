from datetime import date


def today_hint() -> str:
    """注入当前日期，避免模型把近期信息误判为"未来"""
    return f"今天是 {date.today():%Y年%m月%d日}。早于今天的日期属于已发生的事实，晚于今天的属于预测。"
