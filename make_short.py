"""Telugu historical love-story Shorts generator.

Usage:
    python make_short.py            # make one video in output/
    python make_short.py --upload   # make one video and upload it privately
    python make_short.py --preview  # send preview to Telegram for approval
"""

import argparse
import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import edge_tts
import requests
from google import genai

OUT = Path("output")
OUT.mkdir(exist_ok=True)
USED = Path("used_topics.json")

VOICE = os.environ.get("TTS_VOICE") or "te-IN-ShrutiNeural"  # or te-IN-MohanNeural
RATE = os.environ.get("TTS_RATE") or "+0%"  # slower: "-8%", faster: "+8%"
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"
FONT = "Noto Sans Telugu"
W, H = 1080, 1920


class TooLong(ValueError):
    pass


def load_used():
    try:
        return json.loads(USED.read_text(encoding="utf-8")) if USED.exists() else []
    except (ValueError, OSError):
        return []


def generate_script():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    used = load_used()[-60:]

    prompt = f"""You write viral YouTube Shorts scripts in simple, spoken Telugu.
Topic style: one real HISTORICAL LOVE STORY or well-known legend, told as a complete story
(India first: Telugu lands, Kakatiya, Vijayanagara, Mughal, Rajput, Maratha eras; also world history).
Do not repeat these topics: {used}

Rules:
- 9 to 11 short lines, each at most 12 words, natural spoken Telugu.
- Line 1 is a strong hook that creates curiosity in 2 seconds.
- Tell it like a gripping serial storyteller (a news-style suspense narrator): short punchy sentences,
  open questions ("కానీ అప్పుడు ఏమైంది?"), build tension line by line.
- Tell the story in chronological order. Do NOT wrap everything up: end on a cliffhanger or an
  unanswered question that makes viewers want the next part, and make the final line invite them to
  follow for part 2 (for example "తర్వాత ఏమైందో రేపటి భాగంలో... ఫాలో అవ్వండి").
- Everything stated must still be faithful to recorded history or clearly marked as legend.
- Total spoken length about 40-52 seconds.
- Stay faithful to recorded history. Never invent facts, quotes or dialogue as real.
- Do not say someone was 'the only', 'the first', 'the greatest' or 'the most powerful' unless that is
  firmly documented. Prefer plain wording such as 'one of the few' or 'a famous'.
- If it is a legend or historians disagree, make that clear in the narration.
- Respectful tone: no sexual content, graphic violence, or attacks on religion,
  caste or community. No health or financial claims.

VISUAL MATCHING RULES:
- Create exactly one visual search phrase for each narration line.
- Each phrase must describe the actual action, place, people, clothing or object
  shown in that specific line, not merely the general mood of the story.
- Use 3-6 simple English words that work as stock-video search terms.
- Keep the same historical period, region and visual style throughout the story.
- For Indian historical stories, prefer historically appropriate Indian architecture,
  clothing, jewellery, weapons and landscapes.
- Do not use modern rooms, modern clothes, coffee shops, cars, modern city streets,
  generic nature, unrelated animals, or Egyptian imagery unless relevant to the story.
- Do not use people's names in visual search phrases.
- Avoid abstract terms such as "love", "sadness", "destiny" on their own.
- Example: for a line about a queen entering a fort, use
  "Rajput queen entering stone fort", not "romantic sunset".
- If the line describes a letter, show an old handwritten letter or messenger.
- If the line describes a palace meeting, show a historical palace court.

Return ONLY valid JSON with exactly these keys:
{{
  "topic": "short English topic name",
  "title": "Telugu title under 70 characters",
  "description": "two Telugu lines mentioning era or place",
  "tags": ["8 mixed Telugu and English hashtags or tags"],
  "lines": ["9 to 11 Telugu narration lines"],
  "keywords": ["one English visual search phrase per narration line"]
}}
The lines and keywords arrays MUST have exactly the same number of items.
"""

    data = None
    models_to_try = [MODEL, "gemini-3.8-flash"]

    for model_name in models_to_try:
        for attempt in range(3):
            try:
                print(
                    f"Trying {model_name} (attempt {attempt + 1}/3)...",
                    flush=True,
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                data = json.loads(response.text)
                print(f"Success with {model_name}", flush=True)
                break
            except Exception as e:
                print(f"{model_name} failed: {e}", flush=True)
                error_text = str(e)
                retryable = any(
                    code in error_text
                    for code in ["429", "500", "502", "503", "504"]
                )
                if retryable and attempt < 2:
                    delay = 10 * (attempt + 1)
                    print(f"Retrying in {delay} seconds...", flush=True)
                    time.sleep(delay)
                else:
                    break
        if data is not None:
            break

    if data is None:
        raise RuntimeError("All Gemini model attempts failed.")

    required = ("topic", "title", "description", "tags", "lines", "keywords")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"Gemini response is missing fields: {missing}")

    if not isinstance(data["lines"], list) or not isinstance(data["keywords"], list):
        raise ValueError("lines and keywords must be lists")

    if len(data["lines"]) != len(data["keywords"]):
        raise ValueError("lines/keywords mismatch")

    if not 9 <= len(data["lines"]) <= 11:
        raise ValueError("The generated script must contain 9 to 11 lines.")

    if not all(isinstance(x, str) and x.strip() for x in data["keywords"]):
        raise ValueError("Every visual keyword must be a non-empty string.")

    return data


