#!/usr/bin/env python3
"""Native Telegram voice transcription via the owner's user session (Premium).

Args: <unix_date> <duration_sec> [max_wait_sec]. Finds the voice message in the
dialog with the bot by send time + duration, waits until ready (up to max_wait).
Text to stdout, exit 0. Not found / not ready in time -> exit != 0, and the bot
falls back to another transcription path (Whisper / a paid API).

Telegram's own transcription is fast and doesn't load the server's CPU, but it needs
a Premium user session (the bot account can't call it), hence this helper.
"""

import asyncio
import os
import sys
import time

# Telethon credentials come from an env file (api id/hash, session string).
ENV_FILE = os.environ.get("TELEGRAM_ENV_FILE")
if ENV_FILE and os.path.exists(ENV_FILE):
    for line in open(ENV_FILE):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402
from telethon.tl.functions.messages import TranscribeAudioRequest  # noqa: E402

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESS = os.environ["TELEGRAM_SESSION_STRING"]
BOT = os.environ.get("BOT_USERNAME", "your_bot")


def voice_duration(m):
    try:
        return m.voice.attributes[0].duration
    except Exception:
        return None


async def main() -> int:
    target_date = int(sys.argv[1])
    target_dur = int(sys.argv[2]) if len(sys.argv) > 2 else None
    max_wait = int(sys.argv[3]) if len(sys.argv) > 3 else 120

    client = TelegramClient(StringSession(SESS), API_ID, API_HASH)
    await client.connect()
    try:
        bot = await client.get_entity(BOT)
        msgs = await client.get_messages(bot, limit=30)
        voices = [m for m in msgs if getattr(m, "voice", None)]

        # The message id from the Bot API differs from the one in the user session,
        # so match by timestamp first, then fall back to a +-3s window plus duration.
        cand = [m for m in voices if int(m.date.timestamp()) == target_date]
        if not cand:
            cand = [
                m
                for m in voices
                if abs(int(m.date.timestamp()) - target_date) <= 3
                and (target_dur is None or voice_duration(m) == target_dur)
            ]
        if not cand:
            return 2

        cand.sort(
            key=lambda m: (
                abs(int(m.date.timestamp()) - target_date),
                abs((voice_duration(m) or 0) - (target_dur or 0)),
            )
        )
        m = cand[0]

        res = None
        t0 = time.time()
        while time.time() - t0 < max_wait:
            res = await client(TranscribeAudioRequest(peer=bot, msg_id=m.id))
            if not res.pending and res.text:
                break
            await asyncio.sleep(1.0)

        if res and res.text and not res.pending:
            sys.stdout.write(res.text)
            sys.stdout.flush()
            return 0
        return 3
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        sys.stderr.write(f"native-transcribe error: {type(e).__name__}: {e}\n")
        sys.exit(1)
