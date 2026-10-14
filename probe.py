from datetime import date
import econ_values as v, calendar_values as c
for d, it in [(date(2026,10,14), "★미 9월 소비자물가(CPI)"), (date(2026,10,15), "미 9월 PPI·소매판매"), (date(2026,10,15), "★TSMC 실적"), (date(2026,10,14), "ASML 실적")]:
    print("DASH", d, it, "=>", v.values(d, it))
print("KB", c.annotate(date(2026,10,14), "macro", "★미 9월 소비자물가(CPI)"))
