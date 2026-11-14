# Local Assessment AI — Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kişilik envanteri puanlama algoritmasını yerel AI ile keşfeden, eksik bilgiyi kullanıcıya soran, deterministik puanlayan, tamamen çevrimdışı çalışan masaüstü uygulaması + onu kuran/onaran idempotent kurulum motoru.

**Architecture:** `lai_setup` (yalnız stdlib, kabuk betikleri tarafından sistem Python'u ile çalıştırılır) ortamı tespit eder, izole Ollama profilini (127.0.0.1:11435) ve uygulama venv'ini kurar. `lai` paketi katmanlıdır: `core` (saf puanlama), `store` (SQLite), `ingest`, `ai` (yalnız loopback Ollama istemcisi), `learning`, `reports`, `ui` (tek Qt katmanı).

**Tech Stack:** Python ≥3.11, PySide6, SQLite, PyMuPDF, python-docx, openpyxl, ReportLab, pytest; PyInstaller + Inno Setup (Windows), AppImage (Linux).

**Spec:** `docs/superpowers/specs/2026-11-13-local-assessment-ai-design.md`

## Global Constraints

- `lai_setup` yalnız Python standart kütüphanesini kullanır (venv oluşmadan çalışır).
- Uygulama Ollama adresi: `127.0.0.1:11435`; mevcut `127.0.0.1:11434` servisine yalnız salt okunur erişim.
- Ollama ortamı: `OLLAMA_NO_CLOUD=1`, `OLLAMA_HOST=127.0.0.1:11435`, `OLLAMA_MODELS=<runtime>/models`.
- Varsayılan model: `qwen3.5:4b`. Normal uygulama çalışmasında model indirilmez.
- İndirme yalnız `--online` açıkça verildiğinde; `--offline <dizin>` hiçbir ağ erişimi yapmaz.
- `data/` ve `reports/` kullanıcı verisidir; yalnız `uninstall --purge-data` + onayla silinir.
- `lai.core|store|learning|ingest|reports` PySide6 import etmez; `lai.core` `lai.ai` import etmez.
- LLM çıktısı asla kod olarak çalıştırılmaz; puan asla LLM'e bağlı değildir.
- Kullanıcı arayüzü ve kullanıcı belgeleri Türkçe; kod tanımlayıcıları İngilizce.
- Testler gerçek sisteme dokunmaz: `LAI_HOME`, sahte `fs_root`, sahte komut çalıştırıcı, sahte Ollama.

## Review Focus

1. Türkçe karakterli/boşluklu yollar (`~/Projeler/BİTTİ/…`, `LAI_HOME="/tmp/a b/İş"`) → kurulum, durum dosyası ve rapor sorunsuz.
2. Yarıda kesilmiş kurulum (durum dosyası eksik/bozuk JSON) → `repair` toparlar, `doctor` açıkça raporlar; çökme yok.
3. TTY yok ve ne `--online` ne `--offline` verilmiş → hiçbir indirme yapılmadan anlaşılır hata, çıkış kodu 2.
4. Kullanıcının elle düzenlediği `config.toml` → ikinci `install` üzerine yazmaz; bozuksa `repair` önce `.bak` yedeği alır.
5. `uninstall` (purge'süz) → `data/` ve `reports/` dosyaları birebir yerinde kalır.

---

## Yol haritası (12 kilometre taşı)

Her kilometre taşının çıktısı: kod + pytest + `docs/milestones/MXX.md` (ne yapıldı, doğrulama çıktısı, bilinen sınırlamalar). Bir kilometre taşı ancak testleri çalıştırılıp çıktısı belgeye yazıldığında tamamdır. M2–M12'nin ayrıntılı görev planı, bir önceki kilometre taşı bittiğinde bu dosyaya eklenir (önceki taşın gerçek arayüzleri üzerine yazılır).

| # | Ad | Bağımlılık | Teslimatlar | Kabul kriterleri |
|---|---|---|---|---|
| M1 | Kurulum motoru + ortam tespiti | — | `lai_setup` (detect, paths, distro, steps, state, report, cli), `install/doctor/repair/uninstall .sh/.ps1` | İki kez `install` → ikincisi her adımı "zaten tamam" der, dosya değişmez; `doctor` OS/dağıtım/mimari/RAM/disk/CPU bayrakları/GPU/mevcut Ollama/model/kurulumu raporlar; mevcut Ollama'ya yazma yok (testle); uninstall veriyi korur |
| M2 | İzole Ollama + Qwen | M1 | `lai_setup` adımları: runtime tespit/kurulum, `lai-ollama.service` (user), model install/import/verify; `lai.ai.client` (loopback-only) | Sahte Ollama ile: servis birimi doğru ortamı içerir, 0.0.0.0 asla; model doğrulama `/api/show` + kısa üretim; offline import sha256 doğrular; sürüm/digest durum dosyasına sabitlenir |
| M3 | SQLite + bilgi altyapısı | — | `lai.store` şema v1 + migration, repository'ler, append-only bilgi kayıtları, profil sürümleri | Şema 6. bölüm varlıklarını içerir; bilgi kaydı güncellemesi yeni satır + `supersedes_id`; yayımlanmış profil sürümü değiştirilemez (testli); kurulum adımı DB'yi idempotent başlatır |
| M4 | Belge içe aktarma | M3 | `lai.ingest` PDF/DOCX/XLSX/CSV/JSON; madde/seçenek/anahtar çıkarımı; sentetik 40 ve 400 maddelik envanter üreticisi | Sentetik 400 madde her biçimden kayıpsız içe aktarılır; kanıt referansı (belge+sayfa+aralık) saklanır; aynı belge iki kez → yinelenmez (sha256) |
| M5 | Kural keşfi | M2, M4 | `lai.learning.discovery`: LLM aday kural önerisi, JSON şema doğrulama, alıntı doğrulama, örnek sonuçlara deterministik uyum | Sahte LLM ile: alıntısı belgede olmayan öneri reddedilir; örneklerle tutarlı aday STRONGLY_INFERRED, çelişen CONTRADICTORY |
| M6 | Eksik bilgi + etkileşimli öğrenme | M5 | Bütünlük matrisi, soru üretimi/gruplama/tekilleştirme, 5 cevap türü, doğal dil → kural önerisi → onay | 10 eksiklik türünün her biri tespit edilir; cevaplanan soru tekrar sorulmaz; "12. soru ters puanlanıyor" → onay sonrası yeni profil sürümü |
| M7 | Deterministik puanlama | M3 | `lai.core.scoring`: kural türü yorumlayıcıları, PROVISIONAL/VERIFIED, hesaplanamayan boyut | Aynı girdi 1000 çalıştırmada aynı çıktı; UNKNOWN kritik kural → boyut puanı yok + açıklama; core'da Qt/AI importu yok (testli) |
| M8 | Tekil + toplu değerlendirme | M7 | Katılımcı (anonim kimlik zorunlu, diğerleri isteğe bağlı), cevap seti içe aktarma, toplu iş, yeniden hesaplama | 400 madde × 200 katılımcı toplu iş < 30 sn (CPU); yeniden hesaplama yeni kayıt üretir, eskiyi değiştirmez |
| M9 | Türkçe masaüstü arayüz | M6, M8 | PySide6 pencereler: içe aktarma, AI Öğrenme Merkezi (sohbet dahil), değerlendirme, katılımcılar, ayarlar; masaüstü kısayolu adımı | `pytest-qt`/offscreen duman testleri; tüm metinler Türkçe; uygulama ağsız açılır |
| M10 | Raporlama | M8 | PDF (ReportLab, Türkçe font gömülü), XLSX, JSON | Raporlar PROVISIONAL uyarısını ve profil sürümünü taşır; Türkçe karakterler PDF'te doğru |
| M11 | Güvenlik + ağ yalıtımı | M2, M9 | Kaynak dış-URL taraması, loopback-only istemci testi, `doctor --network` soket denetimi, uygulama + Ollama süreçlerinin **tüm** dış iletişim yollarının incelenmesi (TCP/UDP, DNS dahil; glibc nss-resolve → systemd-resolved Unix soketi gibi ağ ad alanını aşan yollar), gerçek ağ izolasyonu (ör. ağ + bağlama ad alanı) | Loopback dışı bağlantı/dinleme tespit edilir; **DNS sızıntısı dahil gerçek izolasyon doğrulama testi zorunlu** (yalnız HTTP denetimi yeterli değil); güvenlik belgesi |
| M12 | Paketleme | tümü | AppImage, çevrimdışı paket oluşturucu (`lai_setup bundle`), PyInstaller spec + Inno Setup .iss | Temiz dizinde offline paketten kurulum ağ olmadan geçer; Windows betikleri ve .iss sözdizimi doğrulanır (Windows'ta gerçek test bilinen sınırlama) |

Son teslim: README, kurulum kılavuzu, mimari, güvenlik, sorun giderme, test raporu (`docs/`).

### Test politikası (kullanıcı kararı, 2026-11-14)

- **Teknik doğruluk testleri** (`technical`, işaretsiz varsayılan): JSON şema geçerliliği, boyut/kutup eşleştirmesi, veri kaybı, parçalama/birleştirme, deterministik hesaplama, süreç ve bağlantı güvenliği. Başarısızlık = hata; düzeltilmeden kilometre taşı bitmez.
- **Model kalite testleri** (`model_quality`): Türkçe anlama, talimat takibi, soru sınıflandırması, kural keşfi, belirsizlik tespiti, tutarlı açıklama. Birebir kelime eşleşmesi beklenmez; doğruluk, eksiksizlik ve tutarlılık ayrı ölçülür. Başarısızlık = model sınırlaması; test değiştirilmez/gizlenmez, raporda ayrı gösterilir.
- pytest çıktısındaki "Test kategorileri" bölümü iki kategoriyi ayrı sayar (`tests/support/lai_test_categories.py`).
- Hedef model `qwen3.5:4b`; 9B ile yapılan testler yalnız çalışma ortamı entegrasyonunu doğrular. 4B açık izinle temin edilince aynı testler tekrarlanır.

### AI / deterministik iş bölümü (kullanıcı kararı, 2026-11-14)

| İş | Yürüten |
|---|---|
| Soru analizi, kural keşfi | Yerel AI, düşünme modu açık |
| Sayısal hesaplama, puanlama | Tamamen deterministik Python |
| Rapor açıklamaları | Yerel AI |

---

## M1 — Kurulum motoru + ortam tespiti

### Dosya yapısı

```
pyproject.toml                    # proje, pytest ayarı, src düzeni
requirements/app.txt              # uygulama bağımlılıkları (sabit sürümler)
src/lai_setup/__init__.py         # __version__
src/lai_setup/__main__.py         # -> cli.main()
src/lai_setup/paths.py            # AppPaths, resolve_paths
src/lai_setup/detect.py           # SystemInfo, detect_system
src/lai_setup/distro.py           # PackageStrategy, strategy_for
src/lai_setup/runner.py           # CommandRunner (gerçek + test sahtesi için protokol)
src/lai_setup/steps.py            # Step, StepResult, Context, run_steps
src/lai_setup/state.py            # InstallState (atomik JSON)
src/lai_setup/report.py           # Report -> json/txt
src/lai_setup/m1_steps.py         # EnsureDirectories, WriteConfig, CreateVenv, InstallPythonDeps
src/lai_setup/cli.py              # install/doctor/repair/uninstall/detect
install.sh doctor.sh repair.sh uninstall.sh _common.sh
install.ps1 doctor.ps1 repair.ps1 uninstall.ps1 _common.ps1
tests/setup/…                     # her modül için test + fixtures/fsroot_*
docs/milestones/M01.md
```

### Task 1: Proje iskeleti + yollar

**Files:** Create `pyproject.toml`, `src/lai_setup/__init__.py`, `src/lai_setup/paths.py`; Test `tests/setup/test_paths.py`

**Interfaces — Produces:**
- `AppPaths` (frozen dataclass): `root, app, venv, data, runtime, models, config_dir, config_file, reports, logs, state_file: Path`
- `resolve_paths(platform: str, env: Mapping[str,str], home: Path) -> AppPaths` — `platform` ∈ `{"linux","windows"}`; `LAI_HOME` verilirse tüm yollar onun altında (`config_dir = LAI_HOME/config`).

- [ ] Test: Linux varsayılanı `~/.local/share/local-assessment-ai/{app,venv,data,runtime,runtime/models,reports,logs}`, config `~/.config/local-assessment-ai/config.toml`, state `…/local-assessment-ai/install-state.json`; `XDG_DATA_HOME`/`XDG_CONFIG_HOME` saygısı; Windows `LOCALAPPDATA\LocalAssessmentAI\…`, config `APPDATA\LocalAssessmentAI\config.toml`; `LAI_HOME="/tmp/a b/İş"` → hepsi altında.
- [ ] Çalıştır, başarısız; uygula; geçir. Commit.

### Task 2: Ortam tespiti (salt okunur)

**Files:** Create `src/lai_setup/runner.py`, `src/lai_setup/detect.py`; Test `tests/setup/test_detect.py`, `tests/setup/fixtures/fsroot_arch/`, `fsroot_ubuntu/`, `fsroot_fedora/`

**Interfaces — Produces:**
- `CommandRunner` Protocol: `run(argv: list[str], timeout: float = 10, env: Mapping|None = None) -> CompletedProcess`; `which(name) -> str|None`. `SystemRunner` gerçek uygulama; testlerde `FakeRunner(responses: dict[tuple[str,...], CompletedProcess], which: dict)`.
- `SystemInfo` dataclass: `os, distro_id, distro_like: list[str], distro_version, arch, mem_total_mb, disk_free_mb, cpu_model, cpu_cores, cpu_flags: list[str]` (yalnız `avx,avx2,avx512f,f16c,fma,sse4_2,neon`), `gpus: list[GpuInfo(vendor, name, accel)]`, `python_version, ollama: OllamaInfo(binary, version, system_service: bool, listening_11434: bool, listening_11435: bool, models_11434: list[str])`, `existing_install: bool`; `to_dict()`.
- `detect_system(runner, fs_root: Path = Path("/"), paths: AppPaths, http_get=…) -> SystemInfo`. Linux: `/etc/os-release`, `/proc/meminfo`, `/proc/cpuinfo`, `/sys/class/drm/card*/device/vendor` (0x10de nvidia, 0x1002 amd, 0x8086 intel), `/proc/net/tcp` (dinleme durumu 0A), `shutil.disk_usage(paths.root ya da var olan ilk üst dizin)`. Model listesi: yalnız `GET http://127.0.0.1:11434/api/tags` (zaman aşımı 2 sn, hata → boş liste). Windows: `platform`, `wmic`/PowerShell çağrıları runner üzerinden.

- [ ] Test: üç sahte fs_root için distro_id/like doğru (`arch`/`ubuntu` like `debian`/`fedora`); meminfo→MB; cpu bayrak filtresi; NVIDIA yokken `gpus` boş değil yalnız AMD/Intel ise doğru vendor; `/proc/net/tcp` içinde `0100007F:2CAA` 0A satırı → `listening_11434=True`; `FakeRunner` kaydı detect sırasında yalnız okuma komutları (`ollama --version`, `systemctl is-enabled ollama`) çağrıldığını doğrular — `start|stop|enable|disable|restart|pull|rm` hiç çağrılmaz.
- [ ] Test (Review Focus 1): `paths.root` henüz yok → disk boşluğu var olan ilk üst dizinden ölçülür.
- [ ] Uygula, geçir, commit.

### Task 3: Dağıtım stratejileri

**Files:** Create `src/lai_setup/distro.py`; Test `tests/setup/test_distro.py`

**Interfaces — Produces:** `PackageStrategy(name, manager, install_cmd: list[str], packages: dict[str, str])`; `strategy_for(info: SystemInfo) -> PackageStrategy`; `missing_prereqs(info, runner) -> list[str]` (mantıksal adlar: `python-venv`); `suggest_command(strategy, logical: list[str]) -> list[str]` (ör. `["pkexec","pacman","-S","--needed","python"]`, `["pkexec","apt-get","install","-y","python3-venv"]`, `["pkexec","dnf","install","-y","python3"]`; `pkexec` yoksa `sudo`).

- [ ] Test: arch→pacman, ubuntu/debian/mint (like debian)→apt, fedora→dnf, bilinmeyen→`generic` ve komut önerisi yok (kullanıcıya elle kurma mesajı); `python-venv` eksikliği `python3 -c "import venv, ensurepip"` başarısızlığından tespit edilir.
- [ ] Uygula, geçir, commit.

### Task 4: Adım motoru + durum + rapor

**Files:** Create `src/lai_setup/steps.py`, `state.py`, `report.py`; Test `tests/setup/test_steps.py`, `test_state.py`, `test_report.py`

**Interfaces — Produces:**
- `StepStatus` enum: `OK, APPLIED, SKIPPED, FAILED, PLANNED`.
- `StepResult(step_id: str, status: StepStatus, message: str, details: dict)`.
- `Context(paths, info, runner, mode: Literal["online","offline","none"], offline_dir: Path|None, dry_run: bool, assume_yes: bool, interactive: bool, state: InstallState, requirements_file: Path)`.
- `Step` ABC: `id: str`, `title: str` (Türkçe), `check(ctx) -> bool`, `apply(ctx) -> None`, `verify(ctx) -> tuple[bool,str]`.
- `run_steps(steps, ctx, action: Literal["install","repair","doctor"]) -> list[StepResult]` — install: `check` geçerse SKIPPED, değilse (dry_run → PLANNED) `apply`+`verify`; repair: `verify` başarısızsa `apply`; doctor: yalnız `verify`, hiçbir yazma. İlk FAILED sonrası bağımlı adımlar SKIPPED("önceki adım başarısız").
- `InstallState.load(path) -> InstallState` (yoksa boş; bozuk JSON → `corrupt=True`, dosya `.corrupt-<ts>` olarak yeniden adlandırılır yalnız `save` çağrılınca), `.steps: dict[str, dict]`, `.pins: dict[str,str]`, `save()` atomik (`tmp`+`os.replace`), `0600`.
- `Report(action, started, finished, info: SystemInfo, results, pins)`; `write(dir) -> tuple[Path, Path]` → `install-YYYYmmdd-HHMMSS.json` ve `.txt` (Türkçe özet tablo).

- [ ] Test: iki kez `run_steps("install")` → ikinci çalıştırmada tüm adımlar SKIPPED ve `apply` çağrı sayısı 1; doctor modunda `apply` 0 kez; dry_run'da dosya sisteminde değişiklik yok; bozuk state JSON → `load` çökmez, `corrupt=True` (Review Focus 2); rapor dosya adları ve txt'de Türkçe karakterler UTF-8.
- [ ] Uygula, geçir, commit.

### Task 5: M1 kurulum adımları

**Files:** Create `src/lai_setup/m1_steps.py`, `requirements/app.txt`; Test `tests/setup/test_m1_steps.py`

**Interfaces — Produces:**
- `EnsureDirectories` (`id="dirs"`): tüm `AppPaths` dizinleri, `data/`,`reports/` `0700`.
- `WriteConfig` (`id="config"`): yoksa varsayılan TOML yazar; varsa **dokunmaz** (check=dosya var ve `tomllib` ile okunuyor). Bozuksa repair `config.toml.bak-<ts>` yedeği alıp varsayılanı yazar. Varsayılan içerik:
  ```toml
  [ollama]
  host = "127.0.0.1"
  port = 11435
  model = "qwen3.5:4b"
  no_cloud = true
  [app]
  language = "tr"
  network = "offline"
  telemetry = false
  update_check = false
  ```
- `CreateVenv` (`id="venv"`): `sys.executable -m venv <venv>`; check = `<venv>/bin/python` (Windows `Scripts\python.exe`) çalışıyor.
- `InstallPythonDeps` (`id="pydeps"`): mode online → `pip install -r req`; offline → `pip install --no-index --find-links <offline_dir>/wheels -r req`; mode none → FAILED "çevrimiçi ya da çevrimdışı kaynak seçilmedi". check = `requirements` sha256'sı `state.steps["pydeps"]["req_sha256"]` ile aynı **ve** `pip check` başarılı. Pin: `state.pins["requirements_sha256"]`.
- `m1_steps() -> list[Step]` sırası: dirs, config, venv, pydeps.

- [ ] Test: dirs izinleri; config ikinci çalıştırmada kullanıcı düzenlemesi korunur (Review Focus 4); bozuk config + repair → `.bak` oluşur; pydeps FakeRunner ile online/offline argv birebir; mode none → FAILED ve runner'a pip çağrısı yok (Review Focus 3); venv adımı gerçek `venv` ile tmp'de (pip'siz `--without-pip` değil, gerçek) — yavaşsa `@pytest.mark.slow`.
- [ ] Uygula, geçir, commit.

### Task 6: CLI

**Files:** Create `src/lai_setup/cli.py`, `__main__.py`, `src/lai_setup/uninstall.py`; Test `tests/setup/test_cli.py`

**Interfaces — Produces:** `main(argv: list[str] | None = None) -> int`. Komutlar: `detect [--json]`, `install [--online | --offline DIR] [--dry-run] [--yes]`, `repair [...]`, `doctor [--json]`, `uninstall [--purge-data] [--yes]`. Ortak: `--lai-home PATH`, `--requirements PATH` (gelişmiş/test). Çıkış kodları: 0 başarı, 1 adım hatası, 2 kullanım hatası/kaynak seçilmedi, 3 kullanıcı iptal.
- install: mod verilmemiş ve TTY → Türkçe soru (`[1] Çevrimiçi (indirme yapılır: ~X MB)  [2] Çevrimdışı paket dizini  [3] İptal`); TTY yok → çıkış 2.
- uninstall: `app/`, `venv/`, `runtime/` (modeller dahil — kullanıcıya boyutuyla gösterilir, onay ister), `config/` silinir; `data/`, `reports/` korunur. `--purge-data` için etkileşimli `SİL` yazılması gerekir; `--yes` tek başına veri silmez (veri silmek için `--purge-data --yes` ikisi birden ve TTY yoksa bile ek olarak `LAI_CONFIRM_PURGE=SİL`).
- Her komut `Report` yazar (`logs/`).

- [ ] Test: `install --dry-run` dosya yazmaz; iki gerçek `install --offline <boş wheel dizini> --requirements <boş req>` → ikincisi tüm adımlar SKIPPED, `config.toml` mtime aynı; `doctor` sonrası dosya ağacı hash'i değişmez; `uninstall` sonrası `data/x.db` ve `reports/r.pdf` duruyor (Review Focus 5); `--purge-data --yes` onay değişkeni olmadan veri silmez; TTY'siz mod yok → 2.
- [ ] Uygula, geçir, commit.

### Task 7: Kabuk betikleri + belgeler

**Files:** Create `_common.sh`, `install.sh`, `doctor.sh`, `repair.sh`, `uninstall.sh`, `_common.ps1`, `install.ps1`, `doctor.ps1`, `repair.ps1`, `uninstall.ps1`, `docs/milestones/M01.md`, `README.md` (iskelet); Test `tests/setup/test_shell.py`

**Interfaces:** `_common.sh`: Python ≥3.11 bulur (`python3`, `python3.13`…); yoksa `lai_setup` çalışamayacağı için dağıtıma göre öneri komutunu basar ve `pkexec`/`sudo` ile çalıştırmayı **onayla** teklif eder; sonra `PYTHONPATH=<repo>/src exec python -m lai_setup <cmd> "$@"`. Root ile çalıştırılırsa reddeder (kullanıcı düzeyi kurulum). `.ps1`: `py -3` / `python` bulur, aynı şekilde çağırır; yönetici yetkisi gerekmez.

- [ ] Test: `shellcheck *.sh` temiz; `./install.sh --dry-run --offline <tmp> --lai-home <tmp>` çıkış 0; Türkçe/boşluklu `--lai-home` (Review Focus 1); `.ps1` dosyaları `lai_setup` alt komutlarına aynı argümanları geçiriyor (metin kontrolü; pwsh yok — bilinen sınırlama).
- [ ] Tüm testleri çalıştır: `.venv/bin/pytest -q` → hepsi PASS; çıktıyı `docs/milestones/M01.md`'ye yaz. Commit.

---

## M2 — Yerel AI çalışma ortamı (izole Ollama + Qwen)

Kullanıcının kesinleştirdiği ek kurallar (2026-11-13): uygulamanın Ollama'sı uygulama tarafından başlatılan **alt süreçtir**; uygulama kapanınca yalnız kendi başlattığını kapatır. Başlatma anında port yeniden kontrol edilir; çakışmada mevcut servise dokunulmaz. Model indirme yalnız açık izinle; çevrimdışı içe aktarma korunur. CPU yedeği zorunlu. Windows ve Linux süreç yönetimi ayrı. `OLLAMA_NO_CLOUD=1` tek başına ağ yalıtımı kanıtı sayılmaz — dış bağlantılar ayrıca doğrulanır.

**Mimari kararı:** Yeni, yalnız stdlib kullanan `lai_runtime` paketi (süreç yöneticisi, istemci, içe aktarma, ağ denetimi). Hem `lai_setup` hem ileride `lai` bunu kullanır; böylece çalışma ortamı uygulamadan bağımsız güncellenir. Tasarımdaki systemd kullanıcı servisi yerine uygulamaya ait alt süreç (kural 9).

### Dosyalar

```
src/lai_runtime/__init__.py
src/lai_runtime/config.py      # RuntimeConfig, load_runtime_config(paths)
src/lai_runtime/ports.py       # port_in_use(host, port), PortConflictError
src/lai_runtime/env.py         # build_env(cfg, paths, base_env, platform)
src/lai_runtime/process.py     # RuntimeManager: start/stop/restart/status, sahiplik kaydı
src/lai_runtime/_posix.py      # PDEATHSIG, /proc başlangıç zamanı, sinyaller
src/lai_runtime/_windows.py    # Job Object (KILL_ON_JOB_CLOSE), GetProcessTimes
src/lai_runtime/client.py      # OllamaClient (yalnız loopback, proxy yok)
src/lai_runtime/jsonschema_lite.py
src/lai_runtime/chunking.py
src/lai_runtime/models.py      # kaynak model okuma (manifests / manifests-v2 / .gguf), API ile içe aktarma
src/lai_runtime/netaudit.py    # süreç ağacı soketleri, ihlal tespiti, NetworkMonitor
src/lai_setup/m2_steps.py      # RuntimeBinary, ModelAvailable, RuntimeVerify
src/lai_setup/cli.py           # + runtime start|stop|status, model pull|import|verify|list
tests/runtime/fake_ollama.py   # sahte sunucu (OLLAMA_HOST, OLLAMA_NO_CLOUD zorunlu)
tests/runtime/test_*.py        # birim
tests/integration/test_real_runtime.py   # gerçek ollama ikilisi + gerçek model
docs/milestones/M02.md
```

### Arayüzler

- `RuntimeConfig(host="127.0.0.1", port=11435, model="qwen3.5:4b", accel: "auto"|"cpu"="auto", keep_alive="5m", binary: str|None=None)`; `load_runtime_config(paths) -> RuntimeConfig` (config.toml `[ollama]`; host loopback değilse ValueError).
- `build_env(cfg, paths, base_env, platform) -> dict` — **temiz** ortam: yalnız `PATH`, `HOME`/`USERPROFILE`=`runtime/home`, `LANG`, `OLLAMA_HOST=127.0.0.1:<port>`, `OLLAMA_MODELS=<paths.models>`, `OLLAMA_NO_CLOUD=1`, `OLLAMA_ORIGINS=` , `OLLAMA_KEEP_ALIVE`, `LAI_RUNTIME_OWNER=<instance id>`; GPU sürücü yolları (`LD_LIBRARY_PATH`, `CUDA_VISIBLE_DEVICES` varsa) geçirilir; proxy değişkenleri ve üst ortamdaki `OLLAMA_*` asla geçmez. `accel="cpu"` → `CUDA_VISIBLE_DEVICES=""`, `HIP_VISIBLE_DEVICES=""`, `ROCR_VISIBLE_DEVICES=""`, `GGML_VK_VISIBLE_DEVICES=""` ve istemci isteklerinde `num_gpu=0`.
- `RuntimeManager(cfg, paths, platform=None, binary=None)`: `start(timeout=60) -> RuntimeStatus` (port doluysa ve sahibi biz değilsek `PortConflictError`; biz isek yeniden kullanır), `stop(timeout=15) -> bool` (yalnız kayıtlı ve kimliği doğrulanan süreci kapatır), `restart()`, `status() -> RuntimeStatus(running, pid, port, owned, version)`, `base_url`. Sahiplik kaydı `runtime/ollama-process.json` = `{pid, start_id, port, owner}`; kimlik = pid + başlangıç zamanı + ortamdaki `LAI_RUNTIME_OWNER` (Linux `/proc/<pid>/environ`).
- `OllamaClient(base_url, timeout=600)`: loopback dışı → ValueError. `version()`, `list_models()`, `show(model)`, `ps()`, `generate(model, prompt, *, system=None, schema=None, options=None, think=False) -> GenerateResult(text, data, eval_count, total_duration_ns)` (`schema` verilirse `format=schema` ve çıktı `jsonschema_lite.validate` ile denetlenir; geçersizse `StructuredOutputError`), `pull(model, on_progress)`, `upload_blob(path, digest)`, `create(model, files, template=None, system=None, parameters=None)`. Hatalar: `RuntimeUnavailableError`, `ModelNotFoundError`, `OllamaError`.
- `chunk_text(text, max_chars=4000, overlap=None→min(200, max_chars//4)) -> list[Chunk(index, start, end, text)]` — paragraf, sonra cümle sınırında böler; hiçbir karakter kaybolmaz.
- `find_source_model(models_dir, name) -> SourceModel(name, gguf: Path, gguf_digest, template, system, parameters: dict)`; `source_from_gguf(path, name)`; `import_model(client, source, target_name, on_progress=None) -> str (digest)`. Kaynağa asla yazılmaz.
- `process_tree(pid) -> list[int]`, `sockets_of(pids) -> list[Conn(proto, local, remote, state, pid)]`, `violations(conns) -> list[str]` (loopback dışı uzak adres; loopback dışı dinleme; UDP 53), `NetworkMonitor(pid, interval=0.05)` bağlam yöneticisi.

### Görevler (her biri TDD; birim testleri sahte sunucu ile)

1. **config + ports + env** — test: temiz ortam (üst `OLLAMA_HOST=0.0.0.0`, `HTTPS_PROXY` geçmez), cpu modu değişkenleri, loopback olmayan host reddi, gerçek soketle dolu port tespiti.
2. **client + jsonschema_lite + chunking** — test: 0.0.0.0/192.168.x/`example.com` reddedilir; sahte sunucuda 404 → `ModelNotFoundError`; şemaya uymayan JSON → `StructuredOutputError`; chunk birleşimi = orijinal metin, sınırlar cümle sonunda.
3. **process (Linux + Windows ayrı)** — test (sahte sunucu): başlat/durdur/yeniden başlat; port çakışması (başka süreç dinlerken) → `PortConflictError` ve o süreç hâlâ yanıt veriyor; sahiplik kaydı yabancı süreci gösteriyorsa `stop()` onu öldürmez; ebeveyn SIGKILL alınca çocuk ölür (PDEATHSIG); başlangıçta çöken ikili → anlaşılır hata ve kayıt temizlenir. Windows: `_windows` seçimi ve Job Object çağrı sırası sahte ctypes ile.
4. **models** — test: `manifests/` ve `manifests-v2/` (sembolik bağ) düzenleri; salt okunur kaynak ağacı içe aktarma sonrası birebir aynı; bozuk blob (digest uyuşmazlığı) → hata, hedefte model yok.
5. **netaudit** — test: sabit `/proc` örnekleriyle ihlal tespiti; gerçek süreçte yalnız loopback bağlantısı → ihlal yok.
6. **kurulum adımları + CLI** — test: `model pull` `--online`/onay olmadan → çıkış 2, sahte sunucuya `/api/pull` gitmez; `--offline` içe aktarma; `doctor` süreç başlatmaz; sürüm ve model digest'i durum dosyasına sabitlenir; `runtime stop` sistem servisine dokunmaz.
7. **Gerçek entegrasyon** (`tests/integration`, gerçek `/usr/bin/ollama`, geçici `LAI_HOME`, rastgele boş port, sistemdeki modelin **salt okunur** kopyalanarak içe aktarılması): yanıt üretimi; Türkçe talimat; JSON şema çıktısı; uzun metin parçalı analiz; olmayan model; port çakışması; sistem servisi (PID, `/api/tags`, model dizini) değişmedi; `unshare -rn` ile ağsız ad alanında çıkarım; çıkarım sırasında süreç ağacının soketlerinde loopback dışı bağlantı yok; başlat/durdur/yeniden başlat; `accel=cpu` → `size_vram == 0`.

---

## M3 — SQLite ve kalıcı bilgi altyapısı

Kullanıcı kuralları (2026-11-14): şema sürümlü; öğrenilen bilgiler kalıcı; kaynak ve kanıtlar korunur; kullanıcı düzeltmeleri geçmişiyle tutulur; belirsiz kurallar doğrulanmış bilgiye dönüştürülmez; eski değerlendirmeler yeni bilgiyle otomatik değişmez; yedekleme ve geri yükleme. Yalnız sentetik veri. Sisteme kurulum yok.

**Mimari:** `src/lai/store/` (yalnız stdlib `sqlite3`; Qt/AI importu yok). Değişmezlik kuralları **veritabanında** (tetikleyici + CHECK) zorlanır; depo sınıfları ayrıca anlaşılır Türkçe hata verir. Bağlantı `isolation_level=None`, açık `BEGIN IMMEDIATE` işlemleri; `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`; dosya `0600`.

### Dosyalar

```
src/lai/__init__.py
src/lai/store/__init__.py          # Store dışa aktarımı
src/lai/store/db.py                # open_db, migrate, current_version, SchemaTooNewError, MigrationChecksumError
src/lai/store/migrations/__init__.py   # MIGRATIONS (sürüm, ad, sql, sha256)
src/lai/store/migrations/0001_initial.sql
src/lai/store/enums.py             # Confidence, Source, Validation, QuestionStatus, ResponseKind, AssessmentStatus
src/lai/store/store.py             # Store.open(path) + transaction() + depolar
src/lai/store/inventory.py         # envanter, sürüm, belge, kanıt, boyut, kutup, madde, seçenek
src/lai/store/knowledge.py         # bilgi kayıtları (salt eklenir, sürümlü), düzeltmeler, kanıt bağları
src/lai/store/questions.py         # gereksinim matrisi, netleştirme soruları, yanıtlar
src/lai/store/profiles.py          # puanlama profili + donmuş sürümler
src/lai/store/participants.py      # katılımcı (anonim kod zorunlu), cevap setleri
src/lai/store/assessments.py       # değişmez değerlendirme kayıtları, geri bildirim
src/lai/store/backup.py            # backup_database, verify_backup, restore_database
src/lai_setup/m3_steps.py          # InitDatabase adımı
src/lai_setup/cli.py               # + db status|backup|restore
tests/support/synthetic.py         # sentetik envanter/katılımcı üretici (400 madde)
tests/store/test_*.py
docs/milestones/M03.md
```

### Şema v1 (özet)

`schema_migrations`, `app_meta` · `inventories`, `inventory_versions`, `source_documents` (sha256 tekil), `evidence_refs` (belge+sayfa+aralık+alıntı ya da kullanıcı ifadesi/örnek sonuç) · `dimensions`, `poles`, `items`, `options` · `knowledge_entries` (salt eklenir; `entry_key`+`version`+`supersedes_id` zinciri; `kind` fact/rule/note; `rule_type`; JSON `content`; Türkçe `summary`; `item_number`, `dimension_code`; `confidence` 5 düzey; `source` document/user/ai_inference/example_fit/assessment_feedback; `validation` pending/validated/rejected; `confirmation_ref`) · `knowledge_evidence` (supports/contradicts) · `user_corrections` · `requirements` (bütünlük matrisi; durum değişimi tetikleyiciyle geçmişe yazılır) · `clarification_questions` (dedupe_key tekil; 6 alanlı soru içeriği) · `question_responses` (5 yanıt türü) · `learning_history` (salt eklenir; tetikleyicilerle otomatik) · `scoring_profiles`, `scoring_profile_versions` (donmuş anlık görüntü + içerik hash'i + provisional), `profile_version_entries` · `participants` (yalnız `code` zorunlu), `response_sets`, `responses` · `assessments` (değişmez; `recalculated_from`), `assessment_feedback`. Görünümler: `current_knowledge`, `confirmed_rules`, `inferred_rules`, `unresolved_knowledge`, `pending_questions`.

Zorunlu kısıtlar:
- `CHECK(confidence <> 'CONFIRMED' OR (source IN ('document','user') AND confirmation_ref IS NOT NULL AND validation = 'validated'))`
- `knowledge_entries`, `user_corrections`, `learning_history`, `question_responses`, `scoring_profile_versions`, `profile_version_entries`, `responses`, `assessments`, `assessment_feedback`: UPDATE/DELETE → `RAISE(ABORT)`.
- Yeni sürüm yalnız en son sürümün üstüne (`supersedes_id` tekil + aynı anahtar + `version = önceki+1`).
- `assessments`: `VERIFIED` yalnız `provisional = 0` profil sürümüyle.

### Arayüzler

- `open_db(path, *, readonly=False) -> sqlite3.Connection`; `migrate(conn, *, backup_dir: Path | None) -> MigrationResult(from_version, to_version, backup: Path | None)`; `current_version(conn) -> int`; `LATEST_VERSION: int`.
- `Store.open(path, *, backup_dir=None) -> Store` (göç uygular); `store.transaction()` bağlam yöneticisi; `store.inventory`, `.knowledge`, `.questions`, `.profiles`, `.participants`, `.assessments`; `store.close()`.
- `KnowledgeRepo.record(inventory_id, *, entry_key, kind, content: dict, summary, confidence, source, rule_type=None, item_number=None, dimension_code=None, evidence_ids=(), created_by="system") -> Entry`; `.revise(entry_id, *, content=None, summary=None, confidence=None, source, reason, evidence_ids=()) -> Entry` (yeni sürüm); `.confirm(entry_id, *, source: 'user'|'document', confirmation_ref, reason) -> Entry`; `.reject(entry_id, *, reason, by) -> Entry`; `.apply_user_correction(inventory_id, *, entry_key, content, summary, explanation, user_text, target_entry_id=None, kind='rule', rule_type=None, item_number=None, dimension_code=None) -> tuple[Correction, Entry]` (kullanıcı ifadesi kanıtı + CONFIRMED yeni sürüm); `.current(inventory_id, *, kind=None, confidence=None) -> list[Entry]`; `.history(inventory_id, entry_key) -> list[Entry]`.
- `QuestionRepo.upsert_requirement(inventory_id, *, req_key, category, description, critical, status, reason, dimension_code=None, item_number=None) -> Requirement`; `.matrix(inventory_id) -> list[Requirement]`; `.ask(inventory_id, *, dedupe_key, group_key, missing_info, why_it_matters, related: dict, evidence_summary, impact, question_text, choices: list[str] | None, requirement_id=None) -> tuple[Question, bool]` (ikinci değer: yeni mi; yanıtlanmış ya da açık soru tekrar oluşturulmaz, `later`/`skipped` yeniden açılır); `.respond(question_id, kind, value=None) -> Question`; `.pending(inventory_id) -> list[Question]` (open + later, gruplu sıra).
- `ProfileRepo.ensure_profile(inventory_id, name) -> int`; `.publish_version(profile_id, *, note) -> ProfileVersion` (güncel kural kayıtlarının donmuş kopyası; hash; `provisional` = kritik gereksinimlerden biri CONFIRMED değil ya da kural CONFIRMED değil); `.versions(profile_id)`; `.get_version(id)`.
- `ParticipantRepo.create(*, code=None, alias=None, full_name=None, contact=None, category=None, attributes=None) -> Participant` (kod yoksa `ANON-xxxxxxxx`); `.add_response_set(participant_id, inventory_version_id, responses: dict[int, str | None], source_label=None) -> int`.
- `AssessmentRepo.record(*, response_set_id, profile_version_id, status, results: dict, limitations: list[str], engine_version, recalculated_from=None) -> Assessment` (`input_hash` hesaplanır); `.get(id)`; `.for_response_set(id)`; `.add_feedback(assessment_id, text)`.
- `backup_database(db_path, dest_dir, *, label="manual") -> BackupInfo(path, sha256, schema_version, created_at)`; `verify_backup(path) -> BackupInfo`; `restore_database(backup_path, db_path, *, safety_dir) -> RestoreResult(restored, safety_backup)`.

### Görevler (TDD)

1. **db + göç + şema v1** — test: boş dosyada v1 kurulur; ikinci açılış hiçbir şey yapmaz; `user_version` > LATEST → `SchemaTooNewError` ve dosya değişmez; göç dosyası değişmişse `MigrationChecksumError`; yarıda hata → geri alınır (sürüm değişmez); dosya izni `0600`; tüm tablolar/görünümler var.
2. **değişmezlik ve güven kuralları (SQL düzeyi)** — test: her salt-eklenir tabloda UPDATE/DELETE reddedilir; `ai_inference` kaynaklı CONFIRMED eklenemez; onay referanssız CONFIRMED eklenemez; ara sürümün üstüne dal açılamaz; provisional profille VERIFIED değerlendirme eklenemez.
3. **knowledge** — test: record/revise zinciri ve `current` yalnız son sürüm; `history` tüm sürümler; kullanıcı düzeltmesi: yeni CONFIRMED sürüm + `user_corrections` + kullanıcı ifadesi kanıtı, eskisi aynen duruyor; `confirm` ai kaynağıyla çağrılırsa `ValueError`; her işlem `learning_history`'ye düşer.
4. **questions** — test: aynı `dedupe_key` ikinci kez yeni soru üretmez; yanıtlanan soru tekrar sorulmaz; `later` → `pending`'de kalır, kalıcıdır (yeniden açılan Store'da da); 5 yanıt türü; gereksinim durum değişimi geçmişe yazılır.
5. **profiles + participants + assessments** — test: yayımlanan sürüm sonraki bilgi değişikliğinden etkilenmez (hash ve içerik aynı); yeni sürüm yeni satır; anonim katılımcı (yalnız kod); eksik yanıt `None`; değerlendirme kaydı değişmez, yeni profil sürümüyle yeniden hesap yeni satır (`recalculated_from`), eskisi aynen.
6. **backup/restore** — test: yedek bütünlüğü + sha256 + sürüm; bozuk yedek reddedilir ve hedef değişmez; geri yükleme öncesi güvenlik yedeği; göç öncesi otomatik yedek; WAL'deki işlenmiş veri yedekte var.
7. **kurulum adımı + CLI + sentetik ölçek** — test: `install` DB'yi kurar, ikinci kez ATLANDI; `doctor` salt okunur (dosya mtime değişmez); `db backup/restore/status`; `uninstall` DB ve yedekleri korur; 400 madde + 200 katılımcı × 400 yanıt yükleme < 10 sn ve bütünlük denetimi `ok`.

---

## M4 — Belge içe aktarma ve soru çıkarımı

Kullanıcı kuralları (2026-11-14): PDF/DOCX/XLSX/CSV/JSON; ~400 soru; soru ve seçenekleri doğru ayır; numara ve özgün sıra korunur; kaynak dosya + sayfa + konum; soru–seçenek ve cevap anahtarı eşleşmesi korunur; yinelenen soru tespiti; eksik/bozuk/belirsiz kayıt işaretlenir; içerik **sessizce değiştirilmez, eksik veri tahminle doldurulmaz**; ham içerik ve ayrıştırılmış veri ayrı saklanır; aynı belge tekrar → mükerrer kayıt yok; anlaşılır hata raporu; her kayıt kaynağa izlenebilir. Puanlama algoritması tahmin edilmez; cevap anahtarı **yorumlanmadan** ham değer olarak saklanır (bilgi kaydı/kural üretilmez — M5). Ayrıştırma tamamen deterministik; AI yok. Yalnız sentetik belgeler.

**Mimari:** `src/lai/ingest/` (Qt/AI importu yok). Akış: `detect_kind` (uzantı + sihirli bayt) → okuyucu → `RawUnit` listesi (ham metin + konum) → ayrıştırıcı (`textparse` serbest metin; `tabular` tablo; `jsonfmt` JSON şeması) → `checks` (eksik/bozuk/belirsiz/yinelenen) → `ParseResult` → `service.import_document` (DB: ham birimler, ayrıştırılmış kayıtlar, sorunlar; hata yoksa yeni envanter sürümü + madde/seçenek + kanıt).

### Dosyalar

```
src/lai/store/migrations/0003_ingest.sql   # document_raw_units, import_runs, parsed_items, parsed_options,
                                           # parsed_answer_keys, import_issues; evidence_refs.locator;
                                           # options.evidence_id; inventory_versions.source_run_id/content_hash;
                                           # items/options değişmez
src/lai/ingest/__init__.py
src/lai/ingest/model.py      # Locator, RawUnit, ParsedOption, ParsedItem, AnswerKeyEntry, Issue, ParseResult, IngestError
src/lai/ingest/detect.py     # detect_kind, sha256_file (akışlı), MAX_BYTES
src/lai/ingest/readers.py    # read_pdf, read_docx, read_xlsx, read_csv, read_json (→ RawUnit / satırlar)
src/lai/ingest/textparse.py  # parse_text_units: madde/seçenek/satır içi seçenek/cevap anahtarı/başlık/sayfa süsü
src/lai/ingest/tabular.py    # map_columns, parse_rows
src/lai/ingest/jsonfmt.py    # SCHEMA (lai-inventory/1), parse_json_document
src/lai/ingest/checks.py     # run_checks: numara boşluğu/yinelenen/sıra dışı/boş metin/bozuk karakter/seçenek tutarlılığı/anahtar
src/lai/ingest/normalize.py  # tr_casefold, compare_key (yalnız karşılaştırma için; saklanan metin değişmez)
src/lai/ingest/parse.py      # parse_file(path, kind=None, encoding=None) -> ParseResult
src/lai/ingest/service.py    # import_document(store, inventory_id, path, *, documents_dir, ...) -> ImportReport
src/lai/ingest/report.py     # format_report (Türkçe metin), report_dict (JSON)
src/lai/ingest/__main__.py   # python -m lai.ingest parse|import
tests/support/synthetic_docs.py  # Türkçe karakterli sentetik envanter → PDF/DOCX/XLSX/CSV/JSON (+ bozuk varyantlar)
tests/ingest/test_*.py
docs/milestones/M04.md
```

### Kararlar

- Saklanan metin = kaynaktaki karakterler; yalnız satır sonları tek boşlukla birleştirilir (PDF/DOCX satır kaydırması). Satır sonu tirelemesi kaldırılmaz. Ham satırlar ayrıca `document_raw_units`'te birebir durur.
- Serbest metinde seçenek kodu yalnız harf (A–J / a–j); rakamlı seçenekler madde numarasıyla karışacağından yalnız tablo/JSON'da desteklenir.
- Satır içi seçenekler (`a) Evet b) Hayır`) yalnız `a`/`A`'dan başlayıp ardışık ilerlerse bölünür.
- Önem: `error` → envanter sürümü oluşturulmaz (`needs_review`); `warning`/`info` → sürüm oluşur, raporda görünür.
  - error: numara boşluğu, yinelenen numara, boş madde metni, bozuk karakter (U+FFFD/kontrol), madde içinde yinelenen seçenek kodu, diğerleri seçenekliyken seçeneksiz madde, metin katmanı olmayan PDF sayfası, anahtarda olmayan madde, aynı madde için çelişen anahtar, numarasız tablo satırı.
  - warning: aynı metin farklı numarada, çok benzer metin (≥0,92), sıra dışı numara, seçenek sayısı farklı, seçenek harfi atlanmış, çok satırlı seçenek, cevap anahtarı eksik/seçenek dışı değer, eşlenmemiş metin satırı.
  - info: ayrılan yönerge/başlık/sayfa süsü satırları, otomatik numara hesaplandı, hiç seçenek yok (ortak ölçek olabilir), yok sayılan sütun/sayfa.
- Yinelenme: aynı envanterde aynı sha256 + aynı ayrıştırıcı sürümü → `duplicate` çalıştırması, yeni veri yok. Farklı dosya ama içerik hash'i son sürümle aynı → yeni sürüm açılmaz.
- CSV: UTF-8 (BOM'lu/BOM'suz) katı; değilse açık hata + `encoding` parametresi. Ayraç `, ; \t` arasından.
- DOCX otomatik numaralandırma `numbering.xml`'den deterministik hesaplanır (decimal, harf, roma; `start`, düzey sıfırlama); hesaplandığı info olarak raporlanır.
- Boyut sınırı varsayılan 200 MB; sha256 akışlı; XLSX `read_only`; CSV satır satır.

### Görevler (TDD)

1. **Şema v3 + model + tür tespiti** — test: tablolar/sütunlar var, ayrıştırılmış tablolar ve `items/options` değişmez; uzantı–içerik uyuşmazlığı, `.doc/.xls`, boş dosya, çok büyük dosya anlaşılır `IngestError`; sha256 akışlı.
2. **Okuyucular** — test: PDF sayfa/karakter aralığı/bbox; şifreli ve metinsiz PDF; DOCX paragraf + tablo sırası + otomatik numara; XLSX sayfa/hücre; CSV kodlama/ayraç; JSON sözdizimi hatası satır/sütunla.
3. **Ayrıştırıcılar** — test: madde/seçenek ayrımı, satır içi seçenek, çok satırlı metin, sayfa aşan madde, başlık/sayfa süsü, cevap anahtarı; tablo başlık eşleme (Türkçe eş anlamlılar), JSON şema hataları yol bilgisiyle.
4. **Denetimler** — her error/warning kodu için bir test; Türkçe büyük/küçük harf duyarlı yinelenme (`IŞIK` = `ışık`, `İ` = `i`).
5. **Servis + rapor + CLI** — test: hatasız belge → sürüm + madde + seçenek + kanıt (belge, sayfa, aralık, alıntı); hatalı → `needs_review`, sürüm yok, ayrıştırılmış kayıtlar ve sorunlar saklı; aynı belge iki kez → `duplicate`; farklı biçimde aynı içerik → yeni sürüm yok; bozuk dosya → `failed` + belge kaydı + anlaşılır mesaj; özgün dosya kopyası `0400`.
6. **Sentetik 400 + ölçek** — her biçimde 400 madde kayıpsız (metin, seçenek, sıra, Türkçe karakterler birebir); süre; bellek (tracemalloc ve alt süreç RSS); `integrity_check`.

---

## M5 — Puanlama kuralı keşfi

Kullanıcı onayı: 2026-11-14 ("devam"). Kurallar M4 ile aynı: yalnız sentetik belge, model indirme yok, mevcut Ollama'ya dokunma yok, dış servis yok; testler sahte LLM ile. Puanlama algoritması **tahmin edilmez**: LLM yalnız belgede açıkça yazan kuralları **aday** olarak önerir; her aday sabit kural kataloğunun katı şemasıyla ve belgede birebir bulunan alıntıyla doğrulanır; durum deterministik olarak atanır. Hiçbir aday CONFIRMED olmaz (M6'da kullanıcı onaylar).

**Mimari:** `lai.core.rules` (kural kataloğu + gövde şemaları + anahtar; saf), `lai.core.tally` (boyut modeli + puan sayımı + karar; saf — M7 bunun üstüne kurulur), `lai.ai` (LLM protokolü, Ollama bağdaştırıcısı, Türkçe istem + çıktı şeması), `lai.learning` (`corpus` belge metni/parçalama/alıntı bulma, `examples` örnek sonuç içe aktarma, `fit` örnek uyumu, `discovery` akış, `__main__` CLI). Başvuru belgeleri (puanlama yönergesi gibi madde içermeyen belgeler) `lai.ingest.service.import_reference_document` ile ham metin olarak saklanır.

### Kural kataloğu (v1)

| Tür | Gövde | Anahtar |
|---|---|---|
| `dimension_def` | `{dimension, poles:[p1,p2]}` | `rule:dimension_def:dim:D` |
| `dimension_items` | `{dimension, items:[n…]}` | `rule:dimension_items:dim:D` |
| `default_option_map` | `{dimension, options:{kod:kutup}}` | `rule:default_option_map:dim:D` |
| `item_pole_map` | `{item, dimension, options:{kod:kutup}}` | `rule:item_pole_map:item:N:dim:D` |
| `option_weight` | `{item, dimension, weights:{kod:{kutup:puan}}}` | `rule:option_weight:item:N:dim:D` |
| `reverse` | `{item, dimension}` | `rule:reverse:item:N:dim:D` |
| `threshold` | `{dimension, method:"higher_wins"}` ya da `{dimension, method:"min_points", pole, value}` | `rule:threshold:dim:D` |
| `tie_break` | `{dimension, pole}` | `rule:tie_break:dim:D` |
| `missing_answer_policy` | `{dimension ("*" = tümü), policy: skip\|count_as_pole\|invalidate, pole?}` | `rule:missing_answer_policy:dim:D` |
| `normalization` | `{dimension, method: percentage\|scaled\|other, parameters:{}}` | `rule:normalization:dim:D` (M5'te sınanmaz) |

Katalog dışı tür → red. Bilgi kaydı içeriği: `{"body": …, "explanation": …}`; çelişen öneri varsa `"alternatives": […]`, `"conflict": true`.

### Durum atama (deterministik)

- Şema geçersiz / katalog dışı / alıntı belgede yok / alıntı < 8 karakter / madde ya da seçenek envanterde yok / kutup boyutta yok → aday **reddedilir** (bilgi kaydı yok; `discovery_candidates`'ta gerekçesiyle saklanır).
- Aynı anahtar için farklı gövdeler (aynı çalıştırmada) → CONTRADICTORY (`conflict`), uyum testine girmez.
- Kullanıcı onaylı (CONFIRMED) ya da reddedilmiş kayıtla çelişen aday → yazılmaz (`conflicts_confirmed` / `previously_rejected`).
- Örnek uyumu: tutarlı → STRONGLY_INFERRED (`example_fit`); tutarsız → CONTRADICTORY (`example_fit`); sınanamaz → WEAKLY_INFERRED (`ai_inference`).
- Uyum testi: (a) karar kuralları (`threshold`, `tie_break`) örnekte bildirilen puanlara uygulanıp harfle karşılaştırılır; (b) puan kümesi (üyelik + eşlemeler + ters + ağırlık + eksik cevap politikası) bildirilen puanlarla, puan yoksa ve karar kuralı tutarsız değilse harfle karşılaştırılır; (c) `dimension_def` örnek harfleri/puan anahtarları kutuplarda mı. Bir kural katıldığı testlerden biri tutarsızsa tutarsız; en az biri tutarlıysa tutarlı; hiç sınanmadıysa sınanamaz. Kümede tutarsızlık varsa kümedeki tüm katılımcı kurallar CONTRADICTORY olur (hangi kuralın hatalı olduğu tahmin edilmez).
- Öncelik (puan hesabı): `option_weight` > `item_pole_map` > `default_option_map` (± `reverse`). Eksik eşleme → model eksik → sınanamaz (eksikler ayrıntıda listelenir).

### Şema v4 (`0004_discovery.sql`)

`reference_documents`, `example_sets`, `example_results`, `discovery_runs` (yalnız `running` → son durum güncellenebilir), `discovery_chunks` (istem sha256, ham çıktı, düşünme metni), `discovery_candidates`, `fit_runs`, `fit_results`; hepsi (çalıştırma durum geçişi dışında) değiştirilemez; denetim kaydı tetikleyicileri.

### Arayüzler

- `lai.core.rules`: `RULE_TYPES`, `RuleError(code, message)`, `validate_rule(rule_type, body) -> list[str]`, `rule_key(rule_type, body) -> str`, `rule_dimension(body) -> str`, `canonical(body) -> str`.
- `lai.core.tally`: `build_model(dimension, rules: list[tuple[key, type, body]]) -> DimensionModel` (`.missing: list[str]`, `.used: set[key]`), `score(model, answers: dict[int, str|None]) -> Tally(points, letter, status, note, used)`, `decide(rules_for_dim, points) -> (letter|None, used_keys)`.
- `lai.ai.llm`: `LLMReply(text, thinking, eval_count, duration_ms)`, `LLM` Protocol `complete(*, system, prompt, schema) -> LLMReply` + `.model: str`; `OllamaLLM(base_url, model, *, think=True, timeout)` (loopback istemcisi, `temperature 0`, `seed 0`); `lai.ai.prompts`: `PROMPT_VERSION`, `DISCOVERY_SYSTEM`, `discovery_prompt(chunk_text, context) -> str`, `OUTPUT_SCHEMA`.
- `lai.learning.corpus`: `Corpus.for_document(store, document_id)`, `.text`, `.chunks(max_chars)`, `.find(quote) -> QuoteHit|None` (boşluk dizileri tek boşluk sayılır, başka normalleştirme yok), `QuoteHit.evidence_fields()`.
- `lai.learning.examples`: `parse_examples(data) -> list[Example]` (`lai-examples` v1), `import_examples(store, inventory_id, path, *, documents_dir) -> ExampleImport`.
- `lai.learning.fit`: `run_fit(store, inventory_id, *, discovery_run_id=None) -> FitReport` (güncel, onaylanmamış, reddedilmemiş kural kayıtlarını yeniden değerlendirir; değişen durum = yeni sürüm).
- `lai.learning.discovery`: `discover_rules(store, inventory_id, llm, *, document_ids=None, max_chars=6000) -> DiscoveryReport`.
- CLI: `python -m lai.learning reference|examples|discover|fit|rules`.

### Görevler (TDD)

1. **Şema v4 + kural kataloğu** — test: tablolar; değişmezlik; çalıştırma yalnız `running`'den çıkar; her türün geçerli/geçersiz gövdesi; anahtar kararlı; katalog dışı tür.
2. **Başvuru belgesi + derlem + alıntı** — test: kılavuz PDF/DOCX ham metin olarak saklanır, ikinci kez yinelenmez; alıntı satır sonu/çoklu boşluk farkıyla bulunur, harf farkıyla bulunmaz; kanıt sayfa + sayfa içi aralık + belgedeki birebir metin; tablo satırları hücre metniyle; parçalar tüm metni örter.
3. **Örnekler + puan sayımı + uyum** — test: `lai-examples` şema hataları yol bilgisiyle; tutarlı küme → STRONGLY_INFERRED; tek ters madde hatalı → küme CONTRADICTORY; puansız harfli örnek + eşik; berabere + tie_break; eksik cevap politikası; eksik eşleme → sınanamaz; örnek yok → WEAKLY_INFERRED.
4. **AI katmanı + keşif** — test (sahte LLM): alıntısı belgede olmayan öneri reddedilir; katalog dışı / şema dışı / olmayan madde reddedilir; aynı öneri iki parçadan → tek kayıt, iki kanıt; çelişen öneri → CONTRADICTORY; onaylı kayıtla çelişen öneri yazılmaz; ikinci çalıştırma yeni sürüm üretmez; LLM ulaşılamaz → çalıştırma `failed`, bilgi yazılmaz; bozuk JSON → parça `invalid_output`; istem enjeksiyonu metni veri olarak kalır; hiçbir aday CONFIRMED değil.
5. **CLI + ölçek + belge** — test: sahte Ollama sunucusuyla uçtan uca `discover`; 11434 reddedilir; 400 maddelik envanter + kılavuz + 50 örnek → süre ve doğruluk; `docs/milestones/M05.md`.

---

## M7 — Deterministik puanlama (2026-11-14)

Kapsam: saf Python ortak giriş noktası; yalnız sentetik veri. M8 arayüzü/toplu iş akışı,
kurulum, model ve dış servis çağrısı yok. M6 commit'leri 209cfc8, 3337a49, 03e6c7d
ata doğrulamasıyla kontrol edilir; başlangıç referansı 513 teknik testtir.

1. Başlangıç takımını çalıştır ve sonucu M07'ye kaydet.
2. Katalog v2: mevcut on türün biçimini koru; `aggregation` (sum/mean),
   `dimension_weight`, `zero_score`, `precision` ekle. Normalizasyon parametrelerinin
   yürütülebilir alt kümesini motor ayrıca doğrulasın; serbest formül/kod çalıştırma yok.
3. `lai.core.scoring`: sınav/profil sürümü ve cevaplardan Decimal puan + izlenebilir
   sonuç. M5 `build_model` eşleme/ters önceliği ve `decide` karar semantiği yeniden kullanılır.
   Yerel 50 basamaklı Decimal bağlamı; seçenek → toplam/ortalama → boyut ağırlığı →
   normalizasyon → sıfır/eşik/eşitlik → yalnız gösterimde yuvarlama.
4. Donmuş profillere envanter sürümü, sıralama, kaynak/doğrulama/kanıt metaverisi,
   gereksinimlerin boyut kapsamı ve boyut incelemelerini ekle. Eski snapshot'ları değiştirme.
   UNKNOWN/CONTRADICTORY kritik durum etkilenen boyutu engeller; çıkarım kesinleşmez.
5. `lai.scoring` depo bağdaştırıcısı: sürümleri yükle ve saf motoru çağır; değerlendirme
   yazma veya toplu iş yapma. M8 her iki akışta aynı giriş noktasını kullanabilir.
6. El hesabı örnekleri, ENTP harf eşleme (1.85,-1.32,2.45,-3.10), sınırlar, hatalı girdiler,
   M5 uyumu, kısmi sonuç, eski sürüm/sonuç koruması, 1000 tekrar, 400 madde ölçümü.
7. Tam teknik takım, statik/katman kontrolü, M07 raporu ve ayrı yerel commit'ler.
   Push yok; M7 tamamlanınca dur.

Sayısal/formül semantiği gerçek envanterden çıkarılmış varsayımlar değildir. Parametre
anlamı belirsiz normalizasyon ve M6 serbest metin bilgisi puanlama koduna dönüştürülmez.

---

## M8 — Tekil ve toplu değerlendirme (2026-11-14)

Referans M7: 602 teknik test; 0,0079 sn yalnız motor süresidir. M8 toplam ölçümü
CSV/XLSX okuma, sütun/cevap doğrulaması, puanlama ve SQLite kaydını kapsayacaktır.

1. M07, ana plan ve mevcut assessment/participant/response depolarını incele;
   başlangıç takımını çalıştır. Yalnız sentetik veri, model/kurulum/dış servis yok.
2. CSV/XLSX ön doğrulayıcı: açık veya kesin sütun eşlemesi, envanter soruları,
   kimlik/takma ad, boş cevap, satır/seçenek/tekrar hataları ve Türkçe kaynak konumu.
   Şema hatası katılımcı yazmadan reddedilir; satır hataları bağımsız saklanır.
3. Şema v6: sürümleri ve kaynak/girdi snapshot'ını sabitleyen işler; katılımcı başına
   pending/succeeded/partial/failed satırlar, sonuç bağlantısı ve denetim geçmişi.
4. Tekil ve toplu akış aynı `lai.scoring.evaluate` girişini kullanır. Katılımcı,
   cevap seti, değişmez değerlendirme ve iş satırı tek transaction içindedir.
   Satır başına SQLite yazma kilidi ve yeniden pending kontrolü mükerrer sonucu önler.
5. Durum/ilerleme, duraklatma, güvenli iptal ve tekrar devam; kesintide satırın tamamı
   geri alınır. İş kaynak dosyasını tekrar okumaz veya güncel profil seçmez.
6. Mevcut `python -m lai.learning` CLI'ına assess/batch/job/rescore ekle. Kimlik veya
   takma ad yeterli; ad/e-posta/telefon isteğe bağlıdır. Yeniden hesap yeni kayıttır.
7. Tekil/toplu motor eşitliği, kısmi/engellenmiş boyut, hata yalıtımı, crash/continue,
   çift çalışan, sürüm sabitleme, CSV/XLSX ve 400×200 toplam süre testleri.
8. Tam regresyon ve M08 raporu; ayrı yerel commit'ler, push yok. M9'a geçme.

---

## M9 — Türkçe PySide6 masaüstü (2026-11-14)

M8 referansı 671 teknik testtir; 16 gerçek entegrasyon ayrı raporlanır. Yalnız sentetik
veri; kurulum, model indirme/dış servis ve mevcut Ollama/OpenCode ayarına yazma yok.

1. Belge önizleme/kayıt (soru, anahtar ve yönerge), kaynak konumlu hatalar; ayrı cevap
   anahtarını mevcut ham belge/parsed_answer_keys depolarına yazan Qt'siz servis.
2. Türkçe gezinme, envanter seçimi, öğrenme/soru/kanıt/profil ekranı; serbest yanıt,
   erteleme/bilmiyorum, açık kural formu ve model yorumu → açık kullanıcı onayı.
3. Sürüm seçimiyle tekil cevap formu, kalıcı sonuç ve açılabilir hesap dökümü;
   geçmiş ve katılımcı ekranları. M8/M7 servisleri aynen kullanılır.
4. CSV/XLSX eşleme/satır önizlemesi, hazır iş, ilerleme, iptal/duraklatma/devam;
   kapanış bekleyen satırı güvenli noktada bırakır. Yeni açılışta işler listelenir.
5. QThread çalışanı, işlem başına Store; GUI iş parçacığında uzun hizmet çağrısı yok.
   Qt yalnız `lai.ui` içinde; yerel model durumu mevcut loopback bağdaştırıcısından.
6. Türkçe dosya seçiciler, klavye kısayolları, açık durumlar ve yerel UI tercihleri.
   Kurulum yapmadan üretilebilen taşınabilir başlatma/kısayol dosyası.
7. QtTest/offscreen sentetik uçtan uca, kes/kapat/aç/devam, CLI eşitliği, model yokluğu,
   duyarlılık ve katman testleri; mümkünse yerel Linux oturumunda otomatik duman denemesi.
8. Tam takım + M09 raporu + ayrı yerel commit'ler; PDF/rapor dışa aktarma M10'da kalır.
