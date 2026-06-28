# inbox — single capture point

Everything dropped to the bot (text, voice, forwards, links) lands here first, in the
canonical format below, and gets sorted later by `/process-inbox`. No sorting at
capture time — just capture.

Canonical entry format (the `---` separator is required; the parser splits on it):

```
---
YYYY-MM-DD HH:MM (source)
<the captured text>
```

`source` ∈ `TG` (typed), `voice` (transcribed), `forward`, `capture`, `manual`.

---
YYYY-MM-DD HH:MM (voice)
example capture — a stray thought to sort later
