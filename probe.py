import asyncio, json
from app.config import load_settings
from app.kb_client import KBClient
from app.market import find_records
async def main():
    kb = KBClient(load_settings())
    d = await kb.call("IVSA0070")
    for r in find_records(d, "invstr_clsf_nm"):
        print(json.dumps(r, ensure_ascii=False))
    print("KEYS", list(d.keys()) if isinstance(d, dict) else type(d))
    for k, v in (d.items() if isinstance(d, dict) else []):
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(k, len(v), list(v[0].keys())[:40])
asyncio.run(main())
