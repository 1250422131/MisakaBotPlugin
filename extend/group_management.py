import asyncio
import base64
import binascii
import io
import json
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp
from PIL import Image as PillowImage
from PIL import UnidentifiedImageError
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .group_avatar import refresh_group_avatar
from .group_name import build_group_name


GROUP_RULES_URL = "https://misakamoe.com/keys"
GROUP_RULES_FILE_ID = "112312478065"
GROUP_RULES_EXPORT_URL = "https://vas-api.kdocs.cn/export/api/v1/image/async/export"
GROUP_RULES_IMAGE_FILENAME = "group_rules.jpg"
TARGET_GROUP_ID = 812128563
WPS_WEB_COOKIE_KEY = "wps_web_cookie"
WPS_EXPORT_TIMEOUT = aiohttp.ClientTimeout(total=90)
MAX_GROUP_RULES_IMAGE_SIZE = 4 * 1024 * 1024


class GroupRulesImageService:
    """导出、压缩并管理本地群规图片缓存。"""

    def __init__(self, plugin_name: str, config: Mapping[str, object]):
        self._config = config
        self._image_path = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / plugin_name
            / GROUP_RULES_IMAGE_FILENAME
        )
        self._update_lock = asyncio.Lock()

    def cached_image_path(self) -> Path | None:
        return self._image_path if self._image_path.is_file() else None

    async def update_image(self) -> Path:
        cookie = _get_wps_cookie(self._config)
        if not cookie:
            raise ValueError("请先配置 WPS网页Cookie")

        async with self._update_lock:
            image_data = await self._export_image(cookie)
            return await self._save_image(image_data)

    async def save_image(self, image_data: bytes) -> Path:
        async with self._update_lock:
            return await self._save_image(image_data)

    async def _save_image(self, image_data: bytes) -> Path:
        await asyncio.to_thread(self._compress_image, image_data)
        return self._image_path

    async def _export_image(self, cookie: str) -> bytes:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cookie": cookie,
            "Origin": "https://www.kdocs.cn",
            "Referer": f"https://www.kdocs.cn/l/{GROUP_RULES_FILE_ID}",
            "X-Biz-Clients": "1",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
        }
        payload = {
            "format": "png",
            "file_id": GROUP_RULES_FILE_ID,
            "client_id": secrets.token_hex(16),
            "options": {
                "dpi": 96,
                "combine2_long_pic": True,
                "from_page": 1,
                "to_page": 15,
            },
            "file_base64": "",
            "file_name": "",
            "with_dw_event": True,
            "action": "export",
            "mask": "default",
        }

        async with aiohttp.ClientSession(timeout=WPS_EXPORT_TIMEOUT) as session:
            async with session.post(
                GROUP_RULES_EXPORT_URL,
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                if response.content_type.startswith("image/"):
                    return await response.read()
                response_data = _parse_export_response(await response.text())

            image_source = _find_image_source(response_data)
            if image_source is None:
                raise ValueError("WPS 未返回可用的群规图片")
            if image_source.startswith("data:image/"):
                return _decode_data_url(image_source)

            async with session.get(image_source, headers={"Cookie": cookie}) as response:
                response.raise_for_status()
                return await response.read()

    def _compress_image(self, image_data: bytes) -> None:
        self._image_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._image_path.with_suffix(".tmp")
        try:
            temporary_path.write_bytes(_compress_image_data(image_data))
            temporary_path.replace(self._image_path)
        finally:
            temporary_path.unlink(missing_ok=True)


def _compress_image_data(image_data: bytes) -> bytes:
    try:
        with PillowImage.open(io.BytesIO(image_data)) as source:
            source.load()
            image = source.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("附件不是有效图片") from exc

    compressed_data = image_data
    for attempt in range(3):
        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=80 - attempt * 20,
            optimize=True,
            progressive=True,
        )
        compressed_data = output.getvalue()
        if len(compressed_data) <= MAX_GROUP_RULES_IMAGE_SIZE:
            return compressed_data

        width, height = image.size
        image = image.resize(
            (max(1, round(width * 0.8)), max(1, round(height * 0.8))),
            PillowImage.Resampling.LANCZOS,
        )

    return compressed_data


def _get_wps_cookie(config: Mapping[str, object]) -> str:
    value = config.get(WPS_WEB_COOKIE_KEY, "")
    if not isinstance(value, str):
        return ""
    return value.removeprefix("Cookie:").replace("\n", "").strip()


