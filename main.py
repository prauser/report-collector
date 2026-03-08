"""엔트리포인트 - 실시간 리스너 실행."""
import asyncio
import structlog

from collector.listener import start_listener

log = structlog.get_logger(__name__)

if __name__ == "__main__":
    log.info("report_collector_starting")
    asyncio.run(start_listener())
