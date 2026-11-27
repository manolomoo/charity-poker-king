import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("index_quotes", os.path.join(ROOT, "scripts", "index_quotes.py"))
iq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iq)


def cnbc(**kw):
    q = {"symbol": ".SPX", "code": "0", "last": "7,182.78", "change": "-18.31", "change_pct": "-0.25%",
         "previous_day_closing": "7,201.09", "realTime": "true", "last_time": "2026-11-27T13:33:15.000+0800"}
    q.update(kw)
    return q


def test_cnbc_commas_and_iso_time():
    it = iq.parse_cnbc_item(cnbc(), "SPX", "S&P 500")
    assert it["last"] == 7182.78 and it["change"] == -18.31 and it["change_pct"] == -0.25
    assert it["prev_close"] == 7201.09
    assert it["data_at"] == "2026-11-27T13:33:15+08:00" and it["date_only"] is False
    assert it["realtime"] is True and it["source"] == "CNBC"


def test_cnbc_plus_and_unch():
    it = iq.parse_cnbc_item(cnbc(change="+156.27", change_pct="+0.40%", realTime="false"), "TWII", "加權指數")
    assert it["change"] == 156.27 and it["change_pct"] == 0.4 and it["realtime"] is False
    z = iq.parse_cnbc_item(cnbc(change="UNCH", change_pct="UNCH"), "SPX", "x")
    assert z["change"] == 0.0 and z["change_pct"] == 0.0


def test_cnbc_date_only_and_bad():
    it = iq.parse_cnbc_item(cnbc(last_time="2026-11-25"), "SPX", "x")
    assert it["data_at"] == "2026-11-25" and it["date_only"] is True
    assert iq.parse_cnbc_item(cnbc(code="1"), "SPX", "x") is None
    assert iq.parse_cnbc_item(cnbc(last=""), "SPX", "x") is None


def test_parse_cnbc_payload():
    p = {"FormattedQuoteResult": {"FormattedQuote": [cnbc(), cnbc(symbol=".FOO")]}}
    assert list(iq.parse_cnbc(p)) == ["SPX"]
    assert iq.parse_cnbc({}) == {}


def test_parse_twse():
    p = {"msgArray": [{"z": "39308.08", "y": "39151.81", "d": "20261127", "t": "13:33:00"}]}
    it = iq.parse_twse(p)
    assert it["last"] == 39308.08 and it["change"] == 156.27 and it["change_pct"] == 0.4
    assert it["data_at"] == "2026-11-27T13:33:00+08:00" and it["realtime"] is True
    assert iq.parse_twse({"msgArray": [{"z": "-", "y": "100", "d": "20261127", "t": "09:00:00"}]}) is None
    assert iq.parse_twse({}) is None


def row(sym, last, d="20261127", t="134459"):
    return {"SymbolID": sym, "DispCName": "臺指期126", "CLastPrice": last, "CRefPrice": "39492.00",
            "CDiff": "-129.00", "CDiffRate": "-0.33", "CDate": d, "CTime": t}


def test_pick_taifex_skips_spot_and_empty():
    p = {"RtData": {"QuoteList": [row("TXF-S", "39000.00"), row("TXFL6-F", ""), row("TXFA7-F", "39363.00")]}}
    it = iq.pick_taifex(p)
    assert it["last"] == 39363.0 and it["session"] == "日盤" and it["change"] == -129.0
    assert it["data_at"] == "2026-11-27T13:44:59+08:00" and it["prev_close"] == 39492.0
    assert iq.pick_taifex({"RtData": {"QuoteList": [row("TXF-S", "1")]}}) is None
    assert iq.pick_taifex({}) is None


def test_taifex_day_vs_night_choice():
    day = iq.pick_taifex({"RtData": {"QuoteList": [row("TXF-S", "1"), row("TXFL6-F", "39363.00")]}})
    night = iq.pick_taifex({"RtData": {"QuoteList": [row("TXF-P", "1"), row("TXFL6-M", "39103.00", t="152953")]}}, night=True)
    assert night["session"] == "夜盤"
    assert iq.choose_taifex(day, night)["session"] == "夜盤"
    # 隔天凌晨的夜盤：CDate 仍為日盤日期 → 加一天，仍較新
    late = iq.pick_taifex({"RtData": {"QuoteList": [row("TXF-P", "1"), row("TXFL6-M", "39000.00", t="020000")]}},
                          night=True, day_date="20261127")
    assert late["data_at"] == "2026-11-28T02:00:00+08:00"
    # 早上日盤開盤前，日盤資料仍是昨天，夜盤較新；日盤更新時改選日盤
    old_night = iq.pick_taifex({"RtData": {"QuoteList": [row("TXFL6-M", "39000.00", d="20261126", t="045900")]}}, night=True)
    assert iq.choose_taifex(day, old_night)["session"] == "日盤"
    assert iq.choose_taifex(None, None) is None


def test_build_keeps_old_on_failure():
    import datetime as dt
    old = {"items": [{"key": "TX", "name": "台指期", "last": 1.0}]}

    def boom(*a):
        raise RuntimeError("x")
    d = iq.build(dt.datetime(2026, 11, 27, tzinfo=iq.TPE), old, {"cnbc": boom, "twse": boom, "taifex": boom})
    assert d["stale"] and len(d["errors"]) >= 3
    assert [i["key"] for i in d["items"]] == ["SPX", "NDX", "SOX", "TWII", "TX"]
    assert d["items"][4]["last"] == 1.0 and d["items"][0]["last"] is None
