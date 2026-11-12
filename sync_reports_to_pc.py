"""매일 올라오는 리포트를 이 컴퓨터(회사 PC 실행기)의 폴더로 내려받는다.

  - 실적발표 종목 보고서(국내 실적카드 요약 PDF): kb-market-report surge-reports 브랜치 reports/<날짜>/<종목코드>.pdf
  - 전일 강세종목 리포트(HTML, 가능하면 PDF 도 만든다): market-dashboard us_strong/<등록일>_<티커>.html

받는 곳(기본): <문서 폴더>\\투자노트 리포트\\{실적발표 종목, 전일 강세종목}  (환경변수 REPORT_DIR 로 바꿀 수 있다)
파일 이름은 '업데이트날짜_종목명' (예: 2026-11-11_위메이드.pdf). 날짜별 폴더로 나누지 않는다.
같은 날 같은 이름이 둘 이상이면 그 파일들만 뒤에 종목코드(티커)를 붙인다.
이미 받은 파일은 건너뛰고, 예전 배치(날짜 폴더·코드가 낀 이름)로 받은 파일은 새 이름으로 옮긴다.
새로 받은 것만 '동기화 기록.txt' 에 적는다. 파이썬 표준 라이브러리만 쓴다.
단독 실행: python sync_reports_to_pc.py
"""
import tls_trust  # noqa: F401  (회사 PC 보안 장비 인증서: 윈도 인증서 저장소 사용)

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
RAW_KB = "https://raw.githubusercontent.com/Sunbong-oh/kb-market-report/surge-reports"
RAW_DASH = "https://raw.githubusercontent.com/Sunbong-oh/market-dashboard/main"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"}
EDGE = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")


def get(url: str, timeout: int = 60) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except Exception as e:  # 없는 파일(404)이나 일시 오류는 건너뛴다
        print(f"  못 받음: {url.rsplit('/', 1)[-1]} ({type(e).__name__})")
        return None


def safe(name: str) -> str:
    """윈도 파일 이름에 못 쓰는 글자를 뺀다."""
    return re.sub(r'[\\/:*?"<>|]+', " ", str(name)).strip().rstrip(".") or "이름없음"


def stems(rows: list[tuple[str, str, str]]) -> dict[tuple[str, str], str]:
    """[(날짜, 코드, 이름)] → {(날짜, 코드): 파일 이름(확장자 없이)}. 같은 날 같은 이름이 둘 이상이면 그 항목만 코드를 붙인다."""
    count = Counter((d, safe(n)) for d, _, n in rows)
    return {(d, c): (f"{d}_{safe(n)}" if count[(d, safe(n))] == 1 else f"{d}_{safe(n)}_{safe(c)}") for d, c, n in rows}


def move(src: Path, dest: Path) -> None:
    """src 를 dest 로 옮긴다. dest 가 이미 있으면 src 는 그대로 둔다(아무것도 지우지 않는다)."""
    if src != dest and not dest.exists():
        src.rename(dest)


def migrate(surge_dir: Path, strong_dir: Path, surge_names: dict, strong_names: dict) -> None:
    """예전 배치로 받은 파일을 새 이름으로 옮긴다(이미 옮겼으면 아무것도 안 한다)."""
    for day_dir in [d for d in surge_dir.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)]:
        for f in day_dir.glob("*.pdf"):
            m = re.fullmatch(r".+_([0-9A-Za-z]{6})\.pdf", f.name)
            stem = surge_names.get((day_dir.name, m.group(1))) if m else None
            if stem:
                move(f, surge_dir / f"{stem}.pdf")
        try:
            day_dir.rmdir()  # 비었을 때만 지워진다
        except OSError:
            pass
    for f in list(strong_dir.glob("*.html")) + list(strong_dir.glob("*.pdf")):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)(\.html|\.pdf)", f.name)
        if m:
            stem = strong_names.get((m.group(1), m.group(2)))
            if stem:
                move(f, strong_dir / f"{stem}{m.group(4)}")


def edge_pdf(html: Path, pdf: Path) -> bool:
    """HTML 리포트를 PDF 로 (윈도 Edge 가 있을 때만, 실패하면 HTML 만 둔다)."""
    exe = next((e for e in EDGE if os.path.exists(e)), None)
    if not exe:
        return False
    try:
        subprocess.run([exe, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", html.resolve().as_uri()],
                       capture_output=True, timeout=90, check=False)
        return pdf.exists() and pdf.stat().st_size > 0
    except Exception:
        return False


def load(url: str) -> list[dict]:
    raw = get(url)
    return json.loads(raw.decode("utf-8")) if raw else []


def main() -> int:
    root = Path(os.environ.get("REPORT_DIR") or (Path.home() / "Documents" / "투자노트 리포트"))
    surge_dir, strong_dir = root / "실적발표 종목", root / "전일 강세종목"
    surge_dir.mkdir(parents=True, exist_ok=True)
    strong_dir.mkdir(parents=True, exist_ok=True)
    new: list[str] = []

    print("목록 받기")
    surge = [r for r in load(f"{RAW_KB}/reports/index.json") if r.get("day") and r.get("code")]
    strong = [it for it in load(f"{RAW_DASH}/us_strong/index.json") if it.get("file") and it.get("date")]
    surge_names = stems([(str(r["day"]), str(r["code"]), r.get("name") or r["code"]) for r in surge])
    strong_names = stems([(str(it["date"]), str(it.get("ticker")), it.get("name") or it.get("ticker")) for it in strong])
    migrate(surge_dir, strong_dir, surge_names, strong_names)

    print("실적발표 종목 보고서 확인")
    for r in surge:
        day, code = str(r["day"]), str(r["code"])
        dest = surge_dir / f"{surge_names[(day, code)]}.pdf"
        if dest.exists() and dest.stat().st_size > 0:
            continue
        data = get(f"{RAW_KB}/reports/{day}/{code}.pdf")
        if data:
            dest.write_bytes(data)
            new.append(f"실적발표 {day} {r.get('name') or code}")

    print("전일 강세종목 리포트 확인")
    for it in strong:
        date, ticker = str(it["date"]), str(it.get("ticker"))
        html = strong_dir / f"{strong_names[(date, ticker)]}.html"
        if not (html.exists() and html.stat().st_size > 0):
            data = get(f"{RAW_DASH}/us_strong/{it['file']}")
            if not data:
                continue
            html.write_bytes(data)
            new.append(f"강세종목 {date} {it.get('name') or ticker}")
        pdf = html.with_suffix(".pdf")
        if not pdf.exists():
            edge_pdf(html, pdf)

    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    with open(root / "동기화 기록.txt", "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] 새로 받음 {len(new)}개\n" + "".join(f"  - {x}\n" for x in new))
    print(f"새로 받음 {len(new)}개 → {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
