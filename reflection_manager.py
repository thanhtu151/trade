import json
import os
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "prediction_history.json")


class ReflectionManager:
    """
    Tracks prior decisions and builds compact context for AI prompts.
    Existing prediction_history.json fields are preserved; new metadata is additive.
    """

    def __init__(self, history_file=HISTORY_FILE):
        self.history_file = history_file

    def _load_history(self):
        if not os.path.exists(self.history_file):
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_history(self, rows):
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_dt(value):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value or ""), fmt)
            except ValueError:
                continue
        return datetime.min

    @staticmethod
    def _direction(action):
        text = str(action or "").upper()
        if "MUA" in text or "BUY" in text:
            return 1
        if "BÁN" in text or "BAN" in text or "SELL" in text:
            return -1
        return 0

    def get_recent_performance(self, ticker, n=5):
        ticker = str(ticker or "").upper()
        rows = [
            row for row in self._load_history()
            if str(row.get("symbol", "")).upper() == ticker
        ]
        rows = sorted(rows, key=lambda row: self._parse_dt(row.get("date")))[-n:]
        evaluated = [row for row in rows if row.get("correct") is not None]
        if not rows:
            return {
                "summary": "Chưa có lịch sử dự đoán cho mã này.",
                "accuracy": None,
                "count": 0,
            }

        returns = []
        best = None
        worst = None
        for row in evaluated:
            entry = self._safe_float(row.get("price_at_prediction"))
            actual = self._safe_float(row.get("actual_price"))
            direction = self._direction(row.get("prediction"))
            if entry <= 0 or actual <= 0:
                continue
            raw_return = (actual - entry) / entry * 100
            signed_return = raw_return * direction if direction else -abs(raw_return)
            returns.append(signed_return)
            label = f"{ticker} {signed_return:+.1f}%"
            if best is None or signed_return > best[0]:
                best = (signed_return, label)
            if worst is None or signed_return < worst[0]:
                worst = (signed_return, label)

        correct = sum(1 for row in evaluated if row.get("correct"))
        total = len(evaluated)
        accuracy = correct / total * 100 if total else None
        avg_return = sum(returns) / len(returns) if returns else 0.0
        summary = (
            f"{correct}/{total} đúng chiều, avg return {avg_return:+.1f}%, "
            f"best: {(best or (0, '-'))[1]}, worst: {(worst or (0, '-'))[1]}"
            if total
            else f"Có {len(rows)} dự đoán gần đây nhưng chưa đủ dữ liệu chấm kết quả."
        )
        return {"summary": summary, "accuracy": accuracy, "count": total}

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(str(value or "").replace(",", "").strip())
        except (TypeError, ValueError):
            return default

    def build_reflection_context(self, ticker):
        perf = self.get_recent_performance(ticker)
        risk_note = ""
        if perf.get("accuracy") is not None and perf["accuracy"] < 40:
            risk_note = "Accuracy lịch sử dưới 40%; giảm confidence và ưu tiên HOLD nếu tín hiệu không rõ."
        return (
            f"LỊCH SỬ DỰ ĐOÁN GẦN NHẤT cho {str(ticker).upper()}:\n"
            f"{perf['summary']}\n"
            f"{risk_note}\n"
            "Nếu không đủ dữ liệu hoặc tín hiệu mâu thuẫn, trả confidence thấp hơn 40."
        )

    def log_prediction(self, ticker, action, price, target, stoploss, confidence, **extra):
        rows = self._load_history()
        row = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "symbol": str(ticker).upper(),
            "price_at_prediction": price,
            "prediction": action,
            "target": target,
            "stoploss": stoploss,
            "timeframe": extra.pop("timeframe", "N/A"),
            "confidence": confidence,
            "actual_price": None,
            "correct": None,
        }
        row.update(extra)
        rows.append(row)
        self._save_history(rows)
        return row

    def update_outcome(self, ticker, prediction_date, actual_price):
        rows = self._load_history()
        ticker = str(ticker or "").upper()
        updated = None
        for row in rows:
            if str(row.get("symbol", "")).upper() != ticker:
                continue
            if str(row.get("date", "")) != str(prediction_date):
                continue
            entry = self._safe_float(row.get("price_at_prediction"))
            if entry <= 0:
                continue
            direction = self._direction(row.get("prediction"))
            change = (float(actual_price) - entry) / entry
            row["actual_price"] = float(actual_price)
            row["evaluated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            if direction > 0:
                row["correct"] = change > 0
            elif direction < 0:
                row["correct"] = change < 0
            else:
                row["correct"] = abs(change) < 0.03
            row["pnl_pct"] = round(change * direction * 100 if direction else -abs(change) * 100, 2)
            updated = row
            break
        if updated:
            self._save_history(rows)
        return updated
