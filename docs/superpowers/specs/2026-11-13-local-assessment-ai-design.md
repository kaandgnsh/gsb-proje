# Local Assessment AI — Tasarım

Tarih: 2026-11-13 · Durum: onaylandı (teknoloji kararı kullanıcı tarafından kesinleştirildi)

## 1. Amaç ve neden

Kullanıcı, yaklaşık 400 maddelik **özel** bir kişilik envanterini (sonuç yapısı bugün bilinen
haliyle E-I, S-N, T-F, J-P) puanlamak istiyor. Sorular, seçenekler, cevap anahtarı ve bazı
değerlendirme belgeleri var; **puanlama algoritması bilinmiyor**. Sistem belgeleri analiz edip
algoritmayı keşfetmeli, bilmediğini açıkça tespit etmeli, kullanıcıya anlaşılır sorular
sormalı, cevapları kalıcı ve sürümlü olarak saklamalı, puanlamayı ise **deterministik** yapmalı.
Hiçbir sınav/katılımcı verisi bilgisayardan çıkmamalı.

İki ürün:

- **A. Kurulum motoru** (`lai_setup`): ortamı tespit eder, izole Ollama profilini kurar,
  modeli açık izinle edinir/içe aktarır, uygulamayı kurar, doğrular, rapor üretir.
  `install / doctor / repair / uninstall` — Linux (`.sh`) birincil, Windows (`.ps1`) ikincil.
- **B. Uygulama** (`lai`): içe aktarma, analiz, öğrenme merkezi, puanlama, raporlama.

## 2. Hedefler / hedef olmayanlar / anti-hedefler

Hedefler: 9. bölümdeki kabul kriterlerinin tamamı (orijinal şartname §9).

Hedef olmayanlar (ilk sürüm): model ince ayarı (fine-tuning) — isteğe bağlı gelecek modül;
çok kullanıcılı/sunucu modu; otomatik güncelleme.

Anti-hedefler (çalışsa bile başarısızlık sayılır):
- Bilinmeyen bir puanlama kuralının uydurulması veya doğrulanmış gibi gösterilmesi.
- Kullanıcının mevcut Ollama servisine, systemd ayarlarına, ortam değişkenlerine,
  modellerine veya OpenCode bağlantısına izinsiz dokunulması.
- Puanın LLM çıktısına bağlı olması (aynı girdi → farklı puan).
- Herhangi bir verinin ağ dışına çıkması; telemetri; güncelleme kontrolü.
- Kurulum/kaldırmanın kullanıcı verisini izinsiz silmesi.
- LLM üretimi kodun çalıştırılması.

## 3. Kısıtlar

- Python ≥ 3.11, PySide6, SQLite (stdlib `sqlite3`), PyMuPDF, python-docx, openpyxl,
  ReportLab, pytest. Windows: PyInstaller + Inno Setup. Linux: AppImage öncelikli.
- Donanım şartı yok; CPU yedeği zorunlu; NVIDIA varsayılmaz.
- Varsayılan model `qwen3.5:4b`. Model indirme yalnızca açık kurulum adımında.
- Geliştirme sırasında sisteme kurulum yok: testler sahte Ollama + geçici dizinlerle.

## 4. Yaklaşımlar

| | Yaklaşım | Artı | Eksi | Karar |
|---|---|---|---|---|
| A | Python + PySide6 | Belge/PDF/Excel kütüphaneleri olgun, tek dil, pytest, Plasma'da doğal | Paket ~150 MB | **Seçildi** |
| B | Tauri (Rust + web) | Küçük ikili | Belge/rapor kütüphaneleri zayıf, iki zincir | Elendi |
| C | Python sunucu + tarayıcı | Hızlı geliştirme | Masaüstü değil, ek yerel HTTP portu → yalıtım doğrulaması zorlaşır | Elendi |

## 5. Mimari

