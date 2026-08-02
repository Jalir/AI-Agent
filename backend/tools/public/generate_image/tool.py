"""通用文生图：SiliconFlow / 火山方舟 / OpenAI 兼容 /images/generations，返回图片 URL。"""

from __future__ import annotations
import time
import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from langchain_core.tools import tool

from backend.common.tool_outcome import format_tool_user_message
from backend.config import settings
from backend.tools.public.generate_image.schema import GenerateImageArgs

logger = logging.getLogger(__name__)

TOOL_NAME = "generate_image"


def _images_endpoint() -> str:
    base = (settings.image_gen_base_url or "").rstrip("/") + "/"
    return urljoin(base, "images/generations")


def _resolve_provider() -> str:
    raw = (settings.image_gen_provider or "auto").strip().lower()
    if raw in ("openai", "siliconflow", "ark"):
        return raw
    if raw in ("volc", "volces", "doubao"):
        return "ark"
    base = (settings.image_gen_base_url or "").lower()
    if "siliconflow" in base:
        return "siliconflow"
    if "volces.com" in base or "volcengine" in base:
        return "ark"
    return "openai"


def _build_payload(prompt: str, size: str, count: int, provider: str) -> dict[str, Any]:
    model = (settings.image_gen_model or "").strip()
    default_size = "1K" if provider == "ark" else "1024x1024"
    size_s = (size or "").strip() or (settings.image_gen_size or default_size).strip()
    size_s = size_s or default_size

    if provider == "siliconflow":
        # SiliconFlow：image_size / batch_size，返回 images[].url
        return {
            "model": model,
            "prompt": prompt,
            "image_size": size_s,
            "batch_size": count,
            "num_inference_steps": int(settings.image_gen_num_inference_steps or 20),
            "guidance_scale": float(settings.image_gen_guidance_scale or 7.5),
        }

    # OpenAI / 火山方舟：size / n / response_format，返回 data[].url
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": count,
        "size": size_s,
        "response_format": "url",
    }
    if provider == "ark":
        payload["watermark"] = bool(settings.image_gen_watermark)
        fmt = (settings.image_gen_output_format or "").strip()
        if fmt:
            payload["output_format"] = fmt
    return payload


def _extract_image_urls(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    urls: list[str] = []
    # SiliconFlow: images[{url}]
    images = body.get("images")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, dict):
                u = str(item.get("url") or "").strip()
                if u:
                    urls.append(u)
    # OpenAI 兼容: data[{url}]
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


def generate_image_to_urls(
    prompt: str,
    *,
    size: str = "",
    n: int = 1,
) -> list[dict[str, str]]:
    """请求文生图 API，返回 [{url, name, mime_type}, ...]。"""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("画面描述为空，请提供 prompt。")

    model = (settings.image_gen_model or "").strip()
    if not model:
        raise ValueError(
            "未配置 IMAGE_GEN_MODEL，无法生图。请在环境变量中设置文生图模型 ID。"
        )
    api_key = (settings.image_gen_api_key or "").strip()
    if not api_key:
        raise ValueError("未配置文生图 API Key（IMAGE_GEN_API_KEY）。")

    try:
        count = max(1, min(int(n or 1), 4))
    except (TypeError, ValueError):
        count = 1
    size_s = (size or "").strip()

    provider = _resolve_provider()
    payload = _build_payload(text, size_s, count, provider)
    url = _images_endpoint()
    timeout = float(settings.image_gen_timeout_sec or 120.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.info(
        "generate_image request: provider=%s model=%s size=%s n=%d prompt=%r",
        provider,
        model,
        payload.get("image_size") or payload.get("size"),
        count,
        text[:120],
    )
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
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
            raise RuntimeError(f"生图接口 HTTP {resp.status_code}: {detail}")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "generate_image done: provider=%s model=%s http=%d elapsed_ms=%d",
        provider,
        model,
        resp.status_code,
        elapsed_ms,
    )

    urls = _extract_image_urls(body)
    if not urls:
        raise RuntimeError(f"生图接口未返回图片 url: {str(body)[:400]}")
    return [
        {
            "url": urls[0],
            "name": f"generated_{time.strftime('%Y%m%d%H%M%S')}.png",
            "mime_type": "image/png",
        }
    ]


def format_image_result(metas: list[dict[str, str]]) -> str:
    n = len(metas)
    return format_tool_user_message(
        f"已生成 {n} 张图片，界面会展示图片预览。",
        ask="请用一两句友好中文告知用户可以查看下方图片；",
    )


@tool(TOOL_NAME, args_schema=GenerateImageArgs)
def generate_image(prompt: str, size: str = "", n: int = 1) -> str:
    """按文字描述生成图片。多条小红书图文请用 make_xhs_pack，勿用本工具画合集。"""
    try:
        metas = generate_image_to_urls(prompt, size=size, n=n)
    except ValueError as e:
        from backend.common.errors import tool_user_error

        return tool_user_error("生图", e)
    except Exception as e:
        logger.exception("generate_image failed")
        from backend.common.errors import tool_user_error

        return tool_user_error("生图", e)
    return format_image_result(metas)
