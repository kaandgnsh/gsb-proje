#!/usr/bin/env bash
# Local Assessment AI — uninstall. Ayrıntı: ./uninstall.sh --help
# shellcheck source=_common.sh
. "${BASH_SOURCE[0]%/*}/_common.sh"
lai_main uninstall "$@"
