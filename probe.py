import subprocess, json
subprocess.run("git fetch -q origin flow-live && git show FETCH_HEAD:map.json > /tmp/map.json", shell=True)
import market_issues as m
md=json.load(open('/tmp/map.json')).get('data')
r=m.market_issues(md)
for g in r: print("ISS",g["title"],g["n"],g["offices"],g["score"],g["sector"],[(s["n"],s["p"]) for s in g["stocks"]],[a["t"][:40] for a in g["articles"]])
