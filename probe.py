import httpx, json
H={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36","Referer":"https://m.stock.naver.com/"}
c=httpx.Client(headers=H,timeout=15,follow_redirects=True)
for mk in ("KOSPI","KOSDAQ"):
    j=c.get(f"https://m.stock.naver.com/api/stocks/up/{mk}",params={"page":1,"pageSize":40}).json()
    for s in j["stocks"]:
        n=s["stockName"]
        if any(k in n for k in ("ETN","ETF","인버스","레버리지","KODEX","TIGER")): continue
        try:
            nj=c.get(f"https://m.stock.naver.com/api/news/stock/{s['itemCode']}",params={"pageSize":5,"page":1}).json()
            if isinstance(nj,list):
                items=[it for g in nj for it in (g.get("items") or [g])]
            else: items=nj.get("items",[])
            hl=[(it.get("datetime",""),it.get("title","")) for it in items[:4]]
        except Exception as e: hl=[("ERR",str(e))]
        print(mk,n,s["fluctuationsRatio"],s.get("marketValue"),json.dumps(hl,ensure_ascii=False)[:500])
