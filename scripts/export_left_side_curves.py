#!/usr/bin/env python3
"""從本機研究樹 /workspace/research/left-side-fix 的引擎，匯出 NDX .com、SPX 2008（跌 10% 起點）三條帳戶淨值曲線。
網站頁面讀的是產出檔 trend/left-side-fix/curves.json，部署不依賴研究樹。
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RES = Path("/workspace/research/left-side-fix")
sys.path.insert(0, str(RES))
import engine as E
import run_all as R

OUT = Path("/workspace/tmp-publish/charity-poker-king/trend/left-side-fix/curves.json")

# 與 run_all.CRASH 相同的高點搜尋窗，起點用「跌 10% 才開始」，持有 15 年
WANTED = [
    ("NDX", "1999-06-01", "2000-12-31", "NDX .com泡沫"),
    ("SPX", "2007-01-01", "2008-06-30", "SPX 2008金融海嘯"),
]
STRATS = [
    ("BH_一次買滿", "一次買滿"),
    ("S0_原計畫後抱", "原計畫"),
    ("FIN_標準(R1+R2+閒置定額)", "標準版"),
]
H = 252 * 15


def downsample(dates, vals, mins_idx):
    """月底 + 起點、最低點、終點，避免 JSON 太大。"""
    idx = pd.DatetimeIndex(dates)
    keep = set()
    keep.add(0)
    keep.add(len(vals) - 1)
    keep.add(int(mins_idx))
    # month ends
    s = pd.Series(np.arange(len(vals)), index=idx)
    for i in s.resample("ME").last().dropna().astype(int):
        keep.add(int(i))
    order = sorted(keep)
    out_d, out_v = [], []
    for i in order:
        out_d.append(idx[i].strftime("%Y-%m-%d"))
        out_v.append(round(float(vals[i]), 6))
    return out_d, out_v


def main():
    print("loading sims...")
    # 只要 NDX、SPX，避免載入全部
    sims = {n: E.Sim(n) for n in ["NDX", "SPX"]}
    crashes = []
    for name, a, b, lab in WANTED:
        s = sims[name]
        c = s.c
        w = c[(c.index >= a) & (c.index <= b)]
        ipk = c.index.get_loc(w.idxmax())
        i0 = R.first_dd(c, ipk, 0.10)
        assert i0 is not None
        series = []
        for sn, label in STRATS:
            sp = R.STRATS[sn]
            vals, dep, ce = s.run(i0, H, sp)
            imin = int(np.argmin(vals))
            dates = c.index[i0:i0 + len(vals)]
            d, v = downsample(dates, vals, imin)
            m = E.metrics(vals, None, cash_end=ce)
            rec = {
                "id": sn,
                "label": label,
                "dates": d,
                "values": v,
                "final": float(vals[-1]),
                "min_vs_capital": float(vals.min() - 1),
                "min_date": dates[imin].strftime("%Y-%m-%d"),
                "start_date": dates[0].strftime("%Y-%m-%d"),
                "end_date": dates[-1].strftime("%Y-%m-%d"),
                "years": round((len(vals) - 1) / 252, 2),
            }
            # 回本（淨值重新 >= 1）
            if vals.min() < 1:
                after = np.where(vals[imin:] >= 1)[0]
                rec["back_to_1"] = dates[imin + int(after[0])].strftime("%Y-%m-%d") if len(after) else None
            else:
                rec["back_to_1"] = dates[0].strftime("%Y-%m-%d")
            series.append(rec)
            print(f"  {lab} {label}: start={rec['start_date']} min={rec['min_vs_capital']:.4f} on {rec['min_date']} final={rec['final']:.4f} points={len(d)}")
        crashes.append({
            "id": name,
            "label": lab,
            "start_mode": "跌10%才開始",
            "peak_date": c.index[ipk].strftime("%Y-%m-%d"),
            "peak_close": float(c.iloc[ipk]),
            "start_date": c.index[i0].strftime("%Y-%m-%d"),
            "start_close": float(c.iloc[i0]),
            "series": series,
        })
    data = {
        "source": "workspace/research/left-side-fix engine.py / run_all.py",
        "generated_by": "scripts/export_left_side_curves.py（一次性從研究引擎匯出）",
        "data_end": E.DATA_END,
        "note": "帳戶淨值以本金=1。起點是該次崩盤高點之後，收盤首次跌 10% 的那天。持有最多 15 年或到資料結束。曲線取每月月底，並保留起點、各策略最低點與終點。",
        "crashes": crashes,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    OUT.write_text(raw + "\n", encoding="utf-8")
    js = OUT.with_suffix(".js")
    js.write_text("window.LEFT_SIDE_CURVES = " + raw + ";\n", encoding="utf-8")
    print("wrote", OUT, "bytes", OUT.stat().st_size)

if __name__ == "__main__":
    main()
