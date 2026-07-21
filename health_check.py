import json, os
from datetime import date

checks = []

with open('paper_portfolio.json', encoding='utf-8') as f:
    p = json.load(f)
checks.append(('Portfolio', bool(p.get('positions')), str(len(p.get('positions',{}))) + ' positions'))

with open('analysis_results.json', encoding='utf-8') as f:
    ar = json.load(f)
checks.append(('Analysis today', ar.get('date') == date.today().isoformat(), ar.get('method')))

with open('scheduler_state.json', encoding='utf-8') as f:
    s = json.load(f)
today = date.today().isoformat()
for task in ['morning_prep', 'market_analysis', 'auto_trade']:
    checks.append(('Scheduler ' + task, s.get(task) == today, s.get(task, 'not run')))

with open('llm_router_usage.json', encoding='utf-8') as f:
    lr = json.load(f)
checks.append(('LLM Router', lr.get('fail_calls', 0) == 0, 'gateway=' + str(lr.get('gateway_calls',0))))

print('=== DASHBOARD HEALTH CHECK ===')
for name, ok, detail in checks:
    status = 'OK' if ok else 'FAIL'
    print(status, name, detail)
