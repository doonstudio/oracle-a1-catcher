#!/usr/bin/env bash
# Oracle Cloud Always Free ARM (VM.Standard.A1.Flex) sunucusu yakalama.
# Populer bolgelerde launch cogunlukla "Out of host capacity" doner; kapasite gun
# icinde kisa araliklarla acilir. Betik tum availability domain'leri dener, sunucu
# olusunca (ya da zaten varsa) durur. Ucretsiz kotayi asacak istegi hic gondermez.
#
# Kucuk al, sonra buyut: hedef boyut (2/12) hicbir AD'de yoksa SMALL boyutu (1/6) denenir.
# Kucuk sunucu varken her turda kapasite raporuna bakilir; hedef boyut icin yer varsa
# sunucu yerinde buyutulur (Oracle bunu yaparken sunucuyu bir kez yeniden baslatir).
#
# Gereken: oci CLI + ~/.oci/config ve SSH acik anahtari (bkz. README.md)
# Kullanim:
#   ./catch-a1.sh          kapasite acilana kadar dongude dener (bilgisayarda)
#   ./catch-a1.sh --once   tek tur dener ve cikar (GitHub Actions / cron)
# Birden fazla hesap: OCI_CLI_CONFIG_FILE ve/veya OCI_CLI_PROFILE ile hesap sec.
# Cikis: 0 = sunucu hazir (zaten vardi ya da olusturuldu)
#       10 = bu turda kapasite yok ya da kucuk sunucu buyutulmeyi bekliyor, sonra tekrar dene
#        1 = yapilandirma / API hatasi    3 = ucretsiz kota asilacakti
set -u
export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True SUPPRESS_LABEL_WARNING=True PYTHONWARNINGS=ignore

ONCE=0; [ "${1:-}" = "--once" ] && ONCE=1

NAME="${INSTANCE_NAME:-a1-free}"
OCPU="${OCPU:-2}"
MEM="${MEMORY_GB:-12}"
FREE_OCPU="${FREE_OCPU:-2}"        # hesabin ucretsiz A1 siniri; ustu ucretlendirilir
FREE_MEM="${FREE_MEM:-12}"
SMALL="${SMALL-1:6}"               # hedef yoksa alinacak boyut (OCPU:GB); SMALL= ile kapatilir
BOOT_GB="${BOOT_GB:-}"             # bos = imaj varsayilani (~47 GB); ucretsiz blok depolama toplam 200 GB
SSH_PUB="${SSH_PUB:-$HOME/.ssh/oracle_a1.pub}"
LOG="${LOG:-$HOME/oracle-a1.log}"
SLEEP_AD="${SLEEP_AD:-15}"         # ayni turda AD'ler arasi bekleme (sn)
SLEEP_ROUND="${SLEEP_ROUND:-120}"  # turlar arasi bekleme (sn), sadece dongu modunda
SHAPE=VM.Standard.A1.Flex
ALIVE="shape=='$SHAPE' && \"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING'"

# Actions loglari herkese acik: OCID'ler maskelenir, IP sadece yerelde yazilir.
log() { echo "$(date '+%F %T') $*" | sed -E 's/ocid1\.[a-z0-9._-]+/ocid1.***/g' | tee -a "$LOG"; }
die() { log "HATA: $2"; exit "$1"; }
# NOTIFY_TOKEN: girisi zorunlu ntfy sunucusu icin erisim token'i (Authorization: Bearer)
notify() {
  [ -n "${NOTIFY_URL:-}" ] || return 0
  curl -fsS -m 15 ${NOTIFY_TOKEN:+-H "Authorization: Bearer $NOTIFY_TOKEN"} \
    -H "Title: $1" -H "Priority: high" -d "$2" "$NOTIFY_URL" >/dev/null 2>&1 || true
}
# OCI hata ciktisini tek satira indirir: "code: message"
why() {
  local c m
  c=$(printf '%s\n' "$1" | sed -n 's/^ *"code": "\(.*\)",*$/\1/p' | head -1)
  m=$(printf '%s\n' "$1" | sed -n 's/^ *"message": "\(.*\)",*$/\1/p' | head -1)
  if [ -n "$c$m" ]; then echo "$c: $m" | cut -c1-160; else printf '%s\n' "$1" | grep -m1 . | cut -c1-160; fi
}
field() { printf '%s\n' "$1" | sed -n "s/.*\"$2\": \([0-9.]*\).*/\1/p" | head -1; }
sfield() { printf '%s\n' "$1" | sed -n "s/.*\"$2\": \"\([^\"]*\)\".*/\1/p" | head -1; }
ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'; }

