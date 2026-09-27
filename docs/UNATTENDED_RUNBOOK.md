# Unattended scheduler runbook

The watchdog runs every 15 minutes and adds a mandatory 20-minute allowance to
every scheduler SLA because GitHub cron execution is not exact. It reads
`system_status.json` directly from the `state` branch and never writes that
branch. It dispatches at most two missed tasks per pass and will only dispatch
`trade` during 09:15–11:25 or 13:00–14:25 ICT.

Three consecutive failures or a task stuck in `running` for more than 110
minutes opens or updates one GitHub Issue and sets repository variable
`TRADING_ENABLED=false`. Re-enable it only after resolving the issue and
reviewing self-healing state.

GitHub may automatically disable scheduled workflows after prolonged repository
inactivity. If runs disappear, inspect the Actions page, re-enable both
`scheduler.yml` and `watchdog.yml`, then use workflow dispatch for required
catch-up. Never run a missed trade outside the allowed trading sessions.

State pushes perform fetch-and-compare against the exact SHA loaded by the run.
Transient push failures retry with a bound; any concurrent state change fails
without merge and is surfaced to the watchdog.
