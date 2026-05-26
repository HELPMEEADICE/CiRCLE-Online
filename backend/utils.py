import logging
import re
import sys
from pathlib import Path
from datetime import datetime


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
            parts.append("[表情]")
        else:
            parts.append(f"[{seg_type}]")
    return "".join(parts)


def extract_image_urls(message_segments: list[dict]) -> list[str]:
    urls = []
    for seg in message_segments:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})
        if seg_type == "image":
            url = data.get("url", "") or data.get("file", "")
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


def timestamp_now() -> int:
    return int(datetime.now().timestamp())
