# vault_rag — hybrid semantic search over a knowledge base

Local, GPU-free, no external API. Gives the assistant grounded answers from a
personal vault of Markdown notes — "where did I write about X", "what's the status
of Y" — instead of guessing from memory.

## How it works

- **Chunking** by Markdown headings, with line numbers preserved, so results point at
  `file:line`. (`core.py`)
- **Two indexes:** vector (semantic) via `sqlite-vec`, and full-text via SQLite `FTS5`.
  The embedder is `paraphrase-multilingual-MiniLM-L12-v2` (384-dim) through `fastembed`
  — runs on CPU, multilingual.
- **Fusion:** results from both indexes are merged with reciprocal-rank fusion (RRF),
  so a hit found semantically *and* lexically ranks highest. Each result is marked
  with how it was found.
- **Incremental sync:** `sync_changed()` re-indexes only files whose mtime moved and
  drops deleted ones — a full rebuild isn't needed after the first one.
- **Warm daemon** (`daemon.py`): keeps the embedding model loaded, listens on a unix
  socket, answers `search` and `reindex` commands, and unloads the model after idle.
  A turn-end hook sends `reindex` after each change, so search is always instant.
- **Chat-log indexing** (`chatlog.py`): rebuilds the daily conversation log from the
  agent's session files so past conversations are searchable too.

## Files

| File | What it is |
| --- | --- |
| `core.py` | Chunking, DB schema, indexing, hybrid search, result formatting. |
| `build_index.py` | One-shot full build of the index. |
| `daemon.py` | Long-lived warm daemon on a unix socket: `search` / `reindex`. |
| `search_client.py` | Thin client that talks to the daemon. |
| `search` | CLI entry: `./search "query" [k]`. |
| `chatlog.py` | Rebuilds the daily chat log (also indexed). |
| `localcfg.example.py` | Machine-specific paths — copy to `localcfg.py` (gitignored). |

## Run

```bash
cp localcfg.example.py localcfg.py     # edit paths
pip install fastembed sqlite-vec
python build_index.py                  # first full build
./search "your query" 5
```

The index DB and the local model cache are gitignored — only code is in the repo.
