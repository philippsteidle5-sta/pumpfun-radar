"""Owner-bound callback parsing. No network and no token use.

If DESK_OWNER_CHAT_ID is unset, every inbound update is rejected.
Group chats are ignored. A callback must name a mint the desk already tracks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Inbound:
    status: str  # accept | reject | ignore
    reason: str
    action: str | None = None
    mint: str | None = None
    callback_id: str | None = None
    chat_id: str | None = None
    text: str | None = None


def _chat(update: dict) -> tuple[str | None, str | None]:
    message = update.get("message") if isinstance(update.get("message"), dict) else None
    callback = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else None
    if callback and isinstance(callback.get("message"), dict):
        chat = callback["message"].get("chat") if isinstance(callback["message"].get("chat"), dict) else {}
    elif message:
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    else:
        chat = {}
    chat_id = chat.get("id")
    chat_type = chat.get("type") if isinstance(chat.get("type"), str) else None
    return (None if chat_id is None else str(chat_id), chat_type)


def parse_update(update: dict, owner_chat_id: str | None) -> Inbound:
    if not isinstance(update, dict):
        return Inbound("reject", "not an update")
    chat_id, chat_type = _chat(update)
    if chat_type in {"group", "supergroup", "channel"}:
        return Inbound("ignore", "group chat", chat_id=chat_id)
    if not owner_chat_id:
        return Inbound("reject", "owner not configured", chat_id=chat_id)
    if chat_id != str(owner_chat_id):
        return Inbound("reject", "not owner", chat_id=chat_id)
    callback = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else None
    if callback:
        data = callback.get("data") if isinstance(callback.get("data"), str) else ""
        callback_id = callback.get("id") if isinstance(callback.get("id"), str) else None
        if ":" not in data or callback_id is None:
            return Inbound("reject", "callback unbound", chat_id=chat_id, callback_id=callback_id)
        action, mint = data.split(":", 1)
        if action not in {"bought", "skip", "sold"} or not mint:
            return Inbound("reject", "callback unbound", chat_id=chat_id, callback_id=callback_id)
        return Inbound("accept", "ok", action, mint, callback_id, chat_id)
    message = update.get("message") if isinstance(update.get("message"), dict) else None
    text = message.get("text") if message and isinstance(message.get("text"), str) else None
    return Inbound("accept", "ok", chat_id=chat_id, text=text)