```
local-assessment-ai/
  install.sh doctor.sh repair.sh uninstall.sh      # ince kabuklar (Linux)
  install.ps1 doctor.ps1 repair.ps1 uninstall.ps1  # ince kabuklar (Windows)
  src/lai_setup/      # A: kurulum motoru (UI'dan, lai'den bağımsız)
  src/lai/
    core/             # saf Python: modeller, scoring engine, completeness — Qt/AI yok
    store/            # SQLite şema, migration, repository
    ingest/           # PDF/DOCX/XLSX/CSV/JSON içe aktarma, soru çıkarımı
    ai/               # Ollama istemcisi (yalnız loopback), prompt'lar, JSON şema doğrulama
    learning/         # bilgi deposu, öneri→doğrulama akışı, soru üretimi
    reports/          # PDF/XLSX/JSON
    ui/               # PySide6 — yalnız bu katman Qt import eder
  tests/              # pytest; sahte Ollama sunucusu; sentetik envanter üreticisi
  packaging/          # AppImage, PyInstaller spec, Inno Setup .iss
```

Katman kuralı (testle zorlanır): `lai.core`, `lai.store`, `lai.learning`, `lai.ingest`,
`lai.reports` **PySide6 import etmez**; `lai.core` ayrıca `lai.ai` import etmez.

### 5.1 Kabuk betikleri → Python motoru

`install.sh` yalnızca: Python ≥3.11 var mı bakar (yoksa dağıtıma uygun komutu **önerir**,
kendisi root ile kurmaz — kullanıcı onayıyla `pkexec`/`sudo` çağrılabilir), uygulama venv'ini
kurar, ardından `python -m lai_setup <komut>` çalıştırır. Tüm mantık Python'da → Linux ve
Windows aynı kodu paylaşır ve pytest ile test edilir.

### 5.2 İzole Ollama profili

- Mevcut kurulum **salt okunur** tespit edilir: ikili yolu, sürüm, `ollama.service`,
  11434'te dinleyen süreç, ortam değişkenleri. Rapora yazılır, değiştirilmez.
- Uygulama kendi sürecini çalıştırır: mevcut `ollama` ikilisi (yoksa uygulamanın kendi
  dizinine indirilen/çevrimdışı paketten çıkarılan ikili), ortam:
  `OLLAMA_HOST=127.0.0.1:11435`, `OLLAMA_MODELS=<lai-data>/models`, `OLLAMA_NO_CLOUD=1`,
  `OLLAMA_ORIGINS` boş, `HOME` uygulama dizini.
- Linux: `~/.config/systemd/user/lai-ollama.service` (kullanıcı servisi, root gerekmez).
  Windows: uygulama başlatırken alt süreç olarak yönetilir (servis kurulmaz).
- Port 11435 doluysa ve sahibi bizim süreç değilse kurulum durur ve sorar; asla 0.0.0.0.
- Mevcut modeli yeniden kullanma: isteğe bağlı `--reuse-model-from <dizin>`; blob'lar
  okunabiliyorsa **kopyalanır** (sha256 doğrulamalı), kaynağa yazılmaz. Varsayılan kapalı.
- Runtime ve model ayrı güncellenir: `lai_setup model install|import|verify`,
  `lai_setup runtime install|verify`.

### 5.3 Dizinler (XDG / Windows)

| | Linux | Windows |
|---|---|---|
| Uygulama | `~/.local/share/local-assessment-ai/app` | `%LOCALAPPDATA%\LocalAssessmentAI\app` |
| Veri (DB, belgeler) | `~/.local/share/local-assessment-ai/data` | `%LOCALAPPDATA%\LocalAssessmentAI\data` |
| Modeller | `…/runtime/models` | `…\runtime\models` |
| Ayarlar | `~/.config/local-assessment-ai/config.toml` | `%APPDATA%\LocalAssessmentAI\config.toml` |
| Raporlar | `~/.local/share/local-assessment-ai/reports` | `…\reports` |

Tüm yollar `LAI_HOME` ile geçersiz kılınabilir (testler bunu kullanır).
`data/` ve `reports/` **kullanıcı verisidir**: `uninstall` yalnızca açık
`--purge-data` + etkileşimli onayla siler; varsayılan olarak korur.

### 5.4 Durum ve idempotens

Kurulum adımları tek tek `Step` nesneleridir: `check()` (zaten tamam mı?), `apply()`,
`verify()`. Kurulum `install-state.json`'a adım sonuçlarını, sürüm sabitlerini (Ollama
sürümü, model digest'i, uygulama sürümü, şema sürümü) yazar. İkinci çalıştırma `check()`
geçen adımları atlar. `repair` = tüm adımları `verify()` et, bozuk olanı yeniden `apply()`.
`doctor` = salt okunur `check()+verify()` raporu. Her çalıştırma JSON + okunur metin
rapor üretir (`reports/install-YYYYmmdd-HHMMSS.{json,txt}`).

