import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star

from .extend.castle_swap import CastleSwapService
from .extend.group_management import (
    handle_new_member_notice,
    handle_refresh_group_name,
    handle_send_group_rules,
    refresh_group_name_at_midnight,
)
from .extend.kotlin_celebration import handle_kotlin_celebration
from .extend.text_to_image import TextToImageGenerator


DAILY_GROUP_NAME_JOB = "misaka_bot_daily_group_name"


class MisakaBotPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._group_name_job_id: str | None = None
        self._castle_swap_service = CastleSwapService()
        self._image_generation_tasks: set[asyncio.Task] = set()

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
        tasks = list(self._image_generation_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._image_generation_tasks.clear()

    async def _refresh_group_name_at_midnight(self):
        await refresh_group_name_at_midnight(self.context)

    @filter.command("改朝换代")
    async def refresh_group_name(self, event: AstrMessageEvent):
        async for result in handle_refresh_group_name(event):
            yield result

    @filter.command("为Kotlin庆生")
    async def celebrate_kotlin(self, event: AstrMessageEvent):
        async for result in handle_kotlin_celebration(event):
            yield result

    @filter.command("王车易位")
    async def castle_swap(self, event: AstrMessageEvent):
        async for result in self._castle_swap_service.handle(
            event,
            self.context,
            self.config,
        ):
            yield result

    @filter.llm_tool(name="misaka_generate_image")
    async def generate_image(self, event: AstrMessageEvent, prompt: str) -> str:
        """使用御坂的图片服务生成一张 1024x1024 图片并直接发送给用户。

        仅当用户明确要求生成、绘制或创作图片时调用。不要把图片链接、Base64 数据或生成结果文字当作图片发送给用户。

        Args:
            prompt(string): 用于生成图片的完整中文或英文描述，需包含主体、场景、风格和用户提出的其他要求。
        """
        try:
            await event.send(MessageChain([Plain("正在努力绘制...")]))
            task = asyncio.create_task(
                self._generate_image_in_background(event, prompt)
            )
            self._image_generation_tasks.add(task)
            task.add_done_callback(self._image_generation_tasks.discard)
            return "图片正在生成，完成后会自动发送给用户。"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"图片生成 Tool 调用失败: {exc}")
            return "图片生成失败，请检查文生图 AI 服务商配置后重试。"

    async def _generate_image_in_background(
        self,
        event: AstrMessageEvent,
        prompt: str,
    ) -> None:
        try:
            result = await TextToImageGenerator.from_config(
                self.context,
                self.config,
            ).generate(
                prompt,
                size="1024x1024",
            )
            if result.source.startswith("base64://"):
                image = Image.fromBase64(result.source.removeprefix("base64://"))
            else:
                image = Image.fromURL(result.source)
            await event.send(MessageChain([image]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"后台图片生成失败: {exc}")
            try:
                await event.send(
                    MessageChain([Plain("图片生成失败，请检查文生图 AI 服务商后重试。")])
                )
            except Exception as send_exc:
                logger.warning(f"后台图片生成失败消息发送失败: {send_exc}")

    @filter.command("群规")
    async def send_group_rules(self, event: AstrMessageEvent):
        async for result in handle_send_group_rules(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def remind_new_member_to_read_rules(self, event: AstrMessageEvent):
        async for result in handle_new_member_notice(event):
            yield result
