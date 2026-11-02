"""Character-first cinematic Shorts.

Order of work (this is the point of the module):
  1. FIX THE CHARACTERS: Gemini writes one fixed look per main character (face, hair, costume,
     jewellery) plus one art direction for the whole story, and plans every shot.
  2. Make one portrait per character. These are the reference pictures.
  3. Generate every scene from the references, so the same people look the same in every shot.
  4. Slow camera moves, film grade, storyteller voice, captions, soft music (cinematic.py does these).

If AI pictures are not available, build() raises CinematicUnavailable and make_short.py falls back to
its stock-footage pipeline, so a video is still produced.

Settings (all optional, GitHub "Variables" or environment):
  CINEMATIC       1 (default) or 0 to switch this module off
  IMAGE_MODEL     default gemini-2.5-flash-image
  GEMINI_MODEL    text model for the character plan (same variable make_short.py uses)
  MAX_CHARACTERS  default 3
  plus everything listed in cinematic.py (voice, music, image sources)
"""
import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

import cinematic as cin

MAX_SECONDS = 58
API = "https://generativelanguage.googleapis.com/v1beta/models"

SAFETY = (
    "modest period clothing, respectful and tasteful, no nudity, no kissing, no blood or violence, "
    "no text, no captions, no watermark, no logos, "
    "fictional-looking faces that do not resemble any real actor or celebrity"
)


class CinematicUnavailable(RuntimeError):
    """AI pictures are not available right now; the caller should use stock footage instead."""


class TooLong(ValueError):
    pass


if not os.environ.get("VOICE_STYLE"):
    os.environ["VOICE_STYLE"] = (
    "Narrate the following in Telugu like a gripping news-style suspense storyteller: a confident, "
    "dramatic voice, slightly faster pace, rising tension, short pauses before key words, and a "
    "curious, teasing tone at the end of each line that makes the listener want to hear what happens next:"
    )


def _env(name, default=""):
    return os.environ.get(name) or default


# --------------------------------------------------------------------------- 1. fix the characters

BIBLE_PROMPT = """You are the art director of a cinematic YouTube Short. FIRST fix the characters, THEN plan every shot.

Story topic: {topic}
Narration, one line per scene (Telugu):
{numbered}

Return ONLY valid JSON with exactly these keys:
{{
  "era_look": "ONE sentence of art direction used in every shot: period, region, architecture, colour palette, lighting, mood",
  "characters": [
    {{"id": "c1", "name": "English name", "role": "who they are in the story",
      "look": "ONE detailed sentence that never changes: age, face shape, skin tone, eyes, hair or beard, exact clothing colours and fabric, jewellery, turban or crown"}}
  ],
  "scenes": [
    {{"characters": ["c1"], "shot": "extreme close-up | close-up | medium | wide",
      "emotion": "one or two words", "setting": "where, in a few words",
      "action": "what is visibly happening in this line, concrete and filmable",
      "wiki": "2-4 word search term for a real historical picture of this place or monument, or empty"}}
  ]
}}

Rules:
- 1 to {max_chars} characters: only the main people of the story.
- The characters look identical in every scene. Their clothing, hair and jewellery never change.
- EXACTLY {n} scenes; scene number i illustrates narration line i.
- Use at least 4 close-ups of a face showing a clear emotion; use wide shots for places and arrivals; vary the shots.
- Historically appropriate for the period and region. No modern objects.
- Romance is shown through glances, distance and gestures: no kissing, no nudity, no violence or blood.
- Faces are fictional-looking and must not resemble any real actor or celebrity.
- A scene with no person (a place, a letter, an object) has "characters": [].
"""


