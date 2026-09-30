#!/bin/bash
# Daily paper run for the ETF core (PaperBroker only, never live, never --reset).
#   scripts/paper_daily.sh            # today, Vietnam time
#   PAPER_DATE=2026-09-30 scripts/paper_daily.sh
# Skips Sat/Sun; if data_fetcher has no bar for the date it logs "no session" and exits 0.
# Any failure -> exit != 0 with the reason in paper_logs/daily_<date>.log.
# Idempotent: paper_run.py skips a date already processed and summary.csv keeps one row per date.

export TZ=Asia/Ho_Chi_Minh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 3

DAY="${PAPER_DATE:-$(date +%F)}"
DOW="$(date -j -f %F "$DAY" +%u 2>/dev/null || date -d "$DAY" +%u)"
mkdir -p paper_logs
LOG="paper_logs/daily_${DAY}.log"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

if [ "$DOW" -ge 6 ]; then
  log "weekend, skip"
  exit 0
fi

# repo venv: this checkout's, or the main checkout's when running from a worktree
PY=""
for cand in "$ROOT/.venv/bin/python" "$ROOT/../../.venv/bin/python"; do
  [ -x "$cand" ] && PY="$cand" && break
done
if [ -z "$PY" ]; then
  log "ERROR: no .venv/bin/python found"
  echo "paper_daily: no .venv/bin/python found" >&2
  exit 4
fi

log "start date=$DAY python=$PY"
"$PY" paper_run.py --date "$DAY" --source data_fetcher --require-exact \
  --summary-csv paper_logs/summary.csv >> "$LOG" 2>>"$LOG"
RC=$?
if [ "$RC" -ne 0 ]; then
  log "ERROR: paper_run.py exit=$RC (see lines above)"
  echo "paper_daily: paper_run.py failed exit=$RC, see $LOG" >&2
  exit "$RC"
fi
log "done"
exit 0
