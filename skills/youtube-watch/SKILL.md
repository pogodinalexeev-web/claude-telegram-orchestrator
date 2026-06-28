---
name: youtube-watch
description: Analyze a YouTube video in three modes — A subtitles/transcript (basic), B audio transcription via batch-aai-transcribe, C visual (ffmpeg frame extraction + read frames). Working path via python3 -m yt_dlp (yt-dlp NOT in PATH on VPS) + clean vtt from timestamps. Use when owner gives youtu.be/ or youtube.com/watch link, says "/youtube-watch <url>", "summarize video", "pull from youtube", "what's in this clip", "what's in the frame".
---

# /youtube-watch — YouTube → vault → summary

Replaces the dead `claude-watch` / `owner-watch.py` branch (yt-dlp pipeline that never worked — transcript stayed empty). This has the working minimal path, tested on VPS.

## Step 1 — Pull subtitles

`yt-dlp` on VPS is a pip package but **NOT in PATH** — call via module:

```bash
cd /tmp && python3 -m yt_dlp \
  --skip-download --write-auto-subs --write-subs \
  --sub-langs "ru,en" --sub-format vtt \
  -o "ytsub.%(ext)s" "<URL>"
```

Notes (caught in production):
- **429 Too Many Requests** on second language — normal, usually first language (ru) already downloaded. If ru also failed — wait and retry, or pull one language.
- Warnings "No JS runtime (deno)" and "impersonation target unavailable" — don't block subtitle download, ignore. If formats disappear entirely — install `deno` (see yt-dlp wiki EJS).
- Auto-subtitles (`--write-auto-subs`) — YouTube machine transcription, may have recognition errors and `&gt;&gt;` artifacts.

This is **basic Mode A** — subtitles. Two more modes for other tasks below.

## Mode B — transcribe audio

When subtitles are missing OR accurate transcription with speaker diarization is needed (not machine auto-subtitles):
1. Pull audio: `cd /tmp && python3 -m yt_dlp -f "ba" -x --audio-format mp3 -o "ytaudio.%(ext)s" "<URL>"`.
2. Transcribe via skill `/batch-aai-transcribe /tmp/ytaudio.mp3` (AssemblyAI, with speaker labels).

## Mode C — what's visually in the frame

When what matters is NOT what's said, but what's shown (silent demo, screen graphics, on-screen actions):
1. Pull video (limit quality — frames are heavy): `cd /tmp && python3 -m yt_dlp -f "bv*[height<=720]+ba/b[height<=720]" -o "ytvid.%(ext)s" "<URL>"`.
2. Extract frames via ffmpeg, one frame every N seconds (e.g. every 10s): `ffmpeg -i /tmp/ytvid.* -vf "fps=1/10" /tmp/ytframe_%04d.jpg`. Adjust frequency by length: short = more frequent, 1h+ = every 30-60s.
3. Read frames via `Read` (multimodal — can see jpg) and describe what's in them.
4. If both words and visuals needed — combine Mode A/B + C.

Check ffmpeg: `which ffmpeg` (usually available on VPS). Clean up frames after use (`rm /tmp/ytframe_*.jpg /tmp/ytvid.*`) — disk space is limited.

## Step 3 — clean vtt to plain text

```bash
cd /tmp && python3 -c "
import re
lines = open('ytsub.ru.vtt', encoding='utf-8').read().splitlines()
seen=[]
for l in lines:
    if '-->' in l or l.strip()=='' or l.startswith(('WEBVTT','Kind:','Language:')): continue
    l=re.sub(r'<[^>]+>','',l).strip().replace('&gt;','>').replace('&lt;','<').replace('&amp;','&')
    if l and (not seen or seen[-1]!=l): seen.append(l)
open('yttext.txt','w',encoding='utf-8').write(' '.join(seen))
"
```
Removes timestamps, inline tags, duplicate lines (auto-subtitles duplicate each line due to sliding window).

## Step 4 — where to store (vault rules)

Ask one hypothesis, don't file silently:
- tied to a project → `Projects/<X>/journal/YYYY-MM-DD <topic>.md` (+ pointer in `ideas.md`);
- no project, reference material → `Resources/atoms/_source-<slug>.md` with YAML (source-URL, date);
- "figure out later" → `inbox.md`.

Save with header: source link, date, duration, channel name.

## Step 5 — summarize

From `yttext.txt` give a structured summary (not verbatim — that's the author's content): what the video is about, key points, conclusions. Length per owner's request.

## What's NOT included from the old approach (intentional)
- **Whisper via systemd** — replaced by `/batch-aai-transcribe` (AssemblyAI), no need to keep own process.
- **Instagram** — separate skill `/ig-pull` (Apify API), don't mix.
- **Viral clip scoring (shorts cutting)** — separate task, not this skill.
- **cookies.txt for private videos** — add `--cookies` flag only if hitting private/age-restricted video.
