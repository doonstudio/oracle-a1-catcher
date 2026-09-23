#!/bin/bash
# Sunucuda periyodik strateji arastirmasi: veriyi tazele, tum stratejileri
# son 180 gun icin backtest et, sonuclari CSV'ye ekle.
# Sunucuya kopyala ve cron'a bagla:
#   17 */4 * * * /home/ubuntu/ft/research.sh
set -u
FT=/home/ubuntu/ft
LOG=$FT/user_data/logs/research.log
CSV=$FT/user_data/research_results.csv
CFG=/freqtrade/user_data/config.json
D() { sudo docker run --rm --network host -v $FT/user_data:/freqtrade/user_data freqtradeorg/freqtrade:stable "$@"; }

mkdir -p "$(dirname "$LOG")"
echo "=== $(date -u +%F' '%T) tur basladi ===" >> $LOG
D download-data --config $CFG --timeframes 5m 15m 1h 4h 1d --days 400 >> $LOG 2>&1

[ -f $CSV ] || echo "tarih,strateji,pencere,islem,kar_yuzde,max_dusus,sortino,profit_factor" > $CSV
END=$(date -u +%Y%m%d)
START=$(date -u -d "-180 days" +%Y%m%d 2>/dev/null || date -u -v-180d +%Y%m%d)

for F in $FT/user_data/strategies/*.py; do
  S=$(basename "$F" .py)
  OUT=$(D backtesting --config $CFG --strategy "$S" --timerange "${START}-${END}" --cache none 2>&1)
  g(){ echo "$OUT" | grep -m1 "│ $1" | awk -F"│" '{print $3}' | tr -d ' %'; }
  TR=$(echo "$OUT" | grep -m1 "Total/Daily Avg Trades" | awk -F"│" '{print $3}' | cut -d/ -f1 | tr -d ' ')
  echo "$(date -u +%F),$S,${START}-${END},${TR},$(g 'Total profit %'),$(g 'Max % of account underwater '),$(g 'Sortino (closed trades)'),$(g 'Profit factor')" >> $CSV
  echo "$(date -u +%T) $S bitti" >> $LOG
done
echo "=== tur bitti ===" >> $LOG
