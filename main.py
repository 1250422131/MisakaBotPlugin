import asyncio
import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star
from markdown import markdown

from .extend.castle_swap import CastleSwapService
from .extend.group_management import (
    GroupRulesImageService,
    handle_new_member_notice,
    handle_refresh_group_name,
    handle_send_group_rules,
    handle_update_group_rules_image,
    refresh_group_name_at_midnight,
)
from .extend.image_generation import ImageGenerationService
from .extend.kotlin_celebration import handle_kotlin_celebration


DAILY_GROUP_NAME_JOB = "misaka_bot_daily_group_name"
MARKDOWN_T2I_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <style>
    * { box-sizing: border-box; }
    body {
      color: #1f2328;
      background: #ffffff;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 16px;
      line-height: 1.7;
      margin: 0;
      padding: 32px;
      overflow-wrap: anywhere;
    }
    h1, h2, h3, h4, h5, h6 { line-height: 1.35; margin: 1.2em 0 0.55em; }
    h1 { border-bottom: 1px solid #d0d7de; font-size: 28px; padding-bottom: 0.3em; }
    h2 { border-bottom: 1px solid #d0d7de; font-size: 23px; padding-bottom: 0.25em; }
    h3 { font-size: 19px; }
    p, ul, ol, blockquote, pre, table { margin: 0.8em 0; }
    blockquote { border-left: 4px solid #d0d7de; color: #57606a; margin-left: 0; padding-left: 1em; }
    pre, code { background: #f6f8fa; font-family: "SFMono-Regular", Menlo, monospace; }
    pre { border-radius: 6px; padding: 14px; white-space: pre-wrap; }
    code { border-radius: 4px; padding: 0.15em 0.3em; }
    pre code { padding: 0; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #d0d7de; padding: 8px 11px; text-align: left; vertical-align: top; }
    th { background: #f6f8fa; font-weight: 600; }
    tr:nth-child(even) { background: #f6f8fa; }
    a { color: #0969da; }
  </style>
</head>
<body>{{ content | safe }}</body>
</html>
"""


def markdown_to_html(markdown_content: str) -> str:
    content = markdown_content.strip()
    if not content:
        raise ValueError("Markdown 内容不能为空")

    safe_content = re.sub(
        r"<[^>\n]+>",
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        content,
    )
    content_html = markdown(
        safe_content,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    return re.sub(r"<img\b[^>]*>", "", content_html, flags=re.IGNORECASE)


class MisakaBotPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._group_name_job_id: str | None = None
        self._image_generation_service = ImageGenerationService(self.context, config)
        self._castle_swap_service = CastleSwapService(self._image_generation_service)
        self._group_rules_image_service = GroupRulesImageService(self.name, config)
        self._markdown_t2i_tasks: set[asyncio.Task[None]] = set()

    async def initialize(self):
        """注册每日零点更新群名和特殊头像的任务。"""
        existing_jobs = await self.context.cron_manager.list_jobs(job_type="basic")
        for job in existing_jobs:
            if job.name == DAILY_GROUP_NAME_JOB:
                await self.context.cron_manager.delete_job(job.job_id)

        job = await self.context.cron_manager.add_basic_job(
            name=DAILY_GROUP_NAME_JOB,
            cron_expression="0 0 * * *",
            handler=self._refresh_group_name_at_midnight,
            description="每日零点更新萌新交流社群名和特殊头像",
            timezone="Asia/Shanghai",
        )
        self._group_name_job_id = job.job_id

    async def terminate(self):
        """移除插件卸载后不再需要的定时任务。"""
        if self._group_name_job_id:
            await self.context.cron_manager.delete_job(self._group_name_job_id)
            self._group_name_job_id = None
        await self._image_generation_service.terminate()
        tasks = list(self._markdown_t2i_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._markdown_t2i_tasks.clear()

    async def _refresh_group_name_at_midnight(self):
        await refresh_group_name_at_midnight(self.context, self.config)

    @filter.command("改朝换代")
    async def refresh_group_name(self, event: AstrMessageEvent):
        async for result in handle_refresh_group_name(event, self.context, self.config):
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

    @filter.llm_tool(name="misaka_render_markdown_to_image")
    async def render_markdown_to_image(
        self,
        event: AstrMessageEvent,
        markdown_content: str,
    ) -> str:
        """将最终 Markdown 回复渲染为图片并直接发送。

        当回答必须使用 Markdown 才能正确表达时必须调用，例如表格、分级标题、列表、引用或代码块。传入完整的最终 Markdown 原文；工具会发送图片，因此调用后不要再输出原始 Markdown 或重复内容。普通的一两句纯文本回复不要调用此工具。

        Args:
            markdown_content(string): 要发送给用户的完整 Markdown 内容，保留标题、表格、列表、代码块等 Markdown 结构。
        """
        try:
            await event.send(
                MessageChain(
                    [Reply(id=event.message_obj.message_id), Plain("正在转换 Markdown 图片...")]
                )
            )
            task = asyncio.create_task(
                self._render_markdown_with_astrbot_t2i(event, markdown_content)
            )
            self._markdown_t2i_tasks.add(task)
            task.add_done_callback(self._markdown_t2i_tasks.discard)
            event.stop_event()
            return "Markdown 正在转换为图片并发送。不要再输出原始 Markdown 或重复内容。"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Markdown T2I 任务启动失败: {exc}")
            return "Markdown 转图片任务启动失败，请稍后重试。"

    async def _render_markdown_with_astrbot_t2i(
        self,
        event: AstrMessageEvent,
        markdown_content: str,
    ) -> None:
        try:
            source = await self.html_render(
                MARKDOWN_T2I_TEMPLATE,
                {"content": markdown_to_html(markdown_content)},
                options={},
            )
            if not source:
                raise ValueError("AstrBot T2I 未返回图片")
            image = (
                Image.fromURL(source)
                if source.startswith(("http://", "https://"))
                else Image.fromFileSystem(source)
            )
            await event.send(
                MessageChain([Reply(id=event.message_obj.message_id), image])
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Markdown T2I 渲染失败: {exc}")
            try:
                await event.send(MessageChain([Plain(f"Markdown 转图片失败：{exc}")]))
            except Exception as send_exc:
                logger.warning(f"Markdown T2I 失败消息发送失败: {send_exc}")

    @filter.command("群规")
    async def send_group_rules(self, event: AstrMessageEvent):
        async for result in handle_send_group_rules(event, self._group_rules_image_service):
            yield result

    @filter.command("更新群规图片")
    async def update_group_rules_image(self, event: AstrMessageEvent):
        async for result in handle_update_group_rules_image(
            event,
            self._group_rules_image_service,
        ):
            yield result

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def remind_new_member_to_read_rules(self, event: AstrMessageEvent):
        async for result in handle_new_member_notice(event, self._group_rules_image_service):
            yield result