command -v oci >/dev/null || die 1 "oci CLI yok (macOS: brew install oci-cli)"
[ -f "$SSH_PUB" ] || die 1 "SSH acik anahtari yok: $SSH_PUB (uret: ssh-keygen -t ed25519 -f ${SSH_PUB%.pub} -N '')"
CFG="${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}"
T="${TENANCY:-$(awk -v p="[${OCI_CLI_PROFILE:-DEFAULT}]" '/^[[:space:]]*\[/ { gsub(/[[:space:]]/, ""); s = ($0 == p); next }
  s && /^[[:space:]]*tenancy[[:space:]]*=/ { sub(/^[^=]*=/, ""); gsub(/[[:space:]]/, ""); print; exit }' "$CFG" 2>/dev/null)}"
[ -n "$T" ] || die 1 "$CFG icinde [${OCI_CLI_PROFILE:-DEFAULT}] profili ya da tenancy satiri yok"
ERR=$(mktemp); trap 'rm -f "$ERR"' EXIT

# Hesaptaki canli A1 sunuculari: bizimki var mi (boyutu, yeri, durumu), toplam ne kadar kullaniliyor
a1_state() {
  local me="[?\"display-name\"=='$NAME'] | [0]"
  oci compute instance list -c "$T" --all 2>"$ERR" --query \
    "data[?$ALIVE] | {mine: length([?\"display-name\"=='$NAME']), ocpu: sum([].\"shape-config\".ocpus), mem: sum([].\"shape-config\".\"memory-in-gbs\"), myid: ($me.id) || '', myo: ($me.\"shape-config\".ocpus) || \`0\`, mym: ($me.\"shape-config\".\"memory-in-gbs\") || \`0\`, myad: ($me.\"availability-domain\") || '', myfd: ($me.\"fault-domain\") || '', myst: ($me.\"lifecycle-state\") || ''}"
}

launch() {  # $1=AD $2=OCPU $3=GB
  # --no-retry: CLI "Out of host capacity" (500) hatasini ~2 dk tekrar deniyor; tur uzamasin
  # shellcheck disable=SC2086
  oci --no-retry compute instance launch -c "$T" --availability-domain "$1" \
    --shape "$SHAPE" --shape-config "{\"ocpus\":$2,\"memoryInGBs\":$3}" \
    --image-id "$IMG" --subnet-id "$SUB" --assign-public-ip true \
    --display-name "$NAME" --ssh-authorized-keys-file "$SSH_PUB" \
    ${BOOT_GB:+--boot-volume-size-in-gbs "$BOOT_GB"} 2>&1
}

created() {  # $1=launch ciktisi $2=AD $3=OCPU $4=GB
  local id ip="" title="Oracle A1 sunucusu acildi" extra=""
  log "BASARILI: $NAME ($3 OCPU / $4 GB) -> $2"
  id=$(printf '%s\n' "$1" | sed -n 's/^ *"id": "\(ocid1\.instance[^"]*\)".*/\1/p' | head -1)
  for _ in $(seq 18); do   # public IP birkac dakikada atanir
    ip=$(oci compute instance list-vnics --instance-id "$id" --query 'data[0]."public-ip"' --raw-output 2>/dev/null)
    [ -n "$ip" ] && [ "$ip" != null ] && break
    ip=""; sleep 10
  done
  if [ -z "${GITHUB_ACTIONS:-}" ] && [ -n "$ip" ]; then
    if [ -f "${SSH_PUB%.pub}" ]; then echo "Baglan: ssh -i ${SSH_PUB%.pub} ubuntu@$ip"; else echo "Baglan: ssh ubuntu@$ip"; fi
  fi
  if ! ge "$3" "$OCPU"; then
    title="Oracle A1 sunucusu acildi (kucuk)"
    extra=" | $OCPU OCPU / $MEM GB icin yer acilinca buyutulecek, o sirada bir kez yeniden baslar"
  fi
  notify "$title" "$NAME ($3 OCPU / $4 GB) ${ip:-IP henuz atanmadi, konsola bak}$extra"
}

