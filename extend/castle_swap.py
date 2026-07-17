import asyncio

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image

from .image_generation import ImageGenerationService


class CastleSwapService:
    """处理王车易位生成任务，并限制同一用户同时只能执行一次。"""

    def __init__(self, image_generation_service: ImageGenerationService):
        self._active_user_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._image_generation_service = image_generation_service

    async def handle(
        self,
        event: AstrMessageEvent,
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
            result = await self._image_generation_service.handle(
                event,
                prompt,
                reference_image_sources=image_sources,
                progress_text="正在融合两张图片，请稍候...",
                reply_id=event.message_obj.message_id,
                on_complete=lambda: self._release_user(user_id),
                stop_event=False,
            )
            if result != "图片正在生成，完成后会自动发送给用户。":
                yield event.plain_result(result)
        except Exception:
            await self._release_user(user_id)
            raise

    async def _reserve_user(self, user_id: str) -> bool:
        async with self._lock:
            if user_id in self._active_user_ids:
                return False
            self._active_user_ids.add(user_id)
            return True

    async def _release_user(self, user_id: str) -> None:
        async with self._lock:
            self._active_user_ids.discard(user_id)
