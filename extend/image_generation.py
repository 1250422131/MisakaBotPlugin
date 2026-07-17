import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context

from .text_to_image import (
    REQUEST_TIMEOUT,
    TextToImageGenerator,
    load_input_image,
    output_size_for_images,
)


class ImageGenerationService:
    """处理文生图工具，并只使用当前消息携带的参考图片。"""

    def __init__(self, context: Context, config: Mapping[str, object]):
        self._context = context
        self._config = config
        self._tasks: set[asyncio.Task[Any]] = set()

    async def handle(
        self,
        event: AstrMessageEvent,
        prompt: str,
        *,
        reference_image_sources: list[str] | None = None,
        progress_text: str = "正在努力绘制...",
        reply_id: str | int | None = None,
        on_complete: Callable[[], Awaitable[None]] | None = None,
        stop_event: bool = True,
    ) -> str:
        """启动图片生成任务，参考图只来自当前消息和当前回复链。"""
        try:
            image_sources = (
                collect_reference_image_sources(event)
                if reference_image_sources is None
                else list(reference_image_sources)
            )
            await event.send(_message_chain(progress_text, reply_id))
            task = asyncio.create_task(
                self._generate_in_background(
                    event, prompt, image_sources, reply_id, on_complete
                )
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            if stop_event:
                event.stop_event()
            return "图片正在生成，完成后会自动发送给用户。"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"图片生成 Tool 调用失败: {exc}")
            if on_complete is not None:
                await on_complete()
            return "图片生成失败，请检查文生图 AI 服务商配置后重试。"

    async def terminate(self) -> None:
        """取消插件卸载时仍在运行的图片生成任务。"""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _generate_in_background(
        self,
        event: AstrMessageEvent,
        prompt: str,
        image_sources: list[str],
        reply_id: str | int | None,
        on_complete: Callable[[], Awaitable[None]] | None,
    ) -> None:
        try:
            generator = TextToImageGenerator.from_config(self._context, self._config)
            if image_sources:
                async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                    input_images = [
                        await load_input_image(session, source)
                        for source in image_sources
                    ]
                result = await generator.generate(
                    prompt,
                    size=output_size_for_images(input_images),
                    input_images=input_images,
                )
            else:
                result = await generator.generate(prompt)

            if result.source.startswith("base64://"):
                image = Image.fromBase64(result.source.removeprefix("base64://"))
            else:
                image = Image.fromURL(result.source)
            await event.send(MessageChain(_components_with_reply(image, reply_id)))
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
        finally:
            if on_complete is not None:
                await on_complete()


def _message_chain(text: str, reply_id: str | int | None) -> MessageChain:
    components: list[object] = []
    if reply_id is not None:
        components.append(Reply(id=reply_id))
    components.append(Plain(text))
    return MessageChain(components)


def _components_with_reply(image: Image, reply_id: str | int | None) -> list[object]:
    if reply_id is None:
        return [image]
    return [Reply(id=reply_id), image]


def collect_reference_image_sources(event: AstrMessageEvent) -> list[str]:
    """收集当前消息图片和当前消息 Reply.chain 中的图片，不读取历史消息。"""
    sources: list[str] = []
    seen: set[str] = set()

    def add_image(component: Image) -> None:
        source = getattr(component, "url", None) or getattr(component, "file", None)
        if not source:
            return
        source_text = str(source)
        if source_text and source_text not in seen:
            seen.add(source_text)
            sources.append(source_text)

    for component in event.get_messages():
        if isinstance(component, Image):
            add_image(component)
        elif isinstance(component, Reply) and component.chain:
            for reply_component in component.chain:
                if isinstance(reply_component, Image):
                    add_image(reply_component)
    return sources
