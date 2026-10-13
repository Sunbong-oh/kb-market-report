from datetime import datetime
import market_calendar as m
print("\n".join(m.build_section(datetime(2026, 10, 13, 15, 50))))
import calendar_values as cv
from datetime import date
print(cv.annotate(date(2026, 10, 1), "earnings_us", "★마이크론"))
print(cv.annotate(date(2026, 9, 17), "macro", "★미 FOMC 금리결정(한국 18일 새벽 3시)"))
print(cv.annotate(date(2026, 10, 15), "macro", "★미 9월 PPI·소매판매"))
