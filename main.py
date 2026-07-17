from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .extend.castle_swap import CastleSwapService
from .extend.group_management import (
    handle_new_member_notice,
    handle_refresh_group_name,
    handle_send_group_rules,
    refresh_group_name_at_midnight,
)
from .extend.image_generation import ImageGenerationService
from .extend.kotlin_celebration import handle_kotlin_celebration


DAILY_GROUP_NAME_JOB = "misaka_bot_daily_group_name"


class MisakaBotPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._group_name_job_id: str | None = None
        self._image_generation_service = ImageGenerationService(self.context, config)
        self._castle_swap_service = CastleSwapService(self._image_generation_service)

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
        await self._image_generation_service.terminate()

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
        async for result in self._castle_swap_service.handle(event):
            yield result

    @filter.llm_tool(name="misaka_generate_image")
    async def generate_image(self, event: AstrMessageEvent, prompt: str) -> str:
        """使用御坂的图片服务生成图片并直接发送给用户。

        仅当用户明确要求生成、绘制或创作图片时调用。用户附带或回复的图片会自动作为参考图进行图生图；没有图片时使用纯文生图。不要把图片链接、Base64 数据或生成结果文字当作图片发送给用户。

        Args:
            prompt(string): 用于生成图片的完整中文或英文描述，需包含主体、场景、风格和用户提出的其他要求。
        """
        return await self._image_generation_service.handle(event, prompt)

    @filter.command("群规")
    async def send_group_rules(self, event: AstrMessageEvent):
        async for result in handle_send_group_rules(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def remind_new_member_to_read_rules(self, event: AstrMessageEvent):
        async for result in handle_new_member_notice(event):
            yield result
