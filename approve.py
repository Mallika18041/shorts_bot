"""Handles your Telegram taps and commands (/idea, /approve N, /reject N).

  python approve.py check   no extra packages: prints work=true if you tapped or messaged
  python approve.py poll    upload approved videos; make a new Short when you send /idea
  python approve.py act     do one approve/reject sent by the Cloudflare Worker (env ACTION, DRAFT_ID)

You only approve the finished video, before upload. Nothing is uploaded without your tap.
A Telegram error (for example HTTP 400 "query is too old" on a late tap) never stops the approval.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PRIVACY = os.environ.get("PRIVACY") or "public"  # public, unlisted or private
STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID") or 0)


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip("\"'")


def _notify(text):
    """Send a Telegram message, but never let a Telegram problem stop the real work."""
    try:
        import tg
        tg.send_message(text)
    except Exception as e:  # noqa: BLE001
        print(f"(Could not send Telegram message: {e})", flush=True)


def _restore_buttons(chat_id, message_id, draft_id):
    """After a failed upload, bring the Approve/Reject buttons back so you can try again."""
    try:
        import tg
        tg.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                reply_markup=tg.buttons(draft_id))
    except Exception as e:  # noqa: BLE001
        print(f"(Could not restore buttons: {e})", flush=True)


def act():
    """Do one approve/reject sent by the Cloudflare Worker (env ACTION, DRAFT_ID, MESSAGE_ID)."""
    import state

    action = os.environ.get("ACTION", "")
    draft_id = os.environ.get("DRAFT_ID", "")
    if action not in ("up_ok", "up_no") or not draft_id.isdigit():
        sys.exit(f"Bad input: action={action!r} draft_id={draft_id!r}")
    st = state.load()
    if draft_id not in st["drafts"]:
        _notify(f"I don't have a Short #{draft_id}.")
        return
    _handle_callback(st, {
        "id": "0",
        "data": f"{action}:{draft_id}",
        "message": {"chat": {"id": CHAT_ID}, "message_id": int(os.environ.get("MESSAGE_ID") or 0)},
    })
    state.save(st)


def check():
    """Standard library only, so an idle run costs a few seconds and installs nothing."""
    if os.environ.get("ACTION"):  # started by the Worker with a ready-made approve/reject
        print("work=true")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("work=true\n")
        return
    offset = 0
    if STATE_PATH.exists():
        offset = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("offset", 0)
    url = f"https://api.telegram.org/bot{_token()}/getUpdates?offset={offset}&timeout=0"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            updates = json.load(r).get("result", [])
    except (urllib.error.URLError, OSError, ValueError) as e:
        # e.g. HTTP 400/401/404/409 from Telegram: show it in the log, but don't crash the workflow
        print(f"::warning::Could not read Telegram updates: {e}")
        updates = []
    work = any(
        u.get("callback_query", {}).get("message", {}).get("chat", {}).get("id") == CHAT_ID
        or u.get("message", {}).get("chat", {}).get("id") == CHAT_ID
        for u in updates
    )
    line = f"work={'true' if work else 'false'}"
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(line + "\n")


def _download_video(draft_id, d):
    """Fetch the full-quality video that the earlier workflow run saved as an artifact."""
    if not d.get("run_id"):
        raise RuntimeError("No run id saved for this video, so the file can't be found.")
    dest = Path("output") / f"dl_{draft_id}"
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gh", "run", "download", str(d["run_id"]), "-n", f"shorts-run-{d['run_id']}", "-D", str(dest)],
        check=True, capture_output=True, text=True,
    )
    matches = list(dest.rglob(f"short_{draft_id}.mp4"))
    if not matches:
        raise RuntimeError(f"short_{draft_id}.mp4 not found in the saved files.")
    return matches[0]


def _handle_callback(st, cq):
    import state
    import tg
    from upload import upload_video

    tg.answer(cq["id"])  # already ignores errors such as HTTP 400 "query is too old"
    action, raw_id = cq["data"].split(":")
    print(f"Handling {action} for Short #{raw_id}", flush=True)
    draft_id = int(raw_id)
    chat_id, message_id = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    d = st["drafts"].get(str(draft_id))
    if not d:
        return

    if action == "up_no":
        if state.claim(st, draft_id, "upload_pending", "rejected"):
            state.save(st)
            tg.clear_buttons(chat_id, message_id)
            _notify(f"Rejected Short #{draft_id}. Not uploaded. Send /idea for another.")

    elif action == "up_ok":
        if not state.claim(st, draft_id, "upload_pending", "uploading"):
            return
        state.save(st)  # saved before uploading, so a crash can never upload twice
        tg.clear_buttons(chat_id, message_id)
        try:
            path = _download_video(draft_id, d)
            vid, privacy = upload_video(path, d["data"], privacy=PRIVACY)
        except subprocess.CalledProcessError as e:
            d["status"] = "upload_pending"
            print(f"Could not fetch the video: {(e.stderr or str(e))[:500]}", flush=True)
            _restore_buttons(chat_id, message_id, draft_id)
            _notify(f"⚠️ Could not fetch the video: {(e.stderr or str(e))[:300]}\nTap Approve again to retry, or send /approve {draft_id}.")
            return
        except Exception as e:  # noqa: BLE001
            d["status"] = "upload_pending"
            print(f"Upload failed: {e}", flush=True)
            _restore_buttons(chat_id, message_id, draft_id)
            _notify(f"⚠️ Upload failed: {e}\nTap Approve again to retry, or send /approve {draft_id}.")
            return
        # the upload is done: record it first, so a Telegram hiccup can't make it look unfinished
        d["status"] = "uploaded"
        d["youtube_id"] = vid
        state.save(st)
        print(f"Uploaded Short #{draft_id}: https://youtube.com/shorts/{vid} ({privacy})", flush=True)
        note = ""
        if privacy != PRIVACY:
            note = f"\n\nNote: YouTube set it to {privacy.upper()}. Unaudited API projects are forced to private; publish it in YouTube Studio."
        _notify(f"✅ Uploaded ({privacy}): https://youtube.com/shorts/{vid}{note}")


def poll():
    import state
    import tg

    st = state.load()
    for u in tg.get_updates(st["offset"]):
        st["offset"] = u["update_id"] + 1
        state.save(st)  # offset first: a tap is never handled twice, and never blocks the ones after it
        cq, msg = u.get("callback_query"), u.get("message")
        try:
            if cq and cq["message"]["chat"]["id"] == CHAT_ID:
                _handle_callback(st, cq)
            elif msg and msg["chat"]["id"] == CHAT_ID:
                text = (msg.get("text") or "").strip()
                if text.startswith("/idea"):
                    _notify("On it, making a new Short…")
                    import make_short
                    st = state.load()
                    try:
                        make_short.create_preview(st)
                    except Exception as e:  # noqa: BLE001
                        _notify(f"⚠️ Could not make a Short: {e}")
                elif text.startswith(("/approve", "/reject")):
                    parts = text.split()
                    if len(parts) == 2 and parts[1].isdigit():
                        action = "up_ok" if text.startswith("/approve") else "up_no"
                        _handle_callback(st, {"id": "0", "data": f"{action}:{parts[1]}",
                                              "message": {"chat": {"id": CHAT_ID}, "message_id": msg["message_id"]}})
                    else:
                        _notify("Send /approve 3 (or /reject 3) with the Short number.")
                elif text.startswith("/start"):
                    _notify("Shorts bot is on. Send /idea to make a Short now, or /approve 3 to upload Short #3. You only approve the finished video; taps are handled within ~30 minutes.")
        except Exception as e:  # noqa: BLE001
            print(f"Problem handling update {u.get('update_id')}: {e}", flush=True)
            _notify(f"⚠️ Something went wrong: {e}")
        state.save(st)
    state.save(st)


if __name__ == "__main__":
    {"check": check, "poll": poll, "act": act}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
