# Çalışma sırasında ağ güvenliği

M11'in doğrulanan sınırı Linux x86_64 masaüstü giriş noktası ve onun başlattığı
süreçlerdir. Kullanıcı arayüzü, model analizi, belge kütüphaneleri ve uygulamanın
kendi yerel çalışma ortamı aynı zorunlu sınır içindedir. Windows doğrulanmadığı
sürece masaüstü değerlendirme başlatmaz. Ayrıntılı kanıt: [M11](milestones/M11.md).

## Güven sınırı

`python -m lai.ui` Qt ve veritabanını açmadan önce bubblewrap başlatır. Kullanıcı,
ağ, mount, PID ve IPC ad alanları ayrılır; capability'ler düşürülür. Yeni ağ ad
alanında yalnız loopback bulunur. Uygulama ve kendi model süreci birbirine bu
loopback üzerinden bağlanır; ana makinenin loopback proxy/servislerine erişilmez.

Özel `/run` ve `/tmp` oluşturulur. Gerçek kullanıcı HOME'u taşınmaz; HOME ve geçici
alan `/run/lai` içindedir. Uygulama/model deposunun açık yolu korunur. Dosya sistemi
okuma erişimi için görünür, varsayılan salt okunurdur. Yalnız uygulama dizinleri,
DB'nin klasörü ve açık `--write-dir` hedefleri yazılabilir. Sistem config/servis veya
firewall kuralı değiştirilmez.

Güvenilen Wayland görüntü soketi filtre yüklenmeden bağlanır; tek bağlı görüntü FD'si
Qt'ye aktarılır. Normal değerlendirme kodu başlamadan libseccomp/no_new_privs
etkinleştirilir. Yeni AF_UNIX soketleri, AF_VSOCK, AF_PACKET, AF_NETLINK ve INET/INET6
dışındaki bütün socket domainleri reddedilir. INET/INET6 yalnız özel namespace'i
görür. `socketpair(AF_UNIX)` yalnız aynı sınır içindeki işçi iletişimi için açıktır.

Bu yüzden systemd-resolved, D-Bus/portal, agent veya dosya/abstract Unix proxy'lerine
bağlanılamaz; salt okunur bir soket dosyasının görülmesi bağlantı izni sağlamaz.
`setns`, `unshare`, yeni namespace clone bayrakları, mount API'leri, ptrace,
process_vm, pidfd_getfd ve io_uring gibi dolaylı socket/FD edinme yolları kapalıdır.
Yeni user namespace ayrıca bubblewrap tarafından engellenir. Exec/fork/thread bu
kısıtları miras alır; no_new_privs ve sıfır capability ile geri kaldıramaz.

Fazla devralınan FD'ler kapatılır; standart FD'lerde bağlı soket reddedilir.
Proxy, oturum bus'ı, SSH agent, yükleyici ve özel Qt eklenti ortamları taşınmaz.
`OLLAMA_NO_CLOUD=1` ek önlemdir; tek başına güvenlik kanıtı değildir.

## Hata ve süreç sahipliği

Yalıtım önkoşulu/Wayland/binary/namespace/filter kurulumu başarısızsa Türkçe hata
verilir; yalıtımsız tekrar çalıştırma yolu yoktur. Ortam bayrağı tek başına yeterli
değildir: namespace değişimi, yalnız lo, seccomp, no_new_privs, capability ve gerçek
AF_UNIX socket reddi kontrol edilir. Kısayol terminali hata mesajını görünür tutar.

Uygulama yalnız kendi özel depolu alt sürecini yönetir. Port çakışmasında mevcut
dinleyiciye sinyal gönderilmez. Normal kapanış sahiplik kontrolüyle kendi sürecini
durdurur; sınırın üst süreci ölürse bubblewrap/PID namespace çocukları kapatır.
Başka uygulamanın servisine veya depolarına yönetim komutu gönderilmez.

## Kapsam sınırları

Bu, güvenilen Linux çekirdeği/bubblewrap/libseccomp ve güvenilen görüntü sunucusu
üzerinde süreç ağ/IPC yalıtımıdır. Fiziksel saldırı, çekirdek açığı, kötü niyetli
compositor, kullanıcının kaynak kodunu değiştirip sınırı atlaması veya Python API'sini
kendi korumasız sürecinde çağırması için bir koruma iddiası değildir. Görüntü/input,
terminal ve kullanıcının açık dosya aktarımı güvenilen yerel I/O kanallarıdır.

Kullanıcının seçtiği çıktı klasörünü başka bir programın buluta eşitlemesini veya
uzak/FUSE dosya sistemi hizmetinin davranışını bu süreç filtresi yönetmez. Rapor ve
veriler yerel, eşitlenmeyen dosya sisteminde tutulmalıdır. Harici görüntüleyici ve
portal işlemleri ağ sınırına tabi kalır; PDF/XLSX uygulama içi önizleme çalışır.
Dosya sistemi yalıtımı/gizlilik veya tüm bilgisayar için firewall iddia edilmez.

Doğrudan kütüphane/CLI çağrısı kendi başına OS yalıtımı değildir. CLI işlemini aynı
sınırda kullanmak için `python -m lai_runtime.sandbox --write-dir DİZİN -- KOMUT`
kullanılır. Desteklenen masaüstü ve oluşturulan başlatıcı bunu otomatik yapar.
Kurulum motorunun açık çevrimiçi edinme komutları bu çalışma zamanı sınırından ayrıdır.

`doctor --network --pid PID` salt okunur TCP/UDP/Unix soket örneğidir. Kısa
bağlantıları kaçırabilir; `enforcement_verified=false` çıktısı bilerek korunur.
Engelleme kanıtı, pozitif kontrollü [test laboratuvarıdır](milestones/assets/m11-linux/network-proof.json).
