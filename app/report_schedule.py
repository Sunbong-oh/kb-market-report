"""장마감 텔레그램 리포트 예약 (월~금).

리포트는 GitHub Actions(Sunbong-oh/kb-market-report)가 만든다. 다만 GitHub의 예약(schedule) 실행은
몇 시간씩 늦어지는 일이 있어(2026-10-02: 15:52 예약 → 22:13 실행), 노트북이 켜져 있으면 이 서버가
15:55에 `gh workflow run … -f mode=close`로 "지금 실행"을 요청한다 (직접 실행은 바로 돈다).
노트북이 꺼져 있으면 GitHub 예약이 예비로 돌고, 워크플로가 하루 한 번만 보내도록 중복을 막는다.

LOCAL_REPORT=on 이면 예전처럼 노트북에서 직접 report.py를 실행한다 (클라우드와 중복 발송 주의).
결과는 reports/report.log.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

from .trading import now_kst

log = logging.getLogger("report_schedule")
ROOT = Path(__file__).resolve().parent.parent
# 정시 발송은 Claude 클라우드 루틴(평일 15:58, trigger/ 폴더에 push)이 맡는다. 노트북은 16:02에 한 번 더
# 요청하는 예비 역할 - 이미 발송됐으면 워크플로가 건너뛴다.
TRIGGER_AT = os.environ.get("REPORT_TRIGGER_AT", "16:02")
REPO = os.environ.get("REPORT_REPO", "Sunbong-oh/kb-market-report")


def _gh() -> str | None:
    return shutil.which("gh") or next((p for p in (r"C:\Program Files\GitHub CLI\gh.exe",) if Path(p).exists()), None)


async def _run(cmd: list[str], label: str) -> None:
    now = now_kst()
    (ROOT / "reports").mkdir(exist_ok=True)
    with (ROOT / "reports" / "report.log").open("ab") as out:
        out.write(f"\n[{now:%Y-%m-%d %H:%M:%S}] {label}\n".encode("utf-8"))
        out.flush()
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=ROOT, stdout=out, stderr=out)
        code = await proc.wait()
        out.write(f"[{now_kst():%H:%M:%S}] 종료 (exit {code})\n".encode("utf-8"))


async def report_loop() -> None:
    local = os.environ.get("LOCAL_REPORT", "on").lower() not in ("off", "0", "false")
    last_run = ""
    while True:
        now = now_kst()
        today = now.strftime("%Y%m%d")
        # 15:55~17:00 사이에 서버가 켜져 있으면 하루 한 번 실행 (절전에서 늦게 깨어나도 17시 전이면 요청)
        if now.weekday() < 5 and TRIGGER_AT <= now.strftime("%H:%M") < "17:00" and last_run != today:
            last_run = today
            try:
                if local:
                    await _run([sys.executable, "report.py"], "리포트 시작 (노트북에서 직접 실행)")
                elif _gh():
                    await _run([_gh(), "workflow", "run", "close-report.yml", "--repo", REPO, "-f", "mode=close", "-f", "send=true"],
                               "GitHub Actions에 장마감 리포트 실행 요청")
                else:
                    log.warning("gh(GitHub CLI)를 찾지 못해 실행 요청을 보내지 못했습니다.")
            except Exception as exc:
                log.warning("리포트 실행 실패: %s", exc)
        await asyncio.sleep(20)
