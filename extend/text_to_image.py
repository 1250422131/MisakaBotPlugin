import base64
import asyncio
import io
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp
from PIL import Image as PillowImage
from astrbot.api.star import Context


TEXT_TO_IMAGE_PROVIDER_ID_KEY = "text_to_image_provider_id"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "high"
DEFAULT_OUTPUT_FORMAT = "png"
IMAGE_GENERATION_TIMEOUT_SECONDS = 120
MAX_OUTPUT_EDGE = 2048
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=300)


@dataclass(frozen=True)
class GeneratedImage:
    """文生图接口返回的首张图片。"""

    source: str


@dataclass(frozen=True)
class InputImage:
    """提交给图文生图接口的图片文件。"""

    data: bytes
    filename: str
    content_type: str


def output_size_for_images(images: list[InputImage]) -> str:
    """按最大原图的比例生成输出尺寸，最长边限制为 2048 像素。"""
    if not images:
        raise ValueError("至少需要一张输入图片")

    dimensions = [_image_dimensions(image) for image in images]
    width, height = max(dimensions, key=lambda size: size[0] * size[1])

    return _limit_output_size(width, height)


def _image_dimensions(image: InputImage) -> tuple[int, int]:
    with PillowImage.open(io.BytesIO(image.data)) as source_image:
        return source_image.size


def _limit_output_size(width: int, height: int) -> str:

    if width <= 0 or height <= 0:
        raise ValueError("输入图片尺寸无效")

    longest_edge = max(width, height)
    if longest_edge > MAX_OUTPUT_EDGE:
        scale = MAX_OUTPUT_EDGE / longest_edge
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))

    return f"{width}x{height}"


async def load_input_image(
    session: aiohttp.ClientSession, source: str
) -> InputImage:
    """读取 AstrBot 图片组件的 URL、Base64 或本地文件来源。"""
    if source.startswith("base64://"):
        image_data = base64.b64decode(source.removeprefix("base64://"))
        return InputImage(image_data, "input.png", "image/png")

    if source.startswith(("http://", "https://")):
        async with session.get(source) as response:
            response.raise_for_status()
            image_data = await response.read()
            content_type = response.content_type or "image/png"
        filename = Path(urlparse(source).path).name or "input.png"
        return InputImage(image_data, filename, content_type)

    file_path = Path(
        unquote(urlparse(source).path if source.startswith("file://") else source)
    )
    image_data = await asyncio.to_thread(file_path.read_bytes)
    content_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
    return InputImage(image_data, file_path.name or "input.png", content_type)


class TextToImageGenerator:
    """复用 AstrBot 已配置的 OpenAI 兼容服务商生成图片。"""

    def __init__(self, context: Context, provider_id: str):
        self._context = context
        self._provider_id = provider_id.strip()

    @classmethod
    def from_config(
        cls,
        context: Context,
        config: Mapping[str, object],
    ) -> "TextToImageGenerator":
        provider_id = config.get(TEXT_TO_IMAGE_PROVIDER_ID_KEY, "")
        return cls(context, provider_id if isinstance(provider_id, str) else "")

    async def generate(
        self,
        prompt: str,
        *,
        size: str = DEFAULT_SIZE,
        quality: str = DEFAULT_QUALITY,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
        count: int = 1,
        input_images: list[InputImage] | None = None,
    ) -> GeneratedImage:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("文生图提示词不能为空")
        if count < 1:
            raise ValueError("文生图数量必须大于 0")

        provider = self._get_provider()
        client = self._get_image_client(provider)
        model = self._get_model(provider)

        if input_images is None:
            response = await client.images.generate(
                model=model,
                prompt=normalized_prompt,
                size=size,
                quality=quality,
                output_format=output_format,
                n=count,
                timeout=IMAGE_GENERATION_TIMEOUT_SECONDS,
            )
        else:
            response = await self._generate_from_image(
                client,
                model,
                normalized_prompt,
                size,
                quality,
                output_format,
                count,
                input_images,
            )

        return self._parse_response(response)

    def _get_provider(self) -> Any:
        if not self._provider_id:
            raise ValueError("请先在插件配置中选择文生图 AI 服务商")

        provider = self._context.get_provider_by_id(provider_id=self._provider_id)
        if provider is None:
            raise ValueError(f"未找到文生图 AI 服务商: {self._provider_id}")
        return provider

    @staticmethod
    def _get_image_client(provider: Any) -> Any:
        client = getattr(provider, "client", None)
        images = getattr(client, "images", None)
        if images is None or not callable(getattr(images, "generate", None)):
            raise ValueError(
                "所选服务商不支持 OpenAI Images 接口，请选择支持图片生成的 OpenAI 兼容服务商"
            )
        return client

    @staticmethod
    def _get_model(provider: Any) -> str:
        get_model = getattr(provider, "get_model", None)
        model = get_model() if callable(get_model) else ""
        if not isinstance(model, str) or not model.strip():
            raise ValueError("所选服务商未配置图片生成模型")
        return model

    @staticmethod
    async def _generate_from_image(
        client: Any,
        model: str,
        prompt: str,
        size: str,
        quality: str,
        output_format: str,
        count: int,
        input_images: list[InputImage],
    ) -> object:
        if not input_images:
            raise ValueError("图文生图的输入图片不能为空")

        image_files = [
            (image.filename, image.data, image.content_type) for image in input_images
        ]
        return await client.images.edit(
            model=model,
            prompt=prompt,
            image=image_files,
            size=size,
            quality=quality,
            output_format=output_format,
            n=count,
            timeout=IMAGE_GENERATION_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _parse_response(response_data: object) -> GeneratedImage:
        images = getattr(response_data, "data", None)
        if not isinstance(images, list) or not images:
            raise ValueError("文生图接口未返回图片")

        image = images[0]
        image_url = getattr(image, "url", None)
        if isinstance(image_url, str) and image_url:
            return GeneratedImage(source=image_url)

        b64_json = getattr(image, "b64_json", None)
        if isinstance(b64_json, str) and b64_json:
            try:
                base64.b64decode(b64_json, validate=True)
            except ValueError as exc:
                raise ValueError("文生图接口返回了无效的图片数据") from exc
            return GeneratedImage(source=f"base64://{b64_json}")

        raise ValueError("文生图接口未返回可用图片")
