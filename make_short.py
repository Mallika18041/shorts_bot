"""Telugu historical love-story Shorts generator.

Usage:
    python make_short.py            # make one video in output/
    python make_short.py --upload   # make one video and upload it straight away (no approval)
    python make_short.py --preview  # make one video and send it to Telegram; upload only after you approve
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
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
FONT = "Noto Sans Telugu"
W, H = 1080, 1920


class TooLong(ValueError):
    pass


def load_used():
    return json.loads(USED.read_text()) if USED.exists() else []


def generate_script():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    used = load_used()[-60:]
    prompt = f"""You write viral YouTube Shorts scripts in simple, spoken Telugu (తెలుగు).
Topic style: one real HISTORICAL LOVE STORY or well-known legend, told as a complete story
(India first: Telugu lands, Kakatiya, Vijayanagara, Mughal, Rajput, Maratha eras; also world history).
Do not repeat these topics: {used}

Rules:
- 9 to 11 short lines, each at most 12 words, natural spoken Telugu.
- Line 1 is a strong hook that creates curiosity in 2 seconds.
- Tell the story in order, end on an emotional or surprising moment, and make the last line
  link back to the hook so people watch again.
- Total spoken length about 40-52 seconds.
- Stay faithful to what history records. Never invent facts, quotes or dialogue as if they were real.
  If it is a legend or historians disagree, say so in the narration (for example 'ఇది జానపద కథ').
- Respectful tone: no sexual content, no graphic violence, nothing that attacks a religion,
  caste or community. No health or financial claims.

Return JSON with keys:
topic (short English, name the couple), title (Telugu, under 70 chars),
description (Telugu, 2 lines, mention the era or place),
tags (list of 8 strings, mix of Telugu and English), lines (list of Telugu strings),
keywords (list, same length as lines, each a 1-3 word ENGLISH stock-video search term for a
generic matching visual such as 'old palace', 'sunset silhouette', 'candle light', 'rain window'.
Never use people's names in keywords)."""
    r = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    data = json.loads(r.text)
    assert len(data["lines"]) == len(data["keywords"]), "lines/keywords mismatch"
    return data


async def tts(text, path):
    last_error = None
    for attempt in range(1, 4):
        try:
            await edge_tts.Communicate(text, VOICE, rate="+5%").save(str(path))
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError("Audio file is missing or empty")
            return
        except Exception as e:
            last_error = e
            if path.exists():
                path.unlink()
            if attempt < 3:
                delay = attempt * 3
                print(f"Voice connection failed (attempt {attempt}/3). Retrying in {delay} seconds...", flush=True)
                await asyncio.sleep(delay)
    raise RuntimeError(f"Telugu voice failed after 3 attempts: {last_error}") from last_error

def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)]
    )
    return float(out)


def run(cmd, cwd=None):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        print("COMMAND FAILED:", " ".join(map(str, cmd)), flush=True)
        print("FFMPEG ERROR:", result.stderr[-6000:], flush=True)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, stderr=result.stderr
        )


def _download(url, dest):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
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
    links = []
    for v in r.json().get("videos", []):
        files = [
            f for f in v.get("video_files", [])
            if f.get("file_type") == "video/mp4"
            and (f.get("height") or 0) > (f.get("width") or 0)
            and (f.get("height") or 0) >= 1280
        ]
        if files:
            links.append(min(files, key=lambda f: f["height"])["link"])
    if not links:
        return False
    _download(random.choice(links), dest)
    return True


def _pixabay(query, dest):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return False
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": query, "per_page": 20, "safesearch": "true"},
        timeout=30,
    )
    r.raise_for_status()
    vertical, other = [], []
    for h in r.json().get("hits", []):
        for size in ("large", "medium"):
            f = (h.get("videos") or {}).get(size) or {}
            if f.get("url") and f.get("height", 0) >= 720:
                (vertical if f["height"] > f["width"] else other).append(f["url"])
                break
    pool = vertical or other
    if not pool:
        return False
    _download(random.choice(pool), dest)
    return True


def fetch_clip(query, dest):
    """Real stock footage from Pexels, then Pixabay, then the local test clip as a last resort."""
    for source in (_pexels, _pixabay):
        name = source.__name__[1:]
        try:
            if source(query, dest):
                print(f"Footage for '{query}' from {name}", flush=True)
                return
        except Exception as e:
            print(f"{name} failed for '{query}': {e}", flush=True)

    local = Path(__file__).resolve().parent / "assets" / "background_test.mp4"
    if not local.exists():
        raise FileNotFoundError(f"No footage source worked and the local clip is missing: {local}")
    dest.write_bytes(local.read_bytes())
    print(f"Using local fallback footage for: {query}", flush=True)


def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def write_ass(lines, durs, path):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{FONT},74,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,520,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    t = 0.0
    rows = []
    for text, d in zip(lines, durs):
        rows.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Default,,0,0,0,,{text}")
        t += d
    path.write_text(header + "\n".join(rows), encoding="utf-8")


