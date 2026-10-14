import asyncio, json
from app.config import load_settings
from app.kb_client import KBClient
from app.market import find_records
async def main():
    kb = KBClient(load_settings())
    d = await kb.call("IVSA0070")
    print("time", d.get("inq_dy_tm"))
    for r in find_records(d, "invstr_clsf_nm"):
        print(json.dumps(r, ensure_ascii=False))
    for r in find_records(d, "is_nm"):
        print(json.dumps(r, ensure_ascii=False))
asyncio.run(main())
