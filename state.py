"""Tiny JSON state file (state.json). The workflows commit it back to the repo."""
import json
import os
from pathlib import Path

PATH = Path(os.environ.get("STATE_PATH", "state.json"))


def load():
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    return {"offset": 0, "next_id": 1, "drafts": {}}


def save(st):
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def claim(st, draft_id, expected, new):
    """Move a draft from one status to another. False if it already moved (blocks double taps)."""
    d = st["drafts"].get(str(draft_id))
    if not d or d["status"] != expected:
        return False
    d["status"] = new
    return True
