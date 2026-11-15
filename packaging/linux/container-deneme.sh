#!/usr/bin/env bash
# Local Assessment AI — AppImage'ı ağsız, temiz bir Arch container'ında deneme modunda çalıştırır.
# Kullanım: packaging/linux/container-deneme.sh <AppImage> [--deneme seçenekleri]
#   Örnek: packaging/linux/container-deneme.sh dist/lai-0.12.1-linux-x86_64.AppImage --yalniz-tani
# Ortam: LAI_CONTAINER_KOK (vars. ~/.cache/gsb-proje/container),
#        LAI_OLLAMA_KAYNAK (vars. /var/lib/ollama), LAI_CONTAINER_BELLEK (vars. 8g).
# İmaj yalnız ilk seferde ağ ile oluşturulur; deneme --network=none ile çalışır. Ollama ikilisi ve
# model deposu salt okunur bağlanır; sistem Ollama servisine erişim yoktur.
set -euo pipefail

appimage=${1:?AppImage yolu gerekli}
shift
appimage=$(realpath "$appimage")
kok=${LAI_CONTAINER_KOK:-$HOME/.cache/gsb-proje/container}
kaynak=${LAI_OLLAMA_KAYNAK:-/var/lib/ollama}
imaj=lai-temiz-arch

command -v podman >/dev/null || { echo "podman gerekli" >&2; exit 2; }
ollama=$(command -v ollama) || { echo "ollama ikilisi gerekli (sistemde kurulu olmalı)" >&2; exit 2; }

if ! podman image exists "$imaj"; then
    echo "Temiz Arch imajı oluşturuluyor (yalnız bu adım ağ kullanır)..."
    podman build --network=host -t "$imaj" -f - . <<'EOF'
FROM docker.io/library/archlinux:latest
RUN pacman -Syu --noconfirm --needed bubblewrap libseccomp wayland libxkbcommon fontconfig \
    ttf-dejavu mesa nss libxcomposite libxdamage libxrandr libxtst libxkbfile alsa-lib libdrm \
    libglvnd dbus xcb-util-cursor && pacman -Scc --noconfirm
RUN useradd -m -u 1000 deneme
USER deneme
WORKDIR /home/deneme
EOF
fi

mkdir -p "$kok/home"
gui=()
if [[ -n ${WAYLAND_DISPLAY:-} && -S ${XDG_RUNTIME_DIR:-}/$WAYLAND_DISPLAY && " $* " != *" --yalniz-tani "* ]]; then
    gui=(-e "WAYLAND_DISPLAY=$WAYLAND_DISPLAY" -e XDG_RUNTIME_DIR=/run/user/1000
         -e QT_QPA_PLATFORM=wayland
         -v "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY:/run/user/1000/$WAYLAND_DISPLAY")
fi

exec podman run --rm --network=none --memory="${LAI_CONTAINER_BELLEK:-8g}" \
    --security-opt seccomp=unconfined --security-opt label=disable --security-opt unmask=ALL \
    --userns=keep-id -e APPIMAGE_EXTRACT_AND_RUN=1 -e HOME=/home/deneme \
    -v "$kok/home:/home/deneme" -v "$appimage:/opt/lai.AppImage:ro" \
    -v "$kaynak:/ollama-kaynak:ro" -v "$ollama:/usr/bin/ollama:ro" \
    -v /usr/lib/ollama:/usr/lib/ollama:ro "${gui[@]}" \
    "$imaj" /opt/lai.AppImage --deneme --kaynak /ollama-kaynak "$@"
