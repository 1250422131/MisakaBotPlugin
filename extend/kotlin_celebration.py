import asyncio
import base64
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image


INPAINT_URL = "https://postcards-service.labs.jb.gg/api/v1/inpaint"
PROGRESS_URL = "https://postcards-service.labs.jb.gg/api/v1/progress"
POLL_INTERVAL_SECONDS = 3
MAX_POLL_COUNT = 60
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def generate_kotlin_celebration(event: AstrMessageEvent) -> str:
    source = _find_message_image(event)
    if source is None:
        source = _qq_avatar_url(event.get_sender_id())

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        image_data, content_type, filename = await _load_image(session, source)
        task_id = await _submit_image(session, image_data, content_type, filename)
        return await _wait_for_result(session, task_id)


def _find_message_image(event: AstrMessageEvent) -> str | None:
    for component in event.get_messages():
        if not isinstance(component, Image):
            continue

        source = getattr(component, "url", None) or getattr(component, "file", None)
        if source:
            return str(source)
    return None


def _qq_avatar_url(sender_id: str) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={sender_id}&s=640"


async def _load_image(
    session: aiohttp.ClientSession, source: str
) -> tuple[bytes, str, str]:
    if source.startswith("base64://"):
        image_data = base64.b64decode(source.removeprefix("base64://"))
        return image_data, "image/jpeg", "input.jpg"

    if source.startswith(("http://", "https://")):
        async with session.get(source) as response:
            response.raise_for_status()
            image_data = await response.read()
            content_type = response.content_type or "image/jpeg"
        filename = Path(urlparse(source).path).name or "input.jpg"
        return image_data, content_type, filename

    file_path = Path(
        unquote(urlparse(source).path if source.startswith("file://") else source)
    )
    image_data = await asyncio.to_thread(file_path.read_bytes)
    content_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    return image_data, content_type, file_path.name or "input.jpg"


async def _submit_image(
    session: aiohttp.ClientSession,
    image_data: bytes,
    content_type: str,
    filename: str,
) -> str:
    form = aiohttp.FormData()
    form.add_field(
        "image",
        image_data,
        filename=filename,
        content_type=content_type,
    )
    async with session.post(INPAINT_URL, data=form) as response:
        response.raise_for_status()
        payload = await response.json()

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("庆生服务未返回任务 ID")
    return task_id


async def _wait_for_result(session: aiohttp.ClientSession, task_id: str) -> str:
    for _ in range(MAX_POLL_COUNT):
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        async with session.get(PROGRESS_URL, params={"id": task_id}) as response:
            response.raise_for_status()
            payload = await response.json()

        if payload.get("state") == "FINISHED" and payload.get("resultUrl"):
            return str(payload["resultUrl"])
        if payload.get("state") not in {"PENDING", "PROCESSING"}:
            raise ValueError(f"庆生服务任务失败: {payload.get('state')}")

    raise asyncio.TimeoutError
