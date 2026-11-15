# Local Assessment AI

Kişilik envanterlerini **tamamen yerel** olarak değerlendiren masaüstü uygulaması ve onu kuran,
denetleyen, onaran kurulum motoru. Hiçbir sınav ya da katılımcı verisi bilgisayardan çıkmaz.

> Durum: M1–M12 tamamlandı — bkz. `docs/milestones/`. Linux x86_64 AppImage (0.12.1)
> ağsız, temiz bir Arch container'ında deneme moduyla doğrulandı. Model pakette yoktur;
> hedef makinede ilk kurulumda açık izinle indirilir (bkz. [Yapay zekâ modeli](#yapay-zekâ-modeli)).
> Bekleyenler: Windows paketinin Windows'ta derlenip doğrulanması, hedef `qwen3.5:4b`
> modelinin kalite ölçümü (indirildiği makinede).

## Hızlı başlangıç (Linux)

```bash
./install.sh --dry-run              # ne yapılacağını göster, hiçbir şey yazma
./install.sh --online               # gerekli paketleri internetten indirerek kur (açık izin)
./install.sh --offline /yol/paket   # çevrimdışı paketten kur, ağ kullanılmaz
./doctor.sh                         # salt okunur sağlık denetimi
./repair.sh --offline /yol/paket    # bozuk adımları onar
./uninstall.sh                      # kaldır (verileriniz korunur)
```

Windows: `.\install.ps1`, `.\doctor.ps1`, `.\repair.ps1`, `.\uninstall.ps1` (yönetici yetkisi gerekmez).

Kurulum kullanıcı düzeyindedir (`~/.local/share/local-assessment-ai`). Sistemde kurulu bir
Ollama varsa ona **dokunulmaz**; uygulama kendi Ollama sürecini `127.0.0.1:11435` üzerinde çalıştırır.

## Belgeler

- Tasarım: `docs/superpowers/specs/2026-11-13-local-assessment-ai-design.md`
- Plan ve kilometre taşları: `docs/superpowers/plans/2026-11-13-local-assessment-ai.md`
- Kilometre taşı raporları: `docs/milestones/`

## Geliştirme

```bash
uv venv -p 3.14 .venv && uv pip install --python .venv -r requirements/app.txt pytest
.venv/bin/pytest
```

## Yapay zekâ modeli

Model pakete gömülmez. Hedef makinede ilk kurulumda indirilir; indirme yalnız kurulum
aracında, `--online` açık izniyle yapılır. Uygulamanın kendisi yalıtım içinde çalışır ve
internete çıkamaz.

```bash
./lai-0.12.1-linux-x86_64.AppImage --setup install --online --yes   # ilk kurulum: Ollama süreci + model
python -m lai_setup model pull --online                 # qwen3.5:4b indir (açık izin)
python -m lai_setup model import --from model.gguf      # çevrimdışı .gguf
python -m lai_setup model import --from /baska/ollama --name model:tag   # başka depodan, salt okunur
python -m lai_setup model verify                         # modeli çalıştırarak doğrula
python -m lai_setup runtime status                       # uygulamanın Ollama süreci
```
(Komutlar depo kökünde `PYTHONPATH=src` ile çalıştırılır.)

Gerçek entegrasyon testleri: `.venv/bin/pytest -m integration tests/integration -v -s`

## Veritabanı

```bash
python -m lai_setup db status                 # şema sürümü, bütünlük, kayıt sayıları
python -m lai_setup db backup                 # data/backups altına doğrulanmış yedek
python -m lai_setup db verify <yedek.db>      # yedeği denetle
python -m lai_setup db restore <yedek.db>     # geri yükle (önce mevcut DB yedeklenir)
```
Veritabanı `~/.local/share/local-assessment-ai/data/lai.db`; kaldırmada korunur.

## Belge içe aktarma

PDF, DOCX, XLSX, CSV ve JSON envanter belgeleri deterministik olarak (AI'sız) ayrıştırılır.
Hata varsa envanter sürümü oluşturulmaz; aynı belge ikinci kez aktarılmaz.

```bash
.venv/bin/python -m lai.ingest parse envanter.pdf          # yalnız ayrıştır ve raporla
.venv/bin/python -m lai.ingest import --db data/lai.db --inventory "Envanterim" envanter.pdf
.venv/bin/python -m lai.ingest import ... --encoding cp1254 eski.csv   # UTF-8 olmayan CSV
```
JSON biçimi (`lai-inventory` sürüm 1) `src/lai/ingest/jsonfmt.py` içinde belgelenmiştir.

## Puanlama kuralı keşfi

Yerel AI belgelerde **açıkça yazan** puanlama kurallarını aday olarak önerir; her aday sabit
kural kataloğuna, birebir alıntıya, envantere ve (varsa) örnek sonuçlara karşı deterministik
olarak doğrulanır. Hiçbir aday kendiliğinden doğrulanmış (CONFIRMED) sayılmaz.

```bash
.venv/bin/python -m lai.learning reference --db data/lai.db --inventory "Envanterim" yönerge.pdf
.venv/bin/python -m lai.learning examples  --db data/lai.db --inventory "Envanterim" örnekler.json
.venv/bin/python -m lai.learning discover  --db data/lai.db --inventory "Envanterim"   # 127.0.0.1:11435
.venv/bin/python -m lai.learning rules     --db data/lai.db --inventory "Envanterim"
```
Örnek sonuç biçimi (`lai-examples` sürüm 1) `src/lai/learning/examples.py`, kural kataloğu
`src/lai/core/rules.py` içinde belgelenmiştir.


## Eksik bilgi ve kalıcı öğrenme (M6)

Depo kökünde `PYTHONPATH=src` ile (kurulu pakette gerekmez):

```bash
python -m lai.learning missing --db /veri/lai.db --inventory "Sentetik Envanter"
python -m lai.learning missing --db /veri/lai.db --inventory "Sentetik Envanter" --all --json
python -m lai.learning answer --db /veri/lai.db --inventory "Sentetik Envanter" --question 1 --action later
python -m lai.learning answer --db /veri/lai.db --inventory "Sentetik Envanter" --question 1 --action resume --text "Yeni bilgi geldi"
python -m lai.learning answer --db /veri/lai.db --inventory "Sentetik Envanter" --question 1 --action teach --text "X boyutunun maddeleri" --structured '{"rule_type":"dimension_items","body":{"dimension":"X","items":[1,2]}}'
```

`missing` örnek uyumunu yeniden sınar; kritik eksikleri, etki alanını, kanıtları ve
Türkçe soruları gruplar. `--all` ertelenmiş, reddedilmiş, bilinmeyen yanıtlı ve çözülmüş
soruları da gösterir. Her kayıt kalıcı bir soru kimliğine sahiptir.

Yanıt eylemleri: `yes` (gösterilen öneriyi açık onay), `no`, `text` (açıklama),
`unknown`, `later`, `reject` (kullanma), `correct` (açıklamalı düzeltme), `resume`.
`teach` açık yapılandırılmış kullanıcı bilgisini şema ve envanter denetiminden geçirir.
Puanlama katalogu dışındaki açıklamalar ayrı bilgi kaydıdır; çalıştırılabilir formüle dönüşmez.

Serbest metni yerel modelle yorumlamak isteğe bağlıdır:

```bash
python -m lai.learning answer --db /veri/lai.db --inventory "Sentetik Envanter" --question 1 --action text --text "Birinci madde X boyutunda ters puanlanır" --interpret-with-model --port 11435 --model YEREL_MODEL
```

Bu komut model indirmez veya servis başlatmaz; mevcut uygulama modeline bağlanır.
11434 yasaktır. Model yorumu `WEAKLY_INFERRED` kalır; kullanıcı öneriyi inceledikten sonra
`--action yes` ile doğrulayabilir. Model yoksa kullanıcı metni kaybolmadan `UNKNOWN` tutulur.
`--action interpret --structured …`, bağdaştırıcıların doğrulanmamış öneri kaydetmesi içindir.

Onay veya düzeltmeden sonra ilgili kurallar yeniden sınanır, sorular güncellenir ve değişiklik
varsa yeni profil sürümü oluşur. Eski değerlendirmeler ve profil sürümleri değişmez.
Ayrıntılar ve test sınırları: [M6 raporu](docs/milestones/M06.md).


## Deterministik puanlama (M7)

`lai.core.scoring.score_assessment(exam, profile, responses)` saf Python girişidir;
Ollama, Qt, ağ veya veritabanı gerektirmez. Depodaki belirli sürümlerle çalışmak için:

```python
from lai.scoring import evaluate

result = evaluate(
    store,
    inventory_version_id=sinav_surumu,
    profile_version_id=puanlama_profili_surumu,
    responses=[(1, "A"), (2, "B")],  # yalnız sentetik örnek; yinelenenler doğrulanır
)
json_safe = result.to_dict()  # ondalık puanlar kayıpsız metindir
```

Bağdaştırıcı salt okunurdur; değerlendirme kaydı ve toplu iş oluşturmaz. Cevap listesi
veya tamsayı madde numaralı sözlük kabul edilir. Hatalı seçenek, yinelenen cevap ve
sürüm uyuşmazlığı Türkçe `ScoringInputError` üretir. Kritik eksiklik/çelişki boyut
sonucunda `NOT_EVALUABLE` olarak açıklanır; diğer boyutların hesapları korunur.
Dört boyutun tamamı hesaplanmadan dört harfli profil üretilmez.

Motor 50 basamaklı yerel `Decimal` bağlamını kullanır. Sıra: seçenek katkısı →
toplam/ortalama → boyut ağırlığı → normalizasyon → sıfır/eşik/eşitlik kararı →
yalnız gösterim için yuvarlama. Her madde, dönüşüm, kaynak kural ve profil sürümü
hesap dökümüne girer. `VERIFIED` doğrulanmış profil kurallarını, `PROVISIONAL`
ise doğrulanmamış/eksik bilgilerin bulunduğunu belirtir; gerçek envanter formülü
bu projede henüz bilinmemektedir.

Katalog v2, mevcut türlere `aggregation`, `dimension_weight`, `zero_score`, `precision`
ekler. Normalizasyon yalnız açık `none`, `percentage` (pozitif payda) veya `scaled`
(çarpan ve sabit) parametreleriyle yürütülür. `other` veya serbest metin formülleri
çalıştırılmaz. M6 açıklamalarından sayısal kural tahmin edilmez.

Ayrıntılı semantik, kabul testleri ve ölçüm: [M7 raporu](docs/milestones/M07.md).


## Tekil ve toplu değerlendirme (M8)

Mevcut CLI kullanılır (`PYTHONPATH=src`; kurulu pakette gerekmez). Sınav ve profil
sürümleri açıkça seçilir; katılımcı kodu veya takma ad yeterlidir. Ad, e-posta ve
telefon isteğe bağlıdır. Aşağıdaki kimlik ve cevaplar sentetiktir:

```bash
python -m lai.learning assess --db /veri/lai.db --inventory-version 1 --profile-version 1 --alias "Sentetik Deniz" --responses '[[1,"A"],[2,null]]' --json
python -m lai.learning assess --db /veri/lai.db --inventory-version 1 --profile-version 1 --participant-code SYN-001 --answers cevaplar.json
python -m lai.learning result --db /veri/lai.db --assessment 1 --json
python -m lai.learning rescore --db /veri/lai.db --assessment 1 --profile-version 2
```

`--answers` JSON çiftler listesi içerir; sözlüğe çevrilmeden yinelenen cevaplar
kontrol edilir. `result` kaydedilmiş puanları, kısmi sonuçları ve tam M7 hesap dökümünü
gösterir. Yeni hesap önceki sonucu değiştirmez; `recalculated_from` bağlantısı saklanır.

CSV (UTF-8/BOM; virgül, noktalı virgül veya sekme) ve XLSX ilk satırda başlık ister:

```csv
participant_id,alias,Q1,Q2
SYN-001,Sentetik Deniz,A,
SYN-002,Sentetik Ece,B,A
```

Soru başlıkları `Q1`, `Soru 1`, `S1` veya `1` olabilir; envanterdeki bütün sorular
tekil eşlenmelidir. Yalnız `alias` sütunu da kullanılabilir. Boş hücre M7'nin eksik
cevap politikasına gider. Seçeneklerin harf büyüklüğü değiştirilmez; dış boşluklar
kaldırılır. Kimlikler NFC ve dış boşluk temizliğiyle, büyük/küçük harfe duyarlı eşleşir.
Numarasal kimliklerde baştaki sıfırları korumak için hücreleri metin olarak saklayın.

```bash
python -m lai.learning batch --db /veri/lai.db --inventory-version 1 --profile-version 1 yanitlar.csv --prepare-only --json
python -m lai.learning batch --db /veri/lai.db --inventory-version 1 --profile-version 1 yanitlar.xlsx --sheet "Yanıtlar" --json
python -m lai.learning job list --db /veri/lai.db
python -m lai.learning job status --db /veri/lai.db --id 1 --json
python -m lai.learning job run --db /veri/lai.db --id 1 --limit 25
python -m lai.learning job pause --db /veri/lai.db --id 1
python -m lai.learning job cancel --db /veri/lai.db --id 1
python -m lai.learning job run --db /veri/lai.db --id 1
python -m lai.learning job clone --db /veri/lai.db --id 1 --profile-version 2
```

`--prepare-only` sütunları ve satırları doğrular, işi hazırlar; katılımcı/cevap/sonuç
kaydını henüz oluşturmaz. Belirsiz başlık bütün dosyayı reddeder. Hatalı satırlar
kaynak konumuyla başarısız tutulur; geçerli katılımcılar işlenir. `--mapping esleme.json`
ile özel başlıklar açıkça eşlenebilir:

```json
{"participant_code":"Kimlik","alias":"Takma Ad","questions":{"1":"Yanıt 1","2":"Yanıt 2"}}
```

Açık eşlemede dışarıda bırakılan sütunlar `column_mapping.ignored` içinde görünür.
XLSX formülleri cevap/kimlik olarak çalıştırılmaz. Dosya sınırları: 50 MiB, 10000 veri
satırı/katılımcı ve 4096 sütun; XLSX açılmış içerik bütçesi 200 MiB.

İş başında dosya özeti, doğrulanmış cevaplar, envanter/profil/motor sürümleri kalıcıdır.
`job run` ertelenmiş, iptal edilmiş veya süreç kesintisinde `running` kalmış işi aynı
girdilerle devam ettirir; kaynak dosyanın yerinde olması gerekmez. Her katılımcı ve
checkpoint aynı SQLite işlemindedir. Tamamlanmış satırlar yeniden işlenmez.
`--limit` verilen sayıda katılımcıdan sonra güvenle duraklatır. Motor sürümü değişmişse
aynı işe sessizce devam edilmez. Yeni profil için `clone` ayrı, hazır bir iş oluşturur;
sonra yeni kimlikle `job run` çağrılır. Önceki değerlendirme bağlantıları korunur.

Başarılı, kısmi, başarısız ve bekleyen sayıları hem CLI hem JSON'da gösterilir. Geçici
ve tamamen engellenmiş fakat gerekçesi kaydedilmiş M7 sonuçları `partial` sayılır;
bu boyutlara varsayılan puan verilmez. Başarısız satır aynı işte tekrar denenmez;
düzeltilmiş dosya veya yeni iş gerekir. Depo/sistem hatasında aktif katılımcı geri alınır
ve iş duraklatılır.

Çıkışlar: 0 işlem/kayıt başarılı (kısmi sonuç olabilir), 1 işte başarısız satır var,
2 girdi/kullanım hatası, 130 kesinti. Toplu işin satır özeti sonuç kimliği, profil,
güven durumu ve sınırlamaları gösterir; tam döküm `result` komutuyla okunur.
Uçtan uca süre ölçümleri ve testler: [M8 raporu](docs/milestones/M08.md).


## Türkçe masaüstü (M9)

PySide6 ve mevcut proje bağımlılıkları hazır olan ortamda, depo kökünden:

```bash
PYTHONPATH=src .venv/bin/python -m lai.ui
PYTHONPATH=src .venv/bin/python -m lai.ui --db /veri/lai.db
```

Kurulu pakette `python -m lai.ui` yeterlidir. `--db` verilmezse mevcut platform
uygulama dizinindeki veritabanı kullanılır; `LAI_HOME` desteklenir. Windows kaynak
çalıştırmada `PYTHONPATH=src` ayarlayıp `.venv\Scripts\python.exe -m lai.ui` kullanın.
Windows masaüstü oturumu bu teslimatta doğrulanmadı.

Ekranlar: **Belgeler, Öğrenme Merkezi, Tekil Değerlendirme, Toplu İşler, Geçmiş,
Katılımcılar, Ayarlar**. Belge kaydından önce soru/anahtar sayıları ve konumlu hatalar
gösterilir. Öğrenme soruları, ertelenen/bilinmeyen cevaplar ve kural kaynakları kalıcıdır.
Açık kural formu model gerektirmez; model yorumu ayrı onay ister. Sonuç ve geçmişte
boyut puanları, hesaplanamama nedenleri ve açılabilir hesap dökümü bulunur.

Toplu işte önce dosya/sütun önizlemesi, sonra **İşi kaydet**, ardından **Başlat / devam et**
kullanılır. Sütunlar ve satır hataları incelenebilir. İşin sabit sürümleri ve dört
ilerleme sayacı görünür. **Duraklat / iptal** güvenli katılımcı sınırında uygulanır.
Pencere kapanırken çalışan iş duraklatılır; yeniden açıp aynı işi seçerek devam edilir.
Kaynak dosya sonradan silinse de işin saklanan cevapları kullanılır.

Uzun işlemler arka plandadır. Kapanış zorla işçi öldürmez; geçerli işlem sınırını
bekler. Model isteğinde iptal, mevcut isteğin dönüşü/zaman aşımında uygulanabilir.
Ayarlar yalnız uygulamanın loopback portunu kontrol eder; **11434 reddedilir**.
Sistem servisi yönetilmez/model indirilmez. M11 ile uygulamanın kendi süreci yalnız ağ yalıtımında başlatılır. Model yokken belge ve hazır profille puanlama çalışır.
Yerel görünüm tercihleri seçilen veritabanının yanındaki `.ui.json` dosyasındadır;
Ollama/OpenCode ayarları değiştirilmez.

Klavye: `Ctrl+1…7` ekranlar, `Ctrl+R` yenile, `Esc` aktif işlem için iptal/duraklatma
isteği, `Tab` alanlar arasında gezinme, hesap ağacında sağ/sol ok aç/kapat.

Ayrı cevap anahtarı JSON biçimi (CSV/XLSX/PDF/DOCX de desteklenir):

```json
{"format":"lai-answer-key","version":1,"answer_key":[{"item":1,"value":"A"}]}
```

Tabloda `Soru No` ve `Cevap Anahtarı` başlıkları, PDF/DOCX'te `Cevap Anahtarı` bölümü
kullanılır. Değerlerin anlamı tahmin edilmez. Aynı servis CLI'da da kullanılabilir:

```bash
PYTHONPATH=src .venv/bin/python -m lai.ingest key --db /veri/lai.db --inventory "Sentetik Envanter" anahtar.csv
PYTHONPATH=src .venv/bin/python -m lai.ui.launcher --output /veri/kisayollar --db /veri/lai.db
```

Kısayol üreteci yalnız belirtilen dizine Linux `.desktop` veya Windows `.cmd` ve
başlatıcı dosyası yazar; sistem/masaüstüne kendiliğinden kurulum yapmaz.

Doğrulanan ortam: Linux x86_64, Qt **offscreen** testleri ve gerçek **Wayland** oturumu.
Ölçüm, testler, ekran görüntüleri ve sınırlamalar: [M9 raporu](docs/milestones/M09.md).

### Yerel raporlar (M10)

Tekil sonuçta PDF/XLSX/CSV/JSON, Geçmiş'te seçilen kaydı dışa aktarma ve Toplu
İşler'de CSV/XLSX veya tekil PDFleri içeren ZIP kullanılabilir. **Dosyayı aç** PDF
ve Excel'i uygulama içinde salt okunur gösterir. Model gerekmez; raporlar eski
kaydın kendi sürüm ve hesap dökümünden üretilir. Eksik puana sıfır atanmaz.

```bash
PYTHONPATH=src .venv/bin/python -m lai.learning export --db /veri/lai.db --id 12 --format pdf --output /veri/sonuc.pdf
PYTHONPATH=src .venv/bin/python -m lai.learning export-job --db /veri/lai.db --id 3 --format xlsx --output /veri/toplu.xlsx
PYTHONPATH=src .venv/bin/python -m lai.learning export-job --db /veri/lai.db --id 3 --format pdf --output /veri/pdfler.zip
```

Biçimler, diğer CLI örnekleri, hassasiyet/atomik yazma, testler ve sınırlamalar:
[M10 raporu](docs/milestones/M10.md).


### Ağ yalıtımı (M11)

Linux masaüstü artık Qt ve değerlendirme deposunu açmadan zorunlu OS yalıtımı kurar.
Bubblewrap/libseccomp/namespace veya Wayland eksikse Türkçe hata verir; yalıtımsız
çalışmaz. Kendi model süreci aynı özel loopback içindedir; yeni Unix soketi dahil
host proxy/çözümleyici yolları kapalıdır. Sistem servisi ve depoları değiştirilmez.
Windows gerçek yalıtımı doğrulanmadığından değerlendirme başlangıcı kapalıdır.

```bash
PYTHONPATH=src .venv/bin/python -m lai.ui --db /veri/lai.db --write-dir /veri/raporlar
PYTHONPATH=src .venv/bin/python -m lai_setup doctor --network --pid PID --json
```

`--write-dir` ek rapor/veri klasörüne yazma yetkisi verir. Ağ tanısı anlık örnektir,
yalıtım kanıtı değildir. CLI'ı korunan sınırda çalıştırma örnekleri, 747 teknik test,
kontrollü DNS/proxy testleri, gerçek yerel çıkarım ve Windows sınırları:
[M11 raporu](docs/milestones/M11.md), [güvenlik kapsamı](docs/security.md).


### Linux paketi (M12)

`dist/lai-<sürüm>-linux-x86_64.AppImage` uygulamayı, Python'u ve bağımlılıkları içerir;
Ollama ve model içermez. Sistemde `glibc`, bubblewrap, libseccomp ve Wayland gerekir.

```bash
./lai-0.12.1-linux-x86_64.AppImage                                   # arayüz (M11 yalıtımıyla)
./lai-0.12.1-linux-x86_64.AppImage --setup install --yes             # kullanıcı köküne kur
./lai-0.12.1-linux-x86_64.AppImage --setup doctor --json             # salt okunur denetim
./lai-0.12.1-linux-x86_64.AppImage --setup repair --yes              # onar
./lai-0.12.1-linux-x86_64.AppImage --setup uninstall --yes           # kaldır (veriler korunur)
PYTHONPATH=src .venv/bin/python -m lai_setup bundle --output dist/yeni.AppDir \
  --appimagetool dist/build-tools/appimagetool-root/AppRun \
  --appimage-runtime dist/build-tools/runtime-x86_64                  # paketi yeniden üret
```

Windows için `packaging/windows/` altında PyInstaller ve Inno Setup tarifleri vardır;
bunlar bir Windows makinesinde `packaging\windows\build.ps1` ile derlenir (Linux'tan
çapraz derleme yapılmaz). Windows ağ yalıtımı uygulanmadığı için Windows'ta değerlendirme
kapalıdır.
Ayrıntılar: [M12 raporu](docs/milestones/M12.md).

### Kurulumsuz deneme modu

Sorunları kurulum yapmadan görmek için paket ayrı bir deneme kökünde
(`~/.cache/local-assessment-ai/deneme`) çalıştırılabilir. Başlatıcı, kısayol veya sistem
kaydı oluşturulmaz; model indirilmez. Mevcut bir Ollama deposundaki model
(`/var/lib/ollama` ya da `~/.ollama/models`) salt okunur kopyalanır, sistem Ollama
servisine istek gönderilmez. Eski `manifests/` düzeni ve manifestleri yalnız `blobs/`
içinde tutan Ollama 0.40+ düzeni tanınır.

```bash
./lai-0.12.1-linux-x86_64.AppImage --deneme                         # tanı + arayüz
./lai-0.12.1-linux-x86_64.AppImage --deneme --yalniz-tani           # yalnız tanı
./lai-0.12.1-linux-x86_64.AppImage --deneme --model qwen3.5-9b-sansursuz:latest
./lai-0.12.1-linux-x86_64.AppImage --deneme --kaynak /baska/ollama-deposu
./lai-0.12.1-linux-x86_64.AppImage --deneme --modelsiz              # modelsiz arayüz
./lai-0.12.1-linux-x86_64.AppImage --deneme --temizle               # deneme kökünü sil
```

Tanı yalıtım içinde ağ ad alanını, internet erişiminin kapalı olduğunu, veritabanını,
uygulamanın Ollama sürecini ve kısa bir model yanıtını sınar; her satırı TAMAM/HATA
olarak yazar. Tanı ve arayüz çıktısı `~/.cache/local-assessment-ai/deneme/logs/` altındaki
günlüğe, Ollama çıktısı `runtime/ollama.log` dosyasına gider. Ayarlar ekranındaki model
adı deneme kökünün `config/config.toml` dosyasından gelir. `--temizle` yalnız deneme
işaret dosyası (`.lai-deneme`) bulunan dizini siler.

Not: yalıtım, uygulamanın bilgisayarda yeni bağlantı noktası (Unix soketi) açmasını
engeller. NVIDIA'nın CUDA kütüphanesi başlarken böyle bir soket açmak istediği için
yalıtılmış uygulamada model GPU yerine CPU'da çalışır (daha yavaş, ama güvenli).

#### Ağsız container'da deneme (kurulumsuz, sistemden ayrık)

Paketi temiz bir dağıtımda ve ağ bağlantısı olmadan sınamak için:

```bash
packaging/linux/container-deneme.sh dist/lai-0.12.1-linux-x86_64.AppImage --yalniz-tani
packaging/linux/container-deneme.sh dist/lai-0.12.1-linux-x86_64.AppImage   # tanı + arayüz (Wayland)
```

Betik ilk seferde `podman` ile minimal bir Arch imajı hazırlar (yalnız bu adım ağ kullanır),
ardından AppImage'ı `--network=none` ile, 8 GB bellek sınırıyla çalıştırır. Sistem Ollama
ikilisi ve model deposu salt okunur bağlanır; deneme kökü
`~/.cache/local-assessment-ai/container/` altındadır. Paket glibc 2.44 ile üretildiği için
daha eski glibc'li dağıtımlarda (ör. Ubuntu 24.04) çalışmaz.
