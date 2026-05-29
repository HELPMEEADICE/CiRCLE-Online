import logging
import re
import sys
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from datetime import datetime


_VOLATILE_MEDIA_KEYS = {
    "url",
    "preview",
    "preview_url",
    "thumb",
    "thumb_url",
    "path",
    "base64",
}

_CQ_CODE_RE = re.compile(r"\[CQ:([^,\]]+)((?:,[^\]]*)?)\]")


def _stable_media_value(value):
    if value is None:
        return ""
    text = str(value).replace("\\", "/")
    parsed = urlparse(text)
    if parsed.query:
        query = parse_qs(parsed.query)
        for key in ("file", "file_id", "md5", "emoji_id"):
            if query.get(key):
                return query[key][0]
    text = text.split("?", 1)[0]
    return text.rsplit("/", 1)[-1]


def _parse_cq_params(params_text: str) -> dict[str, str]:
    params = {}
    for item in params_text.lstrip(",").split(","):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        params[key] = value
    return params


def normalize_raw_message_for_dedup(raw_message: str) -> str:
    """Normalize raw CQ text for cross-port deduplication.

    Reply CQ ids and media URLs can differ between NapCat endpoints for the same
    logical group message. Keep stable target/media identity and user text only.
    """
    def replace(match):
        cq_type = match.group(1)
        params = _parse_cq_params(match.group(2))

        if cq_type == "reply":
            return "[CQ:reply]"
        if cq_type == "at":
            qq = params.get("qq", "")
            return f"[CQ:at,qq={qq}]" if qq else "[CQ:at]"
        if cq_type == "image":
            identity = []
            for key in ("file_unique", "file_id", "md5", "sha", "file", "summary", "sub_type"):
                value = params.get(key)
                if value:
                    identity.append(f"{key}={_stable_media_value(value)}")
            if not identity:
                identity = [
                    f"{key}={_stable_media_value(value)}"
                    for key, value in sorted(params.items())
                    if key not in _VOLATILE_MEDIA_KEYS and value
                ]
            return f"[CQ:image,{','.join(identity)}]" if identity else "[CQ:image]"
        if cq_type in {"face", "mface"}:
            identity = []
            for key in ("id", "raw_id", "emoji_id", "key", "summary", "name", "text"):
                value = params.get(key)
                if value:
                    identity.append(f"{key}={_stable_media_value(value)}")
            return f"[CQ:{cq_type},{','.join(identity)}]" if identity else f"[CQ:{cq_type}]"

        stable_params = [
            f"{key}={_stable_media_value(value)}"
            for key, value in sorted(params.items())
            if key not in _VOLATILE_MEDIA_KEYS and value
        ]
        return f"[CQ:{cq_type},{','.join(stable_params)}]" if stable_params else f"[CQ:{cq_type}]"

    return _CQ_CODE_RE.sub(replace, raw_message or "")


def setup_logging(level: str = "INFO", log_file: str = ""):
    log_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def parse_message_text(message_segments: list[dict]) -> str:
    parts = []
    for seg in message_segments:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})
        if seg_type == "text":
            parts.append(data.get("text", ""))
        elif seg_type == "at":
            qq = data.get("qq", "")
            if qq == "all":
                parts.append("@全体成员")
            else:
                parts.append(f"@{qq}")
        elif seg_type == "image":
            parts.append("[图片]")
        elif seg_type == "voice":
            parts.append("[语音]")
        elif seg_type == "video":
            parts.append("[视频]")
        elif seg_type == "file":
            parts.append(f"[文件: {data.get('name', 'unknown')}]")
        elif seg_type == "reply":
            parts.append("[回复]")
        elif seg_type == "face":
            face_id = data.get("id", "") or data.get("raw_id", "")
            parts.append(f"[表情:{face_id}]" if face_id else "[表情]")
        elif seg_type == "mface":
            emoji_id = data.get("emoji_id", "") or data.get("id", "") or data.get("key", "")
            parts.append(f"[表情包:{emoji_id}]" if emoji_id else "[表情包]")
        else:
            parts.append(f"[{seg_type}]")
    return "".join(parts)


def normalize_message_segments_for_dedup(message_segments: list[dict]) -> tuple:
    """Build a stable message-segment identity for cross-port deduplication.

    Media URLs and local temp paths can differ per NapCat endpoint, while file/id/md5
    fields identify the same QQ image or sticker across endpoints.
    """
    normalized = []
    for seg in message_segments:
        seg_type = seg.get("type", "")
        data = seg.get("data", {}) or {}

        if seg_type == "image":
            identity = []
            for key in ("file_unique", "file_id", "md5", "sha", "file", "summary", "sub_type"):
                value = data.get(key)
                if value:
                    identity.append((key, _stable_media_value(value)))
            if not identity:
                identity = [
                    (key, _stable_media_value(value))
                    for key, value in sorted(data.items())
                    if key not in _VOLATILE_MEDIA_KEYS and value
                ]
            normalized.append((seg_type, tuple(identity)))
        elif seg_type in {"face", "mface"}:
            identity = []
            for key in ("id", "raw_id", "emoji_id", "key", "summary", "name", "text"):
                value = data.get(key)
                if value:
                    identity.append((key, _stable_media_value(value)))
            normalized.append((seg_type, tuple(identity)))
        elif seg_type == "reply":
            normalized.append((seg_type, ()))
        else:
            normalized.append((seg_type, tuple(sorted((str(k), str(v)) for k, v in data.items()))))

    return tuple(normalized)


def normalize_media_identity_values(values: list[str] | None) -> tuple[str, ...]:
    return tuple(_stable_media_value(value) for value in (values or []) if value)


def extract_image_urls(message_segments: list[dict]) -> list[str]:
    urls = []
    for seg in message_segments:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})
        if seg_type in {"image", "mface"}:
            url = data.get("url", "") or data.get("preview", "") or data.get("file", "")
            if url:
                urls.append(url)
    return urls


def resolve_at_mentions(raw_message: str, character_names: list[str], bot_qq_map: dict[str, str], qq_name_map: dict[str, str] = None) -> str:
    """Convert @mentions to [对X说] format to prevent LLM confusing who is speaking.

    - @character_name followed by delimiters → [对character_name说]
    - @qq_number followed by delimiters → [对character_name说] (if in bot_qq_map)
    - @qq_nickname followed by delimiters → [对character_name说] (if in qq_name_map)
    """
    result = raw_message

    for char_name in character_names:
        pattern = f"@{re.escape(char_name)}(?=[：:，,。.！!？? \\t\\n]|$)"
        replacement = f"[对{char_name}说]"
        result = re.sub(pattern, replacement, result)

    for qq_id, char_name in bot_qq_map.items():
        pattern = f"@{re.escape(qq_id)}(?=[：:，,。.！!？? \\t\\n]|$)"
        replacement = f"[对{char_name}说]"
        result = re.sub(pattern, replacement, result)

    if qq_name_map:
        for qq_name, char_name in qq_name_map.items():
            if qq_name and qq_name != char_name:
                pattern = f"@{re.escape(qq_name)}(?=[：:，,。.！!？? \\t\\n]|$)"
                replacement = f"[对{char_name}说]"
                result = re.sub(pattern, replacement, result)

    return result


def build_text_message(text: str) -> list[dict]:
    return [{"type": "text", "data": {"text": text}}]
