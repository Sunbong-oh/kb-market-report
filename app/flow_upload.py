"""장 마감 후 그날의 선물 분 단위 기록을 GitHub에 올린다 (웹사이트 '장마감 수급' 교차 차트용).

외국인 선물 순매수 분 단위 추이는 이 서버가 장중 1분마다 직접 기록한 것(futures_flow.db)이라
GitHub Actions에는 없다. 평일 15:46 이후 한 번, /api/futures 와 같은 내용
(KOSPI200 선물 1분봉 + 투자자별 선물 순매수 누적)을 archive/YYYY-MM-DD/futures_flow.json 으로
저장해 main 브랜치에 push 한다. market-dashboard 사이트가 이 파일로 차트를 그린다.

장중(평일 08:46~15:50)에는 live_loop가 1분마다 같은 내용을 flow-live 브랜치(커밋 1개, 매번 덮어씀)에
올려 사이트가 실시간 차트를 그린다. main 기록은 늘지 않는다.

FLOW_UPLOAD=off 이면 둘 다 끈다. FLOW_LIVE=off 이면 실시간만 끈다. 결과는 reports/report.log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Awaitable, Callable

from .trading import now_kst

log = logging.getLogger("flow_upload")
ROOT = Path(__file__).resolve().parent.parent
UPLOAD_AT = os.environ.get("FLOW_UPLOAD_AT", "15:46")
LIVE_BRANCH = "flow-live"
LIVE_SEC = int(os.environ.get("FLOW_LIVE_SEC", "60"))


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def _push(path: Path | list[Path], label: str) -> str:
    rels = [q.relative_to(ROOT).as_posix() for q in (path if isinstance(path, list) else [path])]
    _git("add", *rels)
    if not _git("diff", "--cached", "--name-only", "--", *rels).stdout.strip():
        return "변경 없음"
    res = _git("commit", "-m", f"선물 수급 기록 {label}", "--", *rels)
    if res.returncode:
        raise RuntimeError(f"commit 실패: {res.stderr.strip()[:200]}")
    for _ in range(3):
        _git("pull", "--rebase", "--autostash", "origin", "main")
        res = _git("push", "origin", "HEAD:main")
        if res.returncode == 0:
            return "GitHub 업로드 완료"
    raise RuntimeError(f"push 실패: {res.stderr.strip()[:200]}")


def _log(msg: str) -> None:
    (ROOT / "reports").mkdir(exist_ok=True)
    with (ROOT / "reports" / "report.log").open("a", encoding="utf-8") as f:
        f.write(f"[{now_kst():%Y-%m-%d %H:%M:%S}] 선물 기록 업로드: {msg}\n")


async def upload_loop(get_payload: Callable[[], Awaitable[dict]]) -> None:
    if os.environ.get("FLOW_UPLOAD", "on").lower() in ("off", "0", "false"):
        return
    done = ""
    while True:
        now = now_kst()
        today = now.strftime("%Y%m%d")
        if now.weekday() < 5 and UPLOAD_AT <= now.strftime("%H:%M") < "18:00" and done != today:
            try:
                payload = await get_payload()
                if payload.get("flow_date") != today or not payload.get("flows"):
                    _log("오늘 기록이 없어 건너뜀 (서버가 장중에 꺼져 있었음)")
                else:
                    path = ROOT / "archive" / f"{now:%Y-%m-%d}" / "futures_flow.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    _log(await asyncio.to_thread(_push, path, f"{now:%Y-%m-%d}"))
                done = today
            except Exception as exc:  # 다음 분에 다시 시도하지 않고 5분 뒤 재시도
                log.warning("선물 기록 업로드 실패: %s", exc)
                _log(f"실패 {type(exc).__name__}: {exc}")
                await asyncio.sleep(300)
                continue
        await asyncio.sleep(60)


def _slim(payload: dict) -> dict:
    """사이트 차트에 필요한 값만 (선물 종가, 외국인·기관계·개인 누적)."""
    keep = ("t", "외국인", "기관계", "개인")
    return {
        "flow_date": payload.get("flow_date"),
        "futures": payload.get("futures"),
        "bars": [{"t": b["t"], "c": b.get("c")} for b in payload.get("bars") or []],
        "flows": [{k: r[k] for k in keep if k in r} for r in payload.get("flows") or []],
        "updated": f"{now_kst():%H:%M}",
    }


def _push_live(data: str) -> None:
    """작업 폴더·main은 건드리지 않고, 파일 하나짜리 커밋을 만들어 flow-live 브랜치에 강제 push."""
    run = lambda args, inp: subprocess.run(["git", *args], cwd=ROOT, input=inp, capture_output=True, timeout=60)
    blob = run(["hash-object", "-w", "--stdin"], data.encode("utf-8")).stdout.decode().strip()
    tree = run(["mktree"], f"100644 blob {blob}\tfutures_flow.json\n".encode()).stdout.decode().strip()
    res = _git("commit-tree", tree, "-m", "실시간 선물 수급")
    if res.returncode or not blob or not tree:
        raise RuntimeError(f"commit-tree 실패: {res.stderr.strip()[:200]}")
    res = _git("push", "-f", "-q", "origin", f"{res.stdout.strip()}:refs/heads/{LIVE_BRANCH}")
    if res.returncode:
        raise RuntimeError(f"push 실패: {res.stderr.strip()[:200]}")


async def live_loop(get_payload: Callable[[], Awaitable[dict]]) -> None:
    if any(os.environ.get(k, "on").lower() in ("off", "0", "false") for k in ("FLOW_UPLOAD", "FLOW_LIVE")):
        return
    logged = ""  # 성공·실패 로그는 하루 한 번씩만 (1분마다 쌓이지 않게)
    while True:
        now = now_kst()
        today = now.strftime("%Y%m%d")
        if now.weekday() < 5 and "08:46" <= now.strftime("%H:%M") <= "15:50":
            try:
                payload = await get_payload()
                if payload.get("flow_date") == today and payload.get("flows"):
                    await asyncio.to_thread(_push_live, json.dumps(_slim(payload), ensure_ascii=False))
                    if not logged.startswith(today + "ok"):
                        _log("실시간 업로드 시작 (flow-live 브랜치, 1분마다)")
                        logged = today + "ok"
            except Exception as exc:
                log.warning("실시간 선물 기록 업로드 실패: %s", exc)
                if logged != today + "err":
                    _log(f"실시간 업로드 실패 {type(exc).__name__}: {exc}")
                    logged = today + "err"
        await asyncio.sleep(LIVE_SEC)


def _report_futures(day: Path) -> dict | None:
    """그날 장마감 리포트 HTML에 저장된 /api/futures 응답 (선물 1분봉·마감 수급)."""
    for h in day.glob("*.html"):
        txt = h.read_text(encoding="utf-8", errors="replace")
        i = txt.find('"/api/futures?minutes=1": {')
        if i >= 0:
            try:
                return json.JSONDecoder().raw_decode(txt[txt.index("{", i):])[0]
            except ValueError:
                return None
    return None


async def backfill(series: Callable[[str], tuple[str, list[dict]]]) -> None:
    """서버를 켤 때 한 번: futures_flow.json이 없는 지난 장마감 날짜 중 이 PC DB에 분 단위 기록이 있는 날을
    (리포트의 선물 1분봉 + DB의 투자자별 누적) 합쳐 올린다. 예: 업로드 기능을 넣기 전인 10/8."""
    if os.environ.get("FLOW_UPLOAD", "on").lower() in ("off", "0", "false"):
        return
    try:
        _git("pull", "--rebase", "--autostash", "origin", "main")
        today = f"{now_kst():%Y-%m-%d}"
        paths = []
        for day in sorted((ROOT / "archive").glob("20??-??-??")):
            out = day / "futures_flow.json"
            if day.name >= today or out.exists():
                continue
            dt, flows = series(day.name.replace("-", ""))
            rep = _report_futures(day) if len(flows) >= 2 else None
            if not rep or not rep.get("bars"):
                continue
            rep.update(flows=flows, flow_date=dt)
            out.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
            paths.append(out)
        if paths:
            _log(f"지난 기록 {', '.join(q.parent.name for q in paths)}: " +
                 await asyncio.to_thread(_push, paths, "지난 날짜"))
    except Exception as exc:
        log.warning("지난 선물 기록 업로드 실패: %s", exc)
        _log(f"지난 기록 업로드 실패 {type(exc).__name__}: {exc}")
