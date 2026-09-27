# Unattended scheduler runbook

The watchdog runs every 15 minutes and adds a mandatory 20-minute allowance to
every scheduler SLA because GitHub cron execution is not exact. It reads
`system_status.json` directly from the `state` branch and never writes that
branch. It dispatches at most two missed tasks per pass and will only dispatch
`trade` during 09:15–11:25 or 13:00–14:25 ICT.

Three consecutive failures or a task stuck in `running` for more than 110
minutes opens or updates one GitHub Issue. Only an `analysis` or `trade`
escalation also dispatches `disable-trading`, which persists
`trading_disabled.json` on the `state` branch. Issue handling runs first and
both operations use `continue-on-error`, so either can succeed independently.

To reopen trading after resolving the incident and reviewing self-healing,
manually dispatch `scheduler.yml` with task `enable-trading`, confirmation
`ENABLE_TRADING`, and an audit reason. This removes `trading_disabled.json` and
appends an event to `trading_switch_audit.json`; never delete the flag by an
unreviewed state-branch edit.

Each missed task is dispatched at most once per ICT calendar day. The watchdog
checks same-day `workflow_dispatch` runs by the scheduler run name. A task whose
persisted state is `blocked` is neither missed nor eligible for catch-up. If
`system_status.json` is absent, corrupt, or has no task records, the watchdog
dispatches nothing and opens/updates the `watchdog cannot read status` Issue.

GitHub may automatically disable scheduled workflows after prolonged repository
inactivity. If runs disappear, inspect the Actions page, re-enable both
`scheduler.yml` and `watchdog.yml`, then use workflow dispatch for required
catch-up. Never run a missed trade outside the allowed trading sessions.

State pushes perform fetch-and-compare against the exact SHA loaded by the run.
Transient push failures retry with a bound; any concurrent state change fails
without merge and is surfaced to the watchdog.
