from datetime import date
import econ_values as v
for d, it in [(date(2026,10,14), "★미 9월 소비자물가(CPI)"), (date(2026,10,15), "미 9월 PPI·소매판매"), (date(2026,10,15), "★TSMC 실적"),
              (date(2026,10,14), "ASML 실적"), (date(2026,10,16), "미 9월 산업생산"), (date(2026,10,14), "연준 베이지북(한국 15일 새벽)")]:
    print(d, it, "=>", v.values(d, it))
