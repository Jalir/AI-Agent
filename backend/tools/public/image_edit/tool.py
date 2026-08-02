"""图像编辑：SiliconFlow / 火山方舟，基于 1～3 张参考图按 prompt 出图。"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from langchain_core.tools import tool

from backend.common.tool_outcome import format_tool_user_message
from backend.config import settings

logger = logging.getLogger(__name__)

TOOL_NAME = "image_edit"
_MAX_IMAGES = 3


def _images_endpoint() -> str:
    base = (settings.image_edit_base_url or "").rstrip("/") + "/"
    return urljoin(base, "images/generations")


def _resolve_provider() -> str:
    raw = (settings.image_edit_provider or "auto").strip().lower()
    if raw in ("openai", "siliconflow", "ark"):
        return raw
    if raw in ("volc", "volces", "doubao"):
        return "ark"
    base = (settings.image_edit_base_url or "").lower()
    if "siliconflow" in base:
        return "siliconflow"
    if "volces.com" in base or "volcengine" in base:
        return "ark"
    return "openai"


def _build_payload(
    prompt: str,
    refs: list[str],
    provider: str,
) -> dict[str, Any]:
    model = (settings.image_edit_model or "").strip()

    if provider == "siliconflow":
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "num_inference_steps": int(settings.image_edit_num_inference_steps or 20),
            "cfg": float(settings.image_edit_cfg or 4),
            "image": refs[0],
        }
        if len(refs) >= 2:
            payload["image2"] = refs[1]
        if len(refs) >= 3:
            payload["image3"] = refs[2]
        return payload

    # 火山方舟 / OpenAI 兼容：单图 string，多图 array
    default_size = "1K" if provider == "ark" else "1024x1024"
    size_s = (settings.image_edit_size or "").strip() or default_size
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size_s,
        "response_format": "url",
        "image": refs[0] if len(refs) == 1 else refs,
    }
    if provider == "ark":
        payload["watermark"] = bool(settings.image_edit_watermark)
        fmt = (settings.image_edit_output_format or "").strip()
        if fmt:
            payload["output_format"] = fmt
    return payload


def _extract_image_urls(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    urls: list[str] = []
    images = body.get("images")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, dict):
                u = str(item.get("url") or "").strip()
                if u:
                    urls.append(u)
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            u = str(item.get("url") or "").strip()
            if u:
                urls.append(u)
            elif item.get("b64_json"):
                raise RuntimeError(
                    "接口返回了 b64_json 而非 url；请确认 response_format=url 或换模型。"
                )
    return urls


def _normalize_image_urls(
    image: str,
    image2: str = "",
    image3: str = "",
) -> list[str]:
    urls: list[str] = []
    for raw in (image, image2, image3):
        u = (raw or "").strip()
        if u and u not in urls:
            urls.append(u)
        if len(urls) >= _MAX_IMAGES:
            break
    return urls


def image_edit_to_urls(
    prompt: str,
    *,
    image: str,
    image2: str = "",
    image3: str = "",
) -> list[dict[str, str]]:
    """请求图像编辑 API，返回 [{url, name, mime_type}, ...]。"""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("编辑描述为空，请提供 prompt。")

    refs = _normalize_image_urls(image, image2, image3)
    if not refs:
        raise ValueError("至少需要 1 张参考图 URL（最多 3 张）。")
    if len(refs) > _MAX_IMAGES:
        raise ValueError(f"参考图最多 {_MAX_IMAGES} 张。")

    model = (settings.image_edit_model or "").strip()
    if not model:
        raise ValueError(
            "未配置 IMAGE_EDIT_MODEL，无法编辑图像。请在环境变量中设置图像编辑模型 ID。"
        )
    api_key = (settings.image_edit_api_key or "").strip()
    if not api_key:
        raise ValueError("未配置图像编辑 API Key（IMAGE_EDIT_API_KEY）。")
    base = (settings.image_edit_base_url or "").strip()
    if not base:
        raise ValueError("未配置图像编辑接口地址（IMAGE_EDIT_BASE_URL）。")

    provider = _resolve_provider()
    payload = _build_payload(text, refs, provider)

    url = _images_endpoint()
    timeout = float(settings.image_edit_timeout_sec or 120.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.info(
        "image_edit request: provider=%s model=%s refs=%d size=%s prompt=%r",
        provider,
        model,
        len(refs),
        payload.get("size") or "-",
        text[:120],
    )
    t0 = time.perf_counter()
    resp = requests.request("POST", url, json=payload, headers=headers, timeout=timeout)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": (resp.text or "")[:500]}
    if resp.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        detail = ""
        if isinstance(err, dict):
            detail = str(err.get("message") or err)
        elif err:
            detail = str(err)
        else:
            detail = str(body)[:400]
        raise RuntimeError(f"图像编辑接口 HTTP {resp.status_code}: {detail}")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "image_edit done: provider=%s model=%s http=%d elapsed_ms=%d",
        provider,
        model,
        resp.status_code,
        elapsed_ms,
    )

    urls = _extract_image_urls(body)
    if not urls:
        raise RuntimeError(f"图像编辑接口未返回图片 url: {str(body)[:400]}")
    return [
        {
            "url": urls[0],
            "name": f"edited_{time.strftime('%Y%m%d%H%M%S')}.png",
            "mime_type": "image/png",
        }
    ]


def format_image_edit_result(metas: list[dict[str, str]]) -> str:
    n = len(metas)
    return format_tool_user_message(
        f"已完成图像编辑，共 {n} 张结果，界面会展示图片预览。",
        ask="请用一两句友好中文告知用户可以查看下方图片；",
    )


@tool(TOOL_NAME)
def image_edit(
    prompt: str,
    image: str,
    image2: str = "",
    image3: str = "",
) -> str:
    """按参考图编辑出图（须有参考图）。无参考图用 generate_image。

    Args:
        prompt: 编辑指令
        image: 参考图 URL（必填）
        image2: 第 2 张参考图 URL
        image3: 第 3 张参考图 URL
    """
    try:
        metas = image_edit_to_urls(prompt, image=image, image2=image2, image3=image3)
    except ValueError as e:
        from backend.common.errors import tool_user_error

        return tool_user_error("图像编辑", e)
    except Exception as e:
        logger.exception("image_edit failed")
        from backend.common.errors import tool_user_error

        return tool_user_error("图像编辑", e)
    return format_image_edit_result(metas)
