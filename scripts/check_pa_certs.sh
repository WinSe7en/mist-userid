#!/usr/bin/env bash
# Check PA management certificate expiry.
#
# Modes:
#   check_pa_certs.sh [threshold_days]
#       Check every host in PA_TARGETS (/etc/mist-userid/env).
#       Prints OK/WARNING/CRITICAL per host; exits non-zero if any cert
#       is unreadable, expired, or expires within threshold (default 30).
#       Run daily via mist-userid-certcheck.timer; warnings land in
#       journald (and any syslog forwarding, e.g. StellarCyber).
#
#   check_pa_certs.sh days <host>
#       Print days remaining for one host (for Zabbix UserParameter).
#       Prints -1 if the certificate cannot be read.
set -uo pipefail

ENV_FILE="/etc/mist-userid/env"

cert_days() {
    local host="$1"
    local enddate end_epoch now_epoch
    enddate=$(echo | timeout 10 openssl s_client -connect "${host}:443" \
        -servername "$host" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    [ -n "$enddate" ] || return 1
    end_epoch=$(date -d "$enddate" +%s) || return 1
    now_epoch=$(date +%s)
    echo $(( (end_epoch - now_epoch) / 86400 ))
}

if [ "${1:-}" = "days" ]; then
    days=$(cert_days "${2:?usage: check_pa_certs.sh days <host>}") || { echo -1; exit 0; }
    echo "$days"
    exit 0
fi

THRESHOLD="${1:-30}"
targets=$(grep -E '^PA_TARGETS=' "$ENV_FILE" | cut -d= -f2- | tr ',' ' ')
if [ -z "$targets" ]; then
    echo "ERROR: no PA_TARGETS found in $ENV_FILE" >&2
    exit 2
fi

rc=0
for url in $targets; do
    host="${url#http://}"; host="${host#https://}"; host="${host%%/*}"
    if ! days=$(cert_days "$host"); then
        echo "ERROR: could not read certificate from $host" >&2
        rc=1
        continue
    fi
    if [ "$days" -lt 0 ]; then
        echo "CRITICAL: certificate for $host EXPIRED $(( -days )) days ago" >&2
        rc=1
    elif [ "$days" -lt "$THRESHOLD" ]; then
        echo "WARNING: certificate for $host expires in $days days" >&2
        rc=1
    else
        echo "OK: $host certificate valid for $days days"
    fi
done
exit $rc
