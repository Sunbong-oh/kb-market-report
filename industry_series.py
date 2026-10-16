"""산업별 정리 시리즈: industry_series/NN_이름.txt 원고를 음성(MP3)으로 만들어 퇴근길 팟캐스트 피드에 '시리즈' 편으로 넣는다.
시리즈 편은 하루 브리핑과 달리 매일 실행에도 지워지지 않는다(close_briefing.update_feed가 series 표시 편을 그대로 둔다).

  python industry_series.py --pod DIR [--only 1,5] [--pub 2026-10-16T14:00]
원고 형식: 첫 줄 '# 제목', 나머지는 읽을 원고(⏸ = 0.5초 쉼, 문단 사이 빈 줄 = 1초 쉼).
"""

from __future__ import annotations

import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import close_briefing as cb

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "industry_series"
SERIES = "industry"
TOTAL = 20


def chapters() -> list[tuple[int, str, str]]:
    out = []
    for f in sorted(SRC.glob("[0-9][0-9]_*.txt")):
        lines = f.read_text(encoding="utf-8").strip().splitlines()
        title = lines[0].lstrip("# ").strip()
        body = "\n".join(lines[1:]).strip()
        body = re.sub(r"\n\s*\n", f"\n{cb.PAUSE * 2}", body)  # 문단 사이 1초 쉼
        out.append((int(f.name[:2]), title, body))
    return out


def main() -> int:
    pod = Path(sys.argv[sys.argv.index("--pod") + 1])
    only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")} if "--only" in sys.argv else None
    base = datetime.fromisoformat(sys.argv[sys.argv.index("--pub") + 1]) if "--pub" in sys.argv else datetime.now(cb.KST)
    base = base if base.tzinfo else base.replace(tzinfo=cb.KST)
    ep_dir = pod / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    eps = cb.load_episodes(pod)
    tmp = ROOT / "reports" / "series_tmp.mp3"
    tmp.parent.mkdir(exist_ok=True)
    for n, title, body in chapters():
        if only and n not in only:
            continue
        kbps = cb.synth(body, tmp)
        if not kbps:
            print(f"{n:02d} 음성 생성 실패", file=sys.stderr)
            return 1
        built = datetime.now(cb.KST)
        name = f"series-{SERIES}-{n:02d}-{built:%Y%m%d%H%M%S}.mp3"  # 다시 만들면 주소가 바뀌어 앱이 새 파일을 받는다
        shutil.copyfile(tmp, ep_dir / name)
        for old in [e for e in eps if e.get("series") == SERIES and e.get("ep") == n]:
            (ep_dir / old["file"]).unlink(missing_ok=True)
            eps.remove(old)
        plain = re.sub(f"{cb.PAUSE}+", " ", body).replace(cb.SLOW, "").replace(cb.SLOW_END, "")
        first = re.split(r"(?<=[.?!])\s", plain.strip(), maxsplit=2)
        eps.append(dict(file=name, title=f"[산업 정리 {n:02d}/{TOTAL}] {title}",
                        pub=(base + timedelta(minutes=n)).isoformat(),  # 1편이 가장 앞, 20편이 가장 뒤
                        bytes=(ep_dir / name).stat().st_size, secs=cb.mp3_seconds(ep_dir / name, kbps),
                        desc=" ".join(first[:2]), series=SERIES, ep=n,
                        guid=str(uuid.uuid5(uuid.NAMESPACE_URL, f"series/{name}"))))
        secs = cb.mp3_seconds(ep_dir / name, kbps)
        print(f"{n:02d} {title}: {len(plain)}자, {secs // 60}분 {secs % 60}초")
    tmp.unlink(missing_ok=True)
    cb.write_feed(pod, eps, datetime.now(cb.KST))
    print(f"피드: 전체 {len(eps)}편 (시리즈 {sum(1 for e in eps if e.get('series'))}편)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
