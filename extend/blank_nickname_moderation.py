from asyncio import Lock
from collections.abc import Mapping
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At, Plain

from .group_name import SHANGHAI_TIMEZONE


def _has_blank_display_name(member: object) -> bool:
    if not isinstance(member, Mapping):
        return False
    display_name = member.get("card") or member.get("nickname")
    return isinstance(display_name, str) and not display_name.strip()


async def moderate_blank_nickname(event: AstrMessageEvent, lock: Lock) -> None:
    raw_event = event.message_obj.raw_message
    if (
        not isinstance(raw_event, Mapping)
        or raw_event.get("post_type") != "message"
        or raw_event.get("message_type") != "group"
        or raw_event.get("anonymous")
        or not _has_blank_display_name(raw_event.get("sender"))
    ):
        return

    group_id = event.get_group_id()
    sender_id = event.get_sender_id()
    self_id = str(raw_event.get("self_id", ""))
    if not (group_id and sender_id and self_id) or sender_id == self_id:
        return

    async with lock:
        try:
            bot_member = await event.bot.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(self_id),
                self_id=self_id,
                no_cache=True,
            )
            if not isinstance(bot_member, Mapping) or bot_member.get("role") not in (
                "owner",
                "admin",
            ):
                return

            member = await event.bot.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(sender_id),
                self_id=self_id,
                no_cache=True,
            )
            if not _has_blank_display_name(member):
                return

            card = f"违规昵称{datetime.now(SHANGHAI_TIMEZONE):%Y%m%d}"
            await event.bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(sender_id),
                self_id=self_id,
                card=card,
            )
        except Exception as exc:
            logger.warning(f"处理空白昵称失败，group_id={group_id}, user_id={sender_id}: {exc}")
            return

    await event.send(
        MessageChain([At(qq=sender_id), Plain("名称违反群规定了，御坂已经帮你重置了，记得修改呦")])
    )
