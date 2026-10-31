"""Handles your Telegram taps.

  python approve.py check   no extra packages: prints work=true if you tapped or messaged
  python approve.py poll    upload approved videos; make a new Short when you send /idea

You only approve the finished video, before upload. Nothing is uploaded without your tap.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PRIVACY = os.environ.get("PRIVACY") or "public"  # public, unlisted or private
STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID") or 0)


def check():
    """Standard library only, so an idle run costs a few seconds and installs nothing."""
    offset = 0
    if STATE_PATH.exists():
        offset = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("offset", 0)
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip().strip('"\'')
    url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=0"
    with urllib.request.urlopen(url, timeout=30) as r:
        updates = json.load(r).get("result", [])
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

    tg.answer(cq["id"])
    action, raw_id = cq["data"].split(":")
    draft_id = int(raw_id)
    chat_id, message_id = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    d = st["drafts"].get(str(draft_id))
    if not d:
        return

    if action == "up_no":
        if state.claim(st, draft_id, "upload_pending", "rejected"):
            tg.clear_buttons(chat_id, message_id)
            tg.send_message(f"Rejected Short #{draft_id}. Not uploaded. Send /idea for another.")

    elif action == "up_ok":
        if not state.claim(st, draft_id, "upload_pending", "uploading"):
            return
        state.save(st)  # saved before uploading, so a crash can never upload twice
        tg.clear_buttons(chat_id, message_id)
        try:
            path = _download_video(draft_id, d)
            vid, privacy = upload_video(path, d["data"], privacy=PRIVACY)
        except subprocess.CalledProcessError as e:
            d["status"] = "upload_pending"  # the Approve button on the preview still works
            tg.send_message(f"⚠️ Could not fetch the video: {(e.stderr or str(e))[:300]}\nTap Approve again to retry.")
            return
        except Exception as e:
            d["status"] = "upload_pending"
            tg.send_message(f"⚠️ Upload failed: {e}\nTap Approve on the preview again to retry.")
            return
        d["status"] = "uploaded"
        d["youtube_id"] = vid
        note = ""
        if privacy != PRIVACY:
            note = f"\n\nNote: YouTube set it to {privacy.upper()}. Unaudited API projects are forced to private; publish it in YouTube Studio."
        tg.send_message(f"✅ Uploaded ({privacy}): https://youtube.com/shorts/{vid}{note}")


def poll():
    import state
    import tg

    st = state.load()
    for u in tg.get_updates(st["offset"]):
        st["offset"] = u["update_id"] + 1
        cq, msg = u.get("callback_query"), u.get("message")
        if cq and cq["message"]["chat"]["id"] == CHAT_ID:
            try:
                _handle_callback(st, cq)
            except Exception as e:
                tg.send_message(f"⚠️ Something went wrong: {e}")
        elif msg and msg["chat"]["id"] == CHAT_ID:
            text = (msg.get("text") or "").strip()
            if text.startswith("/idea"):
                state.save(st)
                tg.send_message("On it, making a new Short…")
                import make_short
                st = state.load()
                try:
                    make_short.create_preview(st)
                except Exception as e:
                    tg.send_message(f"⚠️ Could not make a Short: {e}")
            elif text.startswith("/start"):
                tg.send_message("Shorts bot is on. Send /idea to make a Short now. You only approve the finished video; taps are handled within ~30 minutes.")
        state.save(st)
    state.save(st)


if __name__ == "__main__":
    {"check": check, "poll": poll}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
