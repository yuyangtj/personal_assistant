#!/usr/bin/env bash
# Disk and backup housekeeping for scripts/redeploy-remote.sh. Sourced, not run; kept apart
# so it can be tested (tests/test_housekeeping.py).

# prune_backups DIRECTORY NEWEST_TO_KEEP CUTOFF_DAY
#
# Deletes old database dumps (assistant-YYYYMMDDTHHMMSSZ.dump). Keeps the NEWEST_TO_KEEP
# newest dumps, and also the last dump of every day from CUTOFF_DAY (YYYYMMDD) on, so a
# day with many deploys doesn't push out the dumps of the days before it.
prune_backups() {
    local directory="$1" newest_to_keep="$2" cutoff_day="$3" doomed
    [[ -d "${directory}" ]] || return 0
    # Names sort chronologically; newest first, so the first dump seen for a day is its last.
    find "${directory}" -maxdepth 1 -type f -name 'assistant-*.dump' | sort -r | awk \
        -v newest="${newest_to_keep}" -v cutoff="${cutoff_day}" '
        {
            name = $0
            sub(".*/assistant-", "", name)
            day = substr(name, 1, 8)
            if (NR <= newest) { seen[day] = 1; next }
            if (day >= cutoff && !(day in seen)) { seen[day] = 1; next }
            print
        }' | while IFS= read -r doomed; do
        rm -f -- "${doomed}"
    done
}

# prune_build_cache
#
# Docker keeps every layer it ever built, and each deploy adds more (18 GB after three
# weeks). Drops the cache not used for 3 days, or for 6 hours when the disk is over 85%
# full. Never fails a deployment.
prune_build_cache() {
    local used until_hours=72
    used="$(df --output=pcent / 2>/dev/null | tail -n 1 | tr -dc '0-9')"
    if [[ -n "${used}" && "${used}" -ge 85 ]]; then
        until_hours=6
    fi
    echo "Removing build cache unused for ${until_hours} hours (disk ${used:-?}% full)..."
    docker builder prune --force --filter "until=${until_hours}h" 2>&1 | tail -n 1 || true
}
