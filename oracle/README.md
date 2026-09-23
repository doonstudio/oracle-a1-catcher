# Oracle Cloud ucretsiz ARM sunucusu yakalama

Oracle'in Always Free katmani ARM tabanli (Ampere A1) sunucu veriyor ama populer
bolgelerde surekli **"Out of host capacity"** hatasi alinir. Kapasite gun icinde
kisa araliklarla acilir. `catch-a1.sh` tam o ani yakalamak icin surekli dener ve
sunucular olusunca kendiliginden durur.

> Ornek: Chicago bolgesinde ~1000 denemeden sonra iki sunucu birden acildi.
> Kapasite genelde ABD gece saatlerinde aciliyor, bilgisayari acik birakmak sansi artiriyor.

## 1. Hazirlik

**oci CLI kur** (macOS):
```bash
brew install oci-cli
```
Linux icin: `bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"`

**API anahtari olustur:** Oracle konsolunda sag ust profil > My profile >
Tokens and keys > API keys > Add API key > "Generate API key pair" > iki dosyayi
da indir > Add. Cikan yapilandirma metnini kopyala.

```bash
mkdir -p ~/.oci && chmod 700 ~/.oci
mv ~/Downloads/*-private.pem  ~/.oci/oci_api_key.pem   # indirdigin gizli anahtar
chmod 600 ~/.oci/oci_api_key.pem
# kopyaladigin yapilandirmayi ~/.oci/config dosyasina yapistir,
# key_file satirini ~/.oci/oci_api_key.pem olarak duzelt
chmod 600 ~/.oci/config
oci iam availability-domain list   # calisiyorsa hazirsin (ilk dakikalarda 401 verebilir, bekle)
```

**SSH anahtari uret:**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/oracle_freqtrade -N ""
```

**Ag olustur:** Konsolda Networking > Virtual Cloud Networks > Actions >
Start VCN Wizard > "Create VCN with Internet Connectivity". Public subnet sart.

## 2. Calistir

```bash
./catch-a1.sh 2 1 6      # 2 sunucu, her biri 1 cekirdek 6 GB
```

Ucretsiz sinir 2026'dan beri toplam **2 OCPU / 12 GB**. Yani 2x(1 OCPU, 6 GB)
veya 1x(2 OCPU, 12 GB) alabilirsin. Fazlasi ucretlendirilir.

## Bilinmesi gerekenler

- **Micro (AMD) her bolgede yok.** Chicago gibi yeni bolgelerde `VM.Standard.E2.1.Micro`
  donanimi hic bulunmuyor; konsol "acabilirsin" dese de acilmaz. Kontrol:
  `oci compute shape list -c <tenancy> --availability-domain <AD> --all`
- **Ana bolge degistirilemez.** Ucretsiz hesap tek bolgeye abone olabilir
  (`subscribed-region-count = 1`).
- **Bos sunucu geri alinir.** 7 gun boyunca CPU, ag ve bellek kullanimi
  ayni anda %20'nin altinda kalirsa Oracle sunucuyu geri alabilir. Ucunden
  birini esigin ustunde tutmak yeterli.
- **Kapasiteyi denemeden sorgulama:**
  ```bash
  oci compute compute-capacity-report create --compartment-id <tenancy> \
    --availability-domain <AD> \
    --shape-availabilities '[{"instanceShape":"VM.Standard.A1.Flex","instanceShapeConfig":{"ocpus":1,"memoryInGBs":6}}]'
  ```
