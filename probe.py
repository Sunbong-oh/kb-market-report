import httpx, json, re
H={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36","Referer":"https://m.stock.naver.com/"}
c=httpx.Client(headers=H,timeout=15,follow_redirects=True)
B="https://m.stock.naver.com/api/stocks/theme"
allg=[];p=1
while p<10:
    j=c.get(B,params={"page":p,"pageSize":100}).json()
    g=j.get("groups",[]);allg+=g
    if p==1: print("KEYS",list(j), "G0", json.dumps(g[0],ensure_ascii=False)[:600])
    if len(g)<100: break
    p+=1
print("TOTAL",len(allg))
for i,g in enumerate(allg[:15]): print("TOP",i+1,g.get("name"),g.get("changeRate"),g.get("riseCount"),g.get("totalCount"))
for i,g in enumerate(allg):
    if re.search("호남|광주|전남|전북|새만금|부지|클러스터|용인|반도체",g.get("name","")): print("HIT",i+1,g.get("no"),g.get("name"),g.get("changeRate"))
for s in ("호남 반도체","반도체 부지","새만금"):
    try:
        r=c.get("https://ac.stock.naver.com/ac",params={"q":s,"target":"stock,theme"}); print("AC",s,r.text[:400])
    except Exception as e: print("AC fail",e)
# upper-limit / top risers
for u in ("https://m.stock.naver.com/api/stocks/up/KOSPI?page=1&pageSize=15","https://m.stock.naver.com/api/stocks/up/KOSDAQ?page=1&pageSize=15"):
    try:
        j=c.get(u).json(); print("UPKEYS",list(j)[:5] if isinstance(j,dict) else type(j))
        for s in (j.get("stocks") if isinstance(j,dict) else j)[:15]: print("UP",s.get("stockName"),s.get("fluctuationsRatio"),s.get("itemCode"))
    except Exception as e: print("UP fail",u,e)
