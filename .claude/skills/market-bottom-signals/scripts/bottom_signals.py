#!/usr/bin/env python3
"""폭락장 저점 판별 신호 계산기.

CNN 공포·탐욕 지수의 'Put and Call Options'(5일 평균 풋콜비율)를 Cboe 원자료로 재구성하고,
VIX·지수 가격과 묶어 '투매형 바닥'과 '다이버전스형 바닥' 신호를 계산한다. 하이일드 스프레드로 신용 필터를 건다.

데이터(모두 공개, 헤더 위조 없이 접근):
  - Cboe 풋콜 아카이브 CSV (2006-11 ~ 2019-10-04): equitypc.csv, etppc.csv, totalpc.csv
  - Cboe 일일 통계 JSON (2019-10-07 ~ 현재): cdn.cboe.com/data/us/options/market_statistics/daily/YYYY-MM-DD_daily_options
  - Yahoo Finance 차트 API: 지수(^GSPC, ^IXIC, ^KS11 등)와 ^VIX 일봉(종가·장중 고저)
  - FRED BAMLH0A0HYM2 (ICE BofA 하이일드 OAS, FRED는 최근 약 3년만 제공)

CNN 근사 풋콜 = (주식 풋 + ETP 풋) / (주식 콜 + ETP 콜)의 5일 평균.
  2012-06-11 이전 Cboe 'equity'에는 ETP가 포함돼 있어 그 값을 그대로 쓴다.
  2026년 CNN 차트 역산값과 비교한 오차는 0.01~0.04 수준이었다(11/13 0.799 vs 0.804, 11/20 0.936 vs 0.945).

사용 예:
  python bottom_signals.py --index ^GSPC --start 2011-07-01 --end 2011-10-31
  python bottom_signals.py --index ^KS11 --start 2026-10-01 --ref-low-date 2026-11-06
  python bottom_signals.py --index ^GSPC --start 2025-03-01 --end 2025-04-30 --json out.json
"""
import argparse, csv, datetime as dt, io, json, os, sys, time, urllib.request, subprocess, statistics as st

UA = "Mozilla/5.0"  # Yahoo는 기본 UA를 거부하는 경우가 있어 일반 브라우저 UA 문자열만 지정한다(그 외 헤더 위조 없음)
CBOE_CSV = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{}.csv"
CBOE_JSON = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{}_daily_options"
ARCHIVE_END = "2019-10-04"
EQ_INCLUDES_ETP_UNTIL = "2012-06-08"
CACHE = os.environ.get("BOTTOM_SIGNALS_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "bottom_signals"))


def fetch(url, binary=False):
    """curl을 쓴다(프록시·인증서 설정을 그대로 따르기 위해). 실패 시 None.
    Yahoo에만 브라우저 UA를 붙인다(FRED는 UA를 붙이면 거부한다)."""
    cmd = ["curl", "-s", "-f", "-m", "40"] + (["-A", UA] if "yahoo.com" in url else []) + [url]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
        return out if binary else out.decode("utf-8", "replace")
    except Exception:
        return None


def cached(name, url, max_age_h=12):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < max_age_h * 3600:
        return open(p, encoding="utf-8").read()
    txt = fetch(url)
    if txt:
        open(p, "w", encoding="utf-8").write(txt)
    return txt


def cboe_archive(product):
    """{date: (calls, puts, ratio)}"""
    txt = cached(f"cboe_{product}.csv", CBOE_CSV.format(product), max_age_h=24 * 30) or ""
    out = {}
    for line in txt.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 5:
            continue
        try:
            d = dt.datetime.strptime(p[0], "%m/%d/%Y").date().isoformat()
            out[d] = (float(p[1]), float(p[2]), float(p[4]))
        except ValueError:
            continue
    return out


def cboe_daily(d):
    """일일 통계 JSON → dict 또는 None(휴장·미발표)."""
    name = f"cboe_daily_{d}.json"
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        txt = open(p, encoding="utf-8").read()
    else:
        txt = fetch(CBOE_JSON.format(d))
        if not txt:
            return None
        os.makedirs(CACHE, exist_ok=True)
        open(p, "w", encoding="utf-8").write(txt)
    try:
        j = json.loads(txt)
    except ValueError:
        return None

    def vol(key):
        v = [x for x in j.get(key, []) if x.get("name") == "VOLUME"]
        return (v[0]["call"], v[0]["put"]) if v else (0, 0)
    ec, ep = vol("EQUITY OPTIONS")
    xc, xp = vol("EXCHANGE TRADED PRODUCTS")
    tc, tp = vol("SUM OF ALL PRODUCTS")
    if not (ec + xc) or not tc:
        return None
    return {"eqetp": (ep + xp) / (ec + xc), "total": tp / tc}


def yahoo(ticker, start, end):
    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp())
    p2 = int((dt.datetime.fromisoformat(end) + dt.timedelta(days=2)).replace(tzinfo=dt.timezone.utc).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(ticker)}?period1={p1}&period2={p2}&interval=1d"
    txt = fetch(url)
    if not txt:
        sys.exit(f"Yahoo에서 {ticker}를 받지 못했다.")
    r = json.loads(txt)["chart"]["result"][0]
    tz = r["meta"].get("gmtoffset", 0)
    q = r["indicators"]["quote"][0]
    out = {}
    for i, t in enumerate(r.get("timestamp", [])):
        if q["close"][i] is None:
            continue
        d = dt.datetime.fromtimestamp(t + tz, dt.timezone.utc).date().isoformat()
        out[d] = {"close": q["close"][i], "low": q["low"][i], "high": q["high"][i]}
    return out