def _gemini_json(prompt):
    key = _env("GEMINI_API_KEY")
    if not key:
        raise CinematicUnavailable("GEMINI_API_KEY is not set")
    models = [_env("GEMINI_MODEL", "gemini-3.6-flash"), "gemini-3.8-flash"]
    last = "no answer"
    for model in models:
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{API}/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8},
                    },
                    timeout=180,
                )
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"{model} HTTP {r.status_code}"
                    time.sleep(10 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    last = f"{model} HTTP {r.status_code}: {r.text[:150]}"
                    break
                parts = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
                text = "".join(p.get("text", "") for p in parts).strip()
                text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
                return json.loads(text)
            except (requests.RequestException, ValueError) as e:
                last = f"{model}: {e}"
                time.sleep(3)
    raise CinematicUnavailable(f"Could not plan the characters: {last}")


def plan_characters(data):
    """Step 1. Returns the story bible: art direction, fixed characters, one planned shot per line."""
    lines = data["lines"]
    max_chars = int(_env("MAX_CHARACTERS", "3"))
    prompt = BIBLE_PROMPT.format(
        topic=data.get("topic", ""),
        numbered="\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines)),
        n=len(lines),
        max_chars=max_chars,
    )
    bible = _gemini_json(prompt)
    chars = [c for c in (bible.get("characters") or []) if c.get("id") and c.get("look")][:max_chars]
    scenes = bible.get("scenes") or []
    if not bible.get("era_look") or not chars or len(scenes) != len(lines):
        raise CinematicUnavailable(
            f"The character plan was incomplete (characters={len(chars)}, scenes={len(scenes)}, lines={len(lines)})"
        )
    ids = {c["id"] for c in chars}
    for s in scenes:
        s["characters"] = [c for c in (s.get("characters") or []) if c in ids]
        s["shot"] = (s.get("shot") or "medium").strip()
    bible["characters"] = chars
    bible["scenes"] = scenes
    return bible


# --------------------------------------------------------------------------- 2/3. portraits and scenes

_disabled = False  # set when the image model is not usable, so we stop asking


def _gemini_image(prompt, dest, refs=()):
    """One AI picture, optionally guided by reference pictures. Returns the saved path or None."""
    global _disabled
    key = _env("GEMINI_API_KEY")
    if _disabled or not key:
        return None
    model = _env("IMAGE_MODEL", "gemini-2.5-flash-image")
    parts = [{"text": prompt}]
    for ref in refs:
        mime = "image/png" if str(ref).lower().endswith(".png") else "image/jpeg"
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(Path(ref).read_bytes()).decode()}})
    gen = {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}}
    fallbacks = [
        {"responseModalities": ["IMAGE"]},            # this model has no aspect ratio setting
        {"responseModalities": ["TEXT", "IMAGE"]},    # this model needs both
    ]
    waited = False
    for attempt in range(5):
        try:
            r = requests.post(f"{API}/{model}:generateContent", headers={"x-goog-api-key": key},
                              json={"contents": [{"parts": parts}], "generationConfig": gen}, timeout=240)
        except requests.RequestException as e:
            print(f"AI image request failed: {e}", flush=True)
            return None
        if r.status_code == 400 and fallbacks:
            gen = fallbacks.pop(0)
            continue
        if r.status_code in (401, 403, 404):
            print(f"AI images are not available on this key or model (HTTP {r.status_code}).", flush=True)
            _disabled = True
            return None
        if r.status_code == 429:
            if not waited:
                waited = True
                time.sleep(25)
                continue
            print("AI image quota reached for now.", flush=True)
            _disabled = True
            return None
        if r.status_code != 200:
            print(f"AI image HTTP {r.status_code}: {r.text[:150]}", flush=True)
            return None
        got = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        inline = next((p["inlineData"] for p in got if "inlineData" in p), None)
        if not inline:
            print("The model returned no picture for this prompt.", flush=True)
            return None
        raw = Path(dest).with_suffix(".img")
        raw.write_bytes(base64.b64decode(inline["data"]))
        return cin._sniff(raw)
    return None


_SEED = int(time.time()) % 100000
_free_ok = True


def _free_image(prompt, dest):
    """Free fallback (Pollinations, no key). It cannot take reference pictures, so characters are kept
    consistent only by the identical written look plus one fixed seed for the whole story."""
    global _free_ok
    if not _free_ok or _env("FREE_IMAGES", "1") == "0":
        return None
    from urllib.parse import quote
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt[:900])}"
           f"?width=768&height=1344&model=flux&nologo=true&seed={_SEED}")
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=240)
        except requests.RequestException as e:
            print(f"Free image request failed: {e}", flush=True)
            time.sleep(5)
            continue
        if r.status_code == 200 and r.content[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb"):
            raw = Path(dest).with_suffix(".img")
            raw.write_bytes(r.content)
            return cin._sniff(raw)
        print(f"Free image HTTP {r.status_code}", flush=True)
        time.sleep(8 * (attempt + 1))
    _free_ok = False
    return None


def _image(prompt, dest, refs=()):
    """Gemini first (uses reference pictures); if it is unavailable or over quota, the free generator."""
    return _gemini_image(prompt, dest, refs) or _free_image(prompt, dest)


def make_portraits(bible, work):
    """Step 2. One reference portrait per character. Returns {character id: path}."""
    refs = {}
    for c in bible["characters"]:
        prompt = (
            f"Character reference portrait, waist-up, facing the camera with a calm expression, soft plain "
            f"background, even lighting. {c['name']}, {c['role']}. {c['look']}. {bible['era_look']}. "
            f"{cin.STYLE}. {SAFETY}"
        )
        print(f"Character portrait: {c['name']}", flush=True)
        path = _image(prompt, Path(work) / f"char_{c['id']}")
        if path:
            refs[c["id"]] = path
        time.sleep(1.5)
    return refs


def scene_prompt(bible, scene):
    by_id = {c["id"]: c for c in bible["characters"]}
    people = [by_id[i] for i in scene["characters"] if i in by_id]
    bits = [
        f"{scene['shot']} shot. {scene.get('action', '')}",
        f"Setting: {scene.get('setting', '')}. Emotion: {scene.get('emotion', '')}",
    ]
    if people:
        bits.append(
            "Characters, keep their face and clothing EXACTLY as in the reference pictures: "
            + "; ".join(f"{p['name']}: {p['look']}" for p in people)
        )
    bits += [bible["era_look"], cin.STYLE, SAFETY]
    return ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip()) + "."


