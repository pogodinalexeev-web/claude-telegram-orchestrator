#!/usr/bin/env python3
"""Дежурный vault-rag: держит модель прогретой, слушает unix-сокет(ы).
На запрос — поиск по матрице векторов в RAM; досборка — по команде reindex.
Запрос (одна строка JSON): {"q": "...", "k": 5} или {"cmd": "reindex"}.

Режимы (27.08.2026, шаг 3 аудита 26.08):
- ОДИН ЖИЛЕЦ (по умолчанию, Mac): ragdir = папка кода, vault из localcfg.VAULT.
- МУЛЬТИ (VPS): localcfg.TENANTS = [{"name","ragdir","vault"}, ...] — один процесс,
  ОБЩИЕ модели (одна поисковая + одна досборочная), у каждого жильца СВОИ база,
  матрица и сокет. Сокет лежит в ragdir жильца, chown жильцу, права 0600 —
  изоляция транспортом: чужой сокет не открыть, маршрутизация по сокету,
  а не по полю запроса (против путаницы при гонках). Требует запуска от root."""
import socket, os, json, threading, time, sys, traceback, fcntl, pwd

RAGDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAGDIR)
import core
import localcfg

IDLE_UNLOAD = 900      # секунд простоя ДОСБОРКИ до выгрузки досборочной копии модели.
# Поисковая копия не выгружается никогда — ответ <1с держится всегда.
# Досборочная поднимается заново ~70с на nice 15 в фоне — поиску не мешает (27.08.2026).
LOG = os.path.join(RAGDIR, "daemon.log")

