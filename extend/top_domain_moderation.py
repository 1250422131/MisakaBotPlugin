import re
from collections.abc import Mapping, Sequence
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


TOP_DOMAIN_PATTERN = re.compile(
    r"(?<![a-z0-9-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+top(?![a-z0-9.-])",
    re.IGNORECASE,
)


async def recall_message_with_top_domain(event: AstrMessageEvent) -> None:
    """撤回包含 .top 域名或合并转发内容的群消息。"""
    raw_message = event.message_obj.raw_message
    if not await _contains_top_domain(event.bot, raw_message):
        return

    message_id = event.message_obj.message_id
    try:
        await event.bot.call_action("delete_msg", message_id=message_id)
    except Exception as exc:
        logger.warning(f"撤回包含 .top 域名的消息失败，message_id={message_id}: {exc}")
        return

    event.stop_event()


async def _contains_top_domain(bot: Any, value: object) -> bool:
    if isinstance(value, Mapping):
        message_type = value.get("type")
        if message_type == "text":
            return _text_segment_contains_top_domain(value)
        if message_type == "forward":
            forward_id = _forward_id(value)
            if forward_id and await _forward_contains_top_domain(bot, forward_id):
                return True
        return await _contains_top_domain_fields(bot, value)

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for child in value:
            if await _contains_top_domain(bot, child):
                return True

    return False


async def _contains_top_domain_fields(bot: Any, value: Mapping[str, object]) -> bool:
    for field in ("message", "messages", "content"):
        if field in value and await _contains_top_domain(bot, value[field]):
            return True

    data = value.get("data")
    if isinstance(data, Mapping):
        for field in ("message", "messages", "content"):
            if field in data and await _contains_top_domain(bot, data[field]):
                return True
    return False


def _text_segment_contains_top_domain(segment: Mapping[str, object]) -> bool:
    data = segment.get("data")
    if not isinstance(data, Mapping):
        return False
    text = data.get("text")
    return isinstance(text, str) and bool(TOP_DOMAIN_PATTERN.search(text))


def _forward_id(segment: Mapping[str, object]) -> str | None:
    data = segment.get("data")
    if not isinstance(data, Mapping):
        return None
    forward_id = data.get("id")
    return str(forward_id) if forward_id else None


async def _forward_contains_top_domain(bot: Any, forward_id: str) -> bool:
    try:
        forward_message = await bot.call_action("get_forward_msg", id=forward_id)
    except Exception as exc:
        logger.warning(f"获取合并转发消息失败，forward_id={forward_id}: {exc}")
        return False
    return await _contains_top_domain(bot, forward_message)
