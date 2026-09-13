#!/bin/bash
# Preserve emulator crash evidence before the next launch overwrites it.
#
# diagnostics.txt is reopened with "wt" on every launch, so a crash log survives
# only until the user relaunches - which is exactly what they do straight after a
# crash. This copies the file aside the moment an unhandled exception appears.
D="/c/OddBeta/runtime/diagnostics.txt"
OUT="/c/Users/<you>/New folder/crashlogs"
seen=""
while true; do
  sleep 5
  [ -f "$D" ] || continue
  if grep -aq "UNHANDLED EXCEPTION" "$D" 2>/dev/null; then
    sig=$(grep -a "UNHANDLED EXCEPTION" "$D" | tail -1)
    if [ "$sig" != "$seen" ]; then
      seen="$sig"
      ts=$(date +%H%M%S)
      cp "$D" "$OUT/crash_$ts.txt" 2>/dev/null
      echo "CRASH CAPTURED -> crashlogs/crash_$ts.txt"
      echo "$sig"
    fi
  fi
done
