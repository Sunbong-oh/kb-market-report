"""장마감 리포트용 선물지수 · 외국인 선물 순매수 교차차트 사진 (홈페이지 장마감 수급 차트와 같은 그림).

차트 코드(FUT_CSS·FUT_JS)는 market-dashboard/build_site.py 의 것을 그대로 옮겨 왔다 - 사이트 차트를 바꾸면 여기도 맞춘다.
자료: archive/날짜/futures_flow.json (실시간 작업이 1분마다 쌓은 외국인·기관·개인 선물 누적) +
      리포트 시점의 투자자별 최종값(/api/market) - KB 실시간 값은 15:35 동시호가부터 멈췄다가 장 뒤 최종값으로 바뀐다.
"""

from __future__ import annotations

import json
from pathlib import Path

FUT_CSS = "<style>\n.fut{font-family:'Malgun Gothic','Apple SD Gothic Neo',system-ui,sans-serif;color:#1c2330}\n.fut .badge2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0 0 10px}\n.fut .badge2>div{border-radius:12px;padding:9px 12px;background:#f4f5f8}.fut .badge2 .k{display:block;font-size:12.5px;font-weight:700;color:#6b7385}\n.fut .badge2 b{display:block;font-size:22px;letter-spacing:-.4px;line-height:1.25}.fut .badge2 .fg b{font-size:26px}\n.fut .badge2 .c{display:block;font-size:13px;font-weight:800}.fut .badge2 .fu .c{white-space:nowrap;font-size:13.5px}\n.fut .badge2 .fu .k{color:#15803d;font-size:14px;font-weight:800}\n.fut .badge2 .up{background:#fff0f0}.fut .badge2 .up b,.fut .badge2 .up .c{color:#e03131}\n.fut .badge2 .dn{background:#eef4ff}.fut .badge2 .dn b,.fut .badge2 .dn .c{color:#1c6dd0}\n.fut .g.up{color:#e03131}.fut .g.dn{color:#1c6dd0}\n.fut .lg{margin:0 0 8px;font-size:13px;color:#6b7385;line-height:1.5}\n.fut .note{margin:6px 0 0;font-size:12px;color:#8a92a3}\n.fut .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#e03131;margin-right:5px;animation:futp 1.4s infinite}\n@keyframes futp{50%{opacity:.25}}\n</style>"
FUT_JS = '<script>\nfunction futChart(el,d,live){\n var tm=function(t){t=String(t);while(t.length<4)t=\'0\'+t;return (+t.slice(0,2))*60+(+t.slice(2,4))-525};\n var ok=function(m){return m>=0&&m<=420};\n var bars=(d.bars||[]).filter(function(b){return b.c!=null}).map(function(b){return [tm(b.t),b.c]}).filter(function(p){return ok(p[0])});\n var fl=(d.flows||[]).filter(function(r){return r[\'외국인\']!=null}).map(function(r){return [tm(r.t),r[\'외국인\']]}).filter(function(p){return ok(p[0])});\n if(bars.length<2)return false;\n var hf=fl.length>=2,cf=d.current_flows||{},lf=bars[bars.length-1],last=lf[1];\n var fg=hf?fl[fl.length-1][1]:cf[\'외국인\'];\n var F=d.futures||{},prev=(F.price!=null&&F.change!=null)?F.price-F.change:null;\n /* 장중에는 가로축을 08:45~(마지막 기록+30분)으로 줄여 지금까지의 흐름을 넓게 보여 준다. 장 마감 뒤엔 하루 전체 */\n var lastM=Math.max(lf[0],hf?fl[fl.length-1][0]:0),END=live?Math.min(420,Math.max(lastM+30,90)):420;\n var W=640,H=310,L=92,R=548,T=14,B=262,x=function(m){return L+(R-L)*m/END};\n var sc=function(v){var lo=Math.min.apply(0,v),hi=Math.max.apply(0,v);if(hi==lo){hi+=1;lo-=1}var pd=(hi-lo)*.06;lo-=pd;hi+=pd;\n   return {lo:lo,hi:hi,y:function(a){return B-(B-T)*(a-lo)/(hi-lo)}}};\n var sf=sc(bars.map(function(p){return p[1]})),sg=hf?sc(fl.map(function(p){return p[1]})):null;\n /* 두 축의 비율 고정: 선물 지수 2포인트 = 외국인 200억(1포인트 = 100억). 더 크게 움직인 쪽에 맞춰 둘 다 같은 비율로 */\n if(hf){var RATIO=100,span=Math.max(sf.hi-sf.lo,(sg.hi-sg.lo)/RATIO),fm=(sf.hi+sf.lo)/2,gm=(sg.hi+sg.lo)/2;\n  var mk=function(lo,hi){return {lo:lo,hi:hi,y:function(a){return B-(B-T)*(a-lo)/(hi-lo)}}};\n  sf=mk(fm-span/2,fm+span/2);sg=mk(gm-span*RATIO/2,gm+span*RATIO/2)}\n var path=function(ps,y){return \'M\'+ps.map(function(p){return x(p[0]).toFixed(1)+\',\'+y(p[1]).toFixed(1)}).join(\' L\')};\n var n0=function(v){return Math.round(v).toLocaleString(\'ko-KR\')},sg0=function(v){return (v>0?\'+\':v<0?\'-\':\'\')+n0(Math.abs(v))};\n var f2=function(v){return v.toLocaleString(\'ko-KR\',{minimumFractionDigits:2,maximumFractionDigits:2})};\n var step=[15,30,60,90].filter(function(s){return END/s<=6})[0]||90,tk=[];\n for(var m=15;m<=END;m+=step){var hm=m+525;tk.push((\'0\'+Math.floor(hm/60)).slice(-2)+(\'0\'+hm%60).slice(-2))}\n var grid=tk.map(function(t){var X=x(tm(t));return \'<line x1="\'+X+\'" y1="\'+T+\'" x2="\'+X+\'" y2="\'+B+\'" stroke="#e3e6ec"/>\'+\n  \'<text x="\'+X+\'" y="\'+(B+24)+\'" text-anchor="middle">\'+t.slice(0,2)+\':\'+t.slice(2)+\'</text>\'}).join(\'\');\n var fsvg=\'\';\n if(hf){var yz=sg.y(0);\n  var z0=sg.lo<0&&sg.hi>0;  /* 외국인 선은 자기 범위로 꽉 차게(0을 억지로 넣지 않음), 0선은 범위 안일 때만 */\n  fsvg=(z0?\'<line x1="\'+L+\'" y1="\'+yz.toFixed(1)+\'" x2="\'+R+\'" y2="\'+yz.toFixed(1)+\'" stroke="#e03131" stroke-dasharray="4 4" opacity=".45"/>\':\'\')+\n   \'<text x="\'+(R+6)+\'" y="\'+(T+12)+\'" fill="#e03131">\'+sg0(sg.hi)+\'</text><text x="\'+(R+6)+\'" y="\'+(B+4)+\'" fill="#e03131">\'+sg0(sg.lo)+\'</text>\'+\n   (z0&&yz-T>=24&&B-yz>=20?\'<text x="\'+(R+6)+\'" y="\'+(yz+5).toFixed(1)+\'" fill="#e03131">0</text>\':\'\')+\n   \'<path d="\'+path(fl,sg.y)+\'" fill="none" stroke="#e03131" stroke-width="3.2" stroke-linejoin="round"/>\'+\n   \'<circle cx="\'+x(fl[fl.length-1][0]).toFixed(1)+\'" cy="\'+sg.y(fl[fl.length-1][1]).toFixed(1)+\'" r="5" fill="#e03131"/>\'}\n var svg=\'<svg viewBox="0 0 \'+W+\' \'+H+\'" width="100%" role="img" aria-label="KOSPI200 선물과 외국인 선물 순매수 누적 교차 차트" style="font:15px \\\'Malgun Gothic\\\',sans-serif;fill:#6b7385;display:block">\'+\n  \'<rect x="\'+L+\'" y="\'+T+\'" width="\'+(R-L)+\'" height="\'+(B-T)+\'" fill="#fafbfc" stroke="#e3e6ec"/>\'+grid+\n  \'<text x="\'+(L-6)+\'" y="\'+(T+12)+\'" text-anchor="end">\'+f2(sf.hi)+\'</text><text x="\'+(L-6)+\'" y="\'+(B+4)+\'" text-anchor="end">\'+f2(sf.lo)+\'</text>\'+\n  \'<path d="\'+path(bars,sf.y)+\'" fill="none" stroke="#1c2330" stroke-width="1.4" stroke-linejoin="round" opacity=".7"/>\'+fsvg+\'</svg>\';\n var ch=prev!=null?last-prev:null,pct=ch!=null&&prev?ch/prev*100:0,cls=ch>0?\'up\':ch<0?\'dn\':\'\';\n var back=hf?fl.filter(function(p){return p[0]<=fl[fl.length-1][0]-30}):[],d30=back.length?fg-back[back.length-1][1]:null;\n var gc=fg>0?\'up\':fg<0?\'dn\':\'\';\n var badge=\'<div class="badge2">\'+(fg!=null?\'<div class="fg \'+gc+\'"><span class="k">외국인 선물 누적\'+(hf?\'\':\' (마감)\')+\'</span><b>\'+sg0(fg)+\'억</b>\'+\n  \'<span class="c">\'+(fg>0?\'순매수\':fg<0?\'순매도\':\'\')+(d30!=null?\' · 30분 \'+(d30>0?\'▲\':d30<0?\'▼\':\'\')+n0(Math.abs(d30))+\'억 \'+(d30>0?\'사는 중\':d30<0?\'파는 중\':\'\'):\'\')+\'</span></div>\':\'\')+\n  (ch!=null?\'<div class="fu \'+cls+\'"><span class="k">KOSPI200 선물</span><b>\'+f2(last)+\'</b><span class="c">\'+(ch>0?\'▲\':ch<0?\'▼\':\'\')+\n  f2(Math.abs(ch))+\' (\'+(pct>0?\'+\':\'\')+pct.toFixed(2)+\'%)</span></div>\':\'\')+\'</div>\';\n var rest=hf?(d.flows||[]).slice(-1)[0]||{}:cf,oth=[\'기관계\',\'개인\'].filter(function(k){return rest[k]!=null}).map(function(k){return k+\' \'+sg0(rest[k])}).join(\' · \');\n var hh=lf[0]+525,hhmm=Math.floor(hh/60)+\':\'+(\'0\'+hh%60).slice(-2);\n el.innerHTML=badge+\'<p class="lg">\'+(hf?\'<b style="color:#e03131">━ 외국인 순매수 누적</b> (우, 억원) · \':\'\')+\n  \'<b style="color:#495057">━ KOSPI200 선물</b> (좌) \'+f2(last)+\'</p>\'+svg+\n  \'<p class="note">\'+(live?\'<span class="dot"></span>\'+hhmm+\' 기준 · 1분마다 기록, 화면은 1~2분마다 자동 갱신\':\n  (hf?\'회사 PC 서버가 08:45~15:45 1분마다 기록한 값 (서버가 꺼져 있던 시간은 비어 있음)\':\'외국인 분 단위 추이는 회사 PC 기록이 있는 날부터 함께 그려집니다\'))+\'</p>\';\n return true}\n</script>'
KEEP = ("t", "외국인", "기관계", "개인")


