#!/usr/bin/env python3
"""Transcribe audio with faster-whisper (local, CPU, int8).

Prints the full transcript to stdout. Progress/timing goes to stderr.
Language is auto-detected; no API keys required.

Usage:
    transcribe-whisper.py <audio_path>

Requirements:
    pip install faster-whisper

Model options (first argument to WhisperModel):
    tiny, base, small, medium, large-v2, large-v3
    'base' is a good default: fast on CPU, works well for Russian + English.
"""

import sys
import time
from faster_whisper import WhisperModel

if len(sys.argv) < 2:
    sys.exit("usage: transcribe-whisper.py <audio>")

path = sys.argv[1]

print("[whisper] loading model base…", file=sys.stderr)
t0 = time.time()
model = WhisperModel("base", device="cpu", compute_type="int8")
print(f"[whisper] loaded in {time.time() - t0:.1f}s, transcribing {path}", file=sys.stderr)

t0 = time.time()
segments, info = model.transcribe(path, beam_size=1, vad_filter=True)
print(
    f"[whisper] detected lang={info.language} prob={info.language_probability:.2f}",
    file=sys.stderr,
)

text = " ".join(s.text.strip() for s in segments).strip()
print(f"[whisper] done in {time.time() - t0:.1f}s, {len(text)} chars", file=sys.stderr)
print(text)
