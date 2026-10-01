"""장 마감 텔레그램 리포트 예약 실행 (월~금 15:50).

Windows 작업 스케줄러에서 실행한 Python이 이 PC에서 시작 직후 멈추는 문제가 있어,
항상 켜져 있는 대시보드 서버가 직접 report.py를 실행한다. 결과는 reports/report.log.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from .trading import now_kst

log = logging.getLogger("report_schedule")
ROOT = Path(__file__).resolve().parent.parent
REPORT_AT = "15:50"


async def report_loop() -> None:
    if os.environ.get("LOCAL_REPORT", "on").lower() in ("off", "0", "false"):
        return  # 리포트를 클라우드(GitHub Actions)에서 보내는 경우: 노트북에서는 중복 발송하지 않음
    last_run = ""
    while True:
        now = now_kst()
        today = now.strftime("%Y%m%d")
        if now.weekday() < 5 and now.strftime("%H:%M") >= REPORT_AT and last_run != today and now.hour < 17:
            last_run = today
            (ROOT / "reports").mkdir(exist_ok=True)
            try:
                with (ROOT / "reports" / "report.log").open("ab") as out:
                    out.write(f"\n[{now:%Y-%m-%d %H:%M:%S}] 리포트 시작\n".encode("utf-8"))
                    out.flush()
                    proc = await asyncio.create_subprocess_exec(sys.executable, "report.py", cwd=ROOT, stdout=out, stderr=out)
                    code = await proc.wait()
                    out.write(f"[{now_kst():%H:%M:%S}] 리포트 종료 (exit {code})\n".encode("utf-8"))
            except Exception as exc:
                log.warning("리포트 실행 실패: %s", exc)
        await asyncio.sleep(20)
