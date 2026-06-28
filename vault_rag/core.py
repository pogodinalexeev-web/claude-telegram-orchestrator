#!/usr/bin/env python3
"""Общее ядро vault-rag: чанкинг (с номерами строк), схема БД, индексация файла, поиск.
Используется build_index.py (полная сборка) и daemon.py (досборка + поиск)."""
import re, sqlite3, os, sys
from pathlib import Path
import sqlite_vec

# Пути — относительно расположения кода (через симлинк работает на любой машине).
# Машинная специфика (где vault) — в localcfg.py рядом (НЕ в git, своя на каждой машине).
RAGDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAGDIR)
import localcfg

VAULT = Path(localcfg.VAULT)
DB_PATH = Path(RAGDIR) / "index.db"
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384
SKIP_DIRS = {".git", ".obsidian", "node_modules", "venv", ".venv", "site-packages", "__pycache__", ".claude/worktrees", "memory-backup"}
MAX_CHARS = 1600
POOL = 30
RRF_K = 60


def iter_md_files():
    for p in VAULT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p, p.relative_to(VAULT).as_posix()


def split_into_chunks(text):
    """→ список (heading, body, start_line). start_line — 1-based номер строки начала куска."""
    lines = text.splitlines()
    sections, cur_heading, cur_lines, cur_start = [], "", [], 1
    for i, ln in enumerate(lines, 1):
        if re.match(r"^#{1,6}\s", ln):
            if cur_lines:
                sections.append((cur_heading, "\n".join(cur_lines).strip(), cur_start))
            cur_heading, cur_lines, cur_start = ln.lstrip("#").strip(), [], i
        else:
            if not cur_lines:
                cur_start = i
            cur_lines.append(ln)
    if cur_lines:
        sections.append((cur_heading, "\n".join(cur_lines).strip(), cur_start))

    chunks = []
    for heading, body, start in sections:
        if not body:
            continue
        if len(body) <= MAX_CHARS:
            chunks.append((heading, body, start))
        else:
            buf, off = "", 0
            for para in re.split(r"\n\s*\n", body):
                if len(buf) + len(para) > MAX_CHARS and buf:
                    chunks.append((heading, buf.strip(), start + buf[:off].count("\n")))
                    off = len(buf)
                    buf = para
                else:
                    buf = (buf + "\n\n" + para) if buf else para
            if buf.strip():
                chunks.append((heading, buf.strip(), start))
    return chunks


def connect(db_path=DB_PATH):
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def init_schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY, file TEXT, heading TEXT, text TEXT, line INTEGER)")
    db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{DIM}])")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(heading, text)")
    db.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, n_chunks INTEGER)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_meta_file ON meta(file)")


def remove_file(db, rel):
    """Выкинуть все куски файла из всех таблиц."""
    rows = db.execute("SELECT id FROM meta WHERE file=?", (rel,)).fetchall()
    for (rid,) in rows:
        db.execute("DELETE FROM vec_chunks WHERE rowid=?", (rid,))
        db.execute("DELETE FROM fts_chunks WHERE rowid=?", (rid,))
    db.execute("DELETE FROM meta WHERE file=?", (rel,))
    db.execute("DELETE FROM files WHERE path=?", (rel,))


def index_file(db, model, path, rel):
    """(Пере)индексировать один файл. model — TextEmbedding. Возвращает число кусков."""
    remove_file(db, rel)
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    chunks = split_into_chunks(txt)
    if not chunks:
        db.execute("INSERT OR REPLACE INTO files(path, mtime, n_chunks) VALUES (?,?,0)",
                   (rel, path.stat().st_mtime))
        return 0
    texts = [f"{h}\n{b}" if h else b for (h, b, _) in chunks]
    embs = list(model.embed(texts, batch_size=16))
    cur = db.execute("SELECT COALESCE(MAX(id),0) FROM meta").fetchone()[0]
    for (h, b, line), emb in zip(chunks, embs):
        cur += 1
        db.execute("INSERT INTO meta(id, file, heading, text, line) VALUES (?,?,?,?,?)",
                   (cur, rel, h, b, line))
        db.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                   (cur, sqlite_vec.serialize_float32(emb.tolist())))
        db.execute("INSERT INTO fts_chunks(rowid, heading, text) VALUES (?,?,?)", (cur, h, b))
    db.execute("INSERT OR REPLACE INTO files(path, mtime, n_chunks) VALUES (?,?,?)",
               (rel, path.stat().st_mtime, len(chunks)))
    return len(chunks)


def sync_changed(db, model, log=lambda *a: None):
    """Ленивая досборка: переиндексировать изменённые/новые, выкинуть удалённые. → (n_changed, n_removed)."""
    known = dict(db.execute("SELECT path, mtime FROM files").fetchall())
    seen, changed = set(), 0
    for p, rel in iter_md_files():
        seen.add(rel)
        mt = p.stat().st_mtime
        if rel not in known or mt > known[rel] + 0.001:
            index_file(db, model, p, rel)
            changed += 1
            log(f"  ~ {rel}")
    removed = 0
    for rel in list(known):
        if rel not in seen:
            remove_file(db, rel)
            removed += 1
            log(f"  - {rel}")
    if changed or removed:
        db.commit()
    return changed, removed


def fts_query(q):
    words = [w for w in re.findall(r"\w+", q, re.UNICODE) if len(w) > 2]
    return " OR ".join(f'"{w}"*' for w in words) if words else None


def search(db, model, query, k=5):
    """Гибридный поиск. → список dict(rank, score, mark, file, line, heading, snippet)."""
    emb = list(model.embed([query]))[0]
    vec_rows = db.execute(
        "SELECT rowid FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(emb.tolist()), POOL)).fetchall()
    fts_rows = []
    fq = fts_query(query)
    if fq:
        try:
            fts_rows = db.execute(
                "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ? ORDER BY rank LIMIT ?",
                (fq, POOL)).fetchall()
        except sqlite3.OperationalError:
            fts_rows = []
    vec_set = {r[0] for r in vec_rows}
    fts_set = {r[0] for r in fts_rows}
    score = {}
    for rank, (rid,) in enumerate(vec_rows):
        score[rid] = score.get(rid, 0) + 1.0 / (RRF_K + rank)
    for rank, (rid,) in enumerate(fts_rows):
        score[rid] = score.get(rid, 0) + 1.0 / (RRF_K + rank)
    top = sorted(score.items(), key=lambda x: -x[1])[:k]
    out = []
    for i, (rid, sc) in enumerate(top, 1):
        row = db.execute("SELECT file, heading, text, line FROM meta WHERE id=?", (rid,)).fetchone()
        if not row:
            continue
        f, h, text, line = row
        mark = ("С" if rid in vec_set else "·") + "/" + ("Сл" if rid in fts_set else "·")
        out.append(dict(rank=i, score=sc, mark=mark, file=f, line=line or 0,
                        heading=h or "", snippet=text.replace("\n", " ")[:200]))
    return out


def format_results(results):
    lines = []
    for r in results:
        head = f" › {r['heading']}" if r["heading"] else ""
        loc = f"{r['file']}:{r['line']}" if r["line"] else r["file"]
        lines.append(f"{r['rank']}. [{r['score']:.4f} {r['mark']}] {loc}{head}\n   {r['snippet']}\n")
    return "\n".join(lines)
