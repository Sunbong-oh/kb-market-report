"""네이버 증권만으로 만드는 실시간 수급·글로벌 지수 (KB 와 비교용, 2026-11-12 시작).

KB증권 Open API 가 해외 IP(GitHub 서버)를 막아 회사 PC 실행기에만 의지하고 있어서, KB 없이 GitHub 서버에서
네이버 증권 공개 API 로 같은 화면(투자자별 매매동향 + 외국인 선물 × K200 교차차트)을 만들어 비교한다.
결과는 naver-live 브랜치 naver.json 한 파일 (홈페이지 market-dashboard 가 1분마다 읽는다).

- 투자자별(개인·외국인·기관계, 억원): m.stock.naver.com/api/index/{KOSPI|KOSDAQ|FUT}/integration 의 dealTrendInfo
  (프로그램 매매·등락 종목수도 같은 응답). 금융투자·옵션 투자자별은 네이버가 주지 않는다.
- 지수·선물 분봉: api.stock.naver.com/chart/domestic/{index|futures}/…/minute (장 시작부터 전부 받음)
- 외국인 선물·현물 누적의 '분 단위 흐름'은 네이버에 과거 기록이 없어 이 작업이 1분마다 받아 쌓는다(꺼져 있던 시간은 빈칸).
- 글로벌 지수(미국·일본·중국·홍콩·대만): polling.finance.naver.com 실시간(아시아는 15~20분 지연) + 최근 1달 일봉.

실행: 평일 08:30~17:20(KST) 은 1분마다 돌며 올리고(최대 5시간 40분, 끝나면 워크플로가 다음 작업을 이어 띄움),
그 밖의 시각에는 한 번만 갱신하고 끝낸다(밤에는 미국 지수만 바뀐다).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

KST = timezone(timedelta(hours=9))
BRANCH = "naver-live"
FILE = "naver.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}
SESSION = ("0845", "1545")          # 선물 주간장 (외국인 누적 기록 구간)
WINDOW = ("0830", "1720")           # 1분 루프를 도는 시간 (홍콩 마감 17:10 까지)
MAX_RUN = 5 * 3600 + 40 * 60        # GitHub 작업 6시간 제한 안쪽

WORLD = [  # (나라, 로이터 코드, 표시 이름)
    ("미국", ".DJI", "다우존스"), ("미국", ".INX", "S&P 500"), ("미국", ".IXIC", "나스닥 종합"), ("미국", ".SOX", "필라델피아 반도체"),
    ("일본", ".N225", "니케이 225"), ("일본", ".TOPX", "토픽스"),
    ("중국", ".SSEC", "상해종합"), ("중국", ".CSI300", "CSI 300"),
    ("홍콩", ".HSI", "항셍"), ("홍콩", ".HSCE", "H지수"),
    ("대만", ".TWII", "가권"),
]


def now() -> datetime:
    return datetime.now(KST)


def get(url: str):
    r = httpx.get(url, headers=UA, timeout=12)
    r.raise_for_status()
    return r.json()


def num(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def signed_change(d: dict) -> float | None:
    """전일 대비: 하락(4·5)인데 양수로 오는 응답이 있어 부호를 맞춘다."""
    c = num(d.get("compareToPreviousClosePrice"))
    if c is not None and (d.get("compareToPreviousPrice") or {}).get("code") in ("4", "5") and c > 0:
        c = -c
    return c


# ---------------- 국내 ----------------
def integration(code: str) -> dict:
    return get(f"https://m.stock.naver.com/api/index/{code}/integration")


def basic(code: str) -> dict:
    return get(f"https://m.stock.naver.com/api/index/{code}/basic")


def minute(kind: str, code: str, day: str, start: str, end: str) -> list[list]:
    rows = get(f"https://api.stock.naver.com/chart/domestic/{kind}/{code}/minute"
               f"?startDateTime={day}{start}&endDateTime={day}{end}") or []
    out = []
    for r in rows:
        t = str(r.get("localDateTime", ""))
        if t.startswith(day) and r.get("currentPrice") is not None:
            out.append([t[8:12], r["currentPrice"]])
    return out


def deal(info: dict | None) -> dict:
    info = info or {}
    out = {"개인": num(info.get("personalValue")), "외국인": num(info.get("foreignValue")),
           "기관계": num(info.get("institutionalValue"))}
    return {k: v for k, v in out.items() if v is not None}


def domestic(t: datetime, st: dict) -> None:
    day = t.strftime("%Y%m%d")
    inv, ix, prog, breadth = {}, [], None, {}
    for code, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("FUT", "선물")):
        try:
            g = integration(code)
        except Exception as e:  # noqa: BLE001
            print("  수급 실패", code, type(e).__name__, flush=True)
            continue
        dt = g.get("dealTrendInfo") or {}
        if dt.get("bizdate") == day:
            inv[name] = deal(dt)
        if code == "KOSPI" and (g.get("programTrendInfo") or {}).get("bizdate") == day:
            p = g["programTrendInfo"]
            prog = {"차익": num(p.get("indexDifferenceReal")), "비차익": num(p.get("indexBiDifferenceReal")),
                    "합계": num(p.get("indexTotalReal"))}
        ud = g.get("upDownStockInfo")
        if ud:
            breadth[name] = {k: num(ud.get(k)) for k in ("upperCount", "riseCount", "steadyCount", "fallCount", "lowerCount")}
    for code, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("FUT", "K200 선물")):
        try:
            b = basic(code)
        except Exception as e:  # noqa: BLE001
            print("  지수 실패", code, type(e).__name__, flush=True)
            continue
        v, c = num(b.get("closePrice")), signed_change(b)
        if v is not None:
            ix.append({"name": name, "v": v, "c": c, "pct": num(b.get("fluctuationsRatio")),
                       "status": b.get("marketStatus"), "at": (b.get("localTradedAt") or "")[:16]})
    st["ix"] = ix or st.get("ix") or []
    traded_today = any(str(r.get("at", "")).startswith(t.strftime("%Y-%m-%d")) for r in ix)
    if inv:
        st["inv"] = inv
        st["inv_date"] = day
    if prog:
        st["prog"] = prog
    if breadth:
        st["breadth"] = breadth
    if not traded_today:
        return
    st["date"] = day

    # 분봉 (장 시작부터 네이버가 전부 준다)
    fut = st.setdefault("fut", {})
    try:
        bars = minute("futures", "FUT", day, "0845", "1545")
        fx = next((r for r in ix if r["name"] == "K200 선물"), None)
        if fx and fx.get("status") == "CLOSE" and bars and bars[-1][0] < "1545":
            bars.append(["1545", fx["v"]])  # 15:35~45 동시호가 종가
        fut["bars"] = [{"t": a, "c": b} for a, b in bars]
        if fx:
            fut["futures"] = {"price": fx["v"], "change": fx["c"]}
    except Exception as e:  # noqa: BLE001
        print("  선물 분봉 실패", type(e).__name__, flush=True)
    idx = fut.setdefault("idx", {})
    for code, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        try:
            pts = minute("index", code, day, "0900", "1530")
            r = next((x for x in ix if x["name"] == name), None)
            if len(pts) > 1 and r and r.get("c") is not None:
                idx[name] = {"p": round(r["v"] - r["c"], 2), "pts": pts}
        except Exception as e:  # noqa: BLE001
            print("  지수 분봉 실패", code, type(e).__name__, flush=True)

    # 외국인·기관·개인 선물 누적 + 외국인 현물 누적을 1분마다 쌓는다
    cur = dict(inv.get("선물") or {})
    for name in ("코스피", "코스닥"):
        if (inv.get(name) or {}).get("외국인") is not None:
            cur["외국인·" + name] = inv[name]["외국인"]
    if not cur:
        return
    fut["current_flows"] = cur
    flows = {r["t"]: r for r in fut.get("flows") or []}
    tm = t.strftime("%H%M")
    if SESSION[0] <= tm <= SESSION[1]:
        flows[tm] = {"t": tm, **cur}
    elif tm > SESSION[1] and flows:  # 장 뒤 확정값으로 마지막 점을 계속 고친다
        flows[SESSION[1]] = {"t": SESSION[1], **cur}
    fut["flows"] = [flows[k] for k in sorted(flows)]


# ---------------- 해외 ----------------
def world(t: datetime, st: dict, spark: bool) -> None:
    codes = ",".join(c for _, c, _ in WORLD)
    try:
        rows = {d["reutersCode"]: d for d in get(f"https://polling.finance.naver.com/api/realtime/worldstock/index/{codes}")["datas"]}
    except Exception as e:  # noqa: BLE001
        print("  해외 지수 실패", type(e).__name__, flush=True)
        return
    old = {w["code"]: w for w in st.get("world") or []}
    out = []
    for nation, code, name in WORLD:
        d = rows.get(code)
        if not d:
            if code in old:
                out.append(old[code])
            continue
        w = {"nation": nation, "code": code, "name": name, "v": num(d.get("closePrice")), "c": signed_change(d),
             "pct": num(d.get("fluctuationsRatio")), "status": d.get("marketStatus"),
             "at": (d.get("localTradedAt") or "")[:16], "tz": (d.get("stockExchangeType") or {}).get("zoneId"),
             "delay": (d.get("stockExchangeType") or {}).get("delayTime"),
             "spark": (old.get(code) or {}).get("spark") or []}
        if spark or not w["spark"]:
            try:
                s = (t - timedelta(days=45)).strftime("%Y%m%d")
                days = get(f"https://api.stock.naver.com/chart/foreign/index/{code}/day"
                           f"?startDateTime={s}0000&endDateTime={t.strftime('%Y%m%d')}2359") or []
                w["spark"] = [[str(x["localDate"]), x["closePrice"]] for x in days if x.get("closePrice") is not None][-22:]
            except Exception as e:  # noqa: BLE001
                print("  일봉 실패", code, type(e).__name__, flush=True)
        # 오늘 장중 값이 아직 일봉에 없으면 끝에 붙인다
        ld = w["at"][:10].replace("-", "")
        if w["v"] is not None and w["spark"] and ld and w["spark"][-1][0] < ld:
            w["spark"] = (w["spark"] + [[ld, w["v"]]])[-22:]
        elif w["v"] is not None and w["spark"] and w["spark"][-1][0] == ld:
            w["spark"][-1][1] = w["v"]
        out.append(w)
    st["world"] = out
    st["world_time"] = t.strftime("%Y-%m-%d %H:%M")


# ---------------- 저장 ----------------
def git(*a, inp: bytes | None = None):
    return subprocess.run(["git", *a], input=inp, capture_output=True)


def load() -> dict:
    if git("fetch", "-q", "origin", BRANCH).returncode:
        return {}
    r = git("show", f"FETCH_HEAD:{FILE}")
    try:
        return json.loads(r.stdout.decode("utf-8")) if r.returncode == 0 else {}
    except ValueError:
        return {}


def push(st: dict) -> None:
    body = json.dumps(st, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if os.environ.get("DRY_RUN"):
        open(FILE, "wb").write(body)
        return
    blob = git("hash-object", "-w", "--stdin", inp=body).stdout.decode().strip()
    tree = git("mktree", inp=f"100644 blob {blob}\t{FILE}\n".encode()).stdout.decode().strip()
    commit = git("commit-tree", tree, "-m", "네이버 실시간 수급 (Actions)").stdout.decode().strip()
    res = git("push", "-f", "-q", "origin", f"{commit}:refs/heads/{BRANCH}")
    if res.returncode:
        raise RuntimeError(res.stderr.decode(errors="replace")[:300])


def update(st: dict, spark: bool) -> None:
    t = now()
    if st.get("date") and st["date"] != t.strftime("%Y%m%d") and t.weekday() < 5 and t.strftime("%H%M") >= "0830":
        st.pop("fut", None)  # 어제 기록은 장 시작 전에 비운다
        st.pop("inv", None)
    if t.weekday() < 5:
        domestic(t, st)
    world(t, st, spark)
    st["time"] = t.strftime("%H:%M")
    st["updated"] = t.strftime("%Y-%m-%d %H:%M")
    st["src"] = "네이버 증권 (m.stock.naver.com · api.stock.naver.com)"


def in_window(t: datetime) -> bool:
    return t.weekday() < 5 and WINDOW[0] <= t.strftime("%H%M") < WINDOW[1]


def main() -> None:
    st = load()
    start = time.time()
    if not in_window(now()) or "--once" in sys.argv:
        update(st, spark=True)
        push(st)
        print("한 번 갱신", st["updated"], flush=True)
        return
    n = 0
    while in_window(now()) and time.time() - start < MAX_RUN:
        t = now()
        if t.strftime("%H%M") >= "0930" and st.get("date") != t.strftime("%Y%m%d"):
            print("오늘 국내 장이 열리지 않음(휴장) → 한 번만 갱신하고 끝", flush=True)
            update(st, spark=True)
            push(st)
            with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as f:
                f.write("again=0\n")
            return
        try:
            update(st, spark=n % 30 == 0)
            push(st)
            f = (st.get("fut") or {}).get("current_flows") or {}
            print(st["updated"], "외국인 선물", f.get("외국인"), "· 기록", len((st.get("fut") or {}).get("flows") or []), flush=True)
        except Exception as e:  # noqa: BLE001
            print("  갱신 실패", type(e).__name__, str(e)[:200], flush=True)
        n += 1
        time.sleep(max(5, 60 - time.time() % 60 + 3))  # 매분 03초쯤
    # 이어달리기 여부는 워크플로가 판단 (창 안이면 다음 작업을 띄운다)
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as f:
        f.write(f"again={'1' if in_window(now()) else '0'}\n")


if __name__ == "__main__":
    main()
