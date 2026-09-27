import json
import socket


class Response:
    status = 204


def test_no_secret_is_silent_and_does_not_send(monkeypatch):
    import notify

    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("sent")))
    assert notify.send_embed("title", "body") is False


def test_http_500_and_timeout_are_retried_but_never_raise(monkeypatch):
    import notify

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/secret-token")
    calls = []

    def fail_500(*_args, **_kwargs):
        calls.append(1)
        raise notify.urllib.error.HTTPError("redacted", 500, "failure", {}, None)

    assert notify.send_embed("title", "body", opener=fail_500, sleep=lambda _n: None) is False
    assert len(calls) == 2
    calls.clear()

    def timeout(*_args, **_kwargs):
        calls.append(1)
        raise socket.timeout("slow")

    assert notify.send_embed("title", "body", opener=timeout, sleep=lambda _n: None) is False
    assert len(calls) == 2


def test_429_honors_retry_after(monkeypatch):
    import notify

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/token")
    waits = []
    calls = []

    class RateLimit(Exception):
        code = 429
        def read(self):
            return b'{"retry_after": 2.5}'

    def opener(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RateLimit()
        return Response()

    assert notify.send_embed("title", "body", opener=opener, sleep=waits.append) is True
    assert waits == [2.5]


def test_fingerprint_deduplicates_successful_delivery(monkeypatch, tmp_path):
    import notify

    calls = []
    monkeypatch.setattr(notify, "send_embed", lambda *_a, **_k: calls.append(1) or True)
    assert notify.send_once("same", "title", "body", base_dir=tmp_path) is True
    assert notify.send_once("same", "title", "body", base_dir=tmp_path) is False
    assert len(calls) == 1


def test_eod_summary_formats_vnd_and_operational_state(tmp_path):
    import notify

    (tmp_path / "paper_portfolio.json").write_text(json.dumps({
        "cash": 34_898_105,
        "positions": {"STB": {"qty": 268.3, "current_price": 76_900, "market_value": 20_630_270}},
    }), encoding="utf-8")
    (tmp_path / "paper_trades.json").write_text("[]", encoding="utf-8")
    (tmp_path / "system_status.json").write_text(json.dumps({"tasks": {"eod": {"state": "success"}}}), encoding="utf-8")
    (tmp_path / "self_healing_state.json").write_text(json.dumps({"trading_allowed": False, "critical": ["kill switch"]}), encoding="utf-8")
    text = notify.build_eod_summary(tmp_path)
    assert "Cash: 34,898,105 VND" in text
    assert "Total assets: 55,528,375 VND" in text
    assert "Positions: 1" in text
    assert "eod:success" in text
    assert "Trading allowed: False" in text


def test_trade_embed_contains_sell_pnl_in_vnd(monkeypatch):
    import notify

    captured = {}
    monkeypatch.setattr(notify, "send_embed", lambda title, description, level, fields: captured.update(
        title=title, description=description, level=level, fields=dict(fields)
    ) or True)
    assert notify.notify_trade("STB", "SELL", 268.3, 76_900, pnl=26_830) is True
    assert captured["fields"]["Quantity"] == "268.3"
    assert captured["fields"]["Price"] == "76,900 VND"
    assert captured["fields"]["Value"] == "20,632,270 VND"
    assert captured["fields"]["PnL"] == "26,830 VND"
