---
name: batch-aai-transcribe
description: Transcribe one or more audio files (mp3/m4a/ogg/wav) via AssemblyAI with speaker diarization. Accepts local files, a folder, or a public cloud storage link. Use with "/batch-aai-transcribe <path_or_url>", "transcribe with roles", "diarize these audio", "split by speakers".
---

# /batch-aai-transcribe — batch transcription via AssemblyAI

## When

- Owner gives a cloud link to a folder with audio (or 1 file).
- Owner gives a path to a local folder / 1+ files.
- Long audio (>2 min) **with dialogue** where `Speaker A/B/C` labels are needed.
- Multiple files where a **single merged transcript** in vault is required.

## When NOT to use

- Short audio (<2 min) and **without** roles — use local Whisper route if available.
- Owner explicitly said "without diarization" — use Whisper route.
- Video file — extract audio first: `ffmpeg -i in.mp4 -vn -acodec mp3 out.mp3`.

## Stack

- **API**: AssemblyAI v2 with `speaker_labels:true`, `language_code:"ru"`, `speech_models:["universal-2"]`.
- **Key**: `/etc/claude-tg/assemblyai-key` (readable by bot user group). 32 bytes.
- **ffprobe** (system-installed) — for duration check and input validation.
- **curl + jq** — upload, submit, polling, parsing.
- **Standard shell** (no Python — avoid extra dependencies).

## Algorithm

### 0. Preparation

- Accept input: list of paths OR cloud public URL OR path to directory.
- Create working folder `/tmp/batch-aai-<ts>/` with `src/` subdirectory for audio.
- If input is a cloud URL (e.g. Yandex.Disk):
  ```bash
  PUBKEY="$1"
  ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$PUBKEY")
  curl -s "https://cloud-api.yandex.net/v1/disk/public/resources?public_key=$ENC&limit=50" \
    | jq -r '._embedded.items[] | select(.media_type=="audio") | .path' \
    | while read p; do
        ENCP=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$p")
        HREF=$(curl -s "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key=$ENC&path=$ENCP" | jq -r .href)
        OUTNAME=$(basename "$p" | tr ' ' '_')
        curl -sL -o "src/$OUTNAME" "$HREF"
      done
  ```
- If input is a folder: `cp <dir>/*.{mp3,m4a,ogg,wav} src/` (via `shopt -s nullglob`).
- If individual files: `cp <files> src/`.
- Check ffprobe on each: duration + format/codec. Corrupt files — drop with warning.

### 1. Read key

```bash
KEY=$(cat /etc/claude-tg/assemblyai-key)
[[ ${#KEY} -ne 32 ]] && { echo "AAI key not 32 bytes — abort"; exit 1; }
```

### 2. Sequential processing of each file

For each `src/*.{mp3,m4a,ogg,wav}`:

**a) Upload**

```bash
UPLOAD_URL=$(curl -sS -X POST https://api.assemblyai.com/v2/upload \
  -H "authorization: $KEY" \
  -T "$F" | jq -r '.upload_url')
```

**b) Submit**

⚠️ **Critical parameters** (API returns error without them):
- `speech_models: ["universal-2"]` — **array**, not string. `speech_model` (single) is deprecated.
- `language_code: "ru"`
- `speaker_labels: true`

```bash
ID=$(curl -sS -X POST https://api.assemblyai.com/v2/transcript \
  -H "authorization: $KEY" -H "content-type: application/json" \
  -d "{\"audio_url\":\"$UPLOAD_URL\",\"speaker_labels\":true,\"language_code\":\"ru\",\"speech_models\":[\"universal-2\"]}" \
  | jq -r '.id')
```

**c) Poll until completion**

```bash
while :; do
  RESP=$(curl -sS "https://api.assemblyai.com/v2/transcript/$ID" -H "authorization: $KEY")
  ST=$(echo "$RESP" | jq -r '.status')
  case "$ST" in
    completed) break ;;
    error) echo "$RESP" | jq -r '.error' >&2; exit 1 ;;
    *) sleep 4 ;;
  esac
done
```

Typical time — 30-90 seconds per 1 hour of audio (depends on AAI queue).

**d) Save result**

Text format:
```bash
echo "$RESP" | jq -r '
  (.utterances // []) as $u
  | if ($u | length) > 0
    then ($u | map("[\((.start/1000)|floor|tostring)s] [Speaker \(.speaker)] \(.text)") | join("\n\n"))
    else .text
    end' > "${F%.*}.aai.txt"
```

Also save **full JSON** to `${F%.*}.aai.json` — for reprocessing without re-transcribing.

### 3. Build single transcript (optional, if owner said "merge into one file")

- Merge files in order (if named `audio_1.mp3, audio_2.mp3` — by number; otherwise by mtime).
- Replace `[Speaker A/B/C]` with names via simple heuristic pass:
  - If owner said "caller is X, respondent is Y" — A=Y, B=X.
  - If not said — keep `Speaker A/B/C`, **ask owner who is who** before finalizing.
- YAML header + title + context.

### 4. Save to vault

Target path depends on topic:
- Personal (conversation with family/friends): `Projects/Personal/journal/YYYY-MM-DD <topic>.md`
- Work call: `Projects/<project>/journal/YYYY-MM-DD <topic>.md`
- No clear topic: `Resources/chat-logs/raw/YYYY-MM-DD <slug>.md`

Ask owner if unclear. Don't guess silently.

### 5. Cleanup

After saving to vault — `rm -rf /tmp/batch-aai-<ts>/` (but **only** after explicit confirmation "saved to vault, everything works").

## Pricing (for context)

- AssemblyAI universal-2 + speaker_labels: ~$0.37/hour of audio (as of 2026-05, **not verified against current pricing**).
- 1 hour of conversation ≈ $0.37 — cheaper than any manual transcription.

## Known quirks

- **If AAI returned error about `speech_model is deprecated`** — you passed a string instead of array. Check field `speech_models` — it **must be an array**.
- **AAI accepts Russian** via `language_code:"ru"` + `universal-2`. The `nano` model doesn't handle Russian well.
- **Files >100 MB** — may need to split via ffmpeg (`-f segment -segment_time 3600`). 1 hour mp3 ≈ 28-30 MB, usually fits.
- **Speaker labels** work for **two or more** distinct voices. If only one person is speaking — all labels will be `Speaker A`.

## Origin

Created after need to transcribe multiple audio files (2h 32min total) of a conversation. First attempted local `faster-whisper small` CPU transcription (estimated 1.5-2 hours). Found existing AssemblyAI key in `/etc/claude-tg/`. Switched to AAI: 3.5 minutes instead of 1+ hour. Skill harvested from recurring pattern.

## Related
- `batch-aai-transcribe` ← this skill
- `stealth-browser` — for browser-based audio capture from web cabinets
- `megafon-calls` — calls to this skill for carrier recordings