async def tts(text, path):
    last_error = None
    for attempt in range(1, 4):
        try:
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(path))
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError("Audio file is missing or empty")
            return
        except Exception as e:
            last_error = e
            if path.exists():
                path.unlink()
            if attempt < 3:
                delay = attempt * 3
                print(
                    f"Voice connection failed (attempt {attempt}/3). "
                    f"Retrying in {delay} seconds...",
                    flush=True,
                )
                await asyncio.sleep(delay)
    raise RuntimeError(
        f"Telugu voice failed after 3 attempts: {last_error}"
    ) from last_error


def duration(path):
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ]
    )
    return float(out)


def run(cmd, cwd=None):
    p = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-40:])
        print("COMMAND FAILED:", " ".join(map(str, cmd)), flush=True)
        print("FFMPEG ERROR:", tail, flush=True)
        raise RuntimeError(f"{cmd[0]} failed (exit {p.returncode}):\n{tail}")


def _download(url, dest):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    f.write(chunk)


def _pexels(query, dest):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return False

    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": key},
        params={"query": query, "orientation": "portrait", "per_page": 15},
        timeout=30,
    )
    r.raise_for_status()

    query_words = _query_words(query)
    candidates = []

    for video in r.json().get("videos", []):
        files = [
            f for f in video.get("video_files", [])
            if f.get("file_type") == "video/mp4"
            and (f.get("height") or 0) > (f.get("width") or 0)
            and (f.get("height") or 0) >= 720
        ]
        if not files:
            continue

        # Pexels search results do not expose reliable descriptive tags.
        # Keep its search ranking, but prefer a portrait file with sufficient resolution.
        chosen = min(files, key=lambda f: abs((f.get("height") or 0) - 1920))
        candidates.append(chosen["link"])

    if not candidates:
        return False

    _download(random.choice(candidates), dest)
    return True


def _query_words(query):
    ignored = {
        "with", "from", "into", "that", "this", "wearing", "scene",
        "historical", "traditional", "cinematic", "beautiful",
    }
    return {
        word.strip(".,'\"()[]{}").lower()
        for word in query.replace("-", " ").split()
        if len(word.strip(".,'\"()[]{}")) > 2
        and word.strip(".,'\"()[]{}").lower() not in ignored
    }


UNSUITABLE_TAGS = {
    "woman", "women", "girl", "girls", "sexy", "bikini", "lingerie", "bath", "bathrobe", "spa",
    "model", "legs", "feet", "nude", "bed", "bedroom", "massage", "fashion", "coffee", "cafe",
    "car", "cars", "office", "laptop", "smartphone", "cartoon", "animation", "anubis",
}
_good_clips = []  # clips that matched a scene in the video being built, reused if a scene finds nothing


