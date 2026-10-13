import httpx, re, json
H={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36","Referer":"https://stock.naver.com/"}
c=httpx.Client(headers=H,timeout=15,follow_redirects=True)
r=c.get('https://finance.naver.com/sise/sise_trans_style.naver'); print('FINAL URL',r.url)
t=r.text
print('APIS',sorted(set(re.findall(r'["\'](/api/[^"\']{3,120}|https?://[a-z.]*naver\.com/api/[^"\']{3,120})',t)))[:60])
for js in sorted(set(re.findall(r'src="([^"]+\.js)"',t)))[:40]:
    u=js if js.startswith('http') else 'https://stock.naver.com'+js
    try:
        s=c.get(u).text
    except Exception: continue
    hits=sorted(set(re.findall(r'["`\'](/?api/[A-Za-z0-9_/\-{}$.?=&]*invest[A-Za-z0-9_/\-{}$.?=&]*)',s)))
    hits+=sorted(set(re.findall(r'["`\'](/?api/[A-Za-z0-9_/\-{}$.?=&]*(?:trend|futures|FUT)[A-Za-z0-9_/\-{}$.?=&]*)',s)))
    if hits: print('JS',u[-60:],hits[:30])
for u in ['https://m.stock.naver.com/api/index/FUT/investor','https://m.stock.naver.com/api/futures/FUT/trend','https://m.stock.naver.com/api/index/KPI200/investor',
          'https://stock.naver.com/api/domestic/market/investor/trend?marketType=FUTURES','https://m.stock.naver.com/front-api/market/investor/trend?category=futures',
          'https://m.stock.naver.com/api/index/KOSPI/trend','https://m.stock.naver.com/api/index/FUT/trend']:
    try: rr=c.get(u); print('TRY',u,rr.status_code,rr.text[:300])
    except Exception as e: print('TRY fail',u,e)
