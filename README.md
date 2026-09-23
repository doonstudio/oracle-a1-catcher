# oracle-a1-catcher

Oracle Cloud **Always Free** ARM sunucusunu (VM.Standard.A1.Flex, **hesap başına 1 adet,
2 OCPU / 12 GB**) kapasite açıldığı anda yakalar. Birden fazla Oracle hesabını destekler.

Popüler bölgelerde sunucu açmaya çalışınca Oracle neredeyse her seferinde
**"Out of host capacity"** döner. Kapasite gün içinde kısa aralıklarla açılıp kapanır.
Bu repo GitHub Actions üzerinde **5 dakikada bir** tanımlı her Oracle hesabına bakar,
kapasite bulunca o hesapta sunucuyu açar. Tüm hesaplarda sunucu hazır olunca kendi
zamanlamasını kapatır. Bilgisayarın açık kalması gerekmez.

## Nasıl çalışır

Her çalışmada, her hesap için [`catch-a1.sh`](catch-a1.sh) sırasıyla şunları yapar:

1. `a1-free` adında canlı bir sunucu varsa hiçbir şey yapmadan geçer.
2. Hesaptaki tüm A1 sunucularının toplam OCPU ve RAM'ine bakar. Yeni sunucu ücretsiz
   sınırı (2 OCPU / 12 GB) aşacaksa **istek göndermeden durur**, böylece ücret çıkmaz.
3. Bölgedeki tüm availability domain'lerde sırayla sunucu açmayı dener.
4. Açılınca bildirim gönderir (isteğe bağlı).

## Kurulum

Bilgisayarında sadece `gh` CLI yeterli, `oci` CLI kurmana gerek yok. 1-3. adımları
**her Oracle hesabı için** tekrarla (hesap 1, 2, 3...).

### 1. API anahtarı oluştur

Oracle konsolunda sağ üstteki profil menüsünden **My profile → Tokens and keys → API keys →
Add API key** yolunu izle. **Generate API key pair** seç, **Download private key** ile
`.pem` dosyasını indir ve **Add**'e bas. Açılan **Configuration file preview** penceresindeki
metnin tamamını kopyala (panoda kalsın).

### 2. Hesabı ekle

Repo klasöründe:

```bash
./add-account.sh 1 ~/Downloads/<indirdigin>.pem
```

Komut panodaki config metnini ve `.pem` dosyasını `OCI_CONFIG_1` / `OCI_KEY_1` secret'ları
olarak kaydeder. İlk seferde `~/.ssh/oracle_a1` SSH anahtarını da üretip açık anahtarını
yükler; tüm sunucular bu anahtarla açılır. Sonraki hesaplar için `2`, `3` yaz. Config
metnini panodan değil de dosyadan vermek istersen üçüncü argüman olarak dosya yolunu ekle.

### 3. Ağ (VCN) oluştur

**Networking → Virtual Cloud Networks → Actions → Start VCN Wizard →
"Create VCN with Internet Connectivity"**. Varsayılanlarla oluştur. Betik adında
`public` geçen subnet'i kendisi bulur.

### 4. Anahtarları sakla

GitHub secret'ları yalnızca yazılabilir, sonradan okunamaz. Okunabilir tek kopya senin
saklayacağın yer olur. Bitwarden'da:

- Her hesap için bir **Secure Note** aç, örneğin "Oracle A1 – hesap 1 (e-posta)". İçine
  Configuration file preview metnini ve `.pem` dosyasının tüm içeriğini yapıştır.
- `~/.ssh/oracle_a1` SSH anahtarını bir **SSH key** öğesi olarak ekle. Tüm sunuculara
  bununla bağlanırsın.
- Sonra `~/Downloads` içindeki `.pem` dosyalarını sil.

Bir anahtar kaybolursa sorun olmaz. Konsoldan eskisini silip yeni API key oluştur ve
`./add-account.sh <no> <yeni.pem>` ile üzerine yaz.

### 5. Bildirim (isteğe bağlı)

[ntfy](https://ntfy.sh) ile sunucu açılınca telefona bildirim gelir. Tahmin edilmesi zor bir
konu adı seç, telefondaki ntfy uygulamasında o konuya abone ol:

```bash
gh secret set NOTIFY_URL -R doonstudio/oracle-a1-catcher --body "https://ntfy.sh/<rastgele-konu-adi>"
```

### 6. Dene

```bash
gh workflow run catch-a1.yml -R doonstudio/oracle-a1-catcher
gh run watch -R doonstudio/oracle-a1-catcher
```

Logda her hesap ayrı bir grup olarak görünür. "kapasite yok" satırları normal, iş 5
dakikada bir kendiliğinden tekrar dener. Sunucu açılınca IP adresini o hesabın Oracle
konsolunda (Compute → Instances) ya da ntfy bildiriminde görürsün:

```bash
ssh -i ~/.ssh/oracle_a1 ubuntu@<ip>
```

Yeni hesap ekler ya da bir sunucuyu silip yenisini yakalatmak istersen zamanlamayı tekrar aç:

```bash
gh workflow enable catch-a1.yml -R doonstudio/oracle-a1-catcher
```

## Bilgisayarda çalıştırma (isteğe bağlı)

`oci` CLI kurulu ve `~/.oci/config` hazırsa aynı betik yerelde de çalışır. Birden fazla
hesap için config dosyasında her hesaba bir profil aç (`[HESAP1]`, `[HESAP2]`...):

```bash
brew install oci-cli
./catch-a1.sh                            # varsayilan profil, kapasite acilana kadar 2 dakikada bir dener
OCI_CLI_PROFILE=HESAP2 ./catch-a1.sh     # baska hesap
./catch-a1.sh --once                     # tek tur dener ve cikar
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
| `OCI_CLI_CONFIG_FILE` / `OCI_CLI_PROFILE` | `~/.oci/config` / `DEFAULT` | Hangi hesabın kullanılacağı |
| `SUBNET_ID` / `IMAGE_ID` | otomatik | Adında `public` geçen subnet ve en yeni Ubuntu 24.04 ARM imajı |
| `NOTIFY_URL` | boş | Sunucu açılınca buraya POST atılır (ntfy uyumlu) |
| `SLEEP_AD` / `SLEEP_ROUND` | `15` / `120` | AD'ler ve turlar arası bekleme (sn) |

Çıkış kodları: `0` sunucu hazır, `10` bu turda kapasite yok, `1` yapılandırma ya da API
hatası, `3` ücretsiz kota aşılacaktı.

## Bilinmesi gerekenler

- **Kişi başına tek ücretsiz hesap.** Oracle [Free Tier SSS](https://www.oracle.com/cloud/free/faq/)
  kişi başına bir Always Free hesabına izin veriyor, birden fazla ücretsiz hesap açmayı
  yasaklıyor ve kurala uymayan hesapları askıya alabiliyor ya da kapatabiliyor. Buraya
  eklenen her hesap ayrı bir kişiye ya da şirkete ait olmalı ve sahibinin onayıyla kullanılmalı.
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
