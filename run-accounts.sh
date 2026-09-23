#!/usr/bin/env bash
# Kendi sunucunda cron ile calistirmak icin: her hesabi sirayla dener, durumu bildirir.
# Anahtarlar GitHub'a gitmez, sadece bu sunucuda durur.
#
# Hesap basina bir klasor ($ACCOUNTS_DIR, varsayilan ~/.oci/accounts):
#   <ad>/config    Oracle'in "Configuration file preview" metni, key_file=<ad>/key.pem
#   <ad>/key.pem   API private key
#   <ad>/env       (istege bagli) bu hesaba ozel ayarlar, orn. SUBNET_ID=... INSTANCE_NAME=...
# Ortak (istege bagli):
#   ssh.pub        yeni sunuculara yuklenecek SSH acik anahtari
#   notify_url     ntfy adresi, orn. https://ntfy.sh/<rastgele-konu>
#
# Kullanim:
#   run-accounts.sh            her hesap icin tek tur (cron: */5 * * * *)
#   run-accounts.sh --summary  her hesabin son durumunu bildirir (cron: gunde bir)
set -u
DIR="${ACCOUNTS_DIR:-$HOME/.oci/accounts}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export SSH_PUB="${SSH_PUB:-$DIR/ssh.pub}" LOG=/dev/null
[ -f "$DIR/notify_url" ] && NOTIFY_URL="$(tr -d '[:space:]' < "$DIR/notify_url")" && export NOTIFY_URL
notify() { [ -z "${NOTIFY_URL:-}" ] || curl -fsS -m 15 -H "Title: $1" -d "$2" "$NOTIFY_URL" >/dev/null 2>&1 || true; }

if [ "${1:-}" = "--summary" ]; then
  msg=""
  for cfg in "$DIR"/*/config; do
    [ -f "$cfg" ] || continue
    d=$(dirname "$cfg")
    msg="$msg$(basename "$d"): $(cat "$d/last" 2>/dev/null || echo 'henuz calismadi')
"
  done
  notify "Oracle A1 gunluk durum" "${msg:-hic hesap yok}"
  exit 0
fi

# Onceki tur hala suruyorsa bu turu atla
if command -v flock >/dev/null; then exec 9>"${TMPDIR:-/tmp}/oracle-a1.lock"; flock -n 9 || exit 0; fi

for cfg in "$DIR"/*/config; do
  [ -f "$cfg" ] || continue
  d=$(dirname "$cfg"); name=$(basename "$d")
  out=$(set -a; [ -f "$d/env" ] && . "$d/env"; OCI_CLI_CONFIG_FILE="$cfg" "$HERE/catch-a1.sh" --once 2>&1); rc=$?
  printf '%s\n' "$out" | sed "s/^/[$name] /"
  printf '%s\n' "$out" | grep . | tail -1 > "$d/last"

  # Hata bir kez bildirilir; duzelince de bir kez haber verilir.
  # Sunucu acildiginda bildirimi catch-a1.sh kendisi (IP ile) gonderir.
  case $rc in 0) st=hazir ;; 10) st=deneniyor ;; *) st=hata ;; esac
  prev=$(cat "$d/state" 2>/dev/null || echo yeni)
  if [ "$st" != "$prev" ]; then
    if [ "$st" = hata ]; then
      notify "Oracle A1: $name HATA" "$(printf '%s\n' "$out" | grep HATA | tail -1)"
    elif [ "$prev" = hata ]; then
      notify "Oracle A1: $name duzeldi" "Durum: $st"
    fi
    echo "$st" > "$d/state"
  fi
done
