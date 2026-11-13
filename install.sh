#!/usr/bin/env bash
# Local Assessment AI — install. Ayrıntı: ./install.sh --help
# shellcheck source=_common.sh
. "${BASH_SOURCE[0]%/*}/_common.sh"
lai_main install "$@"
