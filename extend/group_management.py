from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Plain
from astrbot.api.star import Context

from .group_name import build_group_name


GROUP_RULES_URL = "https://misakamoe.com/keys"
TARGET_GROUP_ID = 812128563


async def refresh_group_name_at_midnight(context: Context) -> None:
    platforms = [
        platform
        for platform in context.platform_manager.get_insts()
        if platform.meta().name == "aiocqhttp"
    ]
    if not platforms:
        logger.warning("未找到 aiocqhttp 平台，无法更新群名")
        return

    last_error: Exception | None = None
    for platform in platforms:
        try:
            await set_group_name(platform.get_client())
        except Exception as exc:
            last_error = exc
            logger.debug(
                f"aiocqhttp 平台 {platform.meta().id} 更新群名失败: {exc}"
            )
            continue
        return

    raise RuntimeError("所有 aiocqhttp 平台均未能修改目标群群名") from last_error


async def handle_refresh_group_name(event: AstrMessageEvent):
    if str(event.get_group_id()) != str(TARGET_GROUP_ID):
        yield event.plain_result("该指令只能在本群使用。")
        return

    event_self_id = getattr(event.message_obj, "self_id", None)
    try:
        group_name = await set_group_name(
            event.bot,
            self_id=str(event_self_id) if event_self_id else None,
        )
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


async def set_group_name(bot: Any, self_id: str | None = None) -> str:
    group_name = build_group_name()
    connected_clients = getattr(bot, "_wsr_api_clients", {})
    connected_self_ids = (
        tuple(str(client_self_id) for client_self_id in connected_clients)
        if isinstance(connected_clients, dict)
        else ()
    )
    candidate_self_ids: tuple[str | None, ...] = (
        (self_id,) if self_id else connected_self_ids or (None,)
    )
    last_error: Exception | None = None

    for candidate_self_id in candidate_self_ids:
        params = {
            "group_id": TARGET_GROUP_ID,
            "group_name": group_name,
        }
        if candidate_self_id:
            params["self_id"] = candidate_self_id

        try:
            await bot.call_action("set_group_name", **params)
        except Exception as exc:
            last_error = exc
            logger.debug(
                f"机器人 {candidate_self_id or '未指定'} 更新群名失败: {exc}"
            )
            continue

        logger.info(
            f"机器人 {candidate_self_id or '自动选择'} 已更新群 "
            f"{TARGET_GROUP_ID} 名称为：{group_name}"
        )
        return group_name

    raise RuntimeError(
        f"所有候选 OneBot 连接均未能修改群 {TARGET_GROUP_ID} 的群名"
    ) from last_error
