---
name: neoton-sniff
description: Listen to packets from a NEOTONE digital handpan via MITM (bettercap arpspoof + tcpdump). Use when owner says "NEOTONE not online", "handpan not connecting", "/neoton-sniff", "listen to neotone packets", or when the situation repeats "device is in network but doesn't show up in the cabinet".
---

# /neoton-sniff — listen to NEOTONE via MITM

## When to use

Symptoms:
- NEOTONE digital handpan is in Wi-Fi (`<device-ip>` in ARP), but **in the manufacturer's cabinet (`digitalhandpan.com`) doesn't show as online**.
- After firmware update / power-cycle / network change.
- Any complaint like "handpan is in network, but site doesn't see it".

## ⚠ Before starting sniff — check the cheapest thing first

The online indicator on `digitalhandpan.com` **flickers on its own** — front-end doesn't sync status with backend regularly. Device can be fully connected while site shows offline.

**Cheap test (30 seconds, ALWAYS do this first):**
1. `arp -an | grep <device-ip>` — if not in ARP, device is really disconnected, go to sniff.
2. `ping <device-ip>` — should respond after firmware update (post-v6.9).
3. Ask owner to **press F5 on the cabinet page**. If status immediately lit up → it was a UI bug, not device. Done.

Go to sniff only if F5 didn't help and there are reasons to think device is really disconnected.

## What we know about the device

- Raspberry Pi inside, static IP `<device-ip>`, MAC `<device-mac>`.
- Outbound-only client. All TCP ports closed, no ICMP response, no mDNS.
- Backend: **`electrichandpan.com` → backend server:443** (DigitalOcean Amsterdam). NOT digitalhandpan.com — that's the cabinet front-end.
- Can only sniff via MITM in the same L2 subnet (Mac in the same Wi-Fi). **iPhone Personal Hotspot doesn't work** — AP isolation.

## Procedure

### 1. Check prerequisites

```bash
# device must be in ARP
arp -an | grep "<device-ip>"
# tools
which bettercap tcpdump
# Mac must be in same subnet
ifconfig en0 | grep "inet "
```

If `bettercap` missing — `brew install bettercap`. If NEOTONE not in ARP — ping by range or ask owner to power-cycle.

### 2. Start sniff

```bash
osascript -e 'do shell script "$VAULT/.claude/skills/neoton-sniff/scripts/start.sh" with administrator privileges'
```

System window will ask for Mac password (only on first run after boot). After `STARTED tcpdump=... bettercap=...` — Mac is now a middleman between the device and router, pcap accumulates in `/tmp/neotone-sniff/capture.pcap`.

### 3. Owner does something with the device

Ask to:
- power-cycle (3 sec = off → 30 sec wait → on);
- or trigger actions in the cabinet (load preset / change scale) and watch traffic;
- if OTA is in progress — just wait (1-3 minutes), server cuts connection every 5-10 sec but firmware assembles chunked-resume.

### 4. On-the-fly analysis

```bash
bash "$VAULT/.claude/skills/neoton-sniff/scripts/analyze.sh"
```

Output:
- DNS queries from device (should be to `electrichandpan.com`).
- Volume from server to device per minute — sharp rise (×3-5) usually coincides with online appearing on site.
- TCP flags: SYN/SYN-ACK (should be 1:1), RST (if from server >5 per minute = they're cutting — OTA or rate-limit).
- Active TCP sessions and their duration.

### 5. Reference baselines

**Active work (preset load / play session):** 100-300 KB/min from server, large 1440-byte chunks.
**Normal idle online:** 35-40 KB/min, short TLS sessions every ~5 sec with FIN-close, RST <2/min. **Site may falsely show offline — this is their UI bug, not device.**
**OTA in progress:** 1-2 MB over several minutes, 1440-byte chunks, **many RSTs from server** (~28 in 3 min). Normal for their server, firmware assembles chunked-resume.
**Real device disconnect:** not in ARP, no ping response, no SYNs from device in pcap. Then dig into Wi-Fi (hotspot channels/bands via housing).

### 6. Stop

```bash
osascript -e 'do shell script "$VAULT/.claude/skills/neoton-sniff/scripts/stop.sh" with administrator privileges'
```

ARP restored, IP forwarding disabled. Pcap stays in `/tmp/neotone-sniff/capture.pcap` — **move to `Projects/Music/NEOTONE/Resources/attachments/YYYY-MM-DD-HHMM-<slug>.pcap`** if needed as reference. `/tmp` is cleared on reboot.

**Rule: don't close sniffer without explicit "stop" from owner.** Intermediate "seems ok" is not a signal.

## What this skill CAN'T do

- **Doesn't decrypt TLS.** Inside HTTPS payload (what exactly server sends, what response codes) — closed. To decrypt need to inject own CA on Pi, and there's no access to Pi (SSH/admin closed).
- **Doesn't work through iPhone Personal Hotspot** — AP isolation with Apple.
- **Can't send commands to device from outside** — no open ports. Only intercept and analyze outbound traffic.
