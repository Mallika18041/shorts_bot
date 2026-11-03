"""Multi-part stories.

A story is chosen once (by you with /idea <story>, or by Gemini), the WHOLE story is planned and split into
parts, and then one part is made per run: Part 1, Part 2, ... until the story ends. Only then a new story
starts. The characters (their look and portraits) are fixed once and reused in every part.

State lives in series.json (committed by the workflow). Portraits live in series/<id>/.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

PATH = Path(os.environ.get("SERIES_PATH", "series.json"))
DIR = Path("series")
MAX_PARTS = int(os.environ.get("MAX_PARTS", "5"))


def load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {"active": None}
    except (ValueError, OSError):
        return {"active": None}


def save(st):
    PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def unfinished(st):
    a = st.get("active")
    return a if a and a["next_part"] <= a["total"] else None


def _json(prompt):
    import character_story as cs
    return cs._gemini_json(prompt)


PLAN_PROMPT = """You plan a multi-part YouTube Shorts story series in simple spoken Telugu.
Niche: one real HISTORICAL LOVE STORY or well-known legend (India first; also world history).
{choice}
Plan the COMPLETE story from beginning to end, then split it into between 2 and {max_parts} parts, each part
a Short of about 50 seconds. Use as many parts as the story needs (a short story 2 parts, a long one up to {max_parts}).
Each part must end on a cliffhanger, except the last part, which gives the ending.
Stay faithful to recorded history; mark legends and disputed details as such.
Respectful tone: no sexual content, graphic violence, or attacks on religion, caste or community.

Return ONLY valid JSON:
{{
  "topic": "short English name of the story",
  "title": "Telugu title of the whole story, under 60 characters",
  "description": "two Telugu lines mentioning era or place",
  "tags": ["8 mixed Telugu and English tags"],
  "parts": [{{"title": "short Telugu name of this part", "summary": "English: exactly what happens in this part, in order"}}]
}}"""

PART_PROMPT = """You write Part {n} of {total} of a Telugu YouTube Shorts story series, in simple spoken Telugu.
Story: {topic}
Full plan of all parts:
{outline}
{previous}
Write ONLY Part {n}, following its summary exactly. Do not tell events from later parts.

Rules:
- 9 to 11 short lines, each at most 12 words, natural spoken Telugu, total about 45-52 seconds.
{opening}
{closing}
- Tell it like a gripping storyteller: short punchy sentences, tension building line by line.
- Make it feel like a short film: 3 to 5 lines are a character speaking directly (first person, no quote marks,
  no name labels). All other lines are the narrator.
- Stay faithful to recorded history; never invent facts or quotes as real; mark legends clearly.
- Respectful tone: no sexual content, graphic violence, or attacks on religion, caste or community.

Return ONLY valid JSON:
{{
  "lines": ["9 to 11 Telugu lines"],
  "speakers": ["narrator, male or female for each line; male/female only for a line a character speaks"],
  "keywords": ["one English visual search phrase of 3-6 words per line, describing what is shown"]
}}
The three arrays MUST have exactly the same number of items."""


def plan(used, forced=""):
    choice = (f"Tell THIS story: {forced}" if forced else
              f"Choose a story that is NOT any of these already covered: {used[-60:]}")
    d = _json(PLAN_PROMPT.format(choice=choice, max_parts=MAX_PARTS))
    parts = [p for p in (d.get("parts") or []) if p.get("summary")][:MAX_PARTS]
    if len(parts) < 2 or not d.get("topic") or not d.get("title"):
        raise ValueError("The story plan was incomplete")
    sid = re.sub(r"[^a-z0-9]+", "-", d["topic"].lower()).strip("-")[:40] + time.strftime("-%m%d%H%M")
    return {
        "id": sid, "topic": d["topic"], "title": d["title"], "description": d.get("description", ""),
        "tags": d.get("tags") or [], "total": len(parts), "next_part": 1, "parts": parts,
        "made": {}, "bible": None, "portraits": {},
    }


def part_script(s):
    n, total = s["next_part"], s["total"]
    outline = "\n".join(f"Part {i + 1}: {p['title']} - {p['summary']}" for i, p in enumerate(s["parts"]))
    prev = s["made"].get(str(n - 1))
    previous = ("Previous part, as already published:\n" + "\n".join(prev["lines"]) + "\n") if prev else ""
    opening = ("- The first line is a strong hook that creates curiosity in 2 seconds." if n == 1 else
               "- The first line is a very short recap of what happened before, so a new viewer is not lost, then continue.")
    closing = ("- The last line is a cliffhanger or open question, and invites viewers to follow for the next part "
               f"(Part {n + 1})." if n < total else
               "- This is the FINAL part: resolve the story with an emotional ending, and end by inviting viewers to "
               "watch the next story on the channel.")
    for _ in range(3):
        d = _json(PART_PROMPT.format(n=n, total=total, topic=s["topic"], outline=outline, previous=previous,
                                     opening=opening, closing=closing))
        L, K = d.get("lines") or [], d.get("keywords") or []
        sp = d.get("speakers") or []
        if 9 <= len(L) <= 11 and len(K) == len(L):
            if len(sp) != len(L):
                sp = ["narrator"] * len(L)
            sp = [x if x in ("narrator", "male", "female") else "narrator" for x in sp]
            title = f"{s['title']} | పార్ట్ {n}/{total}"
            return {
                "topic": s["topic"] if n == 1 else f"{s['topic']} (Part {n})",
                "title": title,
                "description": f"{s['description']}\n\nపార్ట్ {n} / {total}",
                "tags": s["tags"], "lines": L, "keywords": K, "speakers": sp,
                "series": {"id": s["id"], "part": n, "total": total},
                "series_outline": outline,
            }
    raise ValueError("Could not write a valid script for this part")


def remember_part(st, s, data):
    """After a part is made: save its lines, the fixed characters and portraits, and move to the next part."""
    n = s["next_part"]
    s["made"][str(n)] = {"lines": data["lines"]}
    bible = data.get("production")
    if bible and not s.get("bible"):
        s["bible"] = {"era_look": bible["era_look"], "characters": bible["characters"]}
    folder = DIR / s["id"]
    for cid, src in (data.get("portraits") or {}).items():
        if cid not in s["portraits"] and Path(src).exists():
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"char_{cid}{Path(src).suffix}"
            shutil.copy(src, dest)
            s["portraits"][cid] = str(dest)
    s["next_part"] = n + 1
    st["active"] = s
    save(st)


def rewind(draft_series):
    """A rejected part is made again next time, instead of skipping ahead."""
    st = load()
    a = st.get("active")
    if a and draft_series and a["id"] == draft_series["id"] and a["next_part"] == draft_series["part"] + 1:
        a["next_part"] = draft_series["part"]
        save(st)
        return True
    return False