# Kucuk sunucuyu hedef boyuta buyutur. Oracle boyut degisikliginden once kapasite raporuna
# bakilmasini oneriyor; rapor sunucunun AD/fault domain'inde yer gormedikce istek gonderilmez,
# boylece basarisiz denemelerle bosuna yeniden baslatma olmaz.
upsize() {  # $1=id $2=AD $3=FD $4=OCPU $5=GB $6=durum $7=toplam OCPU $8=toplam GB
  local fd="" st r
  if [ "$6" != RUNNING ]; then log "$NAME $4 OCPU / $5 GB ($6), buyutme icin RUNNING bekleniyor"; return; fi
  awk -v u="$7" -v c="$4" -v n="$OCPU" -v f="$FREE_OCPU" -v um="$8" -v cm="$5" -v nm="$MEM" -v fm="$FREE_MEM" \
    'BEGIN { exit !(u - c + n <= f && um - cm + nm <= fm) }' ||
    { log "$NAME $4 OCPU / $5 GB; $OCPU / $MEM GB'ye buyutmek ucretsiz kotayi asar (kullanimda $7 / $8)"; return; }
  [ -n "$3" ] && fd="\"faultDomain\":\"$3\","
  st=$(oci compute compute-capacity-report create --compartment-id "$T" --availability-domain "$2" \
    --shape-availabilities "[{\"instanceShape\":\"$SHAPE\",$fd\"instanceShapeConfig\":{\"ocpus\":$OCPU,\"memoryInGBs\":$MEM}}]" \
    --query 'data."shape-availabilities"[0]."availability-status"' --raw-output 2>"$ERR")
  if [ "$st" != AVAILABLE ]; then
    log "$NAME $4 OCPU / $5 GB; $OCPU / $MEM GB icin yer yok (${st:-$(why "$(cat "$ERR")")})"; return
  fi
  r=$(oci --no-retry compute instance update --instance-id "$1" --force \
    --shape-config "{\"ocpus\":$OCPU,\"memoryInGBs\":$MEM}" 2>&1)
  if printf '%s' "$r" | grep -q '"lifecycle-state"'; then
    log "BUYUTME: $NAME $4 OCPU / $5 GB -> $OCPU OCPU / $MEM GB, sunucu yeniden basliyor"
    notify "Oracle A1 buyutuluyor" "$NAME $4 OCPU / $5 GB -> $OCPU OCPU / $MEM GB. Sunucu bir kez yeniden basliyor."
  else
    log "buyutme olmadi: $(why "$r")"
  fi
}

