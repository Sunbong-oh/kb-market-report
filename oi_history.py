"""코스피200 선물 미결제약정 분석용 과거 데이터 받기 (oi-history.yml, 수동 실행).

KB OpenAPI 서버는 GitHub Actions 에서만 접속되므로 여기서 받아 out/oi_history.json 으로 남긴다(작업 결과물로 내려받음).
- IVS11560 일봉(mkt_clsf 2): 분기물별 종가·거래량·미결제약정
- IVU10430 종목별투자자: 선물 코드로 일별 외국인 순매수가 나오는지 시험
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import settings
from app.kb_client import KBClient
from app.market import find_records

CODES = [f"A01{y}{m}000" for y in "456" for m in "369C"]


async def main() -> None:
    kb = KBClient(settings)
    out: dict = {"futures": {}, "investor": {}, "index": {}}
    for code in CODES:
        try:
            d = await kb.call("IVS11560", {"info_ccd": "1", "mkt_clsf": "2", "chrt_clsf": "D", "minute_tck_indx": "1",
                                           "is_cd": code, "inq_clsf": "2", "strt_dy": "", "inq_cnt": "300"}, ttl=0)
            out["futures"][code] = find_records(d, "cls_prc_p2")
        except Exception as e:
            out["futures"][code] = repr(e)
        print(code, len(out["futures"][code]), flush=True)
    for code in ("A016C000", "A0169000", "KR4A016C0000", "101C6000"):
        for q in ("1", "2"):
            try:
                d = await kb.call("IVU10430", {"excg_clsf": "1", "is_cd": code, "strt_dt": "20250101", "end_dt": "20261016",
                                               "amt_q_clsf": q, "trd_clsf": "1", "acml_clsf": "0"}, ttl=0)
                out["investor"][f"{code}/{q}"] = d
            except Exception as e:
                out["investor"][f"{code}/{q}"] = repr(e)
    for mk, code in (("7", "101"), ("7", "KGG01P"), ("5", "KGG01P"), ("7", "K2G01P")):
        try:
            d = await kb.call("IVS11560", {"info_ccd": "1", "mkt_clsf": mk, "chrt_clsf": "D", "minute_tck_indx": "1",
                                           "is_cd": code, "inq_clsf": "2", "strt_dy": "", "inq_cnt": "450"}, ttl=0)
            out["index"][f"{mk}/{code}"] = find_records(d, "cls_prc_p2")
        except Exception as e:
            out["index"][f"{mk}/{code}"] = repr(e)
    Path("out").mkdir(exist_ok=True)
    Path("out/oi_history.json").write_text(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
