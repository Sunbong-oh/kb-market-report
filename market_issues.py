"""오늘의 핵심 이슈 (장 시작 전~장중 자동): 네이버 증권 '주요뉴스'를 같은 이야기끼리 묶어 기사가 가장 많이 몰린 이슈를 고르고,
기사에 나온 종목·업종의 지금 등락(맵 데이터)으로 영향을 숫자로 보여 준다. AI 해설 없이 규칙만 쓴다.

live_snapshot.py가 5분마다 불러 flow-live/issues.json 으로 올린다. 단독 실행: python market_issues.py (결과 출력)
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

KST = timezone(timedelta(hours=9))
NEWS = "https://m.stock.naver.com/api/news/list"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
           "Referer": "https://m.stock.naver.com/"}
STOP = set("""코스피 코스닥 증시 주가 주식 시장 국내 오늘 내일 올해 지난 이번 관련 관련주 종목 투자 투자자 외국인 기관 개인 순매수 순매도
상승 하락 급등 급락 강세 약세 마감 개장 출발 전망 분석 기대 우려 영향 가능성 확대 축소 기록 발표 공개 진행 예정 대비 거래 거래일
억원 조원 만원 달러 포인트 연속 최고 최저 사상 전일 전날 이날 당일 업계 회사 기업 정부 대표 시간 이후 이상 이하 가운데 한국
뉴스 단독 속보 특징주 핫종목 마켓 시황 이시각 오전 오후 따르면 통해 위해 대한 등의 했다 있다 밝혔다 것으로 것이다 있는 하는 된다""".split())
JOSA = re.compile(r"(으로|에서|에게|까지|부터|이며|이다|으며|하고|했다|한다|하며|된다|되며|은|는|이|가|을|를|의|에|로|와|과|도|만)$")
# 기사에 종목 이름이 없어도 업종 키워드로 대표 종목을 잇는다 (네이버 업종 이름, 대표 종목)
SECTOR_KEYS = [
    (r"반도체|메모리|D램|디램|낸드|HBM|CXMT|창신|파운드리|TSMC|마이크론", "반도체와반도체장비", ["삼성전자", "SK하이닉스", "한미반도체"]),
    (r"2차전지|이차전지|배터리|양극재|리튬", "전기제품", ["LG에너지솔루션", "삼성SDI", "에코프로비엠"]),
    (r"조선|LNG선|선박", "조선", ["HD한국조선해양", "한화오션", "삼성중공업"]),
    (r"방산|방위|K9|미사일", "우주항공과국방", ["한화에어로스페이스", "LIG넥스원", "현대로템"]),
    (r"원전|원자력|SMR", "전기장비", ["두산에너빌리티", "현대건설", "한전기술"]),
    (r"자동차|관세|전기차", "자동차", ["현대차", "기아", "현대모비스"]),
    (r"바이오|신약|임상|제약", "제약", ["삼성바이오로직스", "셀트리온", "알테오젠"]),
    (r"해운|운임|호르무즈|컨테이너", "해운사", ["HMM", "팬오션", "STX그린로지스"]),
    (r"은행|금리인하|금통위", "은행", ["KB금융", "신한지주", "하나금융지주"]),
]


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9&]+|[가-힣]{2,}", re.sub(r"\[[^\]]*\]", " ", text)):
        if re.match(r"[A-Za-z]", w):
            w = w.upper()
            if len(w) < 2:
                continue
        else:
            w2 = JOSA.sub("", w)
            w = w2 if len(w2) >= 2 else w
        if w not in STOP:
            out.add(w)
    return out


def fetch_news(http: httpx.Client, hours: int = 18, pages: int = 3) -> list[dict]:
    since = (datetime.now(KST) - timedelta(hours=hours)).strftime("%Y%m%d%H%M%S")
    rows, seen = [], set()
    for page in range(1, pages + 1):
        try:
            data = http.get(NEWS, params={"category": "mainnews", "page": page, "pageSize": 20}).json()
        except Exception:
            break
        for it in data if isinstance(data, list) else []:
            title = re.sub(r"<[^>]+>", "", str(it.get("tit") or "")).strip()
            dt = str(it.get("dt") or "")
            if not title or dt < since or (it.get("oid"), it.get("aid")) in seen:
                continue
            seen.add((it.get("oid"), it.get("aid")))
            rows.append({"t": title, "b": str(it.get("subcontent") or ""), "dt": dt, "o": it.get("ohnm") or "",
                         "url": f"https://n.news.naver.com/mnews/article/{it.get('oid')}/{it.get('aid')}"})
    return rows


def cluster(rows: list[dict]) -> list[list[int]]:
    toks = [_tokens(r["t"]) | _tokens(r["b"][:120]) for r in rows]
    df = Counter(t for ts in toks for t in ts)
    n = len(rows)
    sal = [{t for t in ts if 2 <= df[t] <= max(3, n // 3)} for ts in toks]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            common = sal[i] & sal[j]
            # 영문 대문자 고유명사(CXMT 등)는 하나만 겹쳐도, 한글은 두 단어 이상 겹치면 같은 이야기
            if len(common) >= 2 or any(re.match(r"[A-Z]{3,}", c) for c in common):
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _label(rows, members) -> str:
    """대표 제목: 묶음 안 다른 기사들과 가장 많이 겹치는 제목 ([...] 꼬리표 제거)."""
    toks = {i: _tokens(rows[i]["t"]) for i in members}
    best = max(members, key=lambda i: (sum(len(toks[i] & toks[j]) for j in members if j != i), -len(rows[i]["t"])))
    return re.sub(r"\s*\[[^\]]*\]\s*", " ", rows[best]["t"]).strip()


def build(rows: list[dict], map_data: dict | None, top: int = 3) -> list[dict]:
    stocks: dict[str, float] = {}
    sectors: dict[str, float] = {}
    big: dict[str, float] = {}
    for mk in ("kospi", "kosdaq"):
        for sec in (map_data or {}).get(mk) or []:
            sectors.setdefault(sec["s"], sec["p"])
            for st in sec.get("st") or []:
                if st.get("n"):
                    stocks[st["n"]] = st["p"]
                    big[st["n"]] = st.get("v") or 0
    names = sorted((n for n in stocks if len(n) >= 2), key=len, reverse=True)
    out = []
    for members in cluster(rows):
        text = " ".join(rows[i]["t"] + " " + rows[i]["b"] for i in members)
        offices = {rows[i]["o"] for i in members}
        found = []
        for nm in names:  # 긴 이름부터 (삼성전자우 → 삼성전자 중복 방지)
            if nm in text and not any(nm in f for f in found):
                found.append(nm)
        sec = None
        for pat, sname, reps in SECTOR_KEYS:
            if re.search(pat, text):
                sec = sname
                found += [r for r in reps if r not in found]
                break
        cap = sum(big.get(n, 0) for n in found[:5])
        score = len(members) * 2 + len(offices) + (3 if cap > 1_000_000 else 0) + (2 if re.search(r"쇼크|급락|급등|폭락|폭등|충격", text) else 0)
        if len(members) < 2 and score < 6:
            continue
        rel = sorted(({"n": n, "p": stocks[n]} for n in found if n in stocks), key=lambda r: big.get(r["n"], 0), reverse=True)[:6]
        arts = sorted((rows[i] for i in members), key=lambda r: r["dt"], reverse=True)
        out.append({"title": _label(rows, members), "n": len(members), "offices": len(offices), "score": score,
                    "stocks": rel, "sector": {"s": sec, "p": sectors.get(sec)} if sec and sec in sectors else None,
                    "articles": [{"t": a["t"], "o": a["o"], "url": a["url"], "dt": a["dt"]} for a in arts[:4]]})
    out.sort(key=lambda g: g["score"], reverse=True)
    return out[:top]


def market_issues(map_data: dict | None) -> list[dict] | None:
    try:
        with httpx.Client(headers=HEADERS, timeout=10, follow_redirects=True) as http:
            rows = fetch_news(http)
        res = build(rows, map_data)
        print("  이슈:", [(g["title"][:30], g["n"], g["score"]) for g in res], flush=True)
        return res
    except Exception as e:
        print("  이슈 실패:", type(e).__name__, e, flush=True)
        return None


if __name__ == "__main__":
    import json, sys
    md = None
    if len(sys.argv) > 1:
        md = json.load(open(sys.argv[1], encoding="utf-8")).get("data")
    print(json.dumps(market_issues(md), ensure_ascii=False, indent=1))
