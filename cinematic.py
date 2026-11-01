"""Cinematic look for the Shorts: story-matched images with slow camera moves, a film grade,
a storyteller voice, clean captions and a soft music bed.

Everything here degrades gracefully. If one source fails (no image access, voice limit, no internet
source), the next one is tried, and make_short.py falls back to stock video as a last resort.

Settings (all optional, GitHub "Variables" or environment):
  TTS_ENGINE      gemini (default, falls back to edge) | edge
  GEMINI_VOICE    Gemini voice name, default Charon (deep storyteller). Try Kore, Aoede, Sulafat, Algieba.
  TTS_MODEL       default gemini-2.5-flash-preview-tts
  VOICE_STYLE     instruction that sets the narration style
  EDGE_VOICE      fallback voice, default te-IN-MohanNeural (deeper) or te-IN-ShrutiNeural
  IMAGE_SOURCES   comma list, in order: ai,wiki   (default "ai,wiki"; use "wiki" or "" to skip AI images)
  IMAGE_MODEL     default gemini-2.5-flash-image
  MUSIC           1 (default) or 0
"""
import asyncio
import base64
import os
import random
import re
import subprocess
import time
from pathlib import Path

import requests

W, H, FPS = 1080, 1920, 30
PAD = 0.30  # silence added after every narration line, so audio and scenes stay in step

STYLE = (
    "cinematic film still, dramatic natural lighting, rich warm color grading, shallow depth of field, "
    "painterly realism, vertical 9:16 composition, no text, no captions, no watermark, no logos"
)
UA = "ShortsBot/1.0 (https://github.com/Mallika18041/shorts_bot; personal educational channel)"

CREDITS = []          # image credits that must be shown in the video description
_ai_disabled = False  # set when image generation isn't available, so we stop asking


def _env(name, default=""):
    return os.environ.get(name) or default


def _run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       text=True, errors="replace")
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-25:])
        raise RuntimeError(f"{cmd[0]} failed (exit {p.returncode}):\n{tail}")


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return float(out)


# --------------------------------------------------------------------------- voice

