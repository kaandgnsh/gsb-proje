#!/usr/bin/env bash
# Local Assessment AI — repair. Ayrıntı: ./repair.sh --help
# shellcheck source=_common.sh
. "${BASH_SOURCE[0]%/*}/_common.sh"
lai_main repair "$@"
