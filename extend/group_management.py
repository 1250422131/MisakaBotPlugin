from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Plain
from astrbot.api.star import Context

from .group_name import build_group_name


GROUP_RULES_URL = "https://misakamoe.com/keys"
TARGET_GROUP_ID = 812128563


async def refresh_group_name_at_midnight(context: Context) -> None:
    platform = context.get_platform("aiocqhttp")
    if platform is None:
        logger.warning("未找到 aiocqhttp 平台，无法更新群名")
        return

    await set_group_name(platform.get_client())


async def handle_refresh_group_name(event: AstrMessageEvent):
    if str(event.get_group_id()) != str(TARGET_GROUP_ID):
        yield event.plain_result("该指令只能在本群使用。")
        return

    try:
        group_name = await set_group_name(event.bot)
    except Exception as exc:
        logger.exception(f"手动更新群名失败: {exc}")
        yield event.plain_result("群名更新失败，请确认机器人具备群管理权限。")
        return

    yield event.plain_result(f"群名已更新为：{group_name}")


async def handle_send_group_rules(event: AstrMessageEvent):
    yield event.plain_result(f"本群群规：{GROUP_RULES_URL}")


async def handle_new_member_notice(event: AstrMessageEvent):
    raw_event = event.message_obj.raw_message
    if (
        raw_event.get("post_type") != "notice"
        or raw_event.get("notice_type") != "group_increase"
        or str(raw_event.get("user_id")) == str(raw_event.get("self_id"))
    ):
        return

    yield event.chain_result(
        [
            At(qq=event.get_sender_id()),
            Plain(f"请务必阅读本群群规：{GROUP_RULES_URL}"),
        ]
    )


async def set_group_name(bot: Any) -> str:
    group_name = build_group_name()
    await bot.call_action(
        "set_group_name",
        group_id=TARGET_GROUP_ID,
        group_name=group_name,
    )
    logger.info(f"已更新群 {TARGET_GROUP_ID} 名称为：{group_name}")
    return group_name
