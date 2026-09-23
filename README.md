# freqtrade-oracle-lab

Oracle Cloud'un **ucretsiz** ARM sunucularinda birden fazla [freqtrade](https://github.com/freqtrade/freqtrade)
botunu ayni anda **dry-run** (sanal para) modunda calistirmak icin hazirlanmis
betikler ve strateji dosyalari.

Iki isi cozer:

1. **Sunucu yakalama.** Oracle'in ucretsiz ARM sunuculari surekli "Out of host capacity"
   verir. `oracle/catch-a1.sh` kapasite acilana kadar dener, acilinca durur.
2. **Bot filosu.** Tek sunucuda 4-5 strateji, her biri kendi portu, kendi veritabani
   ve kendi sanal cuzdaniyla calisir. Hepsini tek FreqUI'den izlersin.

## Uyari: bu stratejiler kanitlanmis degil

`strategies/` altindaki 9 strateji 2024-09 ile 2026-09 arasi Binance verisiyle
gelistirildi ve bagimsiz olarak denetlendi. **Hicbiri gercek parada kar garantisi vermez.**
Denetim sonuclari ozetle:

- Cogu, egitim doneminde pozitif gorunup gorulmemis veride negatife dondu.
- Karin buyuk kismi genelde 2024 sonu yukselisinden geliyordu.
- Komisyon %0.15'e cikinca bircogu zarara geciyordu.

Bunlari **arastirma malzemesi** olarak kullan, hazir kazanc araci olarak degil.
Kendi verinle yeniden backtest et, dry-run'da haftalarca izle, ancak ondan sonra
gercek para dusun. Yatirim tavsiyesi degildir.

## Onemli: Binance ABD sunucularini engelliyor

Oracle'in ABD bolgelerindeki sunuculardan `api.binance.com` **HTTP 451** doner.
Cozum olarak config, Binance'in halka acik veri ucnoktasina yonlendirilir:

```json
"ccxt_config": {
  "options": { "fetchMarkets": ["spot"], "defaultType": "spot", "fetchCurrencies": false },
  "urls": { "api": { "public":  "https://data-api.binance.vision/api/v3",
                     "private": "https://data-api.binance.vision/api/v3" } }
},
"enable_ws": false
```

Bununla **dry-run ve backtest calisir**. Ama hesap dogrulamasi gerektiren
**gercek islem bu sunuculardan yapilamaz.** Canlı bot ABD disinda bir makinede
calismali.

## Kurulum

```bash
git clone https://github.com/<kullanici>/freqtrade-oracle-lab.git
cd freqtrade-oracle-lab

# 1) Sunucu yakala  (once oracle/README.md icindeki hazirligi yap)
./oracle/catch-a1.sh 2 1 6

# 2) Sunucuyu hazirla (docker + freqtrade imaji)
./deploy/setup-server.sh <sunucu_ip>

# 3) Botlari kur (her birine ayri port, ayri veritabani, ayri 1000 USDT sanal cuzdan)
./deploy/deploy-bots.sh <sunucu_ip> TrendSlow4h SqueezeKeltner RelStrengthBtc RegimeDipBuyer TrendEmaMtf

# 4) Arayuze baglan (hicbir port internete acilmaz)
./deploy/tunnel.sh <sunucu_ip>
```

Sonra FreqUI'yi ac ve cikan `http://127.0.0.1:80xx` adreslerini bot olarak ekle.
Varsayilan giris `freqtrader` / `freqtrader` (uretimde degistir: `UI_PASS=... ./deploy/deploy-bots.sh ...`).

## Periyodik arastirma (istege bagli)

`deploy/research.sh` sunucuda 4 saatte bir tum stratejileri son 180 gun icin
backtest edip `user_data/research_results.csv` dosyasina yazar. Hem strateji
takibi saglar hem sunucuyu "bos" olmaktan cikarir.

```bash
scp -i ~/.ssh/oracle_freqtrade deploy/research.sh ubuntu@<ip>:~/ft/
ssh -i ~/.ssh/oracle_freqtrade ubuntu@<ip> \
  '(crontab -l 2>/dev/null; echo "17 */4 * * * /home/ubuntu/ft/research.sh") | crontab -'
```

## Guvenlik

- Bot arayuzleri sadece `127.0.0.1` dinler, internete acilmaz. Erisim SSH tuneliyle.
- Bu repoda **hicbir API anahtari yoktur** ve olmamalidir. `.gitignore` pem, key ve
  `config-private.json` dosyalarini engeller.
- Borsa anahtari kullanacaksan `dry_run` kapali bir config ile **sadece kendi makinende**
  tut. Anahtarda "withdrawals" izni kapali olsun, IP kisitlamasi ekle.

## Dizin yapisi

```
oracle/catch-a1.sh        ucretsiz ARM sunucu yakalama dongusu
oracle/README.md          oci CLI kurulumu ve Oracle notlari
deploy/setup-server.sh    sunucuya docker + freqtrade imaji
deploy/deploy-bots.sh     N adet dry-run botu kur
deploy/tunnel.sh          arayuzler icin SSH tuneli
deploy/research.sh        periyodik backtest isi
config/config.template.json  anahtarsiz ornek config
strategies/*.py           9 arastirma stratejisi
```

## Lisans

MIT. Kendi riskinle kullan.
