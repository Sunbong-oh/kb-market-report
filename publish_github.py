"""장마감 수급체크를 비공개 GitHub 저장소에 올린다 (report.py가 15:50 리포트 때 호출).

저장소 구조 (코드와 같은 저장소의 최상위):
    README.md                   ← github.com 저장소 첫 화면: 블로그 본문 요약 + 사진 3장
    latest/장마감 수급체크.html   ← 내려받아 브라우저로 열면 차트 확대·체크박스 동작
    latest/1_시장.jpg 2_선물수급.jpg 3_수급.jpg, 제목·본문 txt
    archive/YYYY-MM-DD/…         ← 날짜별 기록

처음 한 번은 git remote(origin)와 GitHub 로그인(Git Credential Manager)이 필요하다.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT  # 코드와 같은 비공개 저장소(kb-market-report)에 README.md · latest/ · archive/ 만 갱신한다
IMAGES = ("1_시장", "2_수급", "3_강세테마")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120, check=check)


def _copy_set(dest: Path, snapshot: Path, shots: list[tuple[Path, str]], blog_dir: Path | None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(snapshot, dest / "장마감 수급체크.html")
    for (path, _), name in zip(shots, IMAGES):
        shutil.copy(path, dest / f"{name}.jpg")
    if blog_dir:
        for txt in ("제목_장마감수급.txt", "본문_장마감수급.txt"):
            if (blog_dir / txt).exists():
                shutil.copy(blog_dir / txt, dest / txt)


def _readme(blog_dir: Path | None, now: datetime) -> str:
    body = ""
    if blog_dir and (blog_dir / "본문_장마감수급.txt").exists():
        body = (blog_dir / "본문_장마감수급.txt").read_text(encoding="utf-8")
    lines = body.strip().splitlines()
    title = lines[0] if lines else f"{now:%Y-%m-%d} 장마감 수급 체크"
    rest = "\n".join(lines[1:]).strip()
    imgs = "\n\n".join(f"![{n}](latest/{n}.jpg)" for n in IMAGES)
    return (f"# {title}\n\n"
            f"업데이트: {now:%Y-%m-%d %H:%M} (평일 15:50 자동)\n\n"
            f"```\n{rest}\n```\n\n{imgs}\n\n"
            "## 파일\n"
            "- [latest/장마감 수급체크.html](latest/) — 내려받아(Download raw) 브라우저로 열면 차트 확대·체크박스·값 확인 가능\n"
            "- [archive/](archive/) — 날짜별 기록\n")


def publish(snapshot: Path, shots: list[tuple[Path, str]], blog_dir: Path | None, now: datetime,
            mark_sent: bool = False) -> str:
    """mark_sent=True(장마감 리포트)이면 archive/날짜/.close_sent 를 남겨 그날의 중복 발송을 막는다."""
    if not (REPO / ".git").exists():
        return "건너뜀: publish 폴더가 git 저장소가 아닙니다"
    if not git("remote", check=False).stdout.strip():
        return "건너뜀: GitHub 저장소(origin)가 연결되지 않았습니다"
    latest = REPO / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    _copy_set(latest, snapshot, shots, blog_dir)
    day = REPO / "archive" / f"{now:%Y-%m-%d}"
    _copy_set(day, snapshot, shots, blog_dir)
    if mark_sent:
        (day / ".close_sent").write_text(f"{now:%Y-%m-%d %H:%M} 장마감 리포트 텔레그램 발송\n", encoding="utf-8")
    (REPO / "README.md").write_text(_readme(blog_dir, now), encoding="utf-8")
    git("add", "README.md", "latest", "archive")  # 리포트 결과물만 (코드 변경은 건드리지 않음)
    if not git("diff", "--cached", "--name-only").stdout.strip():
        return "변경 없음"
    git("commit", "-m", f"{'장마감' if mark_sent else '수급'} 리포트 {now:%Y-%m-%d %H:%M}")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git("pull", "--rebase", "origin", branch, check=False)  # 그 사이 올라온 커밋이 있어도 밀어 넣을 수 있게
    git("push", "-u", "origin", branch)
    return "GitHub 업로드 완료"
