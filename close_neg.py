import json
from data_fetcher import get_stock_data_cached
from datetime import datetime

with open('paper_portfolio.json', encoding='utf-8') as f:
    p = json.load(f)

positions = p.get('positions', {})
negative_ev = ['VIC', 'VHM', 'VRE']

for ticker in negative_ev:
    if ticker in positions:
        pos = positions[ticker]
        df = get_stock_data_cached(ticker, years=0.1)
        price = float(df['close'].iloc[-1])
        qty = pos.get('qty', 0)
        avg_price = pos.get('avg_price', price)
        pnl = (price - avg_price) * qty
        p['cash'] = p.get('cash', 0) + price * qty
        del positions[ticker]
        print('Closed', ticker, qty, 'cp @', price, 'PnL:', pnl)

p['positions'] = positions
p['updated_at'] = datetime.now().isoformat()

with open('paper_portfolio.json', 'w', encoding='utf-8') as f:
    json.dump(p, f, ensure_ascii=False, indent=2)

print('Cash:', p['cash'])
print('Con lai:', list(positions.keys()))