def _find_image_source(response_data: object) -> str | None:
    if isinstance(response_data, str):
        if response_data.startswith(("data:image/", "http://", "https://")):
            return response_data
        return None
    if isinstance(response_data, list):
        for value in response_data:
            source = _find_image_source(value)
            if source:
                return source
        return None
    if not isinstance(response_data, dict):
        return None

    for key in (
        "url",
        "image_url",
        "imageUrl",
        "download_url",
        "downloadUrl",
        "file_url",
        "fileUrl",
        "path",
        "src",
        "img",
    ):
        value = response_data.get(key)
        if isinstance(value, str) and value.startswith(
            ("data:image/", "http://", "https://")
        ):
            return value
    for key in ("data", "result", "image", "image_data", "img_list"):
        source = _find_image_source(response_data.get(key))
        if source:
            return source
    return None


def _parse_export_response(response_text: str) -> object:
    for line in response_text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            response_data = json.loads(line.removeprefix("data:").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("WPS 返回的导出结果无效") from exc
        if not isinstance(response_data, dict):
            raise ValueError("WPS 返回的导出结果无效")
        if response_data.get("code") != "0":
            raise ValueError(f"WPS 图片导出失败：{response_data.get('msg', '未知错误')}")
        return response_data
    raise ValueError("WPS 未返回导出结果")


def _decode_data_url(source: str) -> bytes:
    _, separator, encoded = source.partition(",")
    if not separator or not encoded:
        raise ValueError("WPS 返回的图片数据无效")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("WPS 返回的图片数据无效") from exc


async def refresh_group_name_at_midnight(
    context: Context,
    config: Mapping[str, object],
) -> None:
    platforms = [
        platform
        for platform in context.platform_manager.get_insts()
        if platform.meta().name == "aiocqhttp"
    ]
    if not platforms:
        logger.warning("未找到 aiocqhttp 平台，无法更新群名")
        return

    last_error: Exception | None = None
    for platform in platforms:
        try:
            await set_group_name(platform.get_client())
        except Exception as exc:
            last_error = exc
            logger.debug(
                f"aiocqhttp 平台 {platform.meta().id} 更新群名失败: {exc}"
            )
            continue
        try:
            await refresh_group_avatar(
                context,
                config,
                platform.get_client(),
            )
        except Exception as exc:
            logger.warning(f"群头像更新失败: {exc}")
        return

    raise RuntimeError("所有 aiocqhttp 平台均未能修改目标群群名") from last_error


async def handle_refresh_group_name(
    event: AstrMessageEvent,
    context: Context,
    config: Mapping[str, object],
):
    if str(event.get_group_id()) != str(TARGET_GROUP_ID):
        yield event.plain_result("该指令只能在本群使用。")
        return

    event_self_id = getattr(event.message_obj, "self_id", None)
    try:
        group_name = await set_group_name(
            event.bot,
            self_id=str(event_self_id) if event_self_id else None,
        )
    except Exception as exc:
        logger.exception(f"手动更新群名失败: {exc}")
        yield event.plain_result("群名更新失败，请确认机器人具备群管理权限。")
        return

    try:
        avatar_label = await refresh_group_avatar(
            context,
            config,
            event.bot,
            self_id=str(event_self_id) if event_self_id else None,
        )
    except Exception as exc:
        logger.warning(f"手动更新群头像失败: {exc}")
        yield event.plain_result(f"群名已更新为：{group_name}；群头像更新失败。")
        return

    if avatar_label:
        yield event.plain_result(
            f"群名已更新为：{group_name}；群头像已更新为 {avatar_label} 主题。"
        )
        return

    yield event.plain_result(f"群名已更新为：{group_name}")


async def handle_sync_wps_group_rules(
    event: AstrMessageEvent,
    image_service: GroupRulesImageService,
):
    yield event.plain_result("正在同步 WPS 群规，请稍候...")
    try:
        image_path = await image_service.update_image()
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        logger.warning(f"同步 WPS 群规失败: {exc}")
        yield event.plain_result("WPS 群规同步失败，请检查 WPS网页Cookie 配置后重试。")
        return
    except Exception as exc:
        logger.exception(f"同步 WPS 群规失败: {exc}")
        yield event.plain_result("WPS 群规同步失败，请稍后重试。")
        return

    yield event.chain_result(
        [Plain("WPS 群规已同步完成。"), Image.fromFileSystem(str(image_path))]
    )


async def handle_update_group_rules_image(
    event: AstrMessageEvent,
    image_service: GroupRulesImageService,
):
    image_source = _find_message_image(event)
    if image_source is None:
        yield event.plain_result("请在命令后附带图片，或回复一张图片后再发送该命令。")
        return

    try:
        image_data = await _load_image_data(image_source)
        await image_service.save_image(image_data)
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        logger.warning(f"更新群规图片失败: {exc}")
        yield event.plain_result("群规图片更新失败，请确认图片可访问后重试。")
        return
    except Exception as exc:
        logger.exception(f"更新群规图片失败: {exc}")
        yield event.plain_result("群规图片更新失败，请稍后重试。")
        return

    yield event.plain_result("群规图片已更新完成。")


def _find_message_image(event: AstrMessageEvent) -> str | None:
    for component in event.get_messages():
        if _is_image_or_file_component(component):
            source = _component_source(component)
            if source:
                return str(source)
        elif isinstance(component, Reply) and component.chain:
            for reply_component in component.chain:
                if _is_image_or_file_component(reply_component):
                    source = _component_source(reply_component)
                    if source:
                        return str(source)
    return None


def _is_image_or_file_component(component: object) -> bool:
    return isinstance(component, Image) or component.__class__.__name__ == "File"


def _component_source(component: object) -> object | None:
    return getattr(component, "url", None) or getattr(component, "file", None)


async def _load_image_data(source: str) -> bytes:
    if source.startswith("base64://"):
        try:
            return base64.b64decode(source.removeprefix("base64://"), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("图片数据无效") from exc

    if source.startswith(("http://", "https://")):
        async with aiohttp.ClientSession(timeout=WPS_EXPORT_TIMEOUT) as session:
            async with session.get(source) as response:
                response.raise_for_status()
                return await response.read()

    image_path = Path(
        unquote(urlparse(source).path if source.startswith("file://") else source)
    )
    return await asyncio.to_thread(image_path.read_bytes)


async def handle_send_group_rules(
    event: AstrMessageEvent,
    image_service: GroupRulesImageService,
):
    image_path = image_service.cached_image_path()
    if image_path:
        yield event.chain_result([Image.fromFileSystem(str(image_path))])
        return
    yield event.plain_result(f"本群群规：{GROUP_RULES_URL}")


async def handle_new_member_notice(
    event: AstrMessageEvent,
    image_service: GroupRulesImageService,
):
    raw_event = event.message_obj.raw_message
    if (
        raw_event.get("post_type") != "notice"
        or raw_event.get("notice_type") != "group_increase"
        or str(raw_event.get("user_id")) == str(raw_event.get("self_id"))
    ):
        return

    image_path = image_service.cached_image_path()
    components: list[object] = [At(qq=event.get_sender_id())]
    if image_path:
        components.extend(
            [Plain("请务必阅读本群群规："), Image.fromFileSystem(str(image_path))]
        )
    else:
        components.append(Plain(f"请务必阅读本群群规：{GROUP_RULES_URL}"))
    yield event.chain_result(components)


async def set_group_name(bot: Any, self_id: str | None = None) -> str:
    group_name = build_group_name()
    connected_clients = getattr(bot, "_wsr_api_clients", {})
    connected_self_ids = (
        tuple(str(client_self_id) for client_self_id in connected_clients)
        if isinstance(connected_clients, dict)
        else ()
    )
    candidate_self_ids: tuple[str | None, ...] = (
        (self_id,) if self_id else connected_self_ids or (None,)
    )
    last_error: Exception | None = None

    for candidate_self_id in candidate_self_ids:
        params = {
            "group_id": TARGET_GROUP_ID,
            "group_name": group_name,
        }
        if candidate_self_id:
            params["self_id"] = candidate_self_id

        try:
            await bot.call_action("set_group_name", **params)
        except Exception as exc:
            last_error = exc
            logger.debug(
                f"机器人 {candidate_self_id or '未指定'} 更新群名失败: {exc}"
            )
            continue

        logger.info(
            f"机器人 {candidate_self_id or '自动选择'} 已更新群 "
            f"{TARGET_GROUP_ID} 名称为：{group_name}"
        )
        return group_name

    raise RuntimeError(
        f"所有候选 OneBot 连接均未能修改群 {TARGET_GROUP_ID} 的群名"
    ) from last_error
