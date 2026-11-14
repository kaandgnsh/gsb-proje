# Local Assessment AI

Kişilik envanterlerini **tamamen yerel** olarak değerlendiren masaüstü uygulaması ve onu kuran,
denetleyen, onaran kurulum motoru. Hiçbir sınav ya da katılımcı verisi bilgisayardan çıkmaz.

> Geliştirme sürüyor. Durum: M1–M7 tamamlandı — bkz. `docs/milestones/`.

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

```bash
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
