#!/bin/bash
# Oracle Cloud Always Free ARM (A1.Flex) sunucu yakalama dongusu.
# "Out of host capacity" hatasi alanlar icin: kapasite acilana kadar dener, acilinca durur.
#
# Gereken: oci CLI kurulu ve ~/.oci/config yapilandirilmis olmali (bkz. oracle/README.md)
# Kullanim: ./catch-a1.sh [sunucu_sayisi] [ocpu] [ram_gb]
set -u
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True PYTHONWARNINGS=ignore

COUNT="${1:-2}"      # kac sunucu
OCPU="${2:-1}"       # sunucu basina cekirdek
MEM="${3:-6}"        # sunucu basina GB  (ucretsiz toplam sinir: 4 OCPU / 24 GB, 2026'dan beri 2 OCPU / 12 GB)
NAME_PREFIX="${NAME_PREFIX:-freqtrade}"
SSH_PUB="${SSH_PUB:-$HOME/.ssh/oracle_freqtrade.pub}"
LOG="${LOG:-$HOME/oracle-catch.log}"
SLEEP_AD="${SLEEP_AD:-15}"    # ayni turda AD'ler arasi bekleme
SLEEP_ROUND="${SLEEP_ROUND:-120}"  # turlar arasi bekleme

[ -f "$SSH_PUB" ] || { echo "SSH acik anahtari yok: $SSH_PUB"; echo "Uret: ssh-keygen -t ed25519 -f ${SSH_PUB%.pub} -N ''"; exit 1; }
command -v oci >/dev/null || { echo "oci CLI bulunamadi. Kurulum icin oracle/README.md"; exit 1; }

T=$(grep -m1 '^tenancy' ~/.oci/config | cut -d= -f2)
[ -n "$T" ] || { echo "~/.oci/config icinde tenancy yok"; exit 1; }

echo "Availability domain'ler aliniyor..."
mapfile -t ADS < <(oci iam availability-domain list --query 'data[].name' --raw-output 2>/dev/null | tr -d '[]," ' | grep -v '^$')
[ "${#ADS[@]}" -gt 0 ] || { echo "AD listesi alinamadi, oci config'i kontrol et"; exit 1; }

echo "Public subnet araniyor..."
SUB=$(oci network subnet list -c "$T" --all --query "data[?contains(\"display-name\",'public')].id | [0]" --raw-output 2>/dev/null)
[ -n "$SUB" ] && [ "$SUB" != "null" ] || { echo "Public subnet yok. Konsolda 'Start VCN Wizard' ile internet erisimli bir VCN olustur."; exit 1; }

echo "Ubuntu 24.04 ARM imaji araniyor..."
IMG=$(oci compute image list -c "$T" --operating-system 'Canonical Ubuntu' --operating-system-version '24.04' \
      --shape VM.Standard.A1.Flex --sort-by TIMECREATED --sort-order DESC --query 'data[0].id' --raw-output 2>/dev/null)
[ -n "$IMG" ] && [ "$IMG" != "null" ] || { echo "ARM imaji bulunamadi"; exit 1; }

exists() { oci compute instance list -c "$T" --display-name "$1" \
  --query "length(data[?\"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING'])" --raw-output 2>/dev/null; }

echo "Baslatiliyor: $COUNT sunucu, her biri ${OCPU} OCPU / ${MEM} GB. Log: $LOG"
echo "Durdurmak icin Ctrl+C."
while true; do
  PENDING=0
  for i in $(seq 1 "$COUNT"); do
    NAME="${NAME_PREFIX}-${i}"
    [ "$(exists "$NAME")" = "1" ] && continue
    PENDING=1
    for AD in "${ADS[@]}"; do
      R=$(oci compute instance launch -c "$T" --availability-domain "$AD" \
            --shape VM.Standard.A1.Flex --shape-config "{\"ocpus\":${OCPU},\"memoryInGBs\":${MEM}}" \
            --image-id "$IMG" --subnet-id "$SUB" --assign-public-ip true \
            --display-name "$NAME" --ssh-authorized-keys-file "$SSH_PUB" 2>&1)
      if echo "$R" | grep -q '"lifecycle-state"'; then
        echo "$(date '+%F %T') BASARILI $NAME -> $AD" | tee -a "$LOG"; break
      fi
      echo "$(date '+%F %T') $NAME $AD: $(echo "$R" | grep -m1 '"message"' | cut -c1-110)" >> "$LOG"
      sleep "$SLEEP_AD"
    done
  done
  if [ "$PENDING" = "0" ]; then
    echo "$(date '+%F %T') TUM SUNUCULAR HAZIR, donguden cikiliyor" | tee -a "$LOG"
    oci compute instance list -c "$T" --query 'data[].{isim:"display-name",durum:"lifecycle-state"}' --output table 2>/dev/null
    exit 0
  fi
  sleep "$SLEEP_ROUND"
done
