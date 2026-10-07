"""장 마감 리포트: 대시보드(매매창 제외)를 JPG로 캡처해 텔레그램으로 보낸다.

    python report.py            캡처 + 텔레그램 전송
    python report.py --no-send  캡처만 (reports/ 폴더에 저장)

Windows 작업 스케줄러가 월~금 15:50에 report.bat으로 실행한다.
서버(127.0.0.1:8000)가 꺼져 있으면 잠깐 켜서 캡처한 뒤 다시 끈다.
필요한 .env 값: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from blog_post import save_blog_post
from market_calendar import build_section as calendar_section
from market_calendar import render_image as calendar_image
from publish_github import publish

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"
KST = timezone(timedelta(hours=9))
load_dotenv(ROOT / ".env")


def server_up() -> bool:
    try:
        return httpx.get(f"{URL}/api/status", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def start_server() -> subprocess.Popen:
    """서버가 꺼져 있으면(클라우드 실행 포함) 리포트 전용 모드로 잠깐 켠다 - 봇·기록·예약은 켜지 않음."""
    (ROOT / "reports").mkdir(exist_ok=True)
    log = (ROOT / "reports" / "report_server.log").open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT, stdout=log, stderr=log, env={**os.environ, "REPORT_ONLY": "1"},
    )
    for _ in range(30):
        if server_up():
            return proc
        time.sleep(1)
    proc.terminate()
    raise RuntimeError("대시보드 서버를 시작하지 못했습니다.")


# 폰에서 읽기 좋게 3장으로 나눠 캡처: (파일 접미사, 캡처 폭(px), 포함할 카드, 설명)
PARTS = [
    ("1_market", 480, ["#report-title", "#indices", "#usdkrw"], "시장 지수 · 환율 · 금리"),
    ("2_flows", 480, ["#investors"], "투자자별 순매수 (현물·선물·옵션)"),
    ("3_themes", 480, ["#themes"], "오늘의 강세 테마 · 등락 종목수"),
]


def capture(prefix: Path) -> tuple[list[tuple[Path, str]], dict]:
    """PARTS마다 뷰포트 폭을 바꿔 해당 카드들만 잘라 JPG로 저장한다.
    화면이 불러온 /api 응답도 모아 돌려준다 (스냅샷 HTML용)."""
    shots, api = [], {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 480, "height": 900}, device_scale_factor=2.5, color_scheme="light")

        def keep(res):
            u = urlsplit(res.url)
            if u.path.startswith("/api/") and res.request.method == "GET" and res.ok:
                try:
                    api[u.path + (f"?{u.query}" if u.query else "")] = res.json()
                except Exception:
                    pass

        page.on("response", keep)
        page.goto(f"{URL}/?report=1", wait_until="networkidle")
        # 지수 미니차트·선물 차트·수급 막대가 그려질 때까지 대기
        try:
            page.wait_for_selector("#report-title .t", timeout=30_000)
            page.wait_for_selector("svg.mini polyline", timeout=30_000)
            page.wait_for_selector("#investors .bar-row", timeout=30_000)
            page.wait_for_selector("#themes .theme-row, #themes .empty", timeout=30_000)
        except Exception as exc:
            # KB 서버 접속 실패 등으로 화면에 오류가 떴으면 그 내용을 그대로 알려준다
            errors = page.eval_on_selector_all(".err", "els => els.map(e => e.textContent)")
            raise RuntimeError("리포트 화면을 그리지 못했습니다: " + (" / ".join(errors) or str(exc))) from exc
        for suffix, width, selectors, title in PARTS:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(800)  # resize 이벤트로 차트가 새 폭에 맞게 다시 그려지도록
            box = page.evaluate(
                """(sels) => {
                    const rects = sels.map((s) => document.querySelector(s).closest("section").getBoundingClientRect());
                    const top = Math.min(...rects.map((r) => r.top)) + scrollY - 8;
                    const bottom = Math.max(...rects.map((r) => r.bottom)) + scrollY + 8;
                    return { x: 0, y: Math.max(0, top), width: document.documentElement.clientWidth, height: bottom - top };
                }""",
                selectors,
            )
            path = prefix.with_name(f"{prefix.name}_{suffix}.jpg")
            page.screenshot(path=str(path), type="jpeg", quality=90, full_page=True, clip=box)
            shots.append((path, title))
        browser.close()
    return shots, api


def build_snapshot(api: dict, path: Path, taken: str) -> None:
    """서버 없이 폰에서 열 수 있는 단일 HTML: CSS·JS·데이터를 모두 넣고 fetch를 내장 데이터로 바꾼다."""
    static = ROOT / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    # 리포트 화면에 필요한 시장 데이터만 담는다 (주문내역·주문가능금액 등 개인 모의매매 정보는 제외)
    keep = ("/api/status", "/api/market", "/api/macro", "/api/futures", "/api/indices", "/api/themes")
    api = {k: v for k, v in api.items() if k.startswith(keep)}
    data = json.dumps(api, ensure_ascii=False).replace("</", "<\\/")
    shim = f"""<script>
