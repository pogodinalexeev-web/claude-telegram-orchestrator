#!/usr/bin/env python3
"""Клиент к дежурному vault-rag. Если демон не жив — поднимает его и ждёт.
Usage: search_client.py "запрос" [k]"""
import socket, sys, os, json, time, subprocess

RAGDIR = os.path.dirname(os.path.abspath(__file__))
SOCK = os.path.join(RAGDIR, "daemon.sock")
DAEMON = os.path.join(RAGDIR, "daemon.py")
PY = os.path.join(RAGDIR, "venv/bin/python")


def try_send(q, k):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(120)
    s.connect(SOCK)
    s.sendall(json.dumps({"q": q, "k": k}).encode("utf-8"))
    chunks = []
    while True:
        b = s.recv(65536)
        if not b:
            break
        chunks.append(b)
    s.close()
    return b"".join(chunks).decode("utf-8")


def ensure_daemon():
    if os.path.exists(SOCK):
        return
    # поднять демон в фоне
    subprocess.Popen([PY, DAEMON], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    # ждать появления сокета (первая загрузка модели ~3-5с, с запасом до 40с)
    for _ in range(80):
        if os.path.exists(SOCK):
            time.sleep(0.3)
            return
        time.sleep(0.5)


def main():
    if len(sys.argv) < 2:
        print('Usage: search "запрос" [k]', file=sys.stderr)
        sys.exit(1)
    q = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    for attempt in range(2):
        try:
            print(try_send(q, k))
            return
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
            ensure_daemon()
    print("Не удалось связаться с дежурным vault-rag (см. daemon.log)", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
