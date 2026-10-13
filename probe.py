import asyncio, json
from app.main import kb
from app.market import find_records
async def m():
    d = await kb.call("IVSA0070")
    if isinstance(d, dict):
        for k, v in d.items():
            print("KEY", k, type(v).__name__, (len(v) if isinstance(v, list) else ""))
    for r in find_records(d, "invstr_clsf_nm"):
        print("INV", json.dumps(r, ensure_ascii=False))
    for r in find_records(d, "nstmt_agr_q")[:6]:
        print("FUT", json.dumps(r, ensure_ascii=False))
asyncio.run(m())