def make_scene_image(bible, scene, refs, work, idx, previous):
    """Step 3. Reference-guided AI picture; else the same without references; else Wikimedia; else repeat the last one."""
    dest = Path(work) / f"scene{idx}"
    prompt = scene_prompt(bible, scene)
    guide = [refs[i] for i in scene["characters"] if i in refs]
    path = _image(prompt, dest, guide) or (guide and _image(prompt, dest))
    if path:
        return path
    if scene.get("wiki") and cin._wiki_image(scene["wiki"], Path(work) / f"wiki{idx}.img"):
        found = cin._sniff(Path(work) / f"wiki{idx}.img")
        if found:
            return found
    return previous


# --------------------------------------------------------------------------- 4. assemble

def _run(cmd, cwd):
    cin._run(cmd, cwd=cwd)


def build(data, out_dir):
    """Make the whole cinematic video. Mutates data (description, character_notes). Returns the video path."""
    cin.CREDITS.clear()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    work = (Path(out_dir) / f"cine_{stamp}").resolve()
    work.mkdir(parents=True)
    lines = data["lines"]

    bible = plan_characters(data)  # 1. fix the characters
    print("Characters fixed:", "; ".join(f"{c['name']} ({c['role']})" for c in bible["characters"]), flush=True)
    data["character_notes"] = [f"{c['name']}: {c['look']}" for c in bible["characters"]]
    (work / "bible.json").write_text(json.dumps(bible, ensure_ascii=False, indent=1), encoding="utf-8")

    refs = make_portraits(bible, work)  # 2. reference portraits
    if not refs:
        raise CinematicUnavailable("No character portrait could be generated, so scenes would not look consistent.")

    images, previous = [], None  # 3. scenes
    for i, scene in enumerate(bible["scenes"]):
        print(f"Scene {i + 1}/{len(lines)} ({scene['shot']}): {scene.get('action', '')[:80]}", flush=True)
        img = make_scene_image(bible, scene, refs, work, i, previous)
        if img is None:
            raise CinematicUnavailable(f"Scene {i + 1} could not get a picture.")
        images.append(img)
        previous = img
        time.sleep(1.5)

    durs, names = cin.voice_lines(lines, work)  # voice
    total = sum(durs)
    if total > MAX_SECONDS:
        factor = total / (MAX_SECONDS - 1.0)
        if factor > 1.18:
            raise TooLong(f"video would be {total:.0f}s; Shorts must be under 60s")
        print(f"Speaking {factor:.2f}x faster to fit under 60 seconds", flush=True)
        durs = cin.speed_up(names, work, factor)
        total = sum(durs)

    segs = []
    for i, (img, d) in enumerate(zip(images, durs)):  # slow camera moves + film grade
        seg = work / f"seg{i}.mp4"
        cin.ken_burns(Path(img).resolve(), seg, d, i, work)
        segs.append(seg.name)

    (work / "v.txt").write_text("".join(f"file '{n}'\n" for n in segs), encoding="utf-8")
    (work / "a.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "v.txt", "-c", "copy", "video.mp4"], work)
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "a.txt", "-c:a", "pcm_s16le", "narration.wav"], work)

    audio = "narration.wav"
    if cin.music_bed(total, work / "music.wav", work):
        _run(["ffmpeg", "-y", "-i", "narration.wav", "-i", "music.wav", "-filter_complex",
              "[0:a]volume=1.0[v];[1:a]volume=0.16[m];[v][m]amix=inputs=2:duration=first:normalize=0[a]",
              "-map", "[a]", "-c:a", "pcm_s16le", "mix.wav"], work)
        audio = "mix.wav"

    cin.write_ass(lines, durs, work / "subs.ass", _env("SUBTITLE_FONT", "Noto Sans Telugu"))
    final = (Path(out_dir) / f"short_{stamp}.mp4").resolve()
    cmd = ["ffmpeg", "-y", "-i", "video.mp4", "-i", audio, "-vf", "ass=subs.ass", "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
           "-t", f"{total:.2f}", str(final)]
    try:
        _run(cmd, work)
    except RuntimeError as e:  # captions failed (usually a missing font): still deliver the picture and sound
        print(f"Captions failed, making the video without them: {e}", flush=True)
        cmd[cmd.index("-vf") + 1] = "null"
        _run(cmd, work)

    notes = "\n\nచిత్రాలు AI ద్వారా రూపొందించబడ్డాయి (AI-generated visuals)."
    if cin.CREDITS:
        notes += "\nImages: " + "; ".join(sorted(set(cin.CREDITS)))[:600]
    data["description"] = data["description"] + notes
    return final