# Tek tur. 0 = sunucu hazir (tam boyut), 10 = kapasite yok / kucuk sunucu buyutulmeyi bekliyor
round() {
  local st mine o m myo mym ad r w size so sm n=0
  if ! st=$(a1_state); then log "Sunucu listesi alinamadi: $(why "$(cat "$ERR")")"; return 10; fi
  mine=$(field "$st" mine); o=$(field "$st" ocpu); m=$(field "$st" mem)
  if [ "${mine:-0}" != 0 ]; then
    myo=$(field "$st" myo); mym=$(field "$st" mym)
    if ge "$myo" "$OCPU" && ge "$mym" "$MEM"; then log "$NAME zaten var, yapacak is yok"; return 0; fi
    upsize "$(sfield "$st" myid)" "$(sfield "$st" myad)" "$(sfield "$st" myfd)" "$myo" "$mym" \
      "$(sfield "$st" myst)" "${o:-0}" "${m:-0}"
    return 10
  fi
  awk -v u="${o:-0}" -v n="$OCPU" -v f="$FREE_OCPU" -v um="${m:-0}" -v nm="$MEM" -v fm="$FREE_MEM" \
    'BEGIN { exit !(u + n <= f && um + nm <= fm) }' ||
    die 3 "ucretsiz kota asilacak: kullanimda ${o:-0} OCPU / ${m:-0} GB var, istenen $OCPU / $MEM, sinir $FREE_OCPU / $FREE_MEM"

  # Once hedef boyut tum AD'lerde, olmazsa kucuk boyut tum AD'lerde
  for size in "$OCPU:$MEM" ${SMALL:+"$SMALL"}; do
    so=${size%%:*}; sm=${size#*:}
    [ "$size" = "$OCPU:$MEM" ] || ! ge "$so" "$OCPU" || continue   # kucuk boyut hedeften kucuk olmali
    for ad in $ADS; do
      [ $n -gt 0 ] && sleep "$SLEEP_AD"; n=$((n + 1))
      r=$(launch "$ad" "$so" "$sm")
      if printf '%s' "$r" | grep -q '"lifecycle-state"'; then
        created "$r" "$ad" "$so" "$sm"
        if ge "$so" "$OCPU"; then return 0; else return 10; fi
      fi
      w=$(why "$r")
      case "$w" in
        *"ut of host capacity"*)  log "$ad: $so/$sm kapasite yok" ;;
        TooManyRequests*)         log "$ad: istek siniri (429), tur birakildi"; return 10 ;;
        LimitExceeded*|QuotaExceeded*) die 3 "$ad: $w" ;;
        NotAuthenticated*|NotAuthorizedOrNotFound*|InvalidParameter*) die 1 "$ad: $w" ;;
        *)                        log "$ad: $so/$sm $w" ;;
      esac
    done
  done
  return 10
}

ADS=$(oci iam availability-domain list -c "$T" --query 'data[].name' --raw-output 2>"$ERR") ||
  die 1 "OCI'ye baglanilamadi: $(why "$(cat "$ERR")")"
ADS=$(printf '%s\n' "$ADS" | tr -d '[]," ' | grep -v '^$')
[ -n "$ADS" ] || die 1 "availability domain listesi bos"

# Public IP verilebilen subnet: once bolgesel ve adinda "public" gecen, sonra herhangi bolgesel, en son AD'ye bagli
PUB='!"prohibit-public-ip-on-vnic"'
SUB="${SUBNET_ID:-$(oci network subnet list -c "$T" --all --raw-output --query \
  "(data[?$PUB && !\"availability-domain\" && contains(\"display-name\",'public')].id | [0]) || (data[?$PUB && !\"availability-domain\"].id | [0]) || (data[?$PUB].id | [0])" 2>/dev/null)}"
[ -n "$SUB" ] && [ "$SUB" != null ] || die 1 "public subnet yok. Konsolda 'Start VCN Wizard' ile internet erisimli VCN olustur"
SUB_INFO=$(oci network subnet get --subnet-id "$SUB" --raw-output \
  --query "join('|', [data.\"display-name\", data.\"availability-domain\" || ''])" 2>/dev/null)
SUB_AD="${SUB_INFO#*|}"
[ -n "$SUB_AD" ] && ADS="$SUB_AD"   # AD'ye bagli subnet sadece kendi AD'sinde kullanilabilir

IMG="${IMAGE_ID:-$(oci compute image list -c "$T" --operating-system 'Canonical Ubuntu' --operating-system-version '24.04' \
  --shape "$SHAPE" --sort-by TIMECREATED --sort-order DESC --query 'data[0].id' --raw-output 2>/dev/null)}"
[ -n "$IMG" ] && [ "$IMG" != null ] || die 1 "Ubuntu 24.04 ARM imaji bulunamadi"

log "Hedef: $NAME, $OCPU OCPU / $MEM GB${SMALL:+ (yoksa ${SMALL%%:*}/${SMALL#*:}, sonra buyutme)}, subnet: ${SUB_INFO%%|*}, AD: $(echo $ADS | wc -w | tr -d ' ') adet"
while :; do
  round && exit 0
  [ "$ONCE" = 1 ] && exit 10
  sleep "$SLEEP_ROUND"
done
