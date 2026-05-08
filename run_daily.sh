#!/bin/bash
# Wrapper for cron — loads PATH and runs the daily scanner
export PATH="/usr/local/bin:/usr/bin:/bin:/Users/tuan/Library/Python/3.9/bin:$PATH"
cd /Users/tuan/Projects/analyst-stock-vn
/usr/bin/python3 notify_daily.py daily >> /tmp/analyst-stock-vn.log 2>&1
