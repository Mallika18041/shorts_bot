
"""Temporary Telegram diagnostic mode.
Receives messages and button taps but NEVER uploads videos.
"""

import json
import os
import sys
from pathlib import Path
import urllib.request

STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID") or 0)


def telegram_call(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip("\"'")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

    url = f"https://api.telegram.org/bot{token}/{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    return result["result"]


def send_message(message):
    if CHAT_ID:
        telegram_call("sendMessage", chat_id=CHAT_ID, text=message[:4000])


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))

    return {"offset": 0, "next_id": 1, "drafts": {}}


def save_state(st):
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(
        json.dumps(st, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, STATE_PATH)


def check():
    """Check whether Telegram has updates without consuming them."""
    st = load_state()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip("\"'")

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

    url = (
        f"https://api.telegram.org/bot{token}/getUpdates"
        f"?offset={st.get('offset', 0)}&timeout=0"
    )

    with urllib.request.urlopen(url, timeout=30) as response:
        updates = json.load(response).get("result", [])

    work = any(
        update.get("callback_query", {}).get("message", {}).get("chat", {}).get("id") == CHAT_ID
        or update.get("message", {}).get("chat", {}).get("id") == CHAT_ID
        for update in updates
    )

    line = f"work={'true' if work else 'false'}"
    print(line)

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(line + "\n")


def poll():
    """Diagnostic-only polling. No video uploads or new Shorts."""
    st = load_state()

    updates = telegram_call(
        "getUpdates",
        offset=st.get("offset", 0),
        timeout=0,
        allowed_updates=["message", "callback_query"],
    )

    print(f"Received {len(updates)} Telegram update(s).")

    for update in updates:
        callback = update.get("callback_query")
        message = update.get("message")

        if callback:
            callback_message = callback.get("message", {})
            chat_id = callback_message.get("chat", {}).get("id")
            callback_data = callback.get("data", "unknown")

            print(
                f"CALLBACK received: chat={chat_id}, "
                f"data={callback_data}"
            )

            try:
                telegram_call(
                    "answerCallbackQuery",
                    callback_query_id=callback["id"],
                    text="Diagnostic tap received",
                )
            except Exception as error:
                print(f"Could not answer callback: {error}")

            if chat_id == CHAT_ID:
                send_message(
                    f"Diagnostic successful: received button tap "
                    f"'{callback_data}'. No video was uploaded."
                )

        elif message:
            chat_id = message.get("chat", {}).get("id")
            message_text = (message.get("text") or "").strip()

            print(
                f"MESSAGE received: chat={chat_id}, "
                f"text={message_text[:50]}"
            )

            if chat_id == CHAT_ID:
                send_message(
                    f"Diagnostic successful: received your message "
                    f"'{message_text[:50]}'. No video was uploaded."
                )

        st["offset"] = update["update_id"] + 1
        save_state(st)

    print(f"Saved Telegram offset: {st.get('offset')}")
    save_state(st)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""

    if command == "check":
        check()
    elif command == "poll":
        poll()
    else:
        print("Usage: python approve.py check")
        print("   or: python approve.py poll")
        sys.exit(1)
