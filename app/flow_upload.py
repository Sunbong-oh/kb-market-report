"""장 마감 후 그날의 선물 분 단위 기록을 GitHub에 올린다 (웹사이트 '장마감 수급' 교차 차트용).

외국인 선물 순매수 분 단위 추이는 이 서버가 장중 1분마다 직접 기록한 것(futures_flow.db)이라
GitHub Actions에는 없다. 평일 15:46 이후 한 번, /api/futures 와 같은 내용
(KOSPI200 선물 1분봉 + 투자자별 선물 순매수 누적)을 archive/YYYY-MM-DD/futures_flow.json 으로
저장해 main 브랜치에 push 한다. market-dashboard 사이트가 이 파일로 차트를 그린다.

FLOW_UPLOAD=off 이면 끈다. 결과는 reports/report.log.
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


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def _push(path: Path, label: str) -> str:
    rel = path.relative_to(ROOT).as_posix()
    _git("add", rel)
    if not _git("diff", "--cached", "--name-only", "--", rel).stdout.strip():
        return "변경 없음"
    res = _git("commit", "-m", f"선물 수급 기록 {label}", "--", rel)
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
