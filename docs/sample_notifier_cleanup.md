# 샘플 30개 알림 제거 절차 (일회성 코드 정리)

작성일: 2026-04-22
관련 TODO: `[P2][OPS] 샘플 30개 알림 코드 제거`

## 언제 실행하는가

텔레그램에서 아래 형식의 메시지를 수신한 직후.

```
📊 성과 추적 샘플 30개 도달
- 1m 유효 샘플: N개
- strong_buy: N, buy: N, hold: N, sell: N
- 다음 단계: tools/analyze_performance.py 구현 착수 가능
```

## 왜 제거하는가

- 이 알림은 **일회성** 목적. `data/.sample_notified` 플래그 덕에 실수로 재발송되진 않지만,
  잡은 매일 16:30 KST에 트리거되어 매번 플래그만 확인하고 종료.
- 불필요한 cron 트리거는 로그 노이즈. 메시지 수신 확정 후 정리.

## 제거 절차

### 0. 사전 확인
```
cat data/.sample_notified   # 파일 내용 확인 (samples=N, 신호별 건수)
ls -la data/.sample_notified
```

### 1. main.py에서 스케줄 등록 블록 제거
`start_scheduler()` 내부의 다음 블록 **전체 삭제** (약 20줄):

```python
from tools.sample_threshold_notifier import (
    FLAG_PATH as _SAMPLE_FLAG_PATH,
    scheduled_job as _sample_threshold_job,
)
if not _SAMPLE_FLAG_PATH.exists():
    scheduler.add_job(
        _sample_threshold_job,
        CronTrigger(...),
        id="sample_threshold_notifier",
        ...
    )
    logger.info(
        "스케줄러 등록: 매일 16:30 샘플 30개 알림 (일회성)")
else:
    logger.info(
        "샘플 30개 알림 이미 발송됨 → 잡 등록 스킵 (%s)",
        _SAMPLE_FLAG_PATH,
    )
```

### 2. notifier 모듈 + 테스트 파일 삭제
```
rm tools/sample_threshold_notifier.py
rm tests/test_sample_notifier.py
```

### 3. 플래그 파일 삭제
```
rm data/.sample_notified
```

### 4. main.py import 의존이 잔존하지 않는지 확인
```
grep -rn "sample_threshold_notifier\|sample_notified" --include="*.py"
# 출력 없어야 함
```

### 5. TODO.md 완료 이동
`[P2][OPS] 샘플 30개 알림 코드 제거`를 `## 완료됨` 섹션으로 이동.

### 6. 커밋
```
git add -A
git commit -m "chore: remove one-time sample threshold notifier"
```

### 7. 봇 재시작
```
ps -ef | grep "main.py --bot" | grep -v grep
kill <PID>
sleep 3
cd /root/kospianal/kospi-analyzer
nohup ../venv/bin/python main.py --bot </dev/null >> logs/bot.stdout.log 2>&1 &
disown
tail -30 logs/kospi_analyzer.log
```

재시작 로그에서 "샘플 30개 알림" 문구가 사라졌는지 확인 → 완료.

## 비상 시 재활성화

제거 후 다시 필요해지면 `git revert <removal_commit>` 한 번으로 복원.
