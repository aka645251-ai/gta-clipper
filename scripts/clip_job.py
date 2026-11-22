#!/usr/bin/env python3
"""One clip job for gta-clipper: one stream link, many finished clips.

Auto mode asks Gemini to pick the top N moments from the stream's captions
and, for each, writes a short catchy title, hashtags and a 1-10 virality
score. Each moment becomes a vertical clip (facecam on top, gameplay below,
burned captions). Results go to jobs/<job_id>.json; clips to out/<job_id>/.
Secrets come from the environment only and are never printed.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash"]
FACECAM_PROMPT = (
    "This is one frame from a video game live stream. Is there a webcam (facecam) "
    "overlay showing the streamer's face? Reply with JSON only, no other text: "
    '{"facecam_found": true or false, "x": number, "y": number, "w": number, '
    '"h": number, "confidence": number, "notes": string}. '
    "x, y, w, h are fractions of the full frame width and height between 0 and 1, "
    "with the origin at the top-left corner. If there is no facecam, set "
    "facecam_found to false and x, y, w, h to 0."
)
PICK_PROMPT = (
    "Below are subtitle lines from one section of a video game live stream, each "
    "with its time. Pick the TOP %d best, most separate moments to cut as short "
    "vertical clips: exciting, funny or shocking, each understandable on its own. "
    'Reply with JSON only: {"moments": [{"start": "mm:ss", "end": "mm:ss", '
    '"title": "short catchy YouTube Shorts title in English", '
    '"hashtags": ["#gta6"], "score": 1-10 virality, "reason": "one line"}]}. '
    "Every clip must be between 20 and 90 seconds long, inside the section, and "
    "the moments must not overlap each other. Give exactly %d moments if possible.\n\n"
)
TITLE_PROMPT = (
    "These are the subtitles of one short gameplay clip. Write a short catchy "
    'YouTube Shorts title in English and 3-5 hashtags. Reply JSON only: '
    '{"title": "...", "hashtags": ["#..."]}. The title must not end with the '
    "hashtags; they come separately.\n\n"
)
FACE_H = 690
BOTTOM_H = 1920 - FACE_H
FONT_STYLE = ("force_style=FontName=DejaVu Sans,FontSize=52,Bold=1,"
              "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Shadow=1,"
              "Alignment=2,MarginV=60")

STATUS = {"status": "running", "steps": {}, "clips": []}


def parse_time(text):
    parts = [p for p in str(text).strip().split(":") if p != ""]
    if not parts or len(parts) > 3:
        raise ValueError("bad time: %r" % text)
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def fmt_time(seconds):
    seconds = int(round(seconds))
    return "%02d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    except FileNotFoundError as exc:
        return 127, "", "command not found: %s" % exc


def ytdlp(args, timeout):
    base = ["yt-dlp", "--no-playlist", "--no-warnings"]
    cookies = Path(tempfile.gettempdir()) / "yt_cookies.txt"
    if cookies.exists():
        base += ["--cookies", str(cookies)]
    rc, out, err = run(base + ["--js-runtimes", "node"] + args, timeout)
    if rc != 0 and "no such option" in (err or "").lower():
        rc, out, err = run(base + args, timeout)
    return rc, out, err


def classify_error(text):
    low = (text or "").lower()
    if "not a bot" in low or "sign in to confirm" in low:
        return "bot_block"
    if "http error 429" in low or "too many requests" in low:
        return "rate_limited"
    if "private video" in low or "video unavailable" in low:
        return "video_unavailable"
    if "live event will begin" in low or "premieres in" in low:
        return "not_ready"
    return "other"


def gemini(parts, timeout=90):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None, "GEMINI_API_KEY secret is not set"
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"responseMimeType": "application/json"}}).encode("utf-8")
    last = ""
    for model in GEMINI_MODELS:
        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(text), ""
        except urllib.error.HTTPError as exc:
            last = "%s: HTTP %s" % (model, exc.code)
        except (urllib.error.URLError, ValueError, KeyError, IndexError, OSError) as exc:
            last = "%s: %s" % (model, exc)
    return None, last


def save_status(job_id, data):
    jobs = Path("jobs")
    jobs.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    (jobs / ("%s.json" % job_id)).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_captions(url, out_dir):
    subs = out_dir / "subs"
    subs.mkdir(parents=True, exist_ok=True)
    rc, _, err = ytdlp(["--skip-download", "--write-auto-subs", "--write-subs",
                        "--sub-langs", "en.*,en", "--sub-format", "json3",
                        "-o", str(subs / "%(id)s.%(ext)s"), url], 300)
    files = sorted(subs.glob("*.json3"))
    if not files:
        return None, (err or "")
    data = json.loads(files[0].read_text(encoding="utf-8"))
    lines = []
    for ev in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in ev.get("segs", []) if s).strip()
        if not text or text == "\n":
            continue
        lines.append({"t": ev.get("tStartMs", 0) / 1000.0,
                      "d": ev.get("dDurationMs", 3000) / 1000.0, "text": text.replace("\n", " ")})
    return lines, ""


def clean_tags(tags):
    out = []
    for t in (tags or [])[:5]:
        t = str(t).strip().replace(" ", "")
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        out.append(t)
    return out


def pick_moments(lines, start, window, count):
    in_window = [l for l in lines if start <= l["t"] <= start + window]
    if not in_window:
        return None, "captions empty in the window"
    listing = "\n".join("%s %s" % (fmt_time(l["t"])[3:], l["text"][:110]) for l in in_window[:400])
    result, err = gemini([{"text": (PICK_PROMPT % (count, count)) + listing}])
    moments = (result or {}).get("moments") or []
    chosen = []
    for m in sorted(moments, key=lambda x: -float(x.get("score", 0) or 0)):
        try:
            cs = start + parse_time(m["start"])
            ce = start + parse_time(m["end"])
        except (KeyError, ValueError):
            continue
        if not (start <= cs < ce <= start + window + 5) or ce - cs < 20 or ce - cs > 90:
            continue
        if any(min(ce, c["end"]) - max(cs, c["start"]) > 20 for c in chosen):
            continue
        chosen.append({"start": cs, "end": ce, "title": str(m.get("title", "") or "")[:120],
                       "hashtags": clean_tags(m.get("hashtags")),
                       "score": max(1, min(10, int(float(m.get("score", 5) or 5)))),
                       "reason": str(m.get("reason", "") or "")[:200]})
        if len(chosen) >= count:
            break
    if not chosen:
        return None, "gemini ne sahi moments nahi diye (%s)" % (err or "empty")
    chosen.sort(key=lambda c: -c["score"])
    return chosen, ""


def title_for(lines, start, seconds):
    text = " ".join(l["text"] for l in lines if start <= l["t"] <= start + seconds)[:3000]
    if not text.strip():
        return "", []
    result, _ = gemini([{"text": TITLE_PROMPT + text}])
    if not result:
        return "", []
    return str(result.get("title", "") or "")[:120], clean_tags(result.get("hashtags"))


def download_section(url, start, seconds, out_dir, tag, max_height=1080):
    section = "*%s-%s" % (fmt_time(start), fmt_time(start + seconds + 2))
    fmt = "bv*[height<=%d]+ba/b[height<=%d]" % (max_height, max_height)
    rc, _, err = ytdlp(["-f", fmt, "--download-sections", section,
                        "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
                        "-o", str(out_dir / ("src-%s.%%(ext)s" % tag)), url], 900)
    files = sorted(out_dir.glob("src-%s.*" % tag))
    if rc != 0 or not files:
        return None, (err or "")
    return files[0], ""


def facecam_box(clip, out_dir):
    frame = out_dir / "frame.jpg"
    rc, _, _ = run(["ffmpeg", "-y", "-ss", "3", "-i", str(clip), "-frames:v", "1",
                    "-q:v", "2", str(frame)], 120)
    if rc != 0 or not frame.exists():
        return None
    image = base64.b64encode(frame.read_bytes()).decode("ascii")
    result, _ = gemini([{"text": FACECAM_PROMPT},
                        {"inline_data": {"mime_type": "image/jpeg", "data": image}}])
    if not result or not result.get("facecam_found"):
        return None
    try:
        x, y, w, h = (float(result[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.02 and y + h <= 1.02):
        return None
    return (x, y, w, h)


def probe_size(clip):
    rc, out, _ = run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                      "-select_streams", "v:0", "-of", "json", str(clip)], 60)
    try:
        dims = json.loads(out)["streams"][0]
        return int(dims["width"]), int(dims["height"])
    except (ValueError, KeyError, IndexError):
        return 1920, 1080


def render(clip, box, srt_path, out_path):
    w, h = probe_size(clip)
    if box:
        fx, fy, fw, fh = box
        chain = ("[0:v]split=2[a][b];"
                 "[a]crop=iw*%(fw).4f:ih*%(fh).4f:iw*%(fx).4f:ih*%(fy).4f,"
                 "scale=1080:%(face)d:force_original_aspect_ratio=increase,"
                 "crop=1080:%(face)d[top];"
                 "[b]scale=1080:%(bot)d:force_original_aspect_ratio=increase,"
                 "crop=1080:%(bot)d[bot];"
                 "[top][bot]vstack=inputs=2[out]") % {"fw": fw, "fh": fh, "fx": fx, "fy": fy,
                                                      "face": FACE_H, "bot": BOTTOM_H}
    else:
        chain = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[out]"
    if srt_path:
        vf = chain + ";[out]subtitles=%s:'%s'[out2]" % (srt_path, FONT_STYLE)
        out_label = "[out2]"
    else:
        vf = chain
        out_label = "[out]"
    cmd = ["ffmpeg", "-y", "-i", str(clip), "-filter_complex", vf, "-map", out_label,
           "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path)]
    rc, _, err = run(cmd, 1500)
    if rc != 0 or not out_path.exists():
        return (err or "")[-500:]
    return ""


def write_srt(lines, start, seconds, path):
    def ts(t):
        t = max(0.0, min(t, seconds - 0.1))
        ms = int(round(t * 1000))
        return "%02d:%02d:%02d,%03d" % (ms // 3600000, (ms % 3600000) // 60000,
                                        (ms % 60000) // 1000, ms % 1000)
    kept, last_text = [], None
    for l in sorted(lines, key=lambda x: x["t"]):
        if l["t"] + l["d"] <= start or l["t"] >= start + seconds:
            continue
        text = l["text"].replace("  ", " ").strip()
        if not text or text == last_text:
            continue
        kept.append({"a": max(0.0, l["t"] - start), "b": min(seconds, l["t"] + l["d"] - start), "text": text})
        last_text = text
    if not kept:
        return False
    out = []
    for i, k in enumerate(kept, 1):
        if k["b"] <= k["a"]:
            continue
        out += [str(i), "%s --> %s" % (ts(k["a"]), ts(k["b"])), k["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--start", default="00:00:00")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--mode", default="auto", choices=["manual", "auto"])
    ap.add_argument("--window", type=int, default=1800)
    args = ap.parse_args()

    out_dir = Path("out") / args.job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    STATUS.update({"id": args.job_id, "url": args.url, "mode": args.mode,
                   "status": "running", "error": "", "count": args.count})
    start = parse_time(args.start)

    try:
        meta_rc, meta_out, meta_err = ytdlp(["--dump-single-json", "--skip-download", args.url], 240)
        if meta_rc != 0:
            meta_rc, meta_out, meta_err = ytdlp(["--dump-single-json", "--skip-download", args.url], 240)
        if meta_rc == 0:
            try:
                STATUS["title"] = json.loads(meta_out).get("title")
            except ValueError:
                pass
        else:
            raise RuntimeError("video khulta hi nahi (%s): %s" % (
                classify_error(meta_err), (meta_err or "")[-200:].strip()))

        lines, err = fetch_captions(args.url, out_dir)
        if lines is None:
            raise RuntimeError("captions nahi mile (%s)" % classify_error(err))
        STATUS["steps"]["captions"] = "ok (%d lines)" % len(lines)

        if args.mode == "auto":
            moments, err = pick_moments(lines, start, args.window, args.count)
            if not moments:
                raise RuntimeError("best moments nahi mile: %s" % err)
        else:
            t, tags = title_for(lines, start, args.seconds)
            moments = [{"start": start, "end": start + args.seconds, "title": t,
                        "hashtags": tags, "score": 10, "reason": "manual range"}]

        STATUS["steps"]["pick"] = "%d moments" % len(moments)
        box = None
        rank = 0
        for i, m in enumerate(moments, 1):
            rank += 1
            seconds = int(m["end"] - m["start"])
            clip, err = download_section(args.url, m["start"], seconds, out_dir, "c%d" % i)
            if not clip:
                STATUS["clips"].append({"rank": rank, "status": "error",
                                        "error": "download fail (%s)" % classify_error(err),
                                        "start": fmt_time(m["start"]), "seconds": seconds,
                                        "title": m["title"], "hashtags": m["hashtags"],
                                        "score": m["score"]})
                continue
            if box is None:
                box = facecam_box(clip, out_dir)
            srt = out_dir / ("cap-c%d.srt" % i)
            has_subs = write_srt(lines, m["start"], seconds, srt)
            final = out_dir / ("clip-%d.mp4" % i)
            err = render(clip, box, str(srt) if has_subs else "", final)
            if err:
                STATUS["clips"].append({"rank": rank, "status": "error", "error": "render fail: %s" % err,
                                        "start": fmt_time(m["start"]), "seconds": seconds,
                                        "title": m["title"], "hashtags": m["hashtags"], "score": m["score"]})
                continue
            size_mb = final.stat().st_size / 1048576.0
            entry = {"rank": rank, "status": "done", "file": "out/%s/clip-%d.mp4" % (args.job_id, i),
                     "size_mb": round(size_mb, 1), "start": fmt_time(m["start"]), "seconds": seconds,
                     "title": m["title"], "hashtags": m["hashtags"], "score": m["score"],
                     "reason": m["reason"]}
            if size_mb > 95:
                entry.update({"status": "error", "error": "clip %.0f MB, GitHub limit 95 MB" % size_mb})
                final.unlink()
            STATUS["clips"].append(entry)
            STATUS["steps"]["clip-%d" % i] = entry["status"]

        done = sum(1 for c in STATUS["clips"] if c.get("status") == "done")
        if done:
            STATUS["status"] = "done"
            STATUS["facecam"] = "found" if box else "not found (full gameplay)"
        else:
            STATUS["status"] = "error"
            STATUS["error"] = "koi clip nahi bani"
    except Exception as exc:
        STATUS["status"] = "error"
        STATUS["error"] = str(exc)[:500]
    save_status(args.job_id, STATUS)
    return 0 if STATUS["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
