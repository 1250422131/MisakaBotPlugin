import base64
import tempfile
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from astrbot.api import logger
from astrbot.api.star import Context

from .group_name import SHANGHAI_TIMEZONE, get_special_day_label
from .text_to_image import REQUEST_TIMEOUT, TextToImageGenerator


SPECIAL_GROUP_AVATAR_ENABLED_KEY = "special_group_avatar_enabled"
TARGET_GROUP_ID = 812128563


async def refresh_group_avatar(
    context: Context,
    config: Mapping[str, object],
    bot: Any,
    *,
    self_id: str | None = None,
    today: date | None = None,
) -> str | None:
    """更新特殊日期头像，并在特殊日期结束后的次日恢复日常头像。"""
    if not _is_special_group_avatar_enabled(config):
        return None

    today = today or datetime.now(SHANGHAI_TIMEZONE).date()
    label = get_special_day_label(today)
    if label:
        prompt = _build_special_group_avatar_prompt(label)
    elif get_special_day_label(today - timedelta(days=1)):
        label = "日常"
        prompt = _build_normal_group_avatar_prompt()
    else:
        return None

    image = await TextToImageGenerator.from_config(context, config).generate(
        prompt,
        size="1024x1024",
    )
    avatar_path = await _save_generated_avatar(image.source)
    try:
        await _set_group_avatar(bot, avatar_path, self_id=self_id)
    finally:
        avatar_path.unlink(missing_ok=True)

    logger.info(f"已将群 {TARGET_GROUP_ID} 头像更新为 {label} 主题 22、33 娘形象")
    return label


def _is_special_group_avatar_enabled(config: Mapping[str, object]) -> bool:
    value = config.get(SPECIAL_GROUP_AVATAR_ENABLED_KEY, True)
    return value if isinstance(value, bool) else True


def _build_special_group_avatar_prompt(label: str) -> str:
    return f"""Use case: illustration-story
Asset type: QQ group avatar, square 1:1
Primary request: 为哔哩哔哩 {label} 制作群头像。画面中必须同时出现哔哩哔哩 22 娘和 33 娘，两人都必须清晰可辨、形象特征鲜明。
Subject: 22 娘与 33 娘，日系二次元官方吉祥物风格，Q版大头身、友好笑容、蓝白黑主色服装和发饰，保留两人各自不同的发色、发型与服装辨识度。
Scene/backdrop: {label} 对应的中国节日或二十四节气主题地点和小道具，地点清晰可辨，主题一眼可见，但不得遮挡人物。
Style/medium: 精致清晰的日系 Q 版插画，适合缩小为群头像。
Composition/framing: 严格正方形 1:1 构图，22 娘和 33 娘肩并肩站在画面中央，两人从头部至少完整显示到腿部，不能画成只有头肩的头像；人物姿势、背景地点和前景道具形成完整构图。
Constraints: 只出现 22 娘和 33 娘两名角色；无文字、无 logo、无水印、无边框、无二维码、无其他人物；不要写实照片风格。"""


def _build_normal_group_avatar_prompt() -> str:
    return """Use case: illustration-story
Asset type: QQ group avatar, square 1:1
Primary request: 制作哔哩哔哩 22 娘和 33 娘一起出现的日常可爱群头像。两人必须清晰可辨、形象特征鲜明。
Subject: 22 娘与 33 娘，日系二次元官方吉祥物风格，Q版大头身、友好笑容、蓝白黑主色服装和发饰，保留两人各自不同的发色、发型与服装辨识度。
Scene/backdrop: 温暖明亮的二次元社区花园，能看到石板小径、长椅、花坛和远处建筑，作为清晰且完整的日常背景地点。
Style/medium: 精致清晰的日系 Q 版插画，适合缩小为群头像。
Composition/framing: 严格正方形 1:1 构图，22 娘和 33 娘肩并肩站在画面中央，两人从头部至少完整显示到腿部，不能画成只有头肩的头像；人物姿势、背景地点和前景花草形成完整构图。
Constraints: 只出现 22 娘和 33 娘两名角色；无节日、节气或季节性装饰；无文字、无 logo、无水印、无边框、无二维码、无其他人物；不要写实照片风格。"""


async def _save_generated_avatar(source: str) -> Path:
    suffix = ".png"
    avatar_file = tempfile.NamedTemporaryFile(
        prefix="misaka-special-group-avatar-",
        suffix=suffix,
        delete=False,
    )
    avatar_path = Path(avatar_file.name)
    try:
        if source.startswith("base64://"):
            avatar_file.write(base64.b64decode(source.removeprefix("base64://")))
        else:
            avatar_file.close()
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                async with session.get(source) as response:
                    response.raise_for_status()
                    avatar_path.write_bytes(await response.read())
            return avatar_path
        avatar_file.close()
        return avatar_path
    except Exception:
        avatar_file.close()
        avatar_path.unlink(missing_ok=True)
        raise


async def _set_group_avatar(
    bot: Any,
    avatar_path: Path,
    *,
    self_id: str | None,
) -> None:
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
        params: dict[str, Any] = {
            "group_id": TARGET_GROUP_ID,
            "file": str(avatar_path),
        }
        if candidate_self_id:
            params["self_id"] = candidate_self_id
        try:
            await bot.call_action("set_group_portrait", **params)
            return
        except Exception as exc:
            last_error = exc
            logger.debug(
                f"机器人 {candidate_self_id or '未指定'} 更新群头像失败: {exc}"
            )

    raise RuntimeError(
        f"所有候选 OneBot 连接均未能修改群 {TARGET_GROUP_ID} 的群头像"
    ) from last_error
