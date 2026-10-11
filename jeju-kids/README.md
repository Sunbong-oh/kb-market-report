# 제주 아이 행사 알리미

4살 아이가 신청할 만한 제주 프로그램(도서관 책 읽어주기·공연·체험·체육·영어·미술·요리)과 행사의 **신청 날짜를 놓치지 않게** 텔레그램으로 알려 주고, 모은 내용을 사이트로 보여 줍니다.

## 알림 받는 때
- 신청 시작 **3일 전**, **전날 저녁 8시**, **당일 아침 6시**
- 신청 마감 전날
- 기관 누리집·뉴스에 유아·가족 대상 **새 공고가 뜨면** 바로 (아침 6시, 저녁 8시 확인)
- **월요일 아침**: 앞으로 2주 신청 일정 요약

## 파일
| 파일 | 내용 |
| --- | --- |
| `events.json` | 직접 관리하는 일정. 신청 날짜를 알게 되면 여기에 `apply_start`를 넣으면 알림이 걸립니다 |
| `sources.json` | 매일 확인하는 기관 누리집과 뉴스 검색어 |
| `kids.py` | 수집·알림·사이트 생성 |
| `template.html` | 사이트 화면 |
| `state/` | 이미 본 공고·보낸 알림 기록 (Actions가 자동 갱신) |
| `site/` | 생성된 사이트 (GitHub Pages로 배포) |

## 처음 설정 (한 번만)
1. 이 브랜치를 `main`에 합칩니다. 예약 실행은 기본 브랜치에서만 돕니다.
2. 텔레그램 비밀값은 장마감 리포트에 쓰는 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`를 그대로 씁니다.
   다른 대화방(예: 배우자와 함께 쓰는 그룹)으로 받고 싶으면 Secrets에 `KIDS_TELEGRAM_CHAT_ID`를 추가하세요.
3. 사이트: 저장소 Settings → Pages → Source를 **GitHub Actions**로 바꾸면
   `https://sunbong-oh.github.io/kb-market-report/` 에 열립니다.
4. Actions 탭 → "제주 아이 행사 알리미" → Run workflow로 첫 실행을 해 보세요.

## 일정 추가 예시
```json
{
  "id": "jjkids-saturday-2026-11",
  "title": "토요가족체험 11월 1주",
  "kind": "dated",
  "categories": ["체험"],
  "apply_start": "2026-10-27T09:00",
  "event_start": "2026-11-07",
  "url": "https://org.jje.go.kr/reserve/index.jje"
}
```

## 로컬 실행
```
python jeju-kids/kids.py --no-send             # 텔레그램 대신 화면에 출력
python jeju-kids/kids.py --no-send --now 2026-10-07T20:00   # 특정 시각 기준으로 알림 확인
```