def fred(series, start):
    # 전체 이력을 한 번에 요청하면 응답이 끊기는 경우가 있어 시작일(cosd)을 지정한다
    txt = cached(f"fred_{series}_{start}.csv", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}", max_age_h=6) or ""
    out = {}
    for row in csv.reader(io.StringIO(txt)):
        if len(row) == 2 and row[1] not in ("", ".", series):
            try:
                out[row[0]] = float(row[1])
            except ValueError:
                pass
    return out


def putcall_series(dates):
    """요청한 미국 거래일들에 대해 {'eqetp':..., 'total':...} 일간값. 2019-10-04 이전은 아카이브 CSV, 이후는 일일 JSON(8개 동시)."""
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    old = [d for d in dates if d <= ARCHIVE_END]
    if old:
        eq, etp, tot = cboe_archive("equitypc"), cboe_archive("etppc"), cboe_archive("totalpc")
        for d in old:
            if d not in eq or d not in tot:
                continue
            if d <= EQ_INCLUDES_ETP_UNTIL or d not in etp:
                v = eq[d][2]
            else:
                v = (eq[d][1] + etp[d][1]) / (eq[d][0] + etp[d][0])
            out[d] = {"eqetp": v, "total": tot[d][2]}
    new = [d for d in dates if d > ARCHIVE_END]
    with ThreadPoolExecutor(8) as ex:
        for d, r in zip(new, ex.map(cboe_daily, new)):
            if r:
                out[d] = r
    return out


KR_ARCHIVE_RAW = "https://raw.githubusercontent.com/Sunbong-oh/kb-market-report/main/archive/{}/%EB%B3%B8%EB%AC%B8_%EC%9E%A5%EB%A7%88%EA%B0%90%EC%88%98%EA%B8%89.txt"
PUT_RE = r"풋옵션:\s*외국인\s*([+-]?[\d,]+)\s*/\s*기관\s*([+-]?[\d,]+)\s*/\s*개인\s*([+-]?[\d,]+)"


def _parse_put(txt):
    import re
    m = re.search(PUT_RE, txt or "")
    return {k: int(v.replace(",", "")) for k, v in zip(("foreign", "inst", "retail"), m.groups())} if m else None


