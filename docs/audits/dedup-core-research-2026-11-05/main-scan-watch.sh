#!/usr/bin/env bash
# Watch the owner's own photo-manager instance while it scans.
# Events: server up/down, low RAM, notable app-log lines (progress every 2,000 files,
# exiftool rotation, errors, cancel, manifest write), v5 manifest appears (then exit).
OUT="$(git rev-parse --show-toplevel)/migration_manifest_v6.sqlite"
LOG="$(cygpath -u "$LOCALAPPDATA")/PhotoManager/logs/app_$(date +%Y%m%d).log"
prev_health="down"
low_warned=0
seen=$(wc -l < "$LOG" 2>/dev/null || echo 0)
while true; do
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/health </dev/null || true)
  if [ "$code" = "200" ]; then health="up"; else health="down"; fi
  if [ "$health" != "$prev_health" ]; then
    echo "$(date +%H:%M) server $health (health=$code)"
    prev_health="$health"
  fi
  free_kb=$(powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory" </dev/null 2>/dev/null | tr -d '\r ')
  if [ -n "$free_kb" ]; then
    free_mb=$((free_kb / 1024))
    if [ "$free_mb" -lt 1200 ] && [ "$low_warned" = 0 ]; then
      echo "$(date +%H:%M) LOW RAM free=${free_mb}MB"; low_warned=1
    elif [ "$free_mb" -ge 2500 ] && [ "$low_warned" = 1 ]; then
      echo "$(date +%H:%M) RAM recovered free=${free_mb}MB"; low_warned=0
    fi
  fi
  if [ -f "$LOG" ]; then
    total=$(wc -l < "$LOG")
    if [ "$total" -gt "$seen" ]; then
      sed -n "$((seen + 1)),${total}p" "$LOG" \
        | grep -E "Hashed [0-9]*,?[02468],000/|Total: |Hashing |wedged|rotating|exif-missing|consumer crashed|ERROR|WARNING|cancelled|Classifying|Writing|Scan complete|finished|Traceback" \
        | grep -Ev "LOW RAM" | cut -c1-170
      seen=$total
    fi
  fi
  if [ -f "$OUT" ]; then
    sz=$(stat -c %s "$OUT" 2>/dev/null || echo 0)
    echo "$(date +%H:%M) MANIFEST WRITTEN $OUT size=$sz"
    exit 0
  fi
  sleep 60
done
