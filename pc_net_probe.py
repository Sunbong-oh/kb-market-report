"""회사 PC(실행기)에서 장중 수급이 쓰는 외부 사이트 접속 확인 (다음 증권 투자자 시간대별·네이버 지수)."""
import tls_trust  # noqa: F401  (회사 PC 보안 장비 인증서: 윈도 인증서 저장소 사용)
import httpx

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36", "Referer": "https://finance.daum.net/"}
URLS = (
    ("다음 코스피 투자자", "https://finance.daum.net/api/investor/KOSPI/times?page=1&perPage=3", H),
    ("다음 코스닥 투자자", "https://finance.daum.net/api/investor/KOSDAQ/times?page=1&perPage=3", H),
    ("네이버 코스피 실시간", "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI", {"User-Agent": H["User-Agent"]}),
    ("네이버 증권 메인", "https://m.stock.naver.com/", {"User-Agent": H["User-Agent"]}),
)
for name, url, hd in URLS:
    try:
        r = httpx.get(url, headers=hd, timeout=15, follow_redirects=True)
        print(f"{name}: HTTP {r.status_code}, {len(r.content)}바이트, {r.text[:80]!r}")
    except Exception as e:  # 인증서·방화벽 문제가 보이도록 그대로 찍는다
        print(f"{name}: 실패 {type(e).__name__}: {str(e)[:160]}")