window.__SNAPSHOT__ = {data};
window.__SNAPSHOT_TIME__ = {json.dumps(taken, ensure_ascii=False)};
window.fetch = async (url, opts) => {{
  const u = new URL(url, "http://snapshot");
  const body = (opts && opts.method && opts.method !== "GET") ? undefined : window.__SNAPSHOT__[u.pathname + u.search];
  return body === undefined
    ? new Response(JSON.stringify({{ detail: "스냅샷에 없는 데이터입니다" }}), {{ status: 404 }})
    : new Response(JSON.stringify(body), {{ status: 200, headers: {{ "Content-Type": "application/json" }} }});
}};
</script>"""
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/static/app.js"></script>', f"{shim}\n<script>\n{js.replace('</script', '<\\/script')}\n</script>")
    path.write_text(html, encoding="utf-8")


def send_telegram(shots: list[tuple[Path, str]], caption: str) -> None:
    """3장을 앨범(sendMediaGroup, 사진)으로 보낸다 - 폰 대화창에서 바로 크게 보인다."""
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(".env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID를 넣어 주세요.")
    media, files = [], {}
    for i, (path, title) in enumerate(shots):
        key = f"photo{i}"
        media.append({"type": "photo", "media": f"attach://{key}",
                      "caption": f"{caption}\n{i + 1}/{len(shots)} {title}" if i == 0 else f"{i + 1}/{len(shots)} {title}"})
        files[key] = (path.name, path.read_bytes(), "image/jpeg")
    res = httpx.post(f"https://api.telegram.org/bot{token}/sendMediaGroup",
                     data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)}, files=files, timeout=90)
    if res.status_code != 200 or not res.json().get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: HTTP {res.status_code} {res.text[:200]}")


def send_telegram_text(text: str) -> None:
    token, chat_id = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    res = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": text}, timeout=30)
    if res.status_code != 200 or not res.json().get("ok"):
        raise RuntimeError(f"텔레그램 메시지 전송 실패: HTTP {res.status_code} {res.text[:200]}")


def send_telegram_photo(path: Path, caption: str) -> None:
    token, chat_id = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    res = httpx.post(f"https://api.telegram.org/bot{token}/sendPhoto", data={"chat_id": chat_id, "caption": caption},
                     files={"photo": (path.name, path.read_bytes(), "image/jpeg")}, timeout=60)
    if res.status_code != 200 or not res.json().get("ok"):
        raise RuntimeError(f"텔레그램 사진 전송 실패: HTTP {res.status_code} {res.text[:200]}")


def send_telegram_file(path: Path, caption: str) -> None:
    token, chat_id = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    res = httpx.post(f"https://api.telegram.org/bot{token}/sendDocument", data={"chat_id": chat_id, "caption": caption},
                     files={"document": (path.name, path.read_bytes(), "text/html")}, timeout=60)
    if res.status_code != 200 or not res.json().get("ok"):
        raise RuntimeError(f"텔레그램 파일 전송 실패: HTTP {res.status_code} {res.text[:200]}")


def share_to_onedrive(snapshot: Path, shots: list[tuple[Path, str]], blog_dir: Path | None, now: datetime) -> None:
    """OneDrive에 리포트 파일 자체를 저장 → 같은 OneDrive 계정의 회사 PC 바탕화면·폴더에서도 열린다.

    (바로가기(.lnk)는 이 PC의 C:\\ 경로를 가리켜서 다른 PC에서는 열리지 않으므로 파일을 복사한다)
    """
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if not onedrive or not Path(onedrive).is_dir():
        return
    share = Path(os.environ.get("REPORT_SHARE_DIR", Path(onedrive) / "KB장마감리포트"))
    share.mkdir(parents=True, exist_ok=True)
    shutil.copy(snapshot, share / "장마감 수급체크.html")
    for old in share.glob("장마감수급_*.jpg"):  # 예전 3장 구성의 사진이 남지 않게
        old.unlink()
    for (path, _), name in zip(shots, ("1_시장", "2_수급", "3_강세테마")):
        shutil.copy(path, share / f"장마감수급_{name}.jpg")
    if blog_dir:
        for txt in ("제목_장마감수급.txt", "본문_장마감수급.txt"):
            if (blog_dir / txt).exists():
                shutil.copy(blog_dir / txt, share / txt)
    desktop = Path(onedrive) / "Desktop"
    if desktop.is_dir():
        shutil.copy(snapshot, desktop / "장마감 수급체크.html")
    print(f"OneDrive 저장: {share} (+ 바탕화면 '장마감 수급체크.html') · {now:%H:%M}")


def main() -> int:
    send = "--no-send" not in sys.argv
    now = datetime.now(KST).replace(tzinfo=None)  # 클라우드(UTC)에서도 한국 시간 기준
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    prefix = out_dir / f"KB_market_{now:%Y%m%d_%H%M}"

    proc = None if server_up() else start_server()
    try:
        if httpx.get(f"{URL}/api/status", timeout=5).json().get("demo_mode") and "--allow-demo" not in sys.argv:
            raise RuntimeError("KB API 키가 없어 데모(가짜) 데이터입니다. 리포트를 보내지 않습니다.")
        shots, api = capture(prefix)
    finally:
        if proc:
            proc.terminate()
    # 영업일에만 장마감 리포트를 보낸다: 코스피의 가장 최근 거래일이 오늘이 아니면 휴장일(공휴일 등)
    if os.environ.get("REPORT_MODE") == "close":
        last_day = ((api.get("/api/indices/intraday") or {}).get("KGG01P") or {}).get("date")
        if last_day and last_day != f"{now:%Y%m%d}":
            print(f"휴장일입니다 (최근 거래일 {last_day}) - 장마감 리포트를 보내지 않습니다.")
            return 0
    weekday = "월화수목금토일"[now.weekday()]
    taken = f"{now:%Y-%m-%d}({weekday}) {now:%H:%M}"
    snapshot = prefix.with_name(f"{prefix.name}_site.html")
    build_snapshot(api, snapshot, taken)
    shutil.copy(snapshot, out_dir / "latest.html")  # PC 바탕화면 '장마감 수급체크' 바로가기가 여는 최신 리포트
    for path in [*(p for p, _ in shots), snapshot]:
        print(f"저장: {path}")
    blog_dir = None
    try:
        blog_dir = save_blog_post(api, shots, now)  # 블로그 준비물 (일일 대시보드와 같은 blog_post 폴더)
    except Exception as exc:  # 블로그 준비물이 실패해도 텔레그램 전송은 계속
        print(f"블로그 준비물 저장 실패: {exc}")
    try:
        share_to_onedrive(snapshot, shots, blog_dir, now)  # 회사 PC 등 다른 PC에서도 보이도록
    except Exception as exc:
        print(f"OneDrive 저장 실패: {exc}")
    if send:  # 시험 실행(--no-send)은 텔레그램·GitHub 모두 건드리지 않는다
        send_telegram(shots, f"KB 시장 리포트 {taken}")
        try:  # 오늘 저녁 실적·이슈 + 내일 일정 (캘린더에 없으면 보내지 않음)
            try:
                cal_img = calendar_image(now, prefix.with_name(f"{prefix.name}_calendar.jpg"))
                if cal_img:
                    send_telegram_photo(cal_img, f"오늘 저녁 발표·내일 일정 {taken}")
            except Exception as exc:  # 사진 생성·전송이 안 되면 예전처럼 글자 메시지로
                print(f"일정 사진 실패, 글자로 대체: {exc}")
                cal = calendar_section(now)
                if cal:
                    send_telegram_text("\n".join(cal).strip())
        except Exception as exc:
            print(f"캘린더 메시지 전송 실패: {exc}")
        send_telegram_file(snapshot, "사이트 스냅샷 · 파일을 눌러 브라우저로 열면 차트 확대·체크박스·터치 값 확인이 됩니다")
        print("텔레그램 전송 완료")
        # 전송에 성공한 뒤에 올린다: 장마감(close) 실행이면 '오늘 발송함' 표시도 함께 올라가 중복 발송을 막는다
        try:
            print(publish(snapshot, shots, blog_dir, now, mark_sent=os.environ.get("REPORT_MODE") == "close"))
        except Exception as exc:
            print(f"GitHub 업로드 실패: {getattr(exc, 'stderr', '') or exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