def kr_put_flow(archive_dir, dates=()):
    """코스피200 풋옵션 투자자별 순매수(억원, KB증권 Open API)를 kb-market-report 장마감 기록에서 읽는다.
    archive_dir 가 로컬 폴더면 archive/YYYY-MM-DD/본문_장마감수급.txt 를 읽고,
    'github' 이면 공개 저장소(Sunbong-oh/kb-market-report main)에서 dates 의 파일을 받아 온다(다른 계정·PC에서도 동작).
    줄 형식: '- 풋옵션: 외국인 -28 / 기관 -21 / 개인 +48'. 기록은 2026-10-01부터 있다."""
    out = {}
    if archive_dir == "github":
        for d in dates:
            if d < "2026-10-01":
                continue
            name = f"kr_close_{d}.txt"
            p = os.path.join(CACHE, name)
            txt = open(p, encoding="utf-8").read() if os.path.exists(p) else fetch(KR_ARCHIVE_RAW.format(d))
            if txt and not os.path.exists(p):
                os.makedirs(CACHE, exist_ok=True)
                open(p, "w", encoding="utf-8").write(txt)
            v = _parse_put(txt)
            if v:
                out[d] = v
        return out
    if not archive_dir or not os.path.isdir(archive_dir):
        return out
    for d in sorted(os.listdir(archive_dir)):
        f = os.path.join(archive_dir, d, "본문_장마감수급.txt")
        if os.path.exists(f):
            v = _parse_put(open(f, encoding="utf-8").read())
            if v:
                out[d] = v
    return out


def ma(vals, n=5):
    return [sum(vals[max(0, i - n + 1):i + 1]) / len(vals[max(0, i - n + 1):i + 1]) for i in range(len(vals))]


