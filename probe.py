import subprocess, json
subprocess.run("git fetch -q origin flow-live && git show FETCH_HEAD:map.json > /tmp/map.json", shell=True)
import market_issues as m
md=json.load(open('/tmp/map.json')).get('data')
print(json.dumps(m.market_issues(md),ensure_ascii=False,indent=1))
