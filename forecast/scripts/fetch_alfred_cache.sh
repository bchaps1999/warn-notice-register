#!/bin/bash
# Pre-populate the ALFRED vintage cache with one request per Friday.
# Sandboxed python cannot open sockets here, so fetching happens in shell;
# uiforecast's ingest then reads these cached files.
# Usage: fetch_alfred_cache.sh SERIES START_DATE END_DATE CACHE_DIR
set -euo pipefail
SERIES=$1; START=$2; END=$3; CACHE=$4
mkdir -p "$CACHE"
d=$(python3 -c "
from datetime import date, timedelta
s = date.fromisoformat('$START')
print(s + timedelta(days=(4 - s.weekday()) % 7))")
n=0
while [[ "$d" < "$END" || "$d" == "$END" ]]; do
  out="$CACHE/${SERIES}_${d}.csv"
  if [[ ! -s "$out" ]]; then
    for attempt in 1 2 3 4; do
      if curl -sf --http1.1 --max-time 60 \
        -A "warn-live-uiforecast/0.1 (research)" \
        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=${SERIES}&vintage_date=${d}" \
        -o "$out.tmp"; then
        head -c 16 "$out.tmp" | grep -q "observation_date" && mv "$out.tmp" "$out" && break
      fi
      sleep $((attempt * 5))
    done
    [[ -s "$out" ]] || { echo "FAILED $SERIES $d"; exit 1; }
    sleep 0.3
  fi
  n=$((n+1))
  if (( n % 52 == 0 )); then echo "$SERIES through $d"; fi
  d=$(python3 -c "from datetime import date, timedelta; print(date.fromisoformat('$d') + timedelta(weeks=1))")
done
echo "$SERIES done ($n weeks)"
