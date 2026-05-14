#!/bin/bash
# Daily scanner wrapper — retries every 5 min until email is sent or cutoff reached.
# Cutoff: 14:30 Vietnam time (ICT = UTC+7), after which the market is closed.
export PATH="/usr/local/bin:/usr/bin:/bin:/Users/tuan/Library/Python/3.9/bin:$PATH"
cd /Users/tuan/Projects/analyst-stock-vn

INTERVAL=300          # seconds between retries
CUTOFF_HOUR_ICT=14
CUTOFF_MIN_ICT=30
attempt=0

while true; do
    attempt=$((attempt + 1))
    echo ""
    echo "════════════════════════════════════════"
    echo "  Attempt #${attempt}  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "════════════════════════════════════════"

    /usr/bin/python3 notify_daily.py daily
    status=$?

    if [ $status -eq 0 ]; then
        echo "  ✓ Done — email sent on attempt #${attempt}."
        exit 0
    fi

    # Check whether we are past the daily cutoff (ICT)
    ict_hour=$(TZ=Asia/Ho_Chi_Minh date '+%H')
    ict_min=$(TZ=Asia/Ho_Chi_Minh date '+%M')
    past_cutoff=0
    if [ "$ict_hour" -gt "$CUTOFF_HOUR_ICT" ]; then
        past_cutoff=1
    elif [ "$ict_hour" -eq "$CUTOFF_HOUR_ICT" ] && [ "$ict_min" -ge "$CUTOFF_MIN_ICT" ]; then
        past_cutoff=1
    fi

    if [ $past_cutoff -eq 1 ]; then
        echo "  ✗ Past cutoff (${CUTOFF_HOUR_ICT}:${CUTOFF_MIN_ICT} ICT) — giving up after ${attempt} attempt(s)."
        exit 1
    fi

    echo "  ✗ Failed — retrying in $((INTERVAL / 60)) min…"
    sleep $INTERVAL
done