_model = None            # досборочная копия (все потоки, nice 15, выгружаемая)
_search_model = None     # поисковая копия (2 потока, живёт всегда)
_model_lock = threading.Lock()
_last_used = time.time() # время последней ДОСБОРКИ (для выгрузки досборочной копии)
_lockfiles = []          # flock-замки жильцов — держим открытыми всю жизнь процесса


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def _demote_this_thread():
    """Прижать приоритет ТЕКУЩЕГО потока: досборка жуёт на nice 15,
    поиск остаётся на 0 и получает ядра первым."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
    except Exception:
        pass


class Tenant:
    """Жилец: свой vault, своя база, своя матрица, свой сокет. Модели — общие."""

    def __init__(self, name, ragdir, vault):
        self.name = name
        self.ragdir = ragdir
        self.vault = vault
        self.db_path = os.path.join(ragdir, "index.db")
        self.sock_path = os.path.join(ragdir, "daemon.sock")
        self.lock_path = os.path.join(ragdir, "daemon.lock")
        self.db = None       # пишущее соединение (досборка)
        self.rdb = None      # тёплое читающее соединение
        self.mem = None      # (rowids, matrix) в RAM
        self.sync_lock = threading.Lock()
        self.read_lock = threading.Lock()

    def get_db(self):
        if self.db is None:
            self.db = core.connect(self.db_path)
        return self.db

    def get_rdb(self):
        if self.rdb is None:
            self.rdb = core.connect(self.db_path)
            self.rdb.execute("PRAGMA busy_timeout=5000")
            self.rdb.execute("PRAGMA mmap_size=400000000")
        return self.rdb

    def reload_mem(self, db):
        """Полная сборка матрицы — при старте и как запасной путь."""
        try:
            t0 = time.time()
            self.mem = core.load_vectors(db)
            n = 0 if self.mem[0] is None else len(self.mem[0])
            log(f"[{self.name}] матрица в RAM: {n} строк за {time.time()-t0:.2f}с")
        except Exception:
            log(f"[{self.name}] ERR матрица: " + traceback.format_exc())

    def sync_mem(self, db):
        """Догнать матрицу ДЕЛЬТОЙ после досборки (27.08.2026, лечение регресса той же ночи).
        Было: любая приписка строки в дневной лог → полная пересборка 40к векторов, 1.4с
        и 60 МБ заново, до 15 раз за полчаса. Стало: сверяем только номера строк
        (дёшево, по индексу), выкидываем исчезнувшие, дочитываем появившиеся.
        Порядок строк в матрице не важен — номера едут parallel-массивом."""
        import numpy as np
        if self.mem is None or self.mem[0] is None:
            return self.reload_mem(db)
        t0 = time.time()
        try:
            ids, mat = self.mem
            db_ids = core.vector_ids(db)
            db_set = set(db_ids)
            cur_set = set(int(x) for x in ids)
            gone = cur_set - db_set
            added = [i for i in db_ids if i not in cur_set]
            if not gone and not added:
                return
            # Дельта размером с саму матрицу — дешевле собрать заново.
            if len(gone) + len(added) > max(2000, len(ids) // 8):
                return self.reload_mem(db)
            if gone:
                keep = ~np.isin(ids, np.fromiter(gone, dtype=np.int64, count=len(gone)))
                ids, mat = ids[keep], mat[keep]
            if added:
                a_ids, a_mat = core.fetch_vectors(db, added)
                if a_ids is not None:
                    ids = np.concatenate([ids, a_ids])
                    mat = np.vstack([mat, a_mat])
            self.mem = (ids, np.ascontiguousarray(mat))
            log(f"[{self.name}] матрица +{len(added)}/-{len(gone)} → {len(ids)} строк "
                f"за {time.time()-t0:.2f}с")
        except Exception:
            log(f"[{self.name}] ERR дельта матрицы, собираю заново: " + traceback.format_exc())
            self.reload_mem(db)


def get_search_model():
    """Общая поисковая копия: 2 потока, чтобы досборка не душила ответ."""
    global _search_model
    with _model_lock:
        if _search_model is None:
            try:
                from fastembed import TextEmbedding
                _search_model = TextEmbedding(model_name=core.MODEL_NAME, threads=2)
                log("поисковая копия модели загружена (2 потока)")
            except TypeError:
                _search_model = _get_model_locked()
        return _search_model


def get_model():
    global _model
    with _model_lock:
        return _get_model_locked()


def _get_model_locked():
    global _model
    if _model is None:
        log("грузим досборочную модель…")
        # Создаём в прижатом потоке: пул потоков модели наследует nice создателя,
        # так вся досборочная математика живёт на nice 15 и уступает поиску.
        holder = {}
        def _worker():
            _demote_this_thread()
            from fastembed import TextEmbedding
            holder["m"] = TextEmbedding(model_name=core.MODEL_NAME)
        t = threading.Thread(target=_worker)
        t.start(); t.join()
        _model = holder["m"]
        log("досборочная модель в памяти (nice 15)")
    return _model


def unloader(tenants, pending=None, adopt=None):
    """Сторож: выгружает досборочную модель после IDLE_UNLOAD секунд без досборок.
    Самолечение: исчез сокет обслуживаемого жильца — выходим, systemd поднимет заново.
    Подбор (27.08.2026): жилец, чей замок был занят при старте (гонка с умирающим
    предшественником или клиентским одиночкой), НЕ бросается навсегда — пробуем
    каждые 30 секунд. Без этого жилец, чей замок был занят в момент старта, остался бы без поиска навсегда."""
    global _model
    pending = pending if pending is not None else []
    while True:
        time.sleep(30)
        for t in list(pending):
            if adopt and adopt(t):
                pending.remove(t)
                tenants.append(t)
        for t in tenants:
            if not os.path.exists(t.sock_path):
                log(f"[{t.name}] файл сокета исчез — перезапуск через systemd")
                os._exit(1)
        with _model_lock:
            if IDLE_UNLOAD > 0 and _model is not None and time.time() - _last_used > IDLE_UNLOAD:
                _model = None
                import gc; gc.collect()
                log("досборочная модель выгружена (простой)")


def handle(conn, tenant):
    global _last_used
    try:
        data = conn.recv(65536).decode("utf-8").strip()
        if not data:
            return  # пустой пробник alive() от клиента, не запрос
        req = json.loads(data)
        cmd = req.get("cmd")
        q = req.get("q", "")
        k = int(req.get("k", 5))
        if cmd == "reindex":
            _last_used = time.time()  # простой меряем по досборке, не по поиску
            model = get_model()       # досборочная копия нужна ТОЛЬКО здесь
            _demote_this_thread()
            with tenant.sync_lock:
                t0 = time.time()
                ch, rm = core.sync_changed(tenant.get_db(), model, log, vault=tenant.vault)
                if ch or rm:
                    tenant.sync_mem(tenant.get_db())
            _last_used = time.time()  # отсчёт простоя — от КОНЦА досборки, не от начала
            conn.sendall(f"[reindex {ch}изм/{rm}уд {time.time()-t0:.2f}с]".encode("utf-8"))
            return
        # Поиск: матрица в RAM (вектора без диска), тёплое read-соединение
        # только для словесной части и карточек результатов.
        rdb = tenant.get_rdb()
        smodel = get_search_model()
        with tenant.read_lock:
            t1 = time.time()
            results = core.search(rdb, smodel, q, k, mem=tenant.mem)
            t2 = time.time()
        out = core.format_results(results)
        log(f"[{tenant.name}] поиск {t2-t1:.2f}с ({getattr(core.search, 'last_timing', '?')})")
        meta = f"[поиск {t2-t1:.2f}с]\n"
        conn.sendall((meta + out).encode("utf-8"))
        try:
            conn.close()
        except Exception:
            pass
    except Exception:
        log("ERR " + traceback.format_exc())
        try:
            conn.close()
        except Exception:
            pass


def _bind_tenant(tenant):
    """Замок единственности + сокет жильца. -> server socket или None (замок занят)."""
    lf = open(tenant.lock_path, "a+")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"[{tenant.name}] дежурный уже запущен (замок занят), жильца пропускаю")
        lf.close()
        return None
    _lockfiles.append(lf)
    lf.seek(0); lf.truncate(); lf.write(str(os.getpid())); lf.flush()
    if os.path.exists(tenant.sock_path):
        os.unlink(tenant.sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(tenant.sock_path)
    srv.listen(8)
    os.chmod(tenant.sock_path, 0o600)
    # Мульти-режим под root: сокет отдаётся жильцу — чужой не откроет.
    try:
        u = pwd.getpwnam(tenant.name)
        os.chown(tenant.sock_path, u.pw_uid, u.pw_gid)
        os.chown(tenant.lock_path, u.pw_uid, u.pw_gid)
    except (KeyError, PermissionError):
        pass  # один жилец = свой юзер, chown не нужен
    return srv


def _serve(srv, tenant):
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, tenant), daemon=True).start()


def main():
    tenants_cfg = getattr(localcfg, "TENANTS", None)
    if tenants_cfg:
        tenants = [Tenant(t["name"], t["ragdir"], t["vault"]) for t in tenants_cfg]
    else:
        tenants = [Tenant(pwd.getpwuid(os.getuid()).pw_name, RAGDIR, localcfg.VAULT)]

    bound, pending = [], []
    for t in tenants:
        srv = _bind_tenant(t)
        if srv is not None:
            bound.append((srv, t))
        else:
            pending.append(t)
    if not bound:
        log("ни одного жильца не занял (все замки заняты), выхожу")
        return

    served = [t for _, t in bound]

    def adopt(t):
        """Подобрать жильца, чей замок освободился. -> True если взяли."""
        srv = _bind_tenant(t)
        if srv is None:
            return False
        try:
            t.reload_mem(t.get_rdb())
            threading.Thread(target=_serve, args=(srv, t), daemon=True).start()
            log(f"[{t.name}] подобран сторожем — замок освободился")
            return True
        except Exception:
            log(f"[{t.name}] ERR подбора: " + traceback.format_exc())
            return False

    threading.Thread(target=unloader, args=(served, pending, adopt), daemon=True).start()
    log("дежурный поднят: " + ", ".join(t.name for t in served)
        + (" | ждут замка: " + ", ".join(t.name for t in pending) if pending else ""))

    # Прогрев в фоне: поисковая модель + матрицы всех жильцов.
    def _warmup():
        try:
            m = get_search_model()
            for _, t in bound:
                t.reload_mem(t.get_rdb())
                with t.read_lock:
                    core.search(t.get_rdb(), m, "прогрев", 1, mem=t.mem)
            log("прогрет: модель + матрицы " + ", ".join(t.name for _, t in bound))
        except Exception:
            log("ERR прогрев: " + traceback.format_exc())
    threading.Thread(target=_warmup, daemon=True).start()

    threads = [threading.Thread(target=_serve, args=(srv, t), daemon=True) for srv, t in bound]
    for th in threads:
        th.start()
    for th in threads:
        th.join()


if __name__ == "__main__":
    main()
