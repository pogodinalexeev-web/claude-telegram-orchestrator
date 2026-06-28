#!/usr/bin/env python3
"""Дежурный vault-rag: держит модель прогретой, слушает unix-сокет.
На запрос — ленивая досборка изменённых файлов + поиск. Авто-выгрузка модели после простоя.
Запрос (одна строка JSON): {"q": "...", "k": 5}. Ответ: текст результатов."""
import socket, os, json, threading, time, sys, traceback

RAGDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAGDIR)
import core

SOCK = os.path.join(RAGDIR, "daemon.sock")
IDLE_UNLOAD = 0        # секунд простоя до выгрузки модели; 0 = НЕ выгружать (держим всегда)
LOG = os.path.join(RAGDIR, "daemon.log")

_model = None
_db = None
_last_used = time.time()
_lock = threading.Lock()


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def get_model():
    global _model
    if _model is None:
        log("грузим модель…")
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=core.MODEL_NAME)
        log("модель в памяти")
    return _model


def get_db():
    global _db
    if _db is None:
        _db = core.connect()
    return _db


def unloader():
    """Фоновый сторож: выгружает модель после IDLE_UNLOAD секунд тишины."""
    global _model
    while True:
        time.sleep(30)
        with _lock:
            if IDLE_UNLOAD > 0 and _model is not None and time.time() - _last_used > IDLE_UNLOAD:
                _model = None
                import gc; gc.collect()
                log("модель выгружена (простой)")


def handle(conn):
    global _last_used
    try:
        data = conn.recv(65536).decode("utf-8").strip()
        req = json.loads(data)
        cmd = req.get("cmd")
        q = req.get("q", "")
        k = int(req.get("k", 5))
        with _lock:
            _last_used = time.time()
            model = get_model()
            db = get_db()
            # инкрементальная досборка по запросу (после каждого сообщения) — чтобы
            # поиск был мгновенным, а пересборка не копилась пачкой к моменту поиска
            if cmd == "reindex":
                t0 = time.time()
                ch, rm = core.sync_changed(db, model, log)
                conn.sendall(f"[reindex {ch}изм/{rm}уд {time.time()-t0:.2f}с]".encode("utf-8"))
                return
            t0 = time.time()
            ch, rm = core.sync_changed(db, model, log)
            t1 = time.time()
            results = core.search(db, model, q, k)
            t2 = time.time()
        out = core.format_results(results)
        meta = f"[досборка {ch}изм/{rm}уд {t1-t0:.2f}с · поиск {t2-t1:.2f}с]\n"
        conn.sendall((meta + out).encode("utf-8"))
    except Exception as e:
        log("ERR " + traceback.format_exc())
        try: conn.sendall(f"ERROR: {e}".encode("utf-8"))
        except Exception: pass
    finally:
        conn.close()


def main():
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(8)
    os.chmod(SOCK, 0o600)
    threading.Thread(target=unloader, daemon=True).start()
    log("дежурный поднят, слушаю " + SOCK)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
