#!/usr/bin/env bash
# Bir Oracle hesabini GitHub secret'i olarak ekler; Actions o hesapta da sunucu arar.
# Once Oracle konsolunda API key olustur ve "Configuration file preview" metnini kopyala.
# Kullanim:
#   ./add-account.sh <hesap_no> <indirilen_private_key.pem>             config panodan okunur
#   ./add-account.sh <hesap_no> <indirilen_private_key.pem> <config>    config dosyadan okunur
set -eu
N="${1:?kullanim: ./add-account.sh <hesap_no> <private_key.pem> [config_dosyasi]}"
PEM="${2:?private key .pem dosyasi gerekli}"
REPO="${REPO:-doonstudio/oracle-a1-catcher}"
SSH_PUB="${SSH_PUB:-$HOME/.ssh/oracle_a1.pub}"

case "$N" in ''|*[!0-9]*) echo "hesap_no sayi olmali (1, 2, 3...)"; exit 1 ;; esac
[ -f "$PEM" ] || { echo "dosya yok: $PEM"; exit 1; }
grep -q "PRIVATE KEY" "$PEM" || { echo "$PEM bir private key dosyasi degil"; exit 1; }

if [ -n "${3:-}" ]; then CFG=$(tr -d '\r' < "$3"); else CFG=$(pbpaste | tr -d '\r'); fi
for k in user fingerprint tenancy region; do
  printf '%s\n' "$CFG" | grep -q "^[[:space:]]*$k[[:space:]]*=" || {
    echo "Config metninde '$k' satiri yok. Oracle'daki 'Configuration file preview' metnini kopyaladigindan emin ol."
    exit 1
  }
done
REGION=$(printf '%s\n' "$CFG" | sed -n 's/^[[:space:]]*region[[:space:]]*=[[:space:]]*//p' | head -1)

if gh secret list -R "$REPO" | grep -q "^OCI_CONFIG_$N[[:space:]]"; then
  read -r -p "Hesap $N zaten tanimli, uzerine yazilsin mi? [e/H] " a
  [ "$a" = e ] || [ "$a" = E ] || exit 1
fi

printf '%s\n' "$CFG" | gh secret set "OCI_CONFIG_$N" -R "$REPO"
gh secret set "OCI_KEY_$N" -R "$REPO" < "$PEM"
echo "Hesap $N eklendi (bolge: $REGION): OCI_CONFIG_$N, OCI_KEY_$N"

# Tum hesaplar ayni SSH anahtarini kullanir; ilk seferde yuklenir
if ! gh secret list -R "$REPO" | grep -q "^SSH_PUBLIC_KEY[[:space:]]"; then
  if [ ! -f "$SSH_PUB" ]; then
    mkdir -p "$(dirname "$SSH_PUB")" && chmod 700 "$(dirname "$SSH_PUB")"
    ssh-keygen -q -t ed25519 -f "${SSH_PUB%.pub}" -N ""
  fi
  gh secret set SSH_PUBLIC_KEY -R "$REPO" < "$SSH_PUB"
  echo "SSH_PUBLIC_KEY yuklendi: $SSH_PUB (sunuculara ssh -i ${SSH_PUB%.pub} ubuntu@<ip> ile baglanirsin)"
fi
