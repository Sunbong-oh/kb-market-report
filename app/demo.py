"""데모 모드 가짜 응답.

appKey/appSecret 없이도 화면과 규칙을 확인할 수 있도록, KB 명세(outputSpec)의
필드명을 그대로 쓴 dataBody를 만들어 준다. 값은 실제 시세가 아니다.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta

STOCKS = {
    "005930": ("삼성전자", 71500, "KOSPI"),
    "000660": ("SK하이닉스", 198000, "KOSPI"),
    "373220": ("LG에너지솔루션", 385000, "KOSPI"),
    "207940": ("삼성바이오로직스", 1012000, "KOSPI"),
    "005380": ("현대차", 243500, "KOSPI"),
    "035420": ("NAVER", 187300, "KOSPI"),
    "105560": ("KB금융", 88900, "KOSPI"),
    "012450": ("한화에어로스페이스", 402000, "KOSPI"),
    "035720": ("카카오", 41250, "KOSPI"),
    "068270": ("셀트리온", 176400, "KOSPI"),
    "247540": ("에코프로비엠", 128700, "KOSDAQ"),
    "196170": ("알테오젠", 356000, "KOSDAQ"),
}


def _rng(salt: str) -> random.Random:
    # 30초마다 값이 조금씩 바뀌도록
    return random.Random(f"{salt}-{int(time.time() // 30)}")


def _ccd(chg: float) -> str:
    return "2" if chg > 0 else "5" if chg < 0 else "3"


def _pct(code: str) -> float:
    base = (int(code) % 13 - 6) * 0.55  # 종목별 고정 성향(상승/하락 섞이게)
    return round(base + _rng(code).uniform(-0.4, 0.4), 2)


def _quote(code: str) -> dict:
    name, prev, market = STOCKS.get(code, (f"종목{code}", 10000, "KOSPI"))
    pct = _pct(code)
    now = int(round(prev * (1 + pct / 100), -1))
    r = _rng(code + "q")
    return {
        "is_nm": name,
        "now_prc": str(now),
        "bdy_cmpr_ccd": _ccd(now - prev),
        "bdy_cmpr": str(abs(now - prev)),
        "up_dwn_r_p2": f"{abs(pct):.2f}",
        "acml_vlm": str(r.randint(200_000, 15_000_000)),
        "s_sq1_askprc": str(now + 100),
        "b_sq1_askprc": str(now),
        "bdy_cls_prc": str(prev),
        "sprc": str(prev),
        "ulmt_prc": str(int(prev * 1.3)),
        "llmt_prc": str(int(prev * 0.7)),
        "opn_prc": str(int(prev * (1 + r.uniform(-0.01, 0.01)))),
        "hgh_prc": str(max(now, int(prev * 1.015))),
        "lw_prc": str(min(now, int(prev * 0.985))),
        "fgnr_hld_sgrvt_p2": f"{r.uniform(10, 55):.2f}",
        "per_p2": f"{r.uniform(6, 40):.2f}",
        "pbr_p2": f"{r.uniform(0.5, 4):.2f}",
        "opn_prc_tl_amt": str(int(prev * r.randint(50, 600))),
        "dy250_max_prc": str(int(prev * 1.35)),
        "dy250_min_prc": str(int(prev * 0.72)),
        "mkt_clsf_nm": market,
        "opn_prc_tm": "090000",
    }


def _market_summary() -> dict:
    r = _rng("mkt")
    k_pct, q_pct = r.uniform(-1.2, 1.4), r.uniform(-1.6, 1.6)
    indices = [
        ("KOSPI종합", 2987.45, k_pct), ("KOSDAQ종합", 862.31, q_pct), ("KOSPI200", 401.22, k_pct * 1.1),
    ]
    n_pct = r.uniform(-1.5, 1.5)
    investors = [
        ("8", "개인", -1), ("9", "외국인", 1), ("0", "기관계", 1),
        ("1", "금융투자", 1), ("2", "보험", 1), ("3", "투신", -1), ("7", "연기금", 1),
    ]
    return {
        "inq_dy_tm": datetime.now().strftime("%Y%m%d%H%M%S"),
        "kspi_ulmt_is_c": "2", "kspi_up_is_c": str(r.randint(300, 600)), "kspi_unchng_is_c": "68",
        "kspi_dwn_is_c": str(r.randint(250, 500)), "kspi_llmt_is_c": "0",
        "ksdq_ulmt_is_c": "5", "ksdq_up_is_c": str(r.randint(600, 1000)), "ksdq_unchng_is_c": "120",
        "ksdq_dwn_is_c": str(r.randint(500, 900)), "ksdq_llmt_is_c": "1",
        "mprft_nt_b": str(r.randint(-800, 800)),
        "nmp_nt_b": str(r.randint(-3000, 3000)),
        "out2": [
            {"indx_id": ["KGG01P", "QGG01P", "K2G01P"][i], "indx_nm": n, "now_indx_p2": f"{v * (1 + p / 100):.2f}",
             "bdy_cmpr_ccd": _ccd(p), "bdy_cmpr_p2": f"{abs(v * p / 100):.2f}", "up_dwn_r_p2": f"{abs(p):.2f}",
             "vlm": str(r.randint(10**8, 10**9)), "dl_tw_amt": str(r.randint(50000, 150000))}
            for i, (n, v, p) in enumerate(indices)
        ],
        "out3": [
            {"is_cd": "A016C000", "is_nm": "F 202612", "now_prc_p2": f"{_fut_price(len(_minutes()) - 1):.2f}",
             "bdy_cmpr_ccd": _ccd(k_pct), "bdy_cmpr_p2": f"{abs(400 * k_pct / 100):.2f}",
             "up_dwn_r_p2": f"{abs(k_pct):.2f}", "vlm": "182345", "nstmt_agr_q": "301234"},
        ],
        "out5": [
            {"invstr_cd": c, "invstr_clsf_nm": n,
             "kspi_nt_b": str(sign * r.randint(100, 6000)), "ksdq_nt_b": str(r.randint(-1500, 1500)),
             "fts_nt_b": str(r.randint(-3000, 3000)), "call_opt_nt_b": str(r.randint(-40, 40)),
             "put_opt_nt_b": str(r.randint(-40, 40))}
            for c, n, sign in investors
        ],
        "out4": [
            {"symbl_cd": "CME@NQ", "symbl_nm": "NASDAQ선물", "now_prc": f"{30500 * (1 + n_pct / 100):.2f}",
             "bdy_cmpr_ccd": _ccd(n_pct), "bdy_cmpr_p2": f"{abs(305 * n_pct):.2f}", "up_dwn_r_p2": f"{abs(n_pct):.2f}",
             "dt_tm": datetime.now().strftime("%Y%m%d%H%M%S")},
        ],
    }




def _program_by_stock(code: str, cnt: int) -> dict:
    r = _rng(code + "pgm")
    now = datetime.now().replace(second=0, microsecond=0)
    q = _quote(code)
    rows, acc = [], r.randint(-5000, 5000)
    for i in range(cnt):
        delta = r.randint(-800, 800)
        rows.append({
            "dt": now.strftime("%Y%m%d"), "tm": (now - timedelta(minutes=10 * i)).strftime("%H%M%S"),
            "excg_cd": "KRX", "now_prc": q["now_prc"], "bdy_cmpr_ccd": q["bdy_cmpr_ccd"],
            "up_dwn_r_p2": q["up_dwn_r_p2"], "nt_b_amt": str(acc), "nt_b_amt_incrs_dcrs": str(delta),
            "b_amt": str(abs(acc) + 20000), "s_amt": str(20000),
        })
        acc -= delta
    return {"out": rows}




def _fx() -> dict:
    r = _rng("fx")
    base = [("USDKRWSMBS", "한국", "원/달러", 1378.50), ("JPY", "일본", "일본 엔(100)", 921.34),
            ("EUR", "유럽연합", "유로", 1502.18), ("CNY", "중국", "중국 위안", 190.12),
            ("GBP", "영국", "영국 파운드", 1795.40), ("HKD", "홍콩", "홍콩 달러", 176.55)]
    rows = []
    for cd, ntn, nm, v in base:
        chg = round(v * r.uniform(-0.006, 0.006), 2)
        rows.append({
            "crncy_cd": cd, "ntn_nm": ntn, "crncy_cd_nm": nm, "cls_prc_p4": f"{v + chg:.2f}",
            "bdy_cmpr_ccd": _ccd(chg), "bdy_cmpr_p4": f"{abs(chg):.2f}", "bdy_cmpr_r_p2": f"{abs(chg / v * 100):.2f}",
            "mms1_bfr_cmpr_p4": f"{r.uniform(-20, 20):.2f}", "mms3_bfr_cmpr_p4": f"{r.uniform(-40, 40):.2f}",
            "fnl_rcp_tm": datetime.now().strftime("%H%M%S"),
        })
    return {"inq_cnt": str(len(rows)), "out2": rows}


def _funds() -> dict:
    r = _rng("bond")
    rows = []
    y3, y10 = 2.61, 2.93
    for i in range(10):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        c3, c10 = r.uniform(-0.04, 0.04), r.uniform(-0.04, 0.04)
        rows.append({
            "dt": d, "cs_dpst": str(r.randint(540000, 580000)), "dt2": d,
            "ntnbnd_yr1_thng_yld_p5": f"{y3 - 0.18:.3f}", "ntnbnd_yr1_thng_yld_cmpr_p5": f"{c3 * 0.6:.3f}",
            "ntnbnd_yr3_thng_yld_p5": f"{y3:.3f}", "ntnbnd_yr3_thng_yld_cmpr_p5": f"{c3:.3f}",
            "ntnbnd_yr5_thng_yld_p5": f"{(y3 + y10) / 2:.3f}", "ntnbnd_yr5_thng_yld_cmpr_p5": f"{(c3 + c10) / 2:.3f}",
            "ntnbnd_yr10_thng_yld_p5": f"{y10:.3f}", "ntnbnd_yr10_thng_yld_cmpr_p5": f"{c10:.3f}",
            "msbnd_yr1_yld_p5": f"{y3 - 0.15:.3f}", "msbnd_yr1_yld_cmpr_p5": "0.010",
            "msbnd_yr2_yld_p5": f"{y3 - 0.05:.3f}", "msbnd_yr2_yld_cmpr_p5": "-0.005",
            "cd_dy91_thng_yld_p5": "2.850", "cd_dy91_thng_yld_cmpr_p5": "0.000",
            "cpbnd_yr3_aa_yld_p5": f"{y3 + 0.62:.3f}", "cpbnd_yr3_aa_yld_cmpr_p5": f"{c3:.3f}",
        })
        y3 -= c3
        y10 -= c10
    return {"out": rows}


def _minutes() -> list[str]:
    """08:45부터 현재(최대 15:45)까지의 HHMM 목록. 장 시간이 아니면 하루 전체."""
    now = datetime.now()
    start = now.replace(hour=8, minute=45, second=0, microsecond=0)
    end = min(now, now.replace(hour=15, minute=45, second=0, microsecond=0))
    if end <= start:
        end = now.replace(hour=15, minute=45)
    n = int((end - start).total_seconds() // 60) + 1
    return [(start + timedelta(minutes=i)).strftime("%H%M") for i in range(n)]


def _walk(seed: str, n: int, step: float) -> list[float]:
    r, v, out = random.Random(seed + datetime.now().strftime("%Y%m%d")), 0.0, []
    for _ in range(n):
        v += r.gauss(0, step)
        out.append(v)
    return out


def _fut_price(i: int) -> float:
    return 400 + _walk("fut", i + 1, 0.35)[i]


def _futures_chart(minutes: int) -> dict:
    times = _minutes()
    prices = _walk("fut", len(times), 0.35)
    rows = []
    for i in range(0, len(times), minutes):
        seg = [400 + p for p in prices[i:i + minutes]]
        o = 400 + (prices[i - 1] if i else 0)
        rows.append({
            "dt": datetime.now().strftime("%Y%m%d"), "tm": times[i] + "00",
            "opn_prc_p2": f"{o:.2f}", "hgh_prc_p2": f"{max(seg + [o]) + 0.1:.2f}",
            "lw_prc_p2": f"{min(seg + [o]) - 0.1:.2f}", "cls_prc_p2": f"{seg[-1]:.2f}",
            "vlm": str(random.Random(times[i]).randint(100, 2000)), "nstmt_agr_q": "301234",
        })
    return {"out2": rows[::-1]}


def overseas_points(last: float | None) -> list[dict]:
    """데모용 나스닥 선물 기록 (자정부터 현재까지 5분 간격)."""
    now = datetime.now()
    times = [f"{m // 60:02d}{m % 60:02d}" for m in range(0, now.hour * 60 + now.minute + 1, 5)]
    w = _walk("nq", len(times), 25)
    base = (last or 30500) - w[-1]
    return [{"t": t, "c": round(base + w[k], 2)} for k, t in enumerate(times)]


def futures_flows(times: list[str]) -> list[dict]:
    """데모용 투자자별 선물 순매수 누적 추이 (분봉 시각에 맞춤)."""
    n = len(times)
    f, fi, tr = _walk("ffor", n, 180), _walk("ffin", n, 110), _walk("ftr", n, 60)
    return [{"t": t, "외국인": round(f[k]), "금융투자": round(fi[k]), "투신": round(tr[k]),
             "기관계": round(fi[k] + tr[k]), "개인": round(-f[k] - fi[k] - tr[k]), "은행": 0}
            for k, t in enumerate(times)]


def response(tr_code: str, body: dict) -> dict:
    tr = tr_code.upper()
    if tr == "IVS11560":
        return _futures_chart(int(body.get("minute_tck_indx") or 1))
    if tr == "IVU10140":
        return _quote(body.get("shrt_cd", "005930"))
    if tr == "IVSA0070":
        return _market_summary()
    if tr == "IVU10450":
        return _program_by_stock(body.get("is_cd", "005930"), int(body.get("inq_cnt") or 10))
    if tr == "IVA60190":
        return _fx()
    if tr == "IVA10370":
        return _funds()
    return {}