def _gemini_tts(text, dest, work):
    """One line of narration -> mp3 using Gemini's text-to-speech. Raises on any problem."""
    key = _env("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = _env("TTS_MODEL", "gemini-2.5-flash-preview-tts")
    style = _env(
        "VOICE_STYLE",
        "Narrate the following in Telugu like a warm, slow, emotional documentary storyteller, "
        "with natural pauses and a gentle dramatic tone:",
    )
    body = {
        "contents": [{"parts": [{"text": f"{style}\n\n{text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": _env("GEMINI_VOICE", "Charon")}}},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last = "no answer"
    for attempt in range(5):
        r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            time.sleep(12 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Gemini voice HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        inline = next((p["inlineData"] for p in parts if "inlineData" in p), None)
        if not inline:
            last = "no audio in the answer"
            time.sleep(3)
            continue
        m = re.search(r"rate=(\d+)", inline.get("mimeType", ""))
        rate = int(m.group(1)) if m else 24000
        pcm = Path(work) / (Path(dest).stem + ".pcm")
        pcm.write_bytes(base64.b64decode(inline["data"]))
        trim = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                "silenceremove=start_periods=1:start_threshold=-45dB,areverse")
        _run(["ffmpeg", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", pcm.name,
              "-af", f"{trim},apad=pad_dur={PAD}", "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", Path(dest).name],
             cwd=work)
        pcm.unlink(missing_ok=True)
        return
    raise RuntimeError(f"Gemini voice failed: {last}")


async def _edge_tts(text, dest):
    import edge_tts

    voice = _env("EDGE_VOICE", _env("TTS_VOICE", "te-IN-MohanNeural"))
    last = None
    for attempt in range(1, 4):
        try:
            await edge_tts.Communicate(text, voice, rate="-6%", pitch="-2Hz").save(str(dest))
            if Path(dest).exists() and Path(dest).stat().st_size > 0:
                return
            raise RuntimeError("empty audio file")
        except Exception as e:  # noqa: BLE001
            last = e
            Path(dest).unlink(missing_ok=True)
            await asyncio.sleep(attempt * 3)
    raise RuntimeError(f"Edge voice failed after 3 attempts: {last}")


def voice_lines(lines, work):
    """Narrate every line. Returns (durations, audio file names). The same voice is used for all lines:
    if Gemini fails on any line, the whole video is redone with the Edge voice."""
    work = Path(work)

    def finish(name):
        return duration(work / name)

    if _env("TTS_ENGINE", "gemini").lower() == "gemini" and _env("GEMINI_API_KEY"):
        try:
            names = []
            for i, line in enumerate(lines):
                print(f"Gemini voice {i + 1}/{len(lines)}...", flush=True)
                name = f"a{i}.wav"
                _gemini_tts(line, work / name, work)
                names.append(name)
                time.sleep(1.0)
            print("Voice engine: Gemini", flush=True)
            return [finish(n) for n in names], names
        except Exception as e:  # noqa: BLE001
            print(f"Gemini voice not available ({e}). Using the Edge voice for the whole video.", flush=True)

    names = []
    for i, line in enumerate(lines):
        print(f"Edge voice {i + 1}/{len(lines)}...", flush=True)
        raw = work / f"raw{i}.mp3"
        name = f"a{i}.wav"
        asyncio.run(_edge_tts(line, raw))
        _run(["ffmpeg", "-y", "-i", raw.name, "-af", f"apad=pad_dur={PAD}", "-ar", "44100", "-ac", "1",
              "-c:a", "pcm_s16le", name], cwd=work)
        raw.unlink(missing_ok=True)
        names.append(name)
    print("Voice engine: Edge", flush=True)
    return [finish(n) for n in names], names


def speed_up(names, work, factor):
    """Speak a little faster (pitch stays the same). Returns the new durations."""
    work = Path(work)
    for n in names:
        tmp = work / ("fast_" + n)
        _run(["ffmpeg", "-y", "-i", n, "-af", f"atempo={factor:.4f}", "-c:a", "pcm_s16le", tmp.name], cwd=work)
        tmp.replace(work / n)
    return [duration(work / n) for n in names]


# --------------------------------------------------------------------------- images

def _ai_image(prompt, dest):
    """Generate one cinematic still with Gemini. Returns True on success."""
    global _ai_disabled
    key = _env("GEMINI_API_KEY")
    if _ai_disabled or not key:
        return False
    model = _env("IMAGE_MODEL", "gemini-2.5-flash-image")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gen = {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}}
    for attempt in range(3):
        body = {"contents": [{"parts": [{"text": f"{prompt}. {STYLE}"}]}], "generationConfig": gen}
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
        except requests.RequestException as e:
            print(f"AI image request failed: {e}", flush=True)
            return False
        if r.status_code == 400 and "imageConfig" in gen:
            gen = {"responseModalities": ["IMAGE"]}  # this model has no aspect ratio setting
            continue
        if r.status_code in (401, 403, 404):
            print(f"AI images are not available on this key/model (HTTP {r.status_code}). Skipping them.", flush=True)
            _ai_disabled = True
            return False
        if r.status_code == 429:
            if attempt == 0:
                time.sleep(20)
                continue
            print("AI image quota reached. Skipping AI images for this video.", flush=True)
            _ai_disabled = True
            return False
        if r.status_code != 200:
            print(f"AI image HTTP {r.status_code}: {r.text[:150]}", flush=True)
            return False
        parts = ((((r.json().get("candidates") or [{}])[0]).get("content") or {}).get("parts")) or []
        inline = next((p["inlineData"] for p in parts if "inlineData" in p), None)
        if not inline:
            print("AI image: the model returned no picture for this scene.", flush=True)
            return False
        Path(dest).write_bytes(base64.b64decode(inline["data"]))
        return True
    return False


def _license_ok(lic):
    l = lic.lower()
    if any(t in l for t in ("-nc", " nc", "-nd", " nd", "non-commercial", "noderiv", "fair use")):
        return False
    return "public domain" in l or l.startswith("pd") or "cc0" in l or l.startswith("cc by") or l.startswith("cc-by")


def _wiki_image(query, dest):
    """A free historical image from Wikimedia Commons. Credits are collected for the description."""
    if not query or not query.strip():
        return False
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            headers={"User-Agent": UA},
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 15,
                "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
            },
            timeout=30,
        )
        r.raise_for_status()
        pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
        for p in pages:
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if min(ii.get("width", 0), ii.get("height", 0)) < 700:
                continue
            meta = ii.get("extmetadata") or {}
            lic = (meta.get("LicenseShortName") or {}).get("value", "")
            if not _license_ok(lic):
                continue
            src = ii.get("thumburl") or ii.get("url")
            with requests.get(src, headers={"User-Agent": UA}, stream=True, timeout=60) as dl:
                dl.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in dl.iter_content(1 << 20):
                        f.write(chunk)
            if not lic.lower().startswith("pd") and "public domain" not in lic.lower() and "cc0" not in lic.lower():
                artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()[:60]
                title = p.get("title", "").replace("File:", "")[:60]
                CREDITS.append(f"{title} - {artist or 'unknown'} ({lic}), Wikimedia Commons")
            print(f"Image from Wikimedia Commons: {p.get('title')} [{lic}]", flush=True)
            return True
    except Exception as e:  # noqa: BLE001
        print(f"Wikimedia search failed for '{query}': {e}", flush=True)
    return False


