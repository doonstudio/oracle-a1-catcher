#!/bin/bash
# Sunuculardaki botlarin arayuzunu SSH tuneliyle bilgisayarina getirir.
# Hicbir port internete acilmaz. FreqUI'ye http://127.0.0.1:<port> olarak eklenir.
# Kullanim: ./tunnel.sh <ip1> [ip2]
set -u
KEY="${SSH_KEY:-$HOME/.ssh/oracle_freqtrade}"
BOTS_PER_SERVER="${BOTS_PER_SERVER:-5}"
[ $# -ge 1 ] || { echo "kullanim: ./tunnel.sh <ip1> [ip2]"; exit 1; }
pkill -f "ssh -N -i $KEY" 2>/dev/null; sleep 1
LOCAL=8081
for IP in "$@"; do
  ARGS=""
  for i in $(seq 0 $((BOTS_PER_SERVER-1))); do
    ARGS="$ARGS -L $((LOCAL+i)):127.0.0.1:$((8081+i))"
    echo "  http://127.0.0.1:$((LOCAL+i))  ->  $IP:$((8081+i))"
  done
  # shellcheck disable=SC2086
  ssh -N -i "$KEY" -o ExitOnForwardFailure=yes $ARGS ubuntu@"$IP" &
  LOCAL=$((LOCAL+10))
done
sleep 4
echo "Tunel acik. FreqUI'de yukaridaki adresleri bot olarak ekle."
echo "Kapatmak icin: pkill -f 'ssh -N -i'"
wait
