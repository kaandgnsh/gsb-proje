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