def _pixabay(query, dest):
    """Download a Pixabay result only when its tags meaningfully match the scene."""
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return False

    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={
            "key": key,
            "q": query,
            "per_page": 30,
            "safesearch": "true",
        },
        timeout=30,
    )
    r.raise_for_status()

    query_words = _query_words(query)
    if not query_words:
        return False

    candidates = []

    for hit in r.json().get("hits", []):
        tags = {
            word.strip().lower()
            for word in hit.get("tags", "").replace(",", " ").split()
            if word.strip()
        }

        # Exact and partial tag overlap; reward exact matches more strongly.
        exact = query_words.intersection(tags)
        partial = {
            qword for qword in query_words
            if any(
                qword in tag or tag in qword
                for tag in tags
                if len(tag) > 2
            )
        }
        score = (len(exact) * 2) + len(partial - exact)

        if tags & UNSUITABLE_TAGS:
            continue  # people in modern clothing, bedrooms, bathrobes etc. never fit these stories
        if score < 2:
            continue  # a single weak partial word match is not good enough

        videos = hit.get("videos") or {}
        selected = None

        for size in ("large", "medium"):
            video = videos.get(size) or {}
            width = video.get("width") or 0
            height = video.get("height") or 0
            if video.get("url") and height >= 720:
                selected = video
                break

        if selected:
            # Give portrait clips a small preference, but don't reject landscape
            # because the editor can crop it to vertical format.
            portrait_bonus = 1 if selected["height"] > selected["width"] else 0
            candidates.append(
                (score + portrait_bonus, selected["url"], hit.get("tags", ""))
            )

    if not candidates:
        print(
            f"No Pixabay clip passed tag matching for '{query}'.",
            flush=True,
        )
        return False

    best_score = max(item[0] for item in candidates)
    best = [item for item in candidates if item[0] == best_score]
    score, url, tags = random.choice(best)

    _download(url, dest)
    print(
        f"Pixabay match for '{query}' (score {score}): {tags}",
        flush=True,
    )
    return True


FOOTAGE_DIR = Path(__file__).resolve().parent / "assets" / "footage"
FOOTAGE_EXT = (".mp4", ".mov", ".mkv", ".webm")
_used_clips = set()


def library_clip(query, dest):
    """Prefer local clips whose filename or folder matches the scene description."""
    if not FOOTAGE_DIR.exists():
        return False

    clips = [
        c for c in FOOTAGE_DIR.rglob("*")
        if c.is_file() and c.suffix.lower() in FOOTAGE_EXT
    ]
    if not clips:
        return False

    words = _query_words(query)

    def score_clip(clip):
        label = str(clip.relative_to(FOOTAGE_DIR)).lower()
        label_words = set(label.replace("-", " ").replace("_", " ").split())
        return len(words.intersection(label_words))

    fresh = [c for c in clips if c not in _used_clips] or clips
    scored = [(score_clip(c), c) for c in fresh]
    best_score = max(score for score, _ in scored)

    # Do not use an unrelated library clip just because the library is non-empty.
    if best_score <= 0:
        return False

    pool = [clip for score, clip in scored if score == best_score]
    choice = random.choice(pool)
    _used_clips.add(choice)
    dest.write_bytes(choice.read_bytes())
    print(
        f"Footage for '{query}' from library: {choice.name} "
        f"(match {best_score})",
        flush=True,
    )
    return True


def fetch_clip(query, dest):
    """Use matching local footage, then Pexels/Pixabay, then the configured fallback."""
    if library_clip(query, dest):
        _good_clips.append(dest)
        return

    for source in (_pexels, _pixabay):
        name = source.__name__[1:]
        try:
            if source(query, dest):
                print(f"Footage for '{query}' from {name}", flush=True)
                _good_clips.append(dest)
                return
        except Exception as e:
            print(f"{name} failed for '{query}': {e}", flush=True)

    if _good_clips:  # keep the look consistent: reuse a clip that matched another scene of this story
        reuse = random.choice(_good_clips)
        dest.write_bytes(reuse.read_bytes())
        print(f"No good match for '{query}'; reusing a matched clip from this story ({reuse.name})", flush=True)
        return

    local = (
        Path(__file__).resolve().parent
        / "assets"
        / "background_test.mp4"
    )
    if not local.exists():
        raise FileNotFoundError(
            f"No matching footage source worked and the local clip is missing: {local}"
        )

    dest.write_bytes(local.read_bytes())
    print(
        f"Using local fallback footage for '{query}' "
        "(no matching stock clip found)",
        flush=True,
    )


