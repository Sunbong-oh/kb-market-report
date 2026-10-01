"""종목명 → 종목코드 찾기 (텔레그램 음성·텍스트 주문용).

KB 종목마스터 TR(SIAM4983)은 빈 레코드만 돌려줘서 쓸 수 없다. 대신
1) 주요 종목 내장 목록
2) IVU10210 거래대금상위(코스피·코스닥 각 200종목, 하루 1번 갱신)
3) 코드로 주문했던 종목을 기억한 목록(SQLite)
을 합쳐서 이름을 찾는다. 목록에 없는 종목은 6자리 코드로 주문하면 다음부터 이름으로 찾을 수 있다.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .kb_client import KBClient
from .market import find_records, text

BUILTIN = {
    "005930": "삼성전자", "005935": "삼성전자우", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아", "068270": "셀트리온",
    "035420": "NAVER", "035720": "카카오", "105560": "KB금융", "055550": "신한지주",
    "086790": "하나금융지주", "316140": "우리금융지주", "012450": "한화에어로스페이스", "329180": "HD현대중공업",
    "009540": "HD한국조선해양", "042660": "한화오션", "012330": "현대모비스", "005490": "POSCO홀딩스",
    "051910": "LG화학", "006400": "삼성SDI", "003670": "포스코퓨처엠", "066570": "LG전자",
    "028260": "삼성물산", "032830": "삼성생명", "000810": "삼성화재", "009150": "삼성전기",
    "018260": "삼성에스디에스", "010140": "삼성중공업", "034020": "두산에너빌리티", "064350": "현대로템",
    "047810": "한국항공우주", "079550": "LIG넥스원", "017670": "SK텔레콤", "030200": "KT",
    "032640": "LG유플러스", "015760": "한국전력", "010130": "고려아연", "011200": "HMM",
    "096770": "SK이노베이션", "034730": "SK", "003550": "LG", "402340": "SK스퀘어",
    "267260": "HD현대일렉트릭", "010120": "LS ELECTRIC", "259960": "크래프톤", "036570": "엔씨소프트",
    "251270": "넷마블", "352820": "하이브", "033780": "KT&G", "097950": "CJ제일제당",
    "090430": "아모레퍼시픽", "010950": "S-Oil", "024110": "기업은행", "138040": "메리츠금융지주",
    "323410": "카카오뱅크", "377300": "카카오페이", "000100": "유한양행", "128940": "한미약품",
    "247540": "에코프로비엠", "086520": "에코프로", "196170": "알테오젠", "028300": "HLB",
    "263750": "펄어비스", "293490": "카카오게임즈", "068760": "셀트리온제약", "041510": "에스엠",
    "035900": "JYP Ent.", "112040": "위메이드", "058470": "리노공업", "039030": "이오테크닉스",
    "069500": "KODEX 200", "122630": "KODEX 레버리지", "114800": "KODEX 인버스", "252670": "KODEX 200선물인버스2X",
}
# 자주 부르는 별칭
ALIASES = {"삼전": "005930", "하이닉스": "000660", "sk하이닉스": "000660", "엘지엔솔": "373220", "lg엔솔": "373220",
           "삼바": "207940", "현대자동차": "005380", "네이버": "035420", "한화에어로": "012450", "포스코홀딩스": "005490",
           "엘지전자": "066570", "엘지화학": "051910", "에코프로비엠": "247540"}
CODE_RE = re.compile(r"^[0-9][0-9A-Z]{5}$")


def norm(s: str) -> str:
    return re.sub(r"[\s·.\-_&]", "", s).lower()


class StockResolver:
    def __init__(self, kb: KBClient, db_path: Path):
        self.kb = kb
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._conn:
            self._conn.execute("CREATE TABLE IF NOT EXISTS stock_names (code TEXT PRIMARY KEY, name TEXT NOT NULL)")
        self._top: dict[str, str] = {}
        self._top_at = 0.0

    def learn(self, code: str, name: str) -> None:
        if code and name and name != code:
            with self._conn:
                self._conn.execute("INSERT OR REPLACE INTO stock_names VALUES (?, ?)", (code, name))

    async def _refresh_top(self) -> None:
        if time.time() - self._top_at < 6 * 3600 and self._top:
            return
        top: dict[str, str] = {}
        for segment in ("2", "3"):  # 2 KOSPI, 3 KOSDAQ
            try:
                d = await self.kb.call("IVU10210", {"excg_clsf": "1", "mkt_clsf": segment, "thdy_bdy_clsf": "2",
                                                    "inq_cnt": "200", "srt_clsf": "1"}, ttl=6 * 3600)
                for r in find_records(d, "is_nm"):
                    if text(r, "is_cd") and text(r, "is_nm"):
                        top[text(r, "is_cd")] = text(r, "is_nm")
            except Exception:
                pass  # 목록 갱신 실패해도 내장 목록·기억 목록으로 계속 동작
        if top:
            self._top, self._top_at = top, time.time()

    async def names(self) -> dict[str, str]:
        await self._refresh_top()
        learned = dict(self._conn.execute("SELECT code, name FROM stock_names").fetchall())
        return {**self._top, **BUILTIN, **learned}

    async def search(self, query: str, limit: int = 8) -> list[dict]:
        """웹 검색창 자동완성: 이름(초성 아님)·코드 부분 일치, 정확히 일치·앞부분 일치 우선."""
        q = query.strip()
        if not q:
            return []
        names = await self.names()
        nq = norm(q)
        if nq in ALIASES:
            code = ALIASES[nq]
            return [{"code": code, "name": names.get(code, code)}]
        hits = [(c, n) for c, n in names.items() if nq in norm(n) or c.startswith(q.upper())]
        hits.sort(key=lambda cn: (norm(cn[1]) != nq, not norm(cn[1]).startswith(nq), len(cn[1]), cn[1]))
        return [{"code": c, "name": n} for c, n in hits[:limit]]

    async def resolve(self, query: str) -> list[tuple[str, str]]:
        """후보 [(코드, 이름)] - 정확히 일치하면 1개, 아니면 포함 관계로 최대 5개."""
        q = query.strip().upper()
        if CODE_RE.match(q):
            return [(q, "")]
        names = await self.names()
        nq = norm(query)
        if nq in ALIASES:
            code = ALIASES[nq]
            return [(code, names.get(code, code))]
        exact = [(c, n) for c, n in names.items() if norm(n) == nq]
        if exact:
            return exact[:1]
        partial = [(c, n) for c, n in names.items() if nq and (nq in norm(n) or norm(n) in nq)]
        partial.sort(key=lambda cn: (abs(len(norm(cn[1])) - len(nq)), cn[1]))
        return partial[:5]
