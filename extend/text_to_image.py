import base64
import asyncio
import io
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
from PIL import Image as PillowImage


TEXT_TO_IMAGE_API_HOST_KEY = "text_to_image_api_host"
TEXT_TO_IMAGE_API_KEY_KEY = "text_to_image_api_key"
GENERATIONS_PATH = "/v1/images/generations"
EDITS_PATH = "/v1/images/edits"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "high"
DEFAULT_OUTPUT_FORMAT = "png"
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
    """调用 Packy 兼容的文生图和图文生图接口。"""

    def __init__(self, api_host: str, api_key: str):
        self._api_host = self._validate_api_host(api_host)
        self._api_key = self._validate_api_key(api_key)

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "TextToImageGenerator":
        api_host = config.get(TEXT_TO_IMAGE_API_HOST_KEY, "")
        api_key = config.get(TEXT_TO_IMAGE_API_KEY_KEY, "")
        return cls(
            api_host=api_host if isinstance(api_host, str) else "",
            api_key=api_key if isinstance(api_key, str) else "",
        )

    async def generate(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL,
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

        payload: dict[str, str | int] = {
            "model": model,
            "prompt": normalized_prompt,
            "size": size,
            "quality": quality,
            "output_format": output_format,
            "response_format": "url",
            "n": count,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "*/*",
        }

        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            if input_images is None:
                response_data = await self._generate_from_text(session, headers, payload)
            else:
                response_data = await self._generate_from_image(
                    session,
                    headers,
                    payload,
                    input_images,
                )

        return self._parse_response(response_data)

    @staticmethod
    def _validate_api_host(api_host: str) -> str:
        normalized_host = api_host.strip().rstrip("/")
        parsed_host = urlparse(normalized_host)
        if (
            parsed_host.scheme not in {"http", "https"}
            or not parsed_host.netloc
            or parsed_host.path
            or parsed_host.params
            or parsed_host.query
            or parsed_host.fragment
        ):
            raise ValueError("请先在插件配置中填写有效的文生图服务 Host")
        return normalized_host

    @staticmethod
    def _validate_api_key(api_key: str) -> str:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("请先在插件配置中填写文生图 API Key")
        return normalized_key

    async def _generate_from_text(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        payload: dict[str, str | int],
    ) -> object:
        async with session.post(
            f"{self._api_host}{GENERATIONS_PATH}",
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _generate_from_image(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        payload: dict[str, str | int],
        input_images: list[InputImage],
    ) -> object:
        if not input_images:
            raise ValueError("图文生图的输入图片不能为空")

        form = aiohttp.FormData()
        for key, value in payload.items():
            form.add_field(key, str(value))
        for image in input_images:
            form.add_field(
                "image",
                image.data,
                filename=image.filename,
                content_type=image.content_type,
            )
        async with session.post(
            f"{self._api_host}{EDITS_PATH}",
            data=form,
            headers=headers,
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    @staticmethod
    def _parse_response(response_data: object) -> GeneratedImage:
        if not isinstance(response_data, dict):
            raise ValueError("文生图接口返回格式错误")

        images = response_data.get("data")
        if not isinstance(images, list) or not images or not isinstance(images[0], dict):
            raise ValueError("文生图接口未返回图片")

        image = images[0]
        image_url = image.get("url")
        if isinstance(image_url, str) and image_url:
            return GeneratedImage(source=image_url)

        b64_json = image.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            try:
                base64.b64decode(b64_json, validate=True)
            except ValueError as exc:
                raise ValueError("文生图接口返回了无效的图片数据") from exc
            return GeneratedImage(source=f"base64://{b64_json}")

        raise ValueError("文生图接口未返回可用图片")
