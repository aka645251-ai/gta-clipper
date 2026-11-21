#!/usr/bin/env python3
"""Phase 0 probe for gta-clipper.

Answers four questions, in order, and writes a report:
  1. Can this machine read the YouTube video (metadata)?
  2. Can it fetch the auto-captions (text only, light)?
  3. Can it download just a short section (not the whole stream)?
  4. Can Gemini find the facecam box in one frame of that section?

Secrets are read from the environment only and are never printed.
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

OUT = Path("out")
COOKIES_PATH = Path(tempfile.gettempdir()) / "yt_cookies.txt"  # never inside out/
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


# ---------- small helpers ----------

def parse_time(text):
    """'90', '01:30' or '00:01:30' -> seconds (float)."""
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


def tail(text, n=600):
    text = (text or "").strip()
    return text[-n:] if len(text) > n else text


def classify_error(text):
    low = (text or "").lower()
    if "not a bot" in low or "sign in to confirm" in low:
        return "bot_block"
    if "http error 429" in low or "too many requests" in low:
        return "rate_limited"
    if "javascript runtime" in low or "n challenge" in low or "challenge solving" in low:
        return "js_runtime"
    if "private video" in low or "video unavailable" in low:
        return "video_unavailable"
    if "live event will begin" in low or "premieres in" in low:
        return "not_ready"
    return "other"


def run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    except FileNotFoundError as exc:
        return 127, "", "command not found: %s" % exc


def ytdlp(args, timeout):
    """Run yt-dlp; use Node for YouTube's JS challenge, fall back if the flag is unknown."""
    base = ["yt-dlp", "--no-playlist", "--no-warnings"]
    if COOKIES_PATH.exists():
        base += ["--cookies", str(COOKIES_PATH)]
    rc, out, err = run(base + ["--js-runtimes", "node"] + args, timeout)
    if rc != 0 and "no such option" in (err or "").lower():
        rc, out, err = run(base + args, timeout)
    return rc, out, err


# ---------- steps ----------

def step_meta(url):
    rc, out, err = ytdlp(["--dump-single-json", "--skip-download", url], 240)
    if rc != 0:
        return {"ok": False, "reason": classify_error(err), "error": tail(err)}
    try:
        info = json.loads(out)
    except ValueError:
        return {"ok": False, "reason": "other", "error": "could not read yt-dlp output"}
    auto = info.get("automatic_captions") or {}
    subs = info.get("subtitles") or {}
    return {
        "ok": True,
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration_hours": round((info.get("duration") or 0) / 3600.0, 2),
        "live_status": info.get("live_status"),
        "english_auto_captions": any(k.startswith("en") for k in auto),
        "english_subtitles": any(k.startswith("en") for k in subs),
    }


def step_captions(url, start, end):
    subs_dir = OUT / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)
    rc, _, err = ytdlp(
        ["--skip-download", "--write-auto-subs", "--write-subs",
         "--sub-langs", "en.*,en", "--sub-format", "json3",
         "-o", str(subs_dir / "%(id)s.%(ext)s"), url],
        300,
    )
    files = sorted(subs_dir.glob("*.json3"))
    if not files:
        return {"ok": False, "reason": classify_error(err), "error": tail(err)}
    total_words = 0
    window_words = 0
    sample = []
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            text = "".join(s.get("utf8", "") for s in ev.get("segs", []) if s).strip()
            if not text:
                continue
            words = len(text.split())
            total_words += words
            t = ev.get("tStartMs", 0) / 1000.0
            if start <= t <= end:
                window_words += words
                if len(sample) < 3:
                    sample.append(text[:80])
    except (ValueError, OSError) as exc:
        return {"ok": False, "reason": "other", "error": "could not read captions: %s" % exc}
    return {
        "ok": True,
        "files": [f.name for f in files],
        "size_kb": round(files[0].stat().st_size / 1024.0, 1),
        "total_words": total_words,
        "words_in_clip_window": window_words,
        "sample_lines": sample,
    }


