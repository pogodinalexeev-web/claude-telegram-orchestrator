#!/usr/bin/env python3
"""Общее ядро vault-rag: чанкинг (с номерами строк), схема БД, индексация файла, поиск.
Используется build_index.py (полная сборка) и daemon.py (досборка + поиск)."""
import re, sqlite3, os, sys, time
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
# Сверяется с ОТДЕЛЬНЫМИ частями пути (p.parts ниже), поэтому запись со слешем
# внутри не совпадёт никогда. Было ".claude/worktrees" — мёртвая строка, worktrees
# всё это время лезли в индекс. Чинено 25.08.2026 заодно с "source-book": текст
# книги из скилла developing-ai-agents, 1,2 млн знаков, иначе топит выдачу.
SKIP_DIRS = {".git", ".obsidian", "node_modules", "venv", ".venv", "site-packages",
             "__pycache__", "worktrees", "source-book", "memory-backup"}
MAX_CHARS = 1600
POOL = 30
RRF_K = 60
# Какие расширения тянем в индекс. Дефолт — только заметки; хранилище с ворохом
# выгруженного текста (расшифровки курсов и т.п.) добавляет ".txt" в localcfg.
EXTS = getattr(localcfg, "EXTS", (".md",))


def iter_md_files(vault=None):
    """vault=None — дефолтный из localcfg; иначе путь конкретного жильца (мульти-режим 27.08.2026)."""
    v = Path(vault) if vault else VAULT
    for ext in EXTS:
        for p in v.rglob("*" + ext):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p, p.relative_to(v).as_posix()


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
            # Номера строк считаем накопителем (27.08.2026). Было `start + buf[:off]`,
            # где off — длина ПРЕДЫДУЩЕГО буфера: номера скакали назад, ломая и выдачу
            # «файл:строка», и сортировку кусков (см. index_file).
            buf, consumed = "", 0
            for para in re.split(r"\n\s*\n", body):
                if len(buf) + len(para) > MAX_CHARS and buf:
                    chunks.append((heading, buf.strip(), start + consumed))
                    consumed += buf.count("\n") + 2   # +2 — пустая строка между абзацами
                    buf = para
                else:
                    buf = (buf + "\n\n" + para) if buf else para
            if buf.strip():
                chunks.append((heading, buf.strip(), start + consumed))
    return chunks


def connect(db_path=DB_PATH):
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    # Миграция ord — дешёвая проверка колонки при каждом открытии, работа один раз.
    # Демон открывает базу без init_schema, поэтому вешаем сюда (27.08.2026).
    try:
        if ensure_ord(db):
            print("[vault-rag] база переведена на порядковые номера кусков", file=sys.stderr)
    except Exception:
        pass
    return db


def init_schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY, file TEXT, heading TEXT, text TEXT, line INTEGER)")
    ensure_ord(db)
    db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(embedding float[{DIM}])")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(heading, text)")
    db.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, n_chunks INTEGER)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_meta_file ON meta(file)")


def ensure_ord(db):
    """Колонка ord — порядковый номер куска ВНУТРИ файла (27.08.2026).
    Зачем: дифф в index_file должен идти с обоих концов (все log.md растут СВЕРХУ,
    приписка в начало срывала сравнение по префиксу и файл перекладывался целиком).
    Сортировать по id нельзя — при вставке в середину новые id больше старых;
    по line нельзя — номера строк были кривыми до 27.08. Свой счётчик решает оба.
    Миграция одноразовая: для готовых баз ord заполняется по текущему порядку id
    (он верен, потому что дифф до сих пор трогал только префикс и хвост)."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(meta)").fetchall()]
    if "ord" in cols:
        return False
    db.execute("ALTER TABLE meta ADD COLUMN ord INTEGER")
    db.execute("""UPDATE meta SET ord = (
                    SELECT COUNT(*) FROM meta m2
                    WHERE m2.file = meta.file AND m2.id <= meta.id) - 1""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_meta_file_ord ON meta(file, ord)")
    db.commit()
    return True


