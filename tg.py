"""Minimal Telegram Bot API client (requests only)."""
import json
import os

import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID") or 0)  # only this chat can approve anything
_API = f"https://api.telegram.org/bot{TOKEN}"


def call(method, **params):
    r = requests.post(f"{_API}/{method}", json=params, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]


def buttons(draft_id):
    return {
        "inline_keyboard": [[
            {"text": "✅ Approve upload", "callback_data": f"up_ok:{draft_id}"},
            {"text": "❌ Reject", "callback_data": f"up_no:{draft_id}"},
        ]]
    }


def send_message(text, reply_markup=None):
    params = {"chat_id": CHAT_ID, "text": text[:4000]}
    if reply_markup:
        params["reply_markup"] = reply_markup
    return call("sendMessage", **params)


def send_video(path, caption, reply_markup):
    with open(path, "rb") as f:
        r = requests.post(
            f"{_API}/sendVideo",
            data={
                "chat_id": CHAT_ID,
                "caption": caption[:1000],
                "reply_markup": json.dumps(reply_markup),
                "supports_streaming": "true",
            },
            files={"video": f},
            timeout=600,
        )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendVideo failed: {data}")
    return data["result"]


def get_updates(offset):
    return call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])


def answer(callback_id):
    try:
        call("answerCallbackQuery", callback_query_id=callback_id)
    except Exception:  # old callbacks expire; not important
        pass


def clear_buttons(chat_id, message_id):
    try:
        call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
             reply_markup={"inline_keyboard": []})
    except Exception:
        pass
