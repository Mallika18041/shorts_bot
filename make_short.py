"""Telugu 'surprising facts' Shorts generator + uploader.

Usage:
    python make_short.py            # make one video in output/
    python make_short.py --upload   # make one video and upload it as PRIVATE
"""
import argparse
import asyncio
import json
import os
import random
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

VOICE = os.environ.get("TTS_VOICE", "te-IN-ShrutiNeural")  # or te-IN-MohanNeural
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
FONT = "Noto Sans Telugu"
W, H = 1080, 1920


def load_used():
    return json.loads(USED.read_text()) if USED.exists() else []


def generate_script():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    used = load_used()[-60:]
    prompt = f"""You write viral YouTube Shorts scripts in simple, spoken Telugu (తెలుగు).
Topic style: one surprising, TRUE, verifiable fact (science, history, space, human body, nature, India).
Do not repeat these topics: {used}

Rules:
- 7 to 9 short lines, each at most 12 words, natural spoken Telugu.
- Line 1 is a strong hook that creates curiosity in 2 seconds.
- The last line asks a question or links back to the hook so the video loops.
- Total spoken length about 35-45 seconds.
- Only facts you are certain are correct. No health or financial claims.

Return JSON with keys:
topic (short English), title (Telugu, under 70 chars), description (Telugu, 2 lines),
tags (list of 8 strings, mix of Telugu and English), lines (list of Telugu strings),
keywords (list, same length as lines, each a 1-3 word ENGLISH stock-video search term)."""
    r = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    data = json.loads(r.text)
    assert len(data["lines"]) == len(data["keywords"]), "lines/keywords mismatch"
    return data


async def tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(str(path))


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)]
    )
    return float(out)


def run(cmd, cwd=None):
    subprocess.run(cmd, check=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def fetch_clip(query, dest):
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    for q in (query, "nature", "city"):  # fallbacks so a run never fails on a bad keyword
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": q, "orientation": "portrait", "per_page": 15},
            timeout=30,
        ).json()
        vids = r.get("videos", [])
        if not vids:
            continue
        v = random.choice(vids)
        files = [f for f in v["video_files"] if f.get("height", 0) > f.get("width", 0)]
        files = [f for f in files if f["width"] <= 1080] or files or v["video_files"]
        best = max(files, key=lambda f: f["width"])
        data = requests.get(best["link"], timeout=120).content
        dest.write_bytes(data)
        return
    raise RuntimeError(f"No stock clip found for {query!r}")


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
        asyncio.run(tts(line, a))
        durs.append(duration(a) + 0.15)
        audio_list.append(a.name)

    total = sum(durs)
    if total > 58:
        print(f"Warning: video is {total:.0f}s; Shorts must be under 60s. Re-run for a shorter script.")

    # 2. one stock clip per line, cropped to 9:16
    for i, (kw, d) in enumerate(zip(keywords, durs)):
        raw = work / f"raw{i}.mp4"
        seg = work / f"seg{i}.mp4"
        fetch_clip(kw, raw)
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", raw.name, "-t", f"{d:.2f}",
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30",
             "-an", "-c:v", "libx264", "-preset", "veryfast", seg.name], cwd=work)
        seg_list.append(seg.name)

    # 3. concat audio + video
    (work / "a.txt").write_text("".join(f"file '{n}'\n" for n in audio_list))
    (work / "v.txt").write_text("".join(f"file '{n}'\n" for n in seg_list))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "a.txt",
         "-af", "apad", "-c:a", "libmp3lame", "audio.mp3"], cwd=work)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "v.txt", "-c", "copy", "video.mp4"], cwd=work)

    # 4. burn Telugu captions (libass handles Telugu shaping) and mux
    write_ass(lines, durs, work / "subs.ass")
    final = OUT / f"short_{stamp}.mp4"
    run(["ffmpeg", "-y", "-i", "video.mp4", "-i", "audio.mp3",
         "-vf", "ass=subs.ass", "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}",
         str(final.resolve())], cwd=work)
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true", help="upload as private after creating")
    args = ap.parse_args()

    for key in ("GEMINI_API_KEY", "PEXELS_API_KEY"):
        if key not in os.environ:
            sys.exit(f"Set the {key} environment variable first (see README.md).")

    data = generate_script()
    print("Topic:", data["topic"])
    video = build_video(data)
    (video.with_suffix(".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    USED.write_text(json.dumps(load_used() + [data["topic"]]))
    print("Created:", video)

    if args.upload:
        from upload import upload
        vid = upload(video, data, privacy="private")
        print("Uploaded as private. Review and publish in YouTube Studio: https://studio.youtube.com/video/%s/edit" % vid)


if __name__ == "__main__":
    main()