def remove_file(db, rel):
    """Выкинуть все куски файла из всех таблиц."""
    rows = db.execute("SELECT id FROM meta WHERE file=?", (rel,)).fetchall()
    for (rid,) in rows:
        db.execute("DELETE FROM vec_chunks WHERE rowid=?", (rid,))
        db.execute("DELETE FROM fts_chunks WHERE rowid=?", (rid,))
    db.execute("DELETE FROM meta WHERE file=?", (rel,))
    db.execute("DELETE FROM files WHERE path=?", (rel,))


def index_file(db, model, path, rel):
    """(Пере)индексировать файл ДИФФОМ С ОБОИХ КОНЦОВ (27.08.2026).

    История: 26.08 сделали дифф по префиксу — он лечил дневные логи разговоров
    (растут снизу). Но все `log.md` растут СВЕРХУ: приписка в начало не совпадала
    с первым же куском, и файл на 165 кусков перекладывался целиком каждую правку
    (Journal/log.md, 256 КБ — минуты счёта на каждый ход).

    Теперь считаем общее начало И общий конец, трогаем только середину. Куски
    хвоста не пересчитываются — им лишь меняется порядковый номер `ord`
    (двигаем числа, а не гоняем нейросеть). Возвращает число переложенных кусков."""
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    if path.suffix != ".md":
        txt = re.sub(r"[ \t]{2,}", " ", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
    chunks = split_into_chunks(txt)
    old_rows = db.execute(
        "SELECT id, heading, text FROM meta WHERE file=? ORDER BY ord, id",
        (rel,)).fetchall()
    mt = path.stat().st_mtime

    def same(row, ch):
        return row[1] == ch[0] and row[2] == ch[1]

    n_old, n_new = len(old_rows), len(chunks)
    pref = 0
    while pref < n_old and pref < n_new and same(old_rows[pref], chunks[pref]):
        pref += 1
    suf = 0
    while (suf < n_old - pref and suf < n_new - pref
           and same(old_rows[n_old - 1 - suf], chunks[n_new - 1 - suf])):
        suf += 1

    if pref == n_old and pref == n_new:
        db.execute("INSERT OR REPLACE INTO files(path, mtime, n_chunks) VALUES (?,?,?)",
                   (rel, mt, n_new))
        return 0

    mid_old = old_rows[pref:n_old - suf]
    mid_new = chunks[pref:n_new - suf]

    # Векторы выкидываемых кусков держим под рукой: текст мог просто переехать.
    old_vecs = {}
    for rid, h, b in mid_old:
        row = db.execute("SELECT embedding FROM vec_chunks WHERE rowid=?", (rid,)).fetchone()
        if row:
            old_vecs[f"{h}\n{b}" if h else b] = row[0]
        db.execute("DELETE FROM vec_chunks WHERE rowid=?", (rid,))
        db.execute("DELETE FROM fts_chunks WHERE rowid=?", (rid,))
        db.execute("DELETE FROM meta WHERE id=?", (rid,))

    if mid_new:
        texts = [f"{h}\n{b}" if h else b for (h, b, _) in mid_new]
        fresh = [t for t in texts if t not in old_vecs]
        fresh_iter = iter(model.embed(fresh, batch_size=16)) if fresh else iter(())
        embs = [old_vecs[t] if t in old_vecs
                else sqlite_vec.serialize_float32(next(fresh_iter).tolist())
                for t in texts]
        cur = db.execute("SELECT COALESCE(MAX(id),0) FROM meta").fetchone()[0]
        for k, ((h, b, line), emb) in enumerate(zip(mid_new, embs)):
            cur += 1
            db.execute("INSERT INTO meta(id, file, heading, text, line, ord) VALUES (?,?,?,?,?,?)",
                       (cur, rel, h, b, line, pref + k))
            db.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)", (cur, emb))
            db.execute("INSERT INTO fts_chunks(rowid, heading, text) VALUES (?,?,?)", (cur, h, b))

    # Хвост: пересчитывать нечего, но порядковые номера сдвинулись — правим числа.
    if suf:
        shift = len(mid_new) - len(mid_old)
        if shift:
            for j in range(suf):
                rid = old_rows[n_old - 1 - j][0]
                db.execute("UPDATE meta SET ord=? WHERE id=?", (n_new - 1 - j, rid))
        for j in range(suf):
            rid = old_rows[n_old - 1 - j][0]
            _, _, line = chunks[n_new - 1 - j]
            db.execute("UPDATE meta SET line=? WHERE id=?", (line, rid))

    db.execute("INSERT OR REPLACE INTO files(path, mtime, n_chunks) VALUES (?,?,?)",
               (rel, mt, n_new))
    return len(mid_new)