def build_video(data):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    work = OUT / f"work_{stamp}"
    work.mkdir()
    lines, keywords = data["lines"], data["keywords"]

    # 1. voiceover, one file per line so captions stay in sync
    durs, audio_list, seg_list = [], [], []
    for i, line in enumerate(lines):
        a = work / f"a{i}.mp3"
        print(f"Generating Telugu voice {i + 1}/{len(lines)}...", flush=True)
        try:
            asyncio.run(tts(line, a))
            if not a.exists() or a.stat().st_size == 0:
                raise RuntimeError("Audio file is missing or empty")
            durs.append(duration(a) + 0.15)
            audio_list.append(a.name)
        except Exception as e:
            print(f"VOICE ERROR on line {i + 1}: {e}", flush=True)
            raise


    total = sum(durs)
    if total > 58:
        raise TooLong(f"video would be {total:.0f}s; Shorts must be under 60s")

        # 2. Download relevant Pexels footage for each narration line
    for i, d in enumerate(durs):
        seg = work / f"seg{i}.mp4"
        clip = work / f"clip{i}.mp4"
        query = keywords[i]
        print(f"Downloading footage {i + 1}/{len(durs)}: {query}", flush=True)

        fetch_clip(query, clip)

        run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip),
            "-t", f"{d:.2f}",
            "-vf", (
                f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps=30,format=yuv420p"
            ),
            "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p",
            seg.name
        ], cwd=work)

        seg_list.append(seg.name)
    # 3. concat audio + video
    (work / "a.txt").write_text("".join(f"file '{n}'\n" for n in audio_list))
    (work / "v.txt").write_text("".join(f"file '{n}'\n" for n in seg_list))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "a.txt",
     "-af", "apad", "-t", f"{total:.2f}", "-c:a", "libmp3lame", "audio.mp3"], cwd=work)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "v.txt", "-c", "copy", "video.mp4"], cwd=work)

    # 4. burn Telugu captions (libass handles Telugu shaping) and mux
    write_ass(lines, durs, work / "subs.ass")
    final = OUT / f"short_{stamp}.mp4"
    run(["ffmpeg", "-y", "-i", "video.mp4", "-i", "audio.mp3",
         "-vf", "ass=subs.ass", "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}",
         str(final.resolve())], cwd=work)
    return final


def make_one(retries=3):
    """Write a script and build its video. A too-long script is replaced by a fresh one."""
    last = None
    for _ in range(retries):
        data = generate_script()
        print("Topic:", data["topic"])
        try:
            video = build_video(data)
        except TooLong as e:
            print("Script too long, trying another:", e, flush=True)
            last = e
            continue
        video.with_suffix(".json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        USED.write_text(
            json.dumps(load_used() + [data["topic"]], ensure_ascii=False), encoding="utf-8"
        )
        return data, video
    raise RuntimeError(f"Could not make a short enough script after {retries} tries: {last}")


def make_preview_copy(final):
    """Small copy for Telegram. The full-quality file is what gets uploaded to YouTube."""
    out = final.with_name(final.stem + "_preview.mp4")
    run(["ffmpeg", "-y", "-i", str(final.resolve()), "-vf", "scale=540:960",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-c:a", "aac", "-b:a", "96k", str(out.resolve())])
    return out


def create_preview(st=None):
    """Make a Short and send it to Telegram. It is uploaded only after you tap Approve."""
    import state
    import tg

    own_state = st is None
    if own_state:
        st = state.load()
    data, video = make_one()

    draft_id = st["next_id"]
    st["next_id"] += 1
    pending = OUT / "pending"  # the workflow saves this folder so the approved video can be uploaded later
    pending.mkdir(parents=True, exist_ok=True)
    shutil.copy(video, pending / f"short_{draft_id}.mp4")
    preview = make_preview_copy(video)

    tg.send_message(
        f"📝 Script for Short #{draft_id} ({data['topic']}), in case you want to check the facts:\n\n"
        + "\n".join(data["lines"])
    )
    tg.send_video(
        preview,
        f"Short #{draft_id}: {data['topic']}\n{data['title']}\n\nApprove to upload to YouTube.",
        tg.buttons(draft_id),
    )
    st["drafts"][str(draft_id)] = {
        "status": "upload_pending",
        "topic": data["topic"],
        "data": {k: data[k] for k in ("title", "description", "tags")},
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    if own_state:
        state.save(st)
    return draft_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true", help="upload straight after creating (no approval)")
    ap.add_argument("--preview", action="store_true", help="send to Telegram and wait for your approval")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Set the GEMINI_API_KEY environment variable first.")

    if args.preview:
        try:
            create_preview()
        except Exception as e:
            import tg
            tg.send_message(f"⚠️ Could not make today's Short: {e}")
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