def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


CAPTION_SIZE = int(os.environ.get("CAPTION_SIZE") or 60)
CAPTION_BOTTOM = int(os.environ.get("CAPTION_BOTTOM") or 380)  # pixels from the bottom; keeps clear of YouTube's buttons
CAPTION_WORDS = int(os.environ.get("CAPTION_WORDS") or 6)  # words per caption, so it never needs 3 lines


def caption_chunks(lines, durs, words_per_chunk=None):
    """Split each narration line into short captions (about 2 lines) timed by their share of the line."""
    n = words_per_chunk or CAPTION_WORDS
    out, t = [], 0.0
    for text, d in zip(lines, durs):
        words = text.split()
        parts = [" ".join(words[i:i + n]) for i in range(0, len(words), n)] or [text]
        if len(parts) > 1 and len(parts[-1].split()) <= 1:  # avoid a lonely one-word caption
            last = parts.pop()
            parts[-1] += " " + last
        total = sum(len(x) for x in parts) or 1
        cursor = t
        for x in parts:
            share = d * len(x) / total
            out.append((cursor, cursor + share, x))
            cursor += share
        t += d
    return out


def write_ass(lines, durs, path):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{FONT},{CAPTION_SIZE},&H00FFFFFF,&H000000FF,&H8C000000,&H00000000,-1,0,0,0,100,100,0,0,3,16,0,2,90,90,{CAPTION_BOTTOM},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    rows = [
        f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{text}"
        for a, b, text in caption_chunks(lines, durs)
    ]
    path.write_text(header + "\n".join(rows), encoding="utf-8")


def build_video(data):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    work = OUT / f"work_{stamp}"
    work.mkdir()
    _used_clips.clear()
    _good_clips.clear()

    lines = data["lines"]
    keywords = data["keywords"]

    durs, audio_list, seg_list = [], [], []

    # 1. Generate Telugu voiceover.
    for i, line in enumerate(lines):
        audio = work / f"a{i}.mp3"
        print(
            f"Generating Telugu voice {i + 1}/{len(lines)}...",
            flush=True,
        )
        try:
            asyncio.run(tts(line, audio))
            if not audio.exists() or audio.stat().st_size == 0:
                raise RuntimeError("Audio file is missing or empty")
            durs.append(duration(audio) + 0.15)
            audio_list.append(audio.name)
        except Exception as e:
            print(f"VOICE ERROR on line {i + 1}: {e}", flush=True)
            raise

    total = sum(durs)
    if total > 58:
        raise TooLong(f"video would be {total:.0f}s; Shorts must be under 60s")

    # 2. Download footage matching each narration line.
    for i, d in enumerate(durs):
        seg = work / f"seg{i}.mp4"
        clip = work / f"clip{i}.mp4"
        query = keywords[i]

        print(
            f"Scene {i + 1}/{len(durs)} narration: {lines[i]}",
            flush=True,
        )
        print(f"Scene {i + 1} visual search: {query}", flush=True)

        fetch_clip(query, clip)

        run(
            [
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", clip.name,
                "-t", f"{d:.2f}",
                "-vf",
                (
                    f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},setsar=1,fps=30,format=yuv420p"
                ),
                "-an", "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                seg.name,
            ],
            cwd=work,
        )
        seg_list.append(seg.name)

    # 3. Concatenate audio and video.
    (work / "a.txt").write_text(
        "".join(f"file '{name}'\n" for name in audio_list),
        encoding="utf-8",
    )
    (work / "v.txt").write_text(
        "".join(f"file '{name}'\n" for name in seg_list),
        encoding="utf-8",
    )

    run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", "a.txt",
            "-af", "apad",
            "-t", f"{total:.2f}",
            "-c:a", "libmp3lame",
            "audio.mp3",
        ],
        cwd=work,
    )
    run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", "v.txt",
            "-c", "copy",
            "video.mp4",
        ],
        cwd=work,
    )

    # 4. Burn Telugu captions and combine audio/video.
    write_ass(lines, durs, work / "subs.ass")
    final = OUT / f"short_{stamp}.mp4"

    run(
        [
            "ffmpeg", "-y",
            "-i", "video.mp4",
            "-i", "audio.mp3",
            "-vf", "ass=subs.ass",
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "160k",
            "-t", f"{total:.2f}",
            str(final.resolve()),
        ],
        cwd=work,
    )
    return final


