import json,sys,subprocess,glob,os
import pandas as pd, numpy as np
def sh(p): return subprocess.run(['git','show','origin/state:'+p],capture_output=True,text=True).stdout
t=json.loads(sh('paper_trades.json'))
def stats(r,label):
    r=np.array(r,float); n=len(r)
    if n==0: print(label,'n=0'); return
    w=r[r>0]; l=r[r<0]
    pf=w.sum()/-l.sum() if len(l) and l.sum()<0 else float('nan')
    print(f"{label}: n={n} win={len(w)} loss={len(l)} flat={(r==0).sum()} WR={len(w)/n:.1%} avgW={w.mean() if len(w) else float('nan'):+.2%} avgL={l.mean() if len(l) else float('nan'):+.2%} PF={pf:.2f} exp={r.mean():+.2%} med={np.median(r):+.2%} worst={r.min():+.2%} best={r.max():+.2%}")
rows=[]
for x in t:
    if x.get('side')=='SELL':
        cost=x['value']-x['pnl']
        rows.append(dict(ep=x['epoch_id'],time=x['time'],sym=x['symbol'],pnl=x['pnl'],ret=x['pnl']/cost if cost else np.nan,reason=x['reason'].split(':')[0]))
d=pd.DataFrame(rows)
print('== PAPER (gross, as recorded)')
for ep in (0,1):
    e=d[d.ep==ep]; stats(e.ret,f'epoch{ep} all'); print('  sum pnl VND',e.pnl.sum(), 'PF_vnd', e.pnl[e.pnl>0].sum()/-e.pnl[e.pnl<0].sum())
    print(e.groupby('reason').agg(n=('ret','size'),wins=('ret',lambda s:(s>0).sum()),mean=('ret','mean')).round(4).to_string())
e=d[d.ep==1]
print(e[['time','sym','ret','reason']].round(4).to_string())
ex=e[~((e.sym=='PVD')&(e.time<'2026-07-16'))&~((e.sym=='VPB'))]
stats(ex.ret,'epoch1 excl 2 anomalies(PVD 07-15,VPB 09-24)')
stats(e.ret-0.004,'epoch1 net est. 0.4% roundtrip'); stats(ex.ret-0.004,'epoch1 excl anomalies net est.')
e0=d[(d.ep==0)&(d.pnl!=0)&(d.ret.abs()<0.5)]; stats(e0.ret,'epoch0 nonzero & |ret|<50%')
s=pd.DataFrame(json.loads(sh('portfolio_snapshots.json')));s=s.drop_duplicates('date',keep='last')
print('snapshots',len(s),s.date.iloc[0],s.date.iloc[-1],'equity last',s.equity.iloc[-1])
for a,b,lab in (('2026-06-01','2026-07-09','ep0'),('2026-07-10','2026-12-31','ep1')):
    q=s[(s.date>=a)&(s.date<=b)].equity
    if len(q): print(lab,len(q),'start',q.iloc[0],'end',q.iloc[-1],'ret',q.iloc[-1]/q.iloc[0]-1,'maxDD',(q/q.cummax()-1).min())
pf=json.loads(sh('paper_portfolio.json')); print('open',{k:v.get('pnl_pct') for k,v in pf['positions'].items()},'cash',pf['cash'])
ec=json.loads(sh('etf_core_state.json')); print('etf_core trades',len(ec['trades']),ec['signal'])
print('== BACKTEST trades (net_ret after fees)')
R='research/'
for f,lab in [('t42/trades_breakout_pit.csv','breakout10 PIT FULL 18-23'),('t42/trades_momentum_pit.csv','momentum5p PIT FULL'),('t46/trades_nolu_FULL.csv','L1 breakout no-limit-up'),('t46/trades_etf_FULL.csv','ETF E1 FULL 18-23'),
  ('t51/trades_P1_h20.csv','PEAD P1'),('t51/trades_P2_h40.csv','PEAD P2'),('t51/trades_P3_h60.csv','PEAD P3'),('t51/trades_V1_EP.csv','Value V1'),('t51/trades_V2_BP.csv','Value V2'),('t51/trades_V3_EPBP.csv','Value V3'),
  ('t54/trades_M1_h1.csv','MAX M1'),('t54/trades_M2_h2.csv','MAX M2'),('t55/trades_E1.csv','ETF E1 HOLDOUT 24-26'),('t55/trades_E6.csv','ETF E6 HOLDOUT 24-26')]:
    x=pd.read_csv(R+f); stats(x.net_ret,lab)
    if 'why' in x and len(x)>50: print('   by exit:',{k:(len(g),round((g.net_ret>0).mean(),2),round(g.net_ret.mean(),4)) for k,g in x.groupby('why')})
for f in ['t38/eval_reversion.csv','t38/eval_breakout10.csv','t38/eval_momentum10.csv']:
    x=pd.read_csv(R+f); print(f); print(x[['period','cagr','sharpe','maxdd','n','win','pf','exp_pct']].round(4).to_string())
print(pd.read_csv(R+'t46/summary.csv')[['config','cagr','sharpe','maxdd','n','win','pf','exp_pct']].round(4).to_string())
print(open(R+'t55/holdout_result.json').read()[:1500])
