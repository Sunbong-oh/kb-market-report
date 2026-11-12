"""회사 PC(실행기)에서 장중 수급이 쓰는 외부 사이트 접속 확인 (다음 증권·네이버 지수·강세 테마·거래대금 상위)."""
import tls_trust  # noqa: F401  (회사 PC 보안 장비 인증서: 윈도 인증서 저장소 사용)
import os
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://finance.daum.net/"}
N = {"User-Agent": UA, "Referer": "https://m.stock.naver.com/"}
URLS = (
    ("다음 코스피 투자자", "https://finance.daum.net/api/investor/KOSPI/times?page=1&perPage=3", H),
    ("네이버 코스피 실시간", "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI", {"User-Agent": UA}),
    ("네이버 강세 테마", "https://m.stock.naver.com/api/stocks/theme?page=1&pageSize=5", N),
    ("네이버 상승 종목", "https://m.stock.naver.com/api/stocks/up/KOSPI?page=1&pageSize=5", N),
    ("네이버 뉴스 검색", "https://m.search.naver.com/search.naver?where=m_news&query=%EC%A6%9D%EC%8B%9C", N),
    ("네이버 거래대금 상위", "https://m.stock.naver.com/api/stocks/priceTop/KOSPI?page=1&pageSize=5", N),
    ("구글 뉴스", "https://news.google.com/rss/search?q=%EC%A6%9D%EC%8B%9C&hl=ko&gl=KR&ceid=KR:ko", {"User-Agent": UA}),
)
print("SSL_CERT_FILE =", os.environ.get("SSL_CERT_FILE"))
for name, url, hd in URLS:
    try:
        r = httpx.get(url, headers=hd, timeout=15, follow_redirects=True)
        print(f"{name}: HTTP {r.status_code}, {len(r.content)}바이트, {r.text[:70]!r}")
    except Exception as e:  # 인증서·방화벽 문제가 보이도록 그대로 찍는다
        print(f"{name}: 실패 {type(e).__name__}: {str(e)[:160]}")
