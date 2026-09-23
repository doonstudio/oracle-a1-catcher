# oracle-a1-catcher

Oracle Cloud **Always Free** ARM sunucusunu (VM.Standard.A1.Flex, **1 adet, 2 OCPU / 12 GB**)
kapasite açıldığı anda yakalar.

Popüler bölgelerde sunucu açmaya çalışınca Oracle neredeyse her seferinde
**"Out of host capacity"** döner. Kapasite gün içinde kısa aralıklarla açılıp kapanır.
Bu repo GitHub Actions üzerinde **5 dakikada bir** Oracle'a bakar, kapasite bulunca
sunucuyu açar ve kendi zamanlamasını kapatır. Bilgisayarın açık kalması gerekmez.

## Nasıl çalışır

Her çalışmada [`catch-a1.sh`](catch-a1.sh) sırasıyla şunları yapar:

1. `a1-free` adında canlı bir sunucu varsa hiçbir şey yapmadan çıkar.
2. Hesaptaki tüm A1 sunucularının toplam OCPU ve RAM'ine bakar. Yeni sunucu ücretsiz
   sınırı (2 OCPU / 12 GB) aşacaksa **istek göndermeden durur**, böylece ücret çıkmaz.
3. Bölgedeki tüm availability domain'lerde sırayla sunucu açmayı dener.
4. Açılınca bildirim gönderir (isteğe bağlı) ve workflow kendini devre dışı bırakır.

## Kurulum

Bilgisayarında sadece `gh` CLI yeterli, `oci` CLI kurmana gerek yok.

### 1. Oracle API anahtarı

Oracle konsolunda sağ üstteki profil menüsünden **My profile → Tokens and keys → API keys →
Add API key** yolunu izle. **Generate API key pair** seç, **Download private key** ile
`.pem` dosyasını indir ve **Add**'e bas. Açılan "Configuration file preview" penceresindeki
`user`, `fingerprint`, `tenancy` ve `region` değerlerini not al.

`.pem` dosyası hesabına tam erişim verir. Bir parola yöneticisinde sakla, hiçbir repoya koyma.

### 2. Ağ (VCN)

**Networking → Virtual Cloud Networks → Actions → Start VCN Wizard →
"Create VCN with Internet Connectivity"**. Varsayılanlarla oluştur. Betik adında
`public` geçen subnet'i kendisi bulur.

### 3. SSH anahtarı

Sunucuya bağlanırken kullanacağın anahtar:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oracle_a1 -N ""
```

### 4. GitHub secret'ları

```bash
R=doonstudio/oracle-a1-catcher
gh secret set OCI_USER_OCID    -R $R --body "ocid1.user.oc1..xxxx"
gh secret set OCI_TENANCY_OCID -R $R --body "ocid1.tenancy.oc1..xxxx"
gh secret set OCI_FINGERPRINT  -R $R --body "aa:bb:cc:..."
gh secret set OCI_REGION       -R $R --body "us-chicago-1"
gh secret set OCI_PRIVATE_KEY  -R $R < ~/Downloads/<indirdigin>.pem
gh secret set SSH_PUBLIC_KEY   -R $R < ~/.ssh/oracle_a1.pub
```

İsteğe bağlı bildirim için [ntfy](https://ntfy.sh) kullanabilirsin. Tahmin edilmesi zor bir
konu adı seç, telefondaki ntfy uygulamasında o konuya abone ol:

```bash
gh secret set NOTIFY_URL -R $R --body "https://ntfy.sh/<rastgele-konu-adi>"
```

### 5. Dene

```bash
gh workflow run catch-a1.yml -R doonstudio/oracle-a1-catcher
gh run watch -R doonstudio/oracle-a1-catcher
```

"kapasite yok" satırları normal, iş 5 dakikada bir kendiliğinden tekrar dener. Sunucu
açılınca IP adresini Oracle konsolunda (Compute → Instances) ya da ntfy bildiriminde görürsün:

```bash
ssh -i ~/.ssh/oracle_a1 ubuntu@<ip>
```

Sunucuyu silip yenisini yakalatmak istersen zamanlamayı tekrar aç:

```bash
gh workflow enable catch-a1.yml -R doonstudio/oracle-a1-catcher
```

## Bilgisayarda çalıştırma (isteğe bağlı)

`oci` CLI kurulu ve `~/.oci/config` hazırsa aynı betik yerelde de çalışır:

```bash
brew install oci-cli
./catch-a1.sh          # kapasite açılana kadar 2 dakikada bir dener
./catch-a1.sh --once   # tek tur dener ve çıkar
```

## Ayarlar

Hepsi ortam değişkeniyle değiştirilebilir. Varsayılanlar tek bir 2 OCPU / 12 GB sunucu içindir.

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `INSTANCE_NAME` | `a1-free` | Sunucu adı. Bu adla canlı sunucu varsa betik bir şey yapmaz |
| `OCPU` / `MEMORY_GB` | `2` / `12` | Açılacak sunucunun boyutu |
| `FREE_OCPU` / `FREE_MEM` | `2` / `12` | Hesabın ücretsiz A1 sınırı. Toplam kullanım bunu aşacaksa istek gönderilmez |
| `BOOT_GB` | boş (~47 GB) | Disk boyutu. Ücretsiz blok depolama toplamı 200 GB |
| `SSH_PUB` | `~/.ssh/oracle_a1.pub` | Sunucuya yüklenecek SSH açık anahtarı |
| `SUBNET_ID` / `IMAGE_ID` | otomatik | Adında `public` geçen subnet ve en yeni Ubuntu 24.04 ARM imajı |
| `NOTIFY_URL` | boş | Sunucu açılınca buraya POST atılır (ntfy uyumlu) |
| `SLEEP_AD` / `SLEEP_ROUND` | `15` / `120` | AD'ler ve turlar arası bekleme (sn) |

Çıkış kodları: `0` sunucu hazır, `10` bu turda kapasite yok, `1` yapılandırma ya da API
hatası, `3` ücretsiz kota aşılacaktı.

## Bilinmesi gerekenler

- **Actions logları herkese açık** (repo public). Betik OCID'leri maskeler ve IP adresini
  loga yazmaz. IP'yi konsoldan ya da bildirimden al.
- **Boşta kalan sunucu geri alınır.** 7 gün boyunca CPU, ağ ve bellek kullanımı aynı anda
  %20'nin altında kalırsa Oracle sunucuyu durdurabilir. Sunucuda sürekli çalışan bir iş olsun.
- **Ana bölge değiştirilemez.** Ücretsiz hesap tek bölgeye abonedir; sunucu o bölgede açılır.
- **Zamanlama 60 gün sonra durabilir.** GitHub, 60 gün commit görmeyen public repolardaki
  zamanlanmış işleri kapatır. Öyle olursa `gh workflow enable` ile tekrar aç.
- **Kapasite sabır ister.** Chicago'da ~1000 denemeden sonra açıldığı oldu. Hesabı
  Pay As You Go'ya yükseltenlerin kapasiteyi daha kolay bulduğu sıkça bildiriliyor. Ücretsiz
  sınırın içinde kalındığı sürece ücret çıkmaz; betikteki kota kontrolü bu yüzden var.
- **Kapasiteyi denemeden sorgulamak** için:
  ```bash
  oci compute compute-capacity-report create --compartment-id <tenancy> \
    --availability-domain <AD> \
    --shape-availabilities '[{"instanceShape":"VM.Standard.A1.Flex","instanceShapeConfig":{"ocpus":2,"memoryInGBs":12}}]'
  ```

## Lisans

MIT.
