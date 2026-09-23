#!/bin/bash
# Sunucuya Docker ve freqtrade imajini kurar.
# Kullanim: ./setup-server.sh <sunucu_ip> [ssh_anahtari]
set -eu
IP="${1:?kullanim: ./setup-server.sh <ip> [ssh_key]}"
KEY="${2:-$HOME/.ssh/oracle_freqtrade}"
S="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o BatchMode=yes ubuntu@$IP"

echo "[1/3] Docker kuruluyor..."
$S 'command -v docker >/dev/null || (curl -fsSL https://get.docker.com | sudo sh >/dev/null 2>&1 && sudo usermod -aG docker ubuntu)'
echo "[2/3] freqtrade imaji cekiliyor (birkac dakika surebilir)..."
$S 'sudo docker pull freqtradeorg/freqtrade:stable >/dev/null && sudo docker run --rm freqtradeorg/freqtrade:stable --version | tail -2'
echo "[3/3] Klasorler hazirlaniyor..."
$S 'mkdir -p ~/ft/user_data/{data,logs,backtest_results,bots,strategies}'
echo "Hazir: $IP"