Kurulum modları: `--online` (kullanıcı açıkça seçer; indirme öncesi ne indirileceği ve
boyutu gösterilir), `--offline <paket-dizini>` (wheel'ler, ollama ikilisi, model blob'ları
yerel paketten), `--dry-run` (hiçbir şey yazmadan plan). Root gerektiren tek durum sistem
paketleridir (Python yoksa); betik neden gerektiğini açıklar ve onay ister.

## 6. Veri modeli (SQLite)

Ana varlıklar (M3'te ayrıntılanır):

- `inventory`, `inventory_version` — envanter ve içe aktarılan sürümü
- `source_document` — içe aktarılan belge (sha256, yol, tür); `evidence_ref` (belge+sayfa+
  alıntı aralığı)
- `item` (madde no, metin), `option` (seçenek kodu, metin)
- `dimension` (E-I…; sabit değil, keşfedilir), `pole`
- `rule` — puanlama kuralı: tür (`item_pole_map`, `option_weight`, `reverse`,
  `normalization`, `threshold`, `tie_break`, `missing_answer_policy`, …), JSON gövde,
  **status** (`CONFIRMED | STRONGLY_INFERRED | WEAKLY_INFERRED | UNKNOWN | CONTRADICTORY`),
  kaynak (`document | user | ai_inference | example_fit`), güven, kanıt bağlantıları
- `requirement` — bilgi bütünlüğü matrisi satırı (hangi bilgi gerekli, durumu, bağlı
  kurallar, etkilediği boyutlar, kritik mi)
- `clarification_question` — soru, neden önemli, ilgili madde/boyut, mevcut kanıt, etki,
  seçenekler, durum (`open | answered | dont_know | later | skipped`), grup
- `knowledge_entry` / `learning_record` — içerik, envanter, ilgili madde/boyut, kanıt,
  güven sınıfı, zaman, doğrulama durumu, sürüm; **append-only** (üzerine yazma yok,
  `supersedes_id` ile zincir)
- `user_correction` — kullanıcı düzeltmesi (açık kayıt)
- `scoring_profile` / `scoring_profile_version` — kuralların donmuş anlık görüntüsü
  (içerik hash'i); bir sürüm yayımlandıktan sonra değişmez
- `participant` — anonim kimlik zorunlu; takma ad, isim, iletişim, kategori, serbest
  etiketler **isteğe bağlı**
- `response_set` / `response` — katılımcının cevapları
- `assessment` — profil sürümü + cevap seti → sonuç; `PROVISIONAL` / `VERIFIED`;
  yeniden hesaplama yeni satır üretir, eskisi değişmez
- `assessment_feedback`, `audit_log`

## 7. Öğrenme ve puanlama akışı

1. **İçe aktarma** (deterministik): belgelerden madde/seçenek/anahtar tabloları çıkarılır;
   yapısal ayrıştırıcı önce, LLM yalnız yapısı belirsiz metinde yardımcı olarak.
2. **Keşif** (LLM + deterministik kontroller): LLM belgelerden kural **adayları** önerir;
   her öneri katı JSON şemasıyla doğrulanır, kanıt alıntısı belgede birebir bulunmazsa
   reddedilir. Örnek sonuçlar (varsa) adaylara karşı deterministik olarak sınanır
   (`example_fit`): tutarlı → STRONGLY_INFERRED, tutarsız → CONTRADICTORY.
3. **Bütünlük matrisi**: her boyut için gereken bilgi listesi (madde→kutup eşlemesi,
   ağırlıklar, ters puanlama, normalizasyon, eşik, beraberlik kuralı, eksik cevap
   politikası, sıfır puan davranışı). Durumlar deterministik olarak hesaplanır.
4. **Sorular**: UNKNOWN/CONTRADICTORY/WEAKLY_INFERRED kritik gereksinimlerden üretilir,
   ilişkili olanlar gruplanır; cevaplanmış soru tekrar sorulmaz (gereksinim anahtarıyla
   tekilleştirme). Cevap türleri: seçenek, serbest metin, bilmiyorum, sonra sor, atla.
   Serbest metin LLM ile yapılandırılmış kurala çevrilir → **kullanıcıya geri gösterilir,
   onaylanınca** CONFIRMED olur. Öğrenme Merkezi sohbeti aynı yolu kullanır
   ("12. soru ters puanlanıyor" → kural önerisi → onay → yeni profil sürümü).
5. **Puanlama motoru** (saf Python, LLM yok, kod çalıştırma yok): kurallar sabit bir
   kural türü kümesinden yorumlanır (bir DSL değil; her tür için yazılmış Python fonksiyonu).
   Kritik bir gereksinim CONFIRMED değilse sonuç `PROVISIONAL` ve sınırlamalar listelenir;
   gereksinim UNKNOWN ise o boyut için puan **üretilmez** ("hesaplanamadı: X bilinmiyor").
6. Yeni bilgi → yeni profil sürümü → kullanıcı isterse eski değerlendirmeler yeni sürümle
   **yeni kayıt** olarak yeniden hesaplanır; tarihçe korunur.

## 8. Güvenlik ve ağ yalıtımı

- AI istemcisi yalnız `127.0.0.1`/`::1` hedeflerine bağlanır; başka host'u reddeder (testli).
- Uygulama hiçbir dış URL içermez; CI testi kaynakta `http(s)://` dış adreslerini tarar
  (kurulum motorunun açık indirme URL'leri hariç, beyaz listeli).
- Çalışma zamanı doğrulaması (`doctor --network`): uygulama ve `lai-ollama` süreçlerinin
  soketleri incelenir (`/proc/net/tcp*` / `Get-NetTCPConnection`); loopback dışı bağlantı
  veya 0.0.0.0 dinleme = hata. Linux'ta isteğe bağlı `systemd` `IPAddressDeny=any`
  `IPAddressAllow=localhost` ile servis düzeyinde engel (indirme yalnız `ollama pull`
  sırasında geçici ayrı süreçte).
- LLM çıktısı yalnız veri olarak işlenir; `eval/exec` yok; model ayarları değiştiremez.
- Belgeler ve DB kullanıcı dizininde `0700`/`0600` izinle.

## 9. Kabul kriterleri

Orijinal şartname §9'un 18 maddesi. Her kilometre taşı: kod + otomatik test + doküman +
doğrulama çıktısı + bilinen sınırlamalar (`docs/milestones/MXX.md`).

## 10. Kilometre taşları (özet; ayrıntı plan dosyasında)

| # | Ad | Bağımlılık |
|---|---|---|
| M1 | Kurulum motoru + ortam tespiti | — |
| M2 | İzole Ollama + Qwen entegrasyonu | M1 |
| M3 | SQLite + kalıcı bilgi altyapısı | — |
| M4 | Belge içe aktarma + soru çıkarımı | M3 |
| M5 | Puanlama kuralı keşfi | M2, M4 |
| M6 | Eksik bilgi tespiti + etkileşimli öğrenme | M5 |
| M7 | Deterministik değerlendirme motoru | M3 (M6 ile paralel başlayabilir) |
| M8 | Tekil + toplu değerlendirme | M7 |
| M9 | Türkçe masaüstü arayüz | M6, M8 |
| M10 | PDF/Excel/JSON raporlama | M8 |
| M11 | Güvenlik + ağ yalıtımı testleri | M2, M9 |
| M12 | Linux paketleme + Windows kurulumu | tümü |

## 11. Kurucuya bırakılanlar (benim kararlarım, düzeltilebilir)

- Konfigürasyon biçimi TOML (stdlib `tomllib` okur; yazma için küçük yazıcı).
- Paket yöneticisi geliştirmede `uv`; kurulumda stdlib `venv` + `pip` (uv şart değil).
- Sentetik test envanteri: 40 maddelik + 400 maddelik üretici; gerçek dosya kullanılmaz.
- Kullanıcı tarafı belgeler Türkçe, kod/tanımlayıcılar İngilizce.
- Builder-check alt ajan incelemesi kullanıcının "uzatma" talebi üzerine atlandı.
