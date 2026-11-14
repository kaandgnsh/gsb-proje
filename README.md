# Local Assessment AI

Kişilik envanterlerini **tamamen yerel** olarak değerlendiren masaüstü uygulaması ve onu kuran,
denetleyen, onaran kurulum motoru. Hiçbir sınav ya da katılımcı verisi bilgisayardan çıkmaz.

> Geliştirme sürüyor. Durum: M1–M5 tamamlandı — bkz. `docs/milestones/`.

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
