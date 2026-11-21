# gta-clipper

Private tool: paste a YouTube link on the dashboard, it cuts a short vertical
clip (facecam on top, gameplay below, burned captions) on GitHub's servers and
shows it back on the site with a download button.

## How it works

1. The Netlify site (dashboard) takes the link and options.
2. A Netlify function writes `jobs/<id>.json` and triggers the `clip-job`
   workflow on GitHub.
3. GitHub Actions runs `scripts/clip_job.py`: yt-dlp downloads the section,
   YouTube captions become the burned subtitles, Gemini finds the facecam box
   (auto mode also asks Gemini to pick the best moment), ffmpeg renders
   1080x1920.
4. The workflow commits `out/<id>/clip.mp4` and the status file back to the
   repo; the dashboard polls and shows the download.

## Setup

### GitHub repo secrets (Settings, Secrets and variables, Actions)

| Name | Needed | What for |
|---|---|---|
| `GEMINI_API_KEY` | yes | facecam detection + best-moment picking (free tier is fine) |
| `YT_COOKIES` | only if YouTube blocks the runner | Netscape cookies.txt from a spare Google account |

### Netlify site environment variables (Site configuration, Environment variables)

| Name | Value |
|---|---|
| `GITHUB_TOKEN` | a fine-grained token with Actions: write + Contents: read/write on this repo only |
| `GITHUB_REPO` | `aka645251-ai/gta-clipper` |

## Files

- `web/` - the dashboard (static site, deployed on Netlify)
- `netlify/functions/` - `create-job` and `jobs` (trigger + status + download proxy)
- `scripts/clip_job.py` - the clipping engine (runs on GitHub Actions)
- `scripts/phase0.py` - the Phase 0 probe (kept for reference)
- `.github/workflows/clip-job.yml` - the job runner

Never paste a key into a chat or commit it to the repo.
