import asyncio, json
from app.config import settings
from app.kb_client import KBClient
from app.market import find_records, market_summary

async def main():
    kb = KBClient(settings)
    d = await kb.call("IVSA0070", ttl=0)
    recs = find_records(d, "invstr_clsf_nm")
    print("투자자 레코드", len(recs))
    for r in recs[:4]:
        print({k: v for k, v in r.items() if k in ("invstr_cd","invstr_clsf_nm","kspi_nt_b","ksdq_nt_b","fts_nt_b")})
    top = next((r for r in find_records(d, "inq_dy_tm")), {})
    print("top keys", {k: top.get(k) for k in ("inq_dy_tm","kspi_now_indx_p2","kspi_bdy_cmpr_p2","kspi_up_is_c","mprft_nt_b","nmp_nt_b") if k in top})
    s = await market_summary(kb)
    print("요약 지수", [(i["name"], i["value"], i["change_pct"]) for i in s["indices"]])
    out = json.dumps(d, ensure_ascii=False)
    print("응답 길이", len(out))
    open("ivsa0070.json", "w", encoding="utf-8").write(out)

asyncio.run(main())
