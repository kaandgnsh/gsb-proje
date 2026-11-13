# shellcheck shell=bash
# Local Assessment AI — ortak kabuk yardımcıları. Doğrudan çalıştırılmaz.
# Yalnız bash yerleşik komutlarını kullanır; tüm mantık Python'daki lai_setup içindedir.
set -euo pipefail

LAI_REPO="${BASH_SOURCE[0]%/*}"
[[ "$LAI_REPO" == "${BASH_SOURCE[0]}" ]] && LAI_REPO="."
LAI_REPO="$(cd "$LAI_REPO" && pwd)"

lai_err() { printf '%s\n' "$*" >&2; }

lai_find_python() {
    local c
    for c in python3 python3.14 python3.13 python3.12 python3.11 python; do
        if command -v "$c" >/dev/null 2>&1 &&
           "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            command -v "$c"
            return 0
        fi
    done
    return 1
}

lai_python_hint() {
    local id="" like="" key val
    if [[ -r /etc/os-release ]]; then
        while IFS='=' read -r key val; do
            val="${val%\"}"; val="${val#\"}"
            case "$key" in ID) id="$val" ;; ID_LIKE) like="$val" ;; esac
        done < /etc/os-release
    fi
    case " $id $like " in
        *" arch "*)                 echo "pacman -S --needed python" ;;
        *" debian "*|*" ubuntu "*)  echo "apt-get install -y python3 python3-venv" ;;
        *" fedora "*|*" rhel "*)    echo "dnf install -y python3" ;;
        *)                          echo "" ;;
    esac
}

lai_main() {
    local cmd="$1"; shift
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        lai_err "Bu betik root olarak çalıştırılmamalı: kurulum kullanıcı düzeyindedir."
        lai_err "Yönetici yetkisi gerekirse betik nedenini açıklayıp ayrıca onay ister."
        exit 2
    fi
    local py
    if ! py="$(lai_find_python)"; then
        lai_err "Python 3.11 veya üzeri bulunamadı; kurulum motoru çalışamaz."
        local hint; hint="$(lai_python_hint)"
        if [[ -n "$hint" ]]; then
            lai_err "Kurmak için (yönetici yetkisi gerekir): pkexec $hint"
            if [[ -t 0 ]] && command -v pkexec >/dev/null 2>&1; then
                local ans
                read -r -p "Bu komut şimdi çalıştırılsın mı? [e/H]: " ans
                if [[ "$ans" == "e" || "$ans" == "evet" ]]; then
                    # shellcheck disable=SC2086  # hint kelimelere bölünmeli
                    pkexec $hint && py="$(lai_find_python)" || exit 127
                fi
            fi
        fi
        [[ -n "${py:-}" ]] || exit 127
    fi
    export PYTHONPATH="$LAI_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
    exec "$py" -m lai_setup "$cmd" "$@"
}
