import datetime as dt
from pathlib import Path
import pandas as pd
import pytest

from setups import btst_rally_score, position_size
from tracker import evaluate_signals_chronologically, log_signals


def test_position_size_respects_lot_and_positive_qty():
    res = position_size(entry=500.0, stop_loss=490.0, lot_size=25)
    assert isinstance(res, dict)
    assert int(res.get("qty") or 0) >= 25
    assert int(res["qty"]) % 25 == 0
    assert float(res.get("max_risk") or 0.0) > 0.0


def test_position_size_handles_zero_stop_loss():
    res = position_size(entry=250.0, stop_loss=0.0, lot_size=10)
    assert int(res.get("qty") or 0) >= 10
    assert float(res.get("exposure") or 0.0) >= 2500.0


def test_btst_rally_score_rejects_rsi_above_80():
    row = {
        "close": 1000.0,
        "entry": 1000.0,
        "atr": 20.0,
        "day_low": 982.0,
        "close_pos_ratio": 0.9,
        "vol_ratio": 1.8,
        "ema20": 980.0,
        "ema50": 950.0,
        "ret_5d_pct": 4.0,
        "rsi": 84.5,
    }
    assert btst_rally_score(row, nifty_5d_pct=1.0, sector_chg_pct=0.8) is None


def test_btst_rally_score_computes_targets_and_sl():
    dates = pd.date_range("2026-10-01", periods=25, freq="D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 25,
            "High": [105.0] * 24 + [112.0],
            "Low": [98.0] * 24 + [101.0],
            "Close": [102.0] * 24 + [111.0],
            "Volume": [100000] * 24 + [200000],
        },
        index=dates,
    )
    row = {
        "close": 111.0,
        "entry": 111.0,
        "atr": 16.0,
        "day_low": 101.0,
        "close_pos_ratio": 0.91,
        "vol_ratio": 2.0,
        "ema20": 106.0,
        "ema50": 101.0,
        "ret_5d_pct": 5.2,
        "rsi": 62.0,
    }
    out = btst_rally_score(row, df=df, nifty_5d_pct=1.5, sector_chg_pct=1.2)
    assert out is not None
    assert 0.0 <= float(out["btst_score"]) <= 100.0
    assert float(out["target_1"]) == pytest.approx(111.0 + 0.5 * 16.0)
    assert float(out["target_2"]) == pytest.approx(111.0 + 0.8 * 16.0)
    assert float(out["stop_loss"]) == pytest.approx(max(111.0 - 16.0, 101.0))


def test_log_signals_skips_duplicate_open_signal(tmp_path: Path):
    log_file = tmp_path / "signals_log.csv"
    payload = {
        "mode": "BTST",
        "index_trades": [],
        "options_bullish": [],
        "options_bearish": [],
        "equity_intraday": [
            {
                "symbol": "RELIANCE",
                "sector": "ENERGY",
                "side": "BULLISH",
                "entry": 1500.0,
                "stop_loss": 1475.0,
                "target_1": 1530.0,
                "target_2": 1560.0,
                "qty": 10,
                "max_risk": 250.0,
            }
        ],
    }
    r1 = log_signals(payload, path=log_file)
    r2 = log_signals(payload, path=log_file)
    assert r1["added"] == 1
    assert r2["added"] == 0
    assert r2["skipped_duplicates"] == 1


def test_evaluate_signals_chronologically_closes_sl_and_time_stop(tmp_path: Path):
    log_file = tmp_path / "signals_log.csv"
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    today_str = dt.datetime.now(ist).date().isoformat()
    past_day = (dt.datetime.now(ist).date() - dt.timedelta(days=2)).isoformat()
    next_day = (dt.datetime.now(ist).date() - dt.timedelta(days=1)).isoformat()

    log_signals(
        {
            "mode": "BTST",
            "index_trades": [],
            "options_bullish": [],
            "options_bearish": [],
            "equity_intraday": [
                {
                    "symbol": "TCS",
                    "sector": "IT",
                    "side": "BULLISH",
                    "entry": 4000.0,
                    "stop_loss": 3950.0,
                    "target_1": 4060.0,
                    "target_2": 4120.0,
                    "qty": 5,
                    "max_risk": 250.0,
                }
            ],
        },
        path=log_file,
    )

    txt = log_file.read_text(encoding="utf-8")
    txt = txt.replace(today_str, past_day)
    log_file.write_text(txt, encoding="utf-8")

    candles = {
        "TCS": pd.DataFrame(
            {
                "Open": [3990.0, 4010.0],
                "High": [4020.0, 4070.0],
                "Low": [3980.0, 4000.0],
                "Close": [4010.0, 4065.0],
                "Volume": [500000, 600000],
            },
            index=pd.to_datetime([past_day, next_day]),
        )
    }

    summary = evaluate_signals_chronologically(candles_by_symbol=candles, path=log_file)
    assert summary["closed_now"] == 1
    assert "CLOSED_TP1" in log_file.read_text(encoding="utf-8")
