import json
import sys
from datetime import datetime, timedelta

import pandas as pd
from market_data_adapter import quote_history


def main():
    symbol = sys.argv[1].upper()
    if len(sys.argv) >= 7:
        start, end, output_path, source, interval = sys.argv[2:7]
    else:
        days = int(sys.argv[2])
        output_path = sys.argv[3]
        source, interval = "VCI", "1D"
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        df = quote_history(symbol, source, start=start, end=end, interval=interval)
        if df is None or len(df) == 0:
            rows = []
        else:
            df = df.copy()
            df["time"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d")
            rows = df.sort_values("time").to_dict(orient="records")
        payload = {"status": "ok", "rows": rows}
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
