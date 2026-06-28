#!/bin/bash
# NEOTONE sniff start — MITM через bettercap + tcpdump
# Запускать через: osascript -e 'do shell script "..." with administrator privileges'
set -e
DIR=/tmp/neotone-sniff
TARGET="<device-ip>"
IFACE=en0

mkdir -p $DIR

# IP forwarding (bettercap обычно сам, но дублирую)
sysctl -w net.inet.ip.forwarding=1 >/dev/null 2>&1 || true

# tcpdump в фон. Без nohup — osascript не даёт tty.
(tcpdump -i $IFACE -U -w $DIR/capture.pcap "host $TARGET or (udp port 53)" \
  >$DIR/tcpdump.log 2>&1 </dev/null) &
echo $! > $DIR/tcpdump.pid
disown 2>/dev/null || true

# bettercap arpspoof — Mac между <device-ip> и gateway
(bettercap -iface $IFACE -eval "set arp.spoof.targets $TARGET; set arp.spoof.internal true; arp.spoof on" \
  >$DIR/bettercap.log 2>&1 </dev/null) &
echo $! > $DIR/bettercap.pid
disown 2>/dev/null || true

sleep 2
chmod 644 $DIR/capture.pcap 2>/dev/null || true
echo "STARTED  tcpdump=$(cat $DIR/tcpdump.pid)  bettercap=$(cat $DIR/bettercap.pid)"
echo "Pcap: $DIR/capture.pcap"
