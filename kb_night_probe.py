"""야간선물 자료가 KB 응답에 있는지 확인한다(회사 PC 실행기에서 수동 실행, 결과는 night_probe.json).

1) IVS11560 통합차트: KOSPI200 선물 1분봉을 넉넉히(1500개) 받아 날짜·시간대별로 몇 개씩 오는지, 주간장(08:45~15:45) 밖(야간) 봉이 있는지.
2) IVSA0070 시장종합: 모든 필드 이름 중 야간·night 와 비슷한 것, 투자자별 선물 항목 전체.
결과만 쓰며 거래·설정에는 손대지 않는다.
"""
import tls_trust  # noqa: F401

import asyncio
import collections
import json
import re
import sys

from app.config import settings
from app.kb_client import KBClient
from app.market import find_records, num, text


async def main() -> int:
    kb = KBClient(settings)
    out: dict = {}
    d = await kb.call("IVSA0070", ttl=0)
    fut = None
    for r in find_records(d, "nstmt_agr_q"):
        if text(r, "is_cd") and text(r, "is_nm").startswith("F"):
            fut = r
            break
    out["선물 시세 레코드"] = fut
    keys = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                keys.add(k)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    out["IVSA0070 전체 필드 수"] = len(keys)
    out["야간 비슷한 필드"] = sorted(k for k in keys if re.search(r"ngt|night|ngh|nit|야", k, re.I))
    out["투자자 레코드(선물 순매수 등)"] = [
        {k: v for k, v in r.items() if k in ("invstr_clsf_nm", "fts_nt_b", "kspi_nt_b", "ksdq_nt_b")} for r in find_records(d, "invstr_clsf_nm")]
    out["IVSA0070 레코드 묶음 이름"] = [k for k in (d.keys() if isinstance(d, dict) else [])][:30]
    code = text(fut, "is_cd") if fut else ""
    out["선물 코드"] = code
    if code:
        c = await kb.call("IVS11560", {"info_ccd": "1", "mkt_clsf": "2", "chrt_clsf": "B", "minute_tck_indx": "1", "is_cd": code,
                                         "inq_clsf": "2", "strt_dy": "", "inq_cnt": "1500"}, ttl=0)
        recs = find_records(c, "cls_prc_p2")
        out["분봉 개수"] = len(recs)
        by_day: dict = collections.defaultdict(list)
        for r in recs:
            by_day[text(r, "dt")].append(text(r, "tm").zfill(6))
        summ = {}
        for dt, tms in sorted(by_day.items()):
            tms = sorted(tms)
            day = [t for t in tms if "084500" <= t <= "154500"]
            summ[dt] = {"개수": len(tms), "첫": tms[0], "끝": tms[-1], "주간(08:45~15:45)": len(day), "주간 밖": len(tms) - len(day),
                        "주간 밖 시간대 예": [t for t in tms if not ("084500" <= t <= "154500")][:3] + [t for t in tms if not ("084500" <= t <= "154500")][-3:]}
        out["날짜별 분봉"] = summ
        out["분봉 필드 이름"] = sorted(recs[0].keys()) if recs else []
        out["분봉 한 줄 예"] = recs[0] if recs else None
    json.dump(out, open("night_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("선물 코드", "야간 비슷한 필드", "분봉 개수", "날짜별 분봉") if k in out}, ensure_ascii=False, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
