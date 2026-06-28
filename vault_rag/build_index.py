#!/usr/bin/env python3
"""Полная пересборка индекса vault-rag с нуля (схема с номерами строк + учёт mtime файлов).
В норме не нужна — дежурный делает досборку на лету. Запускать только для пересборки с нуля."""
import time, sys, os
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
from fastembed import TextEmbedding


def main():
    t0 = time.time()
    for ext in ["", "-wal", "-shm", "-journal"]:
        f = Path(str(core.DB_PATH) + ext)
        if f.exists():
            f.unlink()
    db = core.connect()
    core.init_schema(db)
    print("Гружу модель…", flush=True)
    model = TextEmbedding(model_name=core.MODEL_NAME)
    files = list(core.iter_md_files())
    print(f"Файлов: {len(files)}. Индексирую…", flush=True)
    total = 0
    for i, (p, rel) in enumerate(files, 1):
        total += core.index_file(db, model, p, rel)
        if i % 100 == 0:
            db.commit()
            print(f"  {i}/{len(files)} файлов, {total} кусков", flush=True)
    db.commit()
    db.close()
    print(f"Готово: {total} кусков из {len(files)} файлов за {time.time()-t0:.0f} c. "
          f"Индекс {core.DB_PATH.stat().st_size/1e6:.1f} МБ", flush=True)


if __name__ == "__main__":
    main()
