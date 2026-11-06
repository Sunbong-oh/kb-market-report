# trigger

이 폴더의 파일이 push 되면 `close-report.yml` 워크플로가 장마감 리포트(하루 한 번)를 실행합니다.
Claude 클라우드 루틴이 평일 15:55(KST)에 `close-request.txt`를 갱신해 올립니다.

주 경로는 실시간 수급 작업(`live_snapshot.py` `kick_close`)이 평일 15:52·16:45에 직접 띄우는 실행입니다. 이 폴더는 예비 경로입니다.