def _sniff(path):
    """Give the file the right extension for what is really inside it. Returns the new path."""
    path = Path(path)
    head = path.read_bytes()[:12]
    if head.startswith(b"\x89PNG"):
        ext = ".png"
    elif head.startswith(b"\xff\xd8"):
        ext = ".jpg"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        ext = ".webp"
    else:
        path.unlink(missing_ok=True)
        return None
    final = path.with_suffix(ext)
    path.replace(final)
    return final


def scene_image(image_prompt, wiki_query, dest):
    """First working source wins. `dest` has no extension. Returns the picture's path, or None."""
    dest = Path(dest).with_suffix(".img")
    sources = [s.strip() for s in _env("IMAGE_SOURCES", "ai,wiki").split(",") if s.strip()]
    for s in sources:
        got = False
        if s == "ai" and image_prompt and _ai_image(image_prompt, dest):
            print("Image from Gemini", flush=True)
            got = True
        elif s == "wiki" and _wiki_image(wiki_query, dest):
            got = True
        if got:
            final = _sniff(dest)
            if final:
                return final
            print("That picture could not be read; trying the next source.", flush=True)
    return None


# --------------------------------------------------------------------------- camera + grade

MOVES = ("in", "pan_right", "out", "pan_left")


def ken_burns(image, out, dur, idx, work):
    """Turn a still picture into a slow, graded, cinematic clip of `dur` seconds."""
    n = max(2, round(dur * FPS))
    move = MOVES[idx % len(MOVES)]
    cx, cy = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    if move == "in":
        z, x, y = f"1+0.16*on/{n}", cx, cy
    elif move == "out":
        z, x, y = f"1.16-0.16*on/{n}", cx, cy
    elif move == "pan_right":
        z, x, y = "1.14", f"(iw-iw/zoom)*on/{n}", cy
    else:
        z, x, y = "1.14", f"(iw-iw/zoom)*(1-on/{n})", cy
    bw, bh = 1728, 3072  # 1.6x the output size keeps slow movement smooth
    vf = (
        f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},setsar=1,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},"
        "eq=contrast=1.07:saturation=1.10:brightness=-0.02,"
        "vignette=angle=PI/4.5,noise=alls=5:allf=t,format=yuv420p,"
        f"fade=t=in:st=0:d=0.2,fade=t=out:st={max(dur - 0.2, 0):.2f}:d=0.2"
    )
    _run(["ffmpeg", "-y", "-i", str(image), "-vf", vf, "-frames:v", str(n), "-r", str(FPS),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", str(out)], cwd=work)


# --------------------------------------------------------------------------- captions

def _ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _chunks(text, max_words=6):
    words = text.replace("{", "").replace("}", "").split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)] or [text]


def write_ass(lines, durs, path, font):
    """Clean lower-third captions in short chunks, with a soft fade. Timed to the spoken part of each line."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{font},66,&H00FFFFFF,&H000000FF,&H00101010,&H78000000,-1,0,0,0,100,100,1,0,1,5,3,2,100,100,380,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    t, rows = 0.0, []
    for text, d in zip(lines, durs):
        speech = max(d - PAD * 0.6, 0.5)
        parts = _chunks(text)
        total = sum(len(p) for p in parts) or 1
        a = t
        for p in parts:
            b = a + speech * len(p) / total
            rows.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Default,,0,0,0,,{{\\fad(120,120)}}{p}")
            a = b
        t += d
    Path(path).write_text(header + "\n".join(rows), encoding="utf-8")


# --------------------------------------------------------------------------- music

def music_bed(total, dest, work):
    """A soft background bed. Uses your own tracks in assets/music/ if you add any, else a quiet synth pad."""
    if _env("MUSIC", "1") == "0":
        return False
    fade = f"afade=t=in:d=2,afade=t=out:st={max(total - 2.5, 0):.2f}:d=2.5"
    lib = Path(__file__).resolve().parent / "assets" / "music"
    tracks = [p for p in lib.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")] if lib.exists() else []
    try:
        if tracks:
            track = random.choice(tracks)
            print(f"Music: {track.name}", flush=True)
            _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(track), "-t", f"{total:.2f}",
                  "-af", f"{fade},volume=0.6", str(dest)], cwd=work)
        else:
            print("Music: soft synth pad (add your own tracks to assets/music/ for better music)", flush=True)
            freqs = (110.0, 164.81, 220.0, 261.63)
            cmd = ["ffmpeg", "-y"]
            for f in freqs:
                cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=44100"]
            graph = (
                "[0][1][2][3]amix=inputs=4:normalize=0,tremolo=f=0.12:d=0.5,lowpass=f=1100,"
                f"aecho=0.8:0.8:90|180:0.45|0.3,volume=0.6,{fade}"
            )
            cmd += ["-filter_complex", graph, "-t", f"{total:.2f}", str(dest)]
            _run(cmd, cwd=work)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"Music skipped: {e}", flush=True)
        return False
