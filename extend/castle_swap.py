import asyncio
from collections.abc import Mapping

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context

from .text_to_image import (
    REQUEST_TIMEOUT,
    TextToImageGenerator,
    load_input_image,
    output_size_for_images,
)


class CastleSwapService:
    """处理王车易位生成任务，并限制同一用户同时只能执行一次。"""

    def __init__(self):
        self._active_user_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def handle(
        self,
        event: AstrMessageEvent,
        context: Context,
        config: Mapping[str, object],
    ):
        prompt = """将两张参考图中的主体融合为一个单独主体，统一最终图像的风格、服装、配饰和画面表现。
最终画面只能出现一个主体，不要并排、拼贴、分屏、镜像、双重主体或多张脸。
若主体是人物，则必须尽可能保留两个人物的特征，可以让人认出是两个人物结合，必须保留第二张参考图的人脸作为最终人脸，并自然融合第一张图的风格与主体特征。"""
        image_sources = [
            str(getattr(component, "url", None) or getattr(component, "file", None))
            for component in event.get_messages()
            if isinstance(component, Image)
            and (getattr(component, "url", None) or getattr(component, "file", None))
        ]
        if len(image_sources) != 2:
            yield event.plain_result("请携带两张图片后再使用王车易位。")
            return

        user_id = str(event.get_sender_id())
        if not await self._reserve_user(user_id):
            yield event.plain_result("正在生成上一张，请耐心等待。")
            return

        try:
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Plain("正在融合两张图片，请稍候..."),
                ]
            )
            generator = TextToImageGenerator.from_config(context, config)
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                input_images = [
                    await load_input_image(session, source) for source in image_sources
                ]
            result = await generator.generate(
                prompt,
                size=output_size_for_images(input_images),
                input_images=input_images,
            )
            if result.source.startswith("base64://"):
                raise ValueError("文生图接口未返回可发送的图片 URL")
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Image.fromURL(result.source),
                ]
            )
        except asyncio.TimeoutError:
            logger.warning("王车易位请求超时")
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Plain("王车易位生成超时，请稍后再试。"),
                ]
            )
            return
        except (aiohttp.ClientError, ValueError, OSError) as exc:
            logger.warning(f"王车易位生成失败: {exc}")
            yield event.chain_result(
                [
                    Reply(id=event.message_obj.message_id),
                    Plain("王车易位生成失败，请检查所选文生图 AI 服务商后重试。"),
                ]
            )
            return
        finally:
            await self._release_user(user_id)

    async def _reserve_user(self, user_id: str) -> bool:
        async with self._lock:
            if user_id in self._active_user_ids:
                return False
            self._active_user_ids.add(user_id)
            return True

    async def _release_user(self, user_id: str) -> None:
        async with self._lock:
            self._active_user_ids.discard(user_id)
