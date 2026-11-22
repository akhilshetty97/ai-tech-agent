"""Send messages to your Telegram chat through the bot."""

import os

import httpx

MAX_CHARS = 4000  # Telegram's limit is 4096 per message; leave room


def send(chunks: list[str]) -> None:
    """Sends each chunk as one message (Telegram HTML formatting). Raises if Telegram refuses one."""
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    for chunk in chunks:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            timeout=30,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        ).json()
        if not response.get("ok"):
            raise RuntimeError(f"Telegram refused the message: {response.get('description')}")


def split(blocks: list[str]) -> list[str]:
    """Packs text blocks into messages under the length limit, never splitting a block."""
    chunks, current = [], ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > MAX_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks
