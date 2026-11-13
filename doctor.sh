#!/usr/bin/env bash
# Local Assessment AI — doctor. Ayrıntı: ./doctor.sh --help
# shellcheck source=_common.sh
. "${BASH_SOURCE[0]%/*}/_common.sh"
lai_main doctor "$@"
