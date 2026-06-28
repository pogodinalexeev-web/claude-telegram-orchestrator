#!/bin/bash
# Быстрый разбор pcap NEOTONE — что куда ходит, какие RST, объёмы.
PCAP="${1:-/tmp/neotone-sniff/capture.pcap}"
TARGET="<device-ip>"
SERVER=139.59.145.98

if [ ! -f "$PCAP" ]; then echo "pcap не найден: $PCAP"; exit 1; fi

echo "=== pcap: $PCAP ($(ls -lh $PCAP | awk '{print $5}')) ==="
echo ""

echo "=== DNS от <device-ip> (что резолвит) ==="
tcpdump -r $PCAP -nn "src $TARGET and udp port 53" 2>/dev/null | \
  awk '{for(i=8;i<=NF;i++) if($i ~ /\?/) print $i}' | sort -u | head -10

echo ""
echo "=== уникальные внешние IP (SYN от <device-ip>) ==="
tcpdump -r $PCAP -nn "src $TARGET and tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0" 2>/dev/null | \
  awk '{print $5}' | sort -u

echo ""
echo "=== объём server→<device-ip> по минутам ==="
tcpdump -r $PCAP -nn "src $SERVER and dst $TARGET" -tttt 2>/dev/null | \
  awk '{split($2,a,":"); min=a[1]":"a[2]; bytes[min]+=$NF; packets[min]++}
       END {for (m in bytes) printf "  %s  %4dp  %s КБ\n", m, packets[m], bytes[m]/1024}' | sort

echo ""
echo "=== TCP-флаги ==="
S=$(tcpdump -r $PCAP -nn "src $TARGET and host $SERVER and tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0" 2>/dev/null | wc -l)
SA=$(tcpdump -r $PCAP -nn "src $SERVER and tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack != 0" 2>/dev/null | wc -l)
F=$(tcpdump -r $PCAP -nn "host $SERVER and tcp[tcpflags] & tcp-fin != 0" 2>/dev/null | wc -l)
RSRV=$(tcpdump -r $PCAP -nn "src $SERVER and tcp[tcpflags] & tcp-rst != 0" 2>/dev/null | wc -l)
RDEV=$(tcpdump -r $PCAP -nn "src $TARGET and tcp[tcpflags] & tcp-rst != 0" 2>/dev/null | wc -l)
echo "  SYN от <device-ip>:    $S"
echo "  SYN-ACK сервер: $SA"
echo "  FIN:            $F"
echo "  RST от сервера: $RSRV  ← если >5/мин = они режут (OTA или rate-limit)"
echo "  RST от <device-ip>:    $RDEV"

echo ""
echo "=== диагностика ==="
if [ "$RSRV" -gt 15 ]; then
  echo "  ⚠ Много RST от сервера — похоже на OTA-download или rate-limit. См. эталон 08.05."
elif [ "$F" -gt 10 ] && [ "$S" -lt 20 ]; then
  echo "  → штатный idle: короткие TLS-сессии, FIN-close. Девайс на связи."
  echo "    Если на сайте всё равно offline — это баг UI digitalhandpan.com (мерцает индикатор)."
  echo "    Попросить owner нажать F5 на странице ЛК — должно сразу зажечься."
else
  echo "  → нестандартный паттерн, читай pcap руками: tcpdump -r $PCAP -nn"
fi