def make_one(retries=3):
    """Generate a script and video, retrying scripts that are too long."""
    last = None
    for _ in range(retries):
        data = generate_script()
        print("Topic:", data["topic"])
        try:
            video = None
            if os.environ.get("CINEMATIC", "1") != "0":
                try:
                    import character_story
                    video = character_story.build(data, OUT)
                except character_story.TooLong:
                    raise TooLong("cinematic video too long")
                except Exception as e:  # noqa: BLE001
                    print(f"Cinematic mode unavailable ({e}). Using stock footage instead.", flush=True)
            if video is None:
                video = build_video(data)
        except TooLong as e:
            print("Script too long, trying another:", e, flush=True)
            last = e
            continue

        video.with_suffix(".json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        USED.write_text(
            json.dumps(
                load_used() + [data["topic"]],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return data, video

    raise RuntimeError(
        f"Could not make a short enough script after {retries} tries: {last}"
    )


def make_preview_copy(final):
    """Create a smaller Telegram preview."""
    out = final.with_name(final.stem + "_preview.mp4")
    run(
        [
            "ffmpeg", "-y",
            "-i", str(final.resolve()),
            "-vf", "scale=540:960",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "96k",
            str(out.resolve()),
        ]
    )
    return out


def create_preview(st=None):
    """Make a Short and send it to Telegram for approval."""
    import state
    import tg

    own_state = st is None
    if own_state:
        st = state.load()

    data, video = make_one()
    draft_id = st["next_id"]
    st["next_id"] += 1

    pending = OUT / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    shutil.copy(video, pending / f"short_{draft_id}.mp4")

    preview = make_preview_copy(video)

    tg.send_message(
        f"📝 Script for Short #{draft_id} ({data['topic']}), "
        "in case you want to check the facts:\n\n"
        + "\n".join(data["lines"])
        + (("\n\n🎭 Characters:\n" + "\n".join(data["character_notes"])) if data.get("character_notes") else "")
    )
    tg.send_video(
        preview,
        (
            f"Short #{draft_id}: {data['topic']}\n"
            f"{data['title']}\n\n"
            "Approve to upload to YouTube."
        ),
        tg.buttons(draft_id),
    )

    st["drafts"][str(draft_id)] = {
        "status": "upload_pending",
        "topic": data["topic"],
        "data": {
            k: data[k]
            for k in ("title", "description", "tags")
        },
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    if own_state:
        state.save(st)

    return draft_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--upload",
        action="store_true",
        help="upload straight after creating (no approval)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="send to Telegram and wait for approval",
    )
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Set the GEMINI_API_KEY environment variable first.")

    if args.preview:
        try:
            create_preview()
        except Exception as e:
            try:
                import tg
                tg.send_message(f"⚠️ Could not make today's Short: {e}")
            except Exception as tg_err:
                print(
                    f"(Telegram notice also failed: {tg_err})",
                    flush=True,
                )
            raise
        return

    data, video = make_one()
    print("Created:", video)

    if args.upload:
        from upload import upload
        vid = upload(video, data, privacy="private")
        print(
            "Uploaded as private. Review and publish in YouTube Studio: "
            f"https://studio.youtube.com/video/{vid}/edit"
        )


if __name__ == "__main__":
    main()