def compute(index="^GSPC", start=None, end=None, ref_low_date=None, kr_flow_dir=None, kr_put_sell=200, pre_days=380):
    """신호 계산. 결과 {'index', 'start', 'rows', 'signals'}. pre_days 는 1년 표준편차(z) 계산용 앞 기간(대시보드는 40처럼 짧게)."""
    end = end or dt.date.today().isoformat()
    start = start or (dt.date.fromisoformat(end) - dt.timedelta(days=150)).isoformat()

    # 기준 저점이 시작일보다 앞이면 그 앞까지 포함한다
    if ref_low_date and ref_low_date < start:
        start = (dt.date.fromisoformat(ref_low_date) - dt.timedelta(days=10)).isoformat()
    # 기준선(1년 평균·표준편차) 계산을 위해 1년 앞부터 받는다
    pre = (dt.date.fromisoformat(start) - dt.timedelta(days=pre_days)).isoformat()
    idx = yahoo(index, pre, end)
    vix = yahoo("^VIX", pre, end)
    us_days = sorted(vix)  # 풋콜·VIX는 미국 거래일 기준
    pc = putcall_series([d for d in us_days if d >= pre])
    hy = fred("BAMLH0A0HYM2", pre)
    baa = fred("BAA10Y", pre)  # 무디스 Baa-10년, 전체 이력(참고)
    cpf, tb3 = fred("DCPF3M", pre), fred("DTB3", pre)  # 금융CP 3개월 - T-bill 3개월 = 단기자금 스트레스(핵심 필터)
    fdays = sorted(set(cpf) & set(tb3))

    def funding(d):
        k = [x for x in fdays if x <= d][-5:]
        return round(sum(cpf[x] - tb3[x] for x in k) / len(k), 2) if k else None

    pdays = [d for d in us_days if d in pc]
    e5 = dict(zip(pdays, ma([pc[d]["eqetp"] for d in pdays])))
    t5 = dict(zip(pdays, ma([pc[d]["total"] for d in pdays])))

    def z(series, d):
        base = [series[x] for x in pdays if x < d][-252:]
        if len(base) < 60:
            return None
        sd = st.pstdev(base)
        return (series[d] - st.mean(base)) / sd if sd else None

    rows = []
    for d in sorted(set(idx) | set(vix)):
        if d < start:
            continue
        hy_d = max([x for x in hy if x <= d], default=None)
        rows.append({
            "date": d,
            "index_close": idx.get(d, {}).get("close"), "index_low": idx.get(d, {}).get("low"),
            "vix_close": vix.get(d, {}).get("close"), "vix_high": vix.get(d, {}).get("high"),
            "pc_eqetp": pc.get(d, {}).get("eqetp"), "pc_eqetp_5d": e5.get(d), "pc_eqetp_5d_z": z(e5, d) if d in e5 else None,
            "pc_total": pc.get(d, {}).get("total"), "pc_total_5d": t5.get(d), "pc_total_5d_z": z(t5, d) if d in t5 else None,
            "hy_oas": hy.get(hy_d) if hy_d else None, "hy_oas_date": hy_d,
            "baa10y": baa.get(max([x for x in baa if x <= d], default="")) if baa else None,
        })

    # 1차 폭락 저점(기준점) 찾기: 지정일 또는 구간 첫 저점(최저 종가)의 앞쪽 절반에서 최저 종가
    priced = [r for r in rows if r["index_close"] is not None]
    if not priced:
        raise ValueError("구간에 가격 데이터가 없다.")
    if ref_low_date:
        ref = min((r for r in priced if r["date"] <= ref_low_date), key=lambda r: abs(dt.date.fromisoformat(r["date"]).toordinal() - dt.date.fromisoformat(ref_low_date).toordinal()))
    elif len(priced) >= 25:
        # 자동: 최근 5거래일을 뺀 직전 60거래일 안의 최저 종가를 1차 폭락 저점으로 본다(대시보드 매일 갱신용)
        ref = min(priced[-65:-5], key=lambda r: r["index_close"])
    else:
        half = priced[: max(5, len(priced) // 2)]
        ref = min(half, key=lambda r: r["index_close"])

    def window_peak(center, key, k=3):
        ds = [r for r in rows if abs(dt.date.fromisoformat(r["date"]).toordinal() - dt.date.fromisoformat(center).toordinal()) <= k + 2 and r[key] is not None]
        return max(ds, key=lambda r: r[key]) if ds else None

    ref_vix = window_peak(ref["date"], "vix_high")
    ref_pc = window_peak(ref["date"], "pc_eqetp_5d")
    last = priced[-1]
    after = [r for r in priced if r["date"] > ref["date"]]
    new_low = min(after, key=lambda r: r["index_close"]) if after else None
    cur_vix = window_peak(new_low["date"], "vix_high") if new_low else None
    cur_pc = window_peak(new_low["date"], "pc_eqetp_5d") if new_low else None

    def latest(key):
        xs = [r for r in rows if r[key] is not None]
        return xs[-1] if xs else None
    lp, lv, lh = latest("pc_eqetp_5d"), latest("vix_close"), latest("hy_oas")
    vmax = max((r["vix_high"] for r in rows[-10:] if r["vix_high"]), default=None)

    # 판정
    signals = {}
    # 투매형: 구간 안에서 풋콜 5일 평균이 1.0 이상인 날을 찾고, 그 뒤 10거래일 안에 VIX 종가가 그 무렵 장중 고점보다 15% 이상 내려왔는지 본다
    hits = [r for r in rows if r["pc_eqetp_5d"] is not None and r["pc_eqetp_5d"] >= 1.0]
    cap = {"threshold": 1.0, "latest_pc_eqetp_5d": lp and round(lp["pc_eqetp_5d"], 3), "met": False,
           "note": "풋콜(주식+ETP) 5일 평균 1.0 이상 + 이후 VIX 종가가 장중 고점에서 15% 이상 하락하면 투매형 바닥 가능성"}
    if hits:
        pk = max(hits, key=lambda r: r["pc_eqetp_5d"])
        i = rows.index(pk)
        seg = [r for r in rows[max(0, i - 3): i + 11] if r["vix_high"]]
        vpk = max(seg, key=lambda r: r["vix_high"])
        after_v = [r for r in rows[rows.index(vpk) + 1: rows.index(vpk) + 11] if r["vix_close"]]
        off = next((r for r in after_v if r["vix_close"] <= vpk["vix_high"] * 0.85), None)
        cap.update({"pc_peak": {"date": pk["date"], "pc5": round(pk["pc_eqetp_5d"], 3)},
                    "vix_peak": {"date": vpk["date"], "high": vpk["vix_high"]},
                    "vix_off_peak_date": off and off["date"], "met": bool(off)})
    signals["capitulation"] = cap
    if new_low and new_low["index_close"] < ref["index_close"]:
        div = {
            "ref_low": {"date": ref["date"], "close": ref["index_close"], "vix_high": ref_vix and ref_vix["vix_high"], "pc5": ref_pc and ref_pc["pc_eqetp_5d"]},
            "new_low": {"date": new_low["date"], "close": new_low["index_close"], "vix_high": cur_vix and cur_vix["vix_high"], "pc5": cur_pc and cur_pc["pc_eqetp_5d"]},
        }
        div["vix_lower_high"] = bool(ref_vix and cur_vix and cur_vix["vix_high"] < ref_vix["vix_high"])
        div["pc_lower_high"] = bool(ref_pc and cur_pc and cur_pc["pc_eqetp_5d"] < ref_pc["pc_eqetp_5d"])
        div["met"] = div["vix_lower_high"] and div["pc_lower_high"]
        signals["divergence"] = div
    else:
        signals["divergence"] = {"met": False, "note": "기준 저점 이후 아직 더 낮은 종가가 없음(재시험 전)",
                                 "ref_low": {"date": ref["date"], "close": ref["index_close"], "vix_high": ref_vix and ref_vix["vix_high"], "pc5": ref_pc and ref_pc["pc_eqetp_5d"]}}
    def fstate(v):
        return None if v is None else ("ok" if v < 0.5 else "caution" if v < 1.0 else "fail")
    fund_now = funding(last["date"])
    signals["funding_filter"] = {
        "cp_tbill_5d": fund_now, "state": fstate(fund_now),
        "at_new_low": new_low and funding(new_low["date"]),
        "at_capitulation": signals["capitulation"].get("pc_peak") and funding(signals["capitulation"]["pc_peak"]["date"]),
        "note": "금융CP-T-bill 3개월 스프레드 5일 평균. 0.5%p 미만이면 바닥 신호 신뢰, 1.0%p 이상이면 불신(2008.3 1.43, 2008.7 1.15, 2008.11 1.64는 모두 최종 저점이 아니었음; 성공 사례 2009.3 0.42, 2011 0.18~0.25, 2022.10 0.16, 2025.4 0.10). 예외: 2020.3.23(1.12)은 연준 긴급 유동성 장치 발표일",
    }
    hyv = lh["hy_oas"] if lh and lh["hy_oas_date"] and lh["hy_oas_date"] >= (dt.date.fromisoformat(last["date"]) - dt.timedelta(days=10)).isoformat() else None
    lb = latest("baa10y")
    bv = lb["baa10y"] if lb else None
    if hyv is not None:
        state = "calm" if hyv < 4.0 else "watch" if hyv < 4.6 else "stressed"
    elif bv is not None:  # 하이일드가 없는 과거 구간: Baa-10년(참고)
        state = "calm" if bv < 2.0 else "watch" if bv < 3.0 else "stressed"
    else:
        state = None
    signals["credit_filter"] = {
        "hy_oas": hyv, "hy_as_of": lh and lh["hy_oas_date"], "baa10y": bv, "state": state,
        "note": "참고 지표(거부권 아님). 하이일드 4.0% 미만 평온·4.6% 이상 경계(2007-08-16 4.59%). 2011년 바닥은 하이일드 약 7%, Baa 3.3%p에서도 나왔으므로 수준만으로 신호를 버리지 말고 funding_filter와 함께 본다",
    }

    src_kr = kr_flow_dir or ("github" if index.upper() in ("^KS11", "^KQ11", "^KS200") else "none")
    flow = kr_put_flow(None if src_kr == "none" else src_kr, [d for d in sorted(idx) if d >= start])
    if flow:
        recent = [(d, v["foreign"]) for d, v in flow.items() if d >= start]
        hits = [(d, v) for d, v in recent if v <= -kr_put_sell]
        worst = min(recent, key=lambda x: x[1]) if recent else None
        signals["kr_foreign_put_sell"] = {
            "threshold_eok": -kr_put_sell, "latest": recent[-1] if recent else None, "worst": worst,
            "hits": hits, "met_last_10d": any(d >= (recent[-10][0] if len(recent) >= 10 else recent[0][0]) for d, _ in hits),
            "note": "국내 보조 신호(사용자 경험칙): 외국인이 코스피200 풋옵션을 하루 200억 이상 순매도하기 시작하면 바닥 준비 가능성. 과거 장기 검증은 아직 안 됨",
        }
    return {"index": index, "start": start, "rows": rows, "signals": signals}


def main():
    ap = argparse.ArgumentParser(description="폭락장 저점 판별 신호")
    ap.add_argument("--index", default="^GSPC", help="가격 기준 지수(예: ^GSPC, ^IXIC, ^KS11)")
    ap.add_argument("--start", help="시작일(기본: 150일 전)")
    ap.add_argument("--end")
    ap.add_argument("--ref-low-date", help="1차 폭락 저점일(없으면 최근 5거래일을 뺀 직전 60거래일의 최저 종가)")
    ap.add_argument("--json", help="결과를 JSON으로 저장할 경로")
    ap.add_argument("--kr-flow-dir", help="국내 풋옵션 수급 기록: kb-market-report 의 archive 폴더 경로, 또는 'github'(공개 저장소에서 받기), 'none'(끄기). 기본: 한국 지수면 github")
    ap.add_argument("--kr-put-sell", type=int, default=200, help="외국인 풋옵션 하루 순매도 기준(억원, 기본 200)")
    a = ap.parse_args()
    res = compute(a.index, a.start, a.end, a.ref_low_date, a.kr_flow_dir, a.kr_put_sell)
    rows, signals, last = res["rows"], res["signals"], [r for r in res["rows"] if r["index_close"] is not None][-1]
    print(f"\n[{a.index}] {res["start"]} ~ {last['date']}   (풋콜=Cboe 주식+ETP, CNN 근사)")
    print(f"{'date':10} {'close':>10} {'VIX c/h':>13} {'PC d/5d':>13} {'z5':>6} {'TOT5':>6} {'HY':>5} {'Baa':>5}")
    for r in rows[-40:]:
        f = lambda v, fmt: (fmt % v) if v is not None else "-"
        print(f"{r['date']:10} {f(r['index_close'],'%10.1f')} {f(r['vix_close'],'%5.1f')}/{f(r['vix_high'],'%5.1f'):>6} "
              f"{f(r['pc_eqetp'],'%.2f')}/{f(r['pc_eqetp_5d'],'%.3f'):>6} {f(r['pc_eqetp_5d_z'],'%+.1f'):>6} {f(r['pc_total_5d'],'%.3f'):>6} {f(r['hy_oas'],'%.2f'):>5} {f(r['baa10y'],'%.2f'):>5}")
    print("\n[신호]")
    print(json.dumps(signals, ensure_ascii=False, indent=1))
    if a.json:
        json.dump({"index": a.index, "rows": rows, "signals": signals}, open(a.json, "w"), ensure_ascii=False, indent=1)




if __name__ == "__main__":
    main()
