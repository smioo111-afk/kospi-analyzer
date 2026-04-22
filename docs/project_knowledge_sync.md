# Project Knowledge 재업로드 체크리스트

작성일: 2026-04-22
목적: Claude.ai 프로젝트의 Project Knowledge와 서버 실제 코드의 동기화.

## 왜 필요한가

채팅의 Project Knowledge에 업로드된 파일이 서버에서 운영 중인 코드와
버전이 다를 수 있다. 이 상태에서 Claude가 리뷰·설계를 하면
실제 코드와 무관한 조언을 하게 된다.

## 업로드할 파일 목록

필수:
  [ ] config/settings.py
  [ ] collectors/kis_api.py
  [ ] collectors/dart_api.py
  [ ] analysis/scorer.py
  [ ] analysis/signals.py
  [ ] analysis/stoploss.py
  [ ] database/models.py
  [ ] database/history.py
  [ ] bot/telegram_bot.py
  [ ] bot/formatter.py
  [ ] performance_analyzer.py (또는 analysis/performance_analyzer.py)
  [ ] main.py
  [ ] requirements.txt

선택 (있으면 업로드):
  [ ] tests/test_integration.py
  [ ] docs/ 디렉토리 전체
  [ ] .env.example (실제 .env는 절대 올리지 말 것)

## 업로드 전 자동 검증 (현재 상태)

아래 값은 2026-04-22 시점 서버 실코드 조사 결과다.

파일 버전:
  - config/settings.py:            v3.0
  - collectors/kis_api.py:         (모듈 docstring에 버전 표기 없음)
  - collectors/dart_api.py:        (모듈 docstring에 버전 표기 없음. 내부 주석 "v3.0 신설" 존재)
  - analysis/scorer.py:            v3.0

설정값 비교:
  - SUPPLY_DEMAND 최대 배점:       8점            (채팅 메모리: 8점)          ✔ 일치
  - SECTOR_AVG_PER 항목 수:        24개 (기타 포함) (채팅 메모리: 19개 동적)    ✘ 불일치
  - dart_api.py parquet 사용:      Yes            (채팅 메모리: SQLite로 대체됨) ✘ 불일치
  - scorer.py _calc_fair_value:    3-모델 합성 (PER + PBR + EV/EBITDA, 금융/보험/증권은 EV/EBITDA 스킵)  (채팅 메모리: 3-모델 합성) ✔ 일치
  - models.py financial_metrics:   Yes            (채팅 메모리: 존재)         ✔ 일치

불일치 항목: 2건.

해석:
  - "SECTOR_AVG_PER 19개 → 24개"는 섹터가 더 세분화된 방향으로 확장된 것으로 보임
    (보험/증권을 금융에서 분리, 일반서비스·오락·문화·인프라투용 추가).
    채팅 메모리가 낡은 쪽이다.
  - "SQLite로 대체" 메모는 잘못된 기억이거나 취소된 계획.
    현재 DART 재무 캐시는 여전히 _save_to_cache / _load_from_cache에서 parquet 사용 중.

결론: Project Knowledge 재업로드 필요 (yes).

## 업로드 순서

1. 채팅 프로젝트 화면 열기.
2. Project Knowledge 섹션에서 기존 .py 파일을 모두 삭제.
3. 서버에서 최신 파일을 로컬로 내려받기.
     예) scp -r user@server:/root/kospianal/kospi-analyzer/ ./sync_tmp/
4. 위 "업로드할 파일 목록"을 하나씩 업로드.
5. 업로드 완료 후 Claude에게 다음을 요청:
     "Project Knowledge의 scorer.py에서 _calc_fair_value 모델 수를 확인해줘"
   답변이 3-모델이면 동기화 성공.

## 주의 사항

- .env 파일은 절대 업로드하지 말 것 (API Key 노출).
- token_cache/ 디렉토리 내용은 업로드하지 말 것.
- data/*.db 파일은 업로드하지 말 것 (개인 포트폴리오 정보).
- logs/ 디렉토리는 업로드하지 말 것.

## 재동기화 주기

제안: 분기 1회 또는 다음 상황 시.
  - 스코어링 로직 변경 후
  - 새 테이블 추가 후
  - 설계서(kospi_analyzer_design.docx)가 업데이트된 후
