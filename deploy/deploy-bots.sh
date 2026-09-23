#!/bin/bash
# Bir sunucuya N adet dry-run freqtrade botu kurar. Her botun kendi portu,
# kendi veritabani ve kendi sanal cuzdani olur.
# Kullanim: ./deploy-bots.sh <sunucu_ip> <Strateji1> [Strateji2 ...]
set -eu
IP="${1:?kullanim: ./deploy-bots.sh <ip> <Strateji1> [Strateji2 ...]}"; shift
STRATS=("$@"); [ "${#STRATS[@]}" -gt 0 ] || { echo "en az bir strateji adi ver"; exit 1; }
KEY="${SSH_KEY:-$HOME/.ssh/oracle_freqtrade}"
WALLET="${WALLET:-1000}"          # her bota ayri sanal butce
BASE_PORT="${BASE_PORT:-8081}"
UI_USER="${UI_USER:-freqtrader}"
UI_PASS="${UI_PASS:-freqtrader}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
S="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o BatchMode=yes ubuntu@$IP"

command -v python3 >/dev/null || { echo "python3 gerekli"; exit 1; }
echo "Stratejiler gonderiliyor..."
$S 'sudo chown -R ubuntu:ubuntu ~/ft 2>/dev/null; mkdir -p ~/ft/user_data/{strategies,bots}'
scp -q -i "$KEY" "$HERE"/strategies/*.py ubuntu@"$IP":~/ft/user_data/strategies/
$S 'find ~/ft -name "._*" -delete; rm -rf ~/ft/user_data/strategies/__pycache__'

TMP=$(mktemp -d); P=$BASE_PORT
for STRAT in "${STRATS[@]}"; do
  python3 - "$HERE/config/config.template.json" "$TMP/$STRAT.json" "$STRAT" "$P" "$WALLET" "$UI_USER" "$UI_PASS" <<'PY'
import json, secrets, sys
src, dst, strat, port, wallet, user, pw = sys.argv[1:8]
c = json.load(open(src))
c["bot_name"] = strat
c["dry_run"] = True
c["dry_run_wallet"] = float(wallet)
c["exchange"]["key"] = ""; c["exchange"]["secret"] = ""
a = c["api_server"]
a["listen_ip_address"] = "127.0.0.1"       # sadece sunucu icinden erisilir, disariya acilmaz
a["listen_port"] = int(port)
a["username"] = user; a["password"] = pw
a["jwt_secret_key"] = secrets.token_hex(32)
a["ws_token"] = secrets.token_urlsafe(24)
json.dump(c, open(dst, "w"), indent=4)
PY
  P=$((P+1))
done
scp -q -i "$KEY" "$TMP"/*.json ubuntu@"$IP":~/ft/user_data/bots/
rm -rf "$TMP"

P=$BASE_PORT
for STRAT in "${STRATS[@]}"; do
  $S "sudo docker rm -f bot_$STRAT >/dev/null 2>&1 || true
      sudo docker run -d --name bot_$STRAT --restart unless-stopped --network host \
        -v /home/ubuntu/ft/user_data:/freqtrade/user_data freqtradeorg/freqtrade:stable \
        trade --config /freqtrade/user_data/bots/$STRAT.json --strategy $STRAT \
        --db-url sqlite:////freqtrade/user_data/db_$STRAT.sqlite >/dev/null"
  echo "  $STRAT -> port $P"
  P=$((P+1)); sleep 10
done

echo; echo "Kontrol ediliyor..."
sleep 45
$S 'sudo docker ps --filter name=bot_ --format "  {{.Names}}  {{.Status}}"; free -m | awk "/Mem/{printf \"  RAM: %.0f%%\n\", \$3/\$2*100}"'