def step_clip(url, start, seconds, height):
    end = start + seconds
    section = "*%s-%s" % (fmt_time(start), fmt_time(end))
    fmt = "bv*[height<=%d]+ba/b[height<=%d]" % (height, height)
    for old in OUT.glob("clip.*"):
        old.unlink()
    rc, _, err = ytdlp(
        ["-f", fmt, "--download-sections", section, "--force-keyframes-at-cuts",
         "--merge-output-format", "mp4", "-o", str(OUT / "clip.%(ext)s"), url],
        900,
    )
    clips = sorted(OUT.glob("clip.*"))
    if rc != 0 or not clips:
        return {"ok": False, "reason": classify_error(err), "error": tail(err)}
    clip = clips[0]
    rc2, probe_out, _ = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)], 60)
    try:
        seconds_got = round(float(probe_out.strip()), 1)
    except ValueError:
        seconds_got = None
    return {
        "ok": True,
        "file": clip.name,
        "size_mb": round(clip.stat().st_size / 1048576.0, 1),
        "seconds": seconds_got,
    }


def step_frame(clip_name):
    clip = OUT / clip_name
    frame = OUT / "frame.jpg"
    rc, _, err = run(
        ["ffmpeg", "-y", "-ss", "3", "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(frame)], 120)
    if rc != 0 or not frame.exists():
        return {"ok": False, "error": tail(err)}
    return {"ok": True, "file": frame.name}


def valid_box(box):
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.02 and y + h <= 1.02


def step_gemini(frame_name):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return {"ok": False, "skipped": True, "error": "GEMINI_API_KEY secret is not set"}
    image = base64.b64encode((OUT / frame_name).read_bytes()).decode("ascii")
    body = json.dumps({
        "contents": [{"parts": [
            {"text": FACECAM_PROMPT},
            {"inline_data": {"mime_type": "image/jpeg", "data": image}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode("utf-8")
    last_error = ""
    for model in GEMINI_MODELS:
        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = "%s: HTTP %s %s" % (model, exc.code, tail(exc.read().decode("utf-8", "replace"), 200))
            continue
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last_error = "%s: %s" % (model, exc)
            continue
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(text)
        except (KeyError, IndexError, ValueError):
            last_error = "%s: unexpected reply format" % model
            continue
        result["model"] = model
        result["box_valid"] = bool(result.get("facecam_found")) and valid_box(result)
        result["ok"] = True
        return result
    return {"ok": False, "error": last_error or "no model answered"}


# ---------- report ----------

def mark(step):
    if step is None:
        return "-"
    if step.get("skipped"):
        return "skipped"
    return "PASS" if step.get("ok") else "FAIL"


def write_report(report):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["## gta-clipper Phase 0 result", ""]
    for name in ("meta", "captions", "clip", "frame", "gemini"):
        step = report.get(name)
        lines.append("- **%s**: %s" % (name, mark(step)))
        if step and not step.get("ok") and step.get("reason"):
            lines.append("  - reason: `%s`" % step["reason"])
    lines += ["", "```json", json.dumps(report, indent=2, ensure_ascii=False)[:6000], "```"]
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--start", default="00:10:00")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cookies = os.environ.get("YT_COOKIES", "")
    if cookies.strip():
        COOKIES_PATH.write_text(cookies, encoding="utf-8")
        os.chmod(COOKIES_PATH, 0o600)

    start = parse_time(args.start)
    end = start + args.seconds
    report = {"url": args.url, "start": fmt_time(start), "seconds": args.seconds,
              "cookies_used": COOKIES_PATH.exists()}

    report["meta"] = step_meta(args.url)
    report["captions"] = step_captions(args.url, start, end)
    report["clip"] = step_clip(args.url, start, args.seconds, args.height)
    if report["clip"].get("ok"):
        report["frame"] = step_frame(report["clip"]["file"])
        if report["frame"].get("ok"):
            report["gemini"] = step_gemini(report["frame"]["file"])
    write_report(report)
    return 0 if report["clip"].get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