def sync_changed(db, model, log=lambda *a: None, vault=None):
    """Ленивая досборка: переиндексировать изменённые/новые, выкинуть удалённые. -> (n_changed, n_removed)."""
    known = dict(db.execute("SELECT path, mtime FROM files").fetchall())
    seen, changed = set(), 0
    for p, rel in iter_md_files(vault):
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


def load_vectors(db):
    """Все векторы одной матрицей в RAM (27.08.2026, лечение по аудиту 26.08).
    Диагноз: под нагрузкой страницы index.db вылетают из кэша ОС и каждый поиск
    перечитывал сотни МБ с диска (замер: векторная часть 2.7-4с при коде 0.4-0.9с).
    Матрица ~40МБ живёт в памяти процесса — поиску диск не нужен.
    -> (rowids int64[N], mat float32[N,dim] нормированная) либо (None, None) при пустой базе."""
    rows = db.execute("SELECT rowid, embedding FROM vec_chunks").fetchall()
    return _rows_to_matrix(rows)


def _rows_to_matrix(rows):
    """[(rowid, blob)] → (ids int64[N], mat float32[N,dim] нормированная) | (None, None)."""
    import numpy as np
    if not rows:
        return None, None
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return ids, np.ascontiguousarray(mat / norms)


def vector_ids(db):
    """Только номера строк — читается по индексу, без тел векторов (миллисекунды)."""
    return [r[0] for r in db.execute("SELECT rowid FROM vec_chunks").fetchall()]


def fetch_vectors(db, ids):
    """Векторы по списку номеров → (ids, mat). Батчами по 500 (лимит переменных SQLite)."""
    rows = []
    ids = list(ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = "SELECT rowid, embedding FROM vec_chunks WHERE rowid IN (%s)" % ",".join("?" * len(part))
        rows.extend(db.execute(q, part).fetchall())
    return _rows_to_matrix(rows)


def fts_query(q):
    words = [w for w in re.findall(r"\w+", q, re.UNICODE) if len(w) > 2]
    return " OR ".join(f'"{w}"*' for w in words) if words else None


def search(db, model, query, k=5, mem=None):
    """Гибридный поиск. → список dict(rank, score, mark, file, line, heading, snippet).
    mem = (rowids, matrix) из load_vectors(): вектора сравниваются в RAM без диска."""
    _t0 = time.time()
    emb = list(model.embed([query]))[0]
    _t1 = time.time()
    if mem is not None and mem[0] is not None:
        import numpy as np
        ids, mat = mem
        q = np.asarray(emb, dtype=np.float32)
        n = np.linalg.norm(q)
        if n:
            q = q / n
        sims = mat @ q
        take = min(POOL, len(sims))
        top_i = np.argpartition(-sims, take - 1)[:take]
        top_i = top_i[np.argsort(-sims[top_i])]
        vec_rows = [(int(ids[i]),) for i in top_i]
        _lbl = "RAM"
    else:
        vec_rows = db.execute(
            "SELECT rowid FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(emb.tolist()), POOL)).fetchall()
        _lbl = "диск"
    _t2 = time.time()
    fts_rows = []
    fq = fts_query(query)
    if fq:
        try:
            fts_rows = db.execute(
                "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ? ORDER BY rank LIMIT ?",
                (fq, POOL)).fetchall()
        except sqlite3.OperationalError:
            fts_rows = []
    _t3 = time.time()
    # раскладка времени последнего поиска — читает daemon для лога
    search.last_timing = (f"код {_t1-_t0:.2f}с / вект[{_lbl}] {_t2-_t1:.2f}с / "
                          f"слов {_t3-_t2:.2f}с")
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
