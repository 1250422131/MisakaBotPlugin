import asyncio
from typing import Any

import aiohttp

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from .extend.group_name import build_group_name
from .extend.kotlin_celebration import generate_kotlin_celebration

GROUP_RULES_URL = "https://misakamoe.com/keys"
TARGET_GROUP_ID = 812128563
DAILY_GROUP_NAME_JOB = "misaka_bot_daily_group_name"


class MisakaBotPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._group_name_job_id: str | None = None

    async def initialize(self):
        """注册每日零点更新群名的任务。"""
        existing_jobs = await self.context.cron_manager.list_jobs(job_type="basic")
        for job in existing_jobs:
            if job.name == DAILY_GROUP_NAME_JOB:
                await self.context.cron_manager.delete_job(job.job_id)

        job = await self.context.cron_manager.add_basic_job(
            name=DAILY_GROUP_NAME_JOB,
            cron_expression="0 0 * * *",
            handler=self._refresh_group_name_at_midnight,
            description="每日零点更新萌新交流社群名",
            timezone="Asia/Shanghai",
        )
        self._group_name_job_id = job.job_id

    async def terminate(self):
        """移除插件卸载后不再需要的定时任务。"""
        if self._group_name_job_id:
            await self.context.cron_manager.delete_job(self._group_name_job_id)
            self._group_name_job_id = None

    async def _refresh_group_name_at_midnight(self):
        platform = self.context.get_platform("aiocqhttp")
        if platform is None:
            logger.warning("未找到 aiocqhttp 平台，无法更新群名")
            return

        bot = platform.get_client()
        await self._set_group_name(bot)

    async def _set_group_name(self, bot: Any) -> str:
        group_name = build_group_name()
        await bot.call_action(
            "set_group_name",
            group_id=TARGET_GROUP_ID,
            group_name=group_name,
        )
        logger.info(f"已更新群 {TARGET_GROUP_ID} 名称为：{group_name}")
        return group_name

    @filter.command("改朝换代")
    async def refresh_group_name(self, event: AstrMessageEvent):
        """立即按当天节日或节气更新本群群名。"""
        if str(event.get_group_id()) != str(TARGET_GROUP_ID):
            yield event.plain_result("该指令只能在本群使用。")
            return

        try:
            group_name = await self._set_group_name(event.bot)
        except Exception as exc:
            logger.exception(f"手动更新群名失败: {exc}")
            yield event.plain_result("群名更新失败，请确认机器人具备群管理权限。")
            return

        yield event.plain_result(f"群名已更新为：{group_name}")

    @filter.command("为Kotlin庆生")
    async def celebrate_kotlin(self, event: AstrMessageEvent):
        """使用消息图片或 QQ 头像生成 Kotlin 庆生贺卡。"""
        try:
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Plain("开始生成庆生图片，请稍候..."),
                ]
            )
            result_url = await generate_kotlin_celebration(event)
        except asyncio.TimeoutError:
            logger.warning("Kotlin 庆生请求超时")
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Plain("Kotlin 庆生图片生成超时，请稍后再试。"),
                ]
            )
            return
        except (aiohttp.ClientError, ValueError, OSError) as exc:
            logger.warning(f"Kotlin 庆生图片生成失败: {exc}")
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Plain("Kotlin 庆生图片生成失败，请稍后再试。"),
                ]
            )
            return

        yield event.chain_result(
            [
                Reply(id=event.message_obj.message_id),
                Image.fromURL(result_url),
            ]
        )

    @filter.command("群规")
    async def send_group_rules(self, event: AstrMessageEvent):
        """发送本群群规链接。"""
        yield event.plain_result(f"本群群规：{GROUP_RULES_URL}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def remind_new_member_to_read_rules(self, event: AstrMessageEvent):
        """在 OneBot 新成员入群后提醒其阅读群规。"""
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
