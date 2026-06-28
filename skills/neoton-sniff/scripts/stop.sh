#!/bin/bash
# NEOTONE sniff stop — выключить MITM и вернуть ARP в норму
DIR=/tmp/neotone-sniff

# bettercap при graceful kill сам шлёт restore ARP — даём ему секунду
for f in bettercap.pid tcpdump.pid; do
  if [ -f $DIR/$f ]; then
    kill $(cat $DIR/$f) 2>/dev/null || true
    rm -f $DIR/$f
  fi
done
sleep 2

# страховка — добить если остались
pkill -f "bettercap.*arp.spoof" 2>/dev/null || true
pkill -f "tcpdump.*neotone-sniff" 2>/dev/null || true

# IP forwarding off
sysctl -w net.inet.ip.forwarding=0 >/dev/null 2>&1 || true

echo "STOPPED. pcap: $(ls -lh $DIR/capture.pcap 2>/dev/null | awk '{print $5}')"
echo "Перенести в Projects/Музыка/NEOTONE/Resources/attachments/ если нужен референс."