def chart_data(raw: dict, market: dict | None) -> dict:
    fin = {r.get("name"): r.get("futures") for r in (market or {}).get("investors") or [] if r.get("futures") is not None}
    flows = [{k: r[k] for k in KEEP if k in r} for r in raw.get("flows") or []]
    cur = {k: v for k, v in (raw.get("current_flows") or {}).items() if k in KEEP}
    if fin:
        cur.update({k: fin[k] for k in KEEP if k in fin})
        if flows:  # 마지막(15:45) 점을 최종값으로
            flows = [r for r in flows if r.get("t", "") < "1545"] + [{"t": "1545", **{k: fin[k] for k in KEEP[1:] if k in fin}}]
    return {"futures": raw.get("futures"), "bars": [{"t": b["t"], "c": b.get("c")} for b in raw.get("bars") or []],
            "flows": flows, "current_flows": cur}


def render_image(raw: dict, market: dict | None, path: Path) -> Path | None:
    """480px 폭 사진(JPG)으로. 선물 분봉이 없으면 None."""
    data = chart_data(raw, market)
    if len(data["bars"]) < 2:
        return None
    from playwright.sync_api import sync_playwright

    html = ('<!doctype html><html lang="ko"><meta charset="utf-8"><body style="margin:0;padding:12px;width:456px;background:#fff">'
            + FUT_CSS + '<div class="box fut" id="futbox"><h3 style="margin:0 0 8px;font:800 17px \'Malgun Gothic\',sans-serif">'
            '선물지수 · 외국인 선물 순매수 (분 단위)</h3><div></div></div>' + FUT_JS
            + '<script>window.OK=futChart(document.querySelector("#futbox").lastChild,'
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ');</script></body></html>')
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 480, "height": 400}, device_scale_factor=2.5)
        page.set_content(html)
        ok = page.evaluate("window.OK")
        if ok:
            page.screenshot(path=str(path), type="jpeg", quality=90, full_page=True)
        browser.close()
    return path if ok else None
