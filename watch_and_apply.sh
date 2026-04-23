#!/bin/bash
# backfill_dates dry-run 완료 감시 → 자동 apply
LOG="C:/Users/praus/Projects/report-collector/backfill_dates_dryrun.log"
TOTAL=3397
CD="C:/Users/praus/Projects/report-collector"
PYTHON="$CD/.venv/Scripts/python.exe"

while true; do
    sleep 1800  # 30분 대기

    # 로그 파일 크기 변화로 프로세스 활성 여부 확인
    SIZE1=$(wc -c < "$LOG" 2>/dev/null)
    sleep 10
    SIZE2=$(wc -c < "$LOG" 2>/dev/null)
    
    DONE=$(grep -c "key_data_extracted" "$LOG" 2>/dev/null)
    NOW=$(date "+%Y-%m-%d %H:%M:%S")
    
    if [ "$SIZE1" = "$SIZE2" ]; then
        # 로그가 더 이상 안 커지면 → 완료
        echo "[$NOW] dry-run 완료! ($DONE/$TOTAL건). apply 시작합니다."
        cd "$CD"
        $PYTHON scripts/backfill_dates.py --apply 2>&1 | tee backfill_dates_apply.log
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] apply 완료."
        break
    else
        PCT=$(( DONE * 100 / TOTAL ))
        echo "[$NOW] 진행 중... $DONE/$TOTAL건 ($PCT%)"
    fi
done
