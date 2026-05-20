"""Ember Sentinel 엣지 IoT 로깅 모듈.

Python logging 모듈 기반으로, 콘솔 + 파일 로테이션 출력을 지원한다.
config.yaml의 logging 섹션에서 레벨/파일 경로/로테이션 설정을 읽는다.

사용법:
    from logger import get_logger
    logger = get_logger(__name__)
    logger.info("시스템 시작")
    logger.warning("BLE 연결 실패, 재시도 중...")
    logger.error("카메라 열기 실패")
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 로그 포맷
_CONSOLE_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s — %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s (%(filename)s:%(lineno)d) — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 기본 설정값 (config.yaml에 logging 섹션이 없을 때 사용)
_DEFAULTS = {
    "level": "INFO",
    "file": "logs/edge-iot.log",
    "max_bytes": 5_242_880,  # 5MB
    "backup_count": 3,
}

_initialized = False


def setup_logging(cfg=None) -> None:
    """루트 로거를 설정한다.

    Args:
        cfg: config_loader.Config 객체. cfg.logging 속성에서 설정을 읽는다.
             None이면 기본값을 사용한다.
    """
    global _initialized
    if _initialized:
        return

    # 설정값 추출
    level_str = _DEFAULTS["level"]
    log_file = _DEFAULTS["file"]
    max_bytes = _DEFAULTS["max_bytes"]
    backup_count = _DEFAULTS["backup_count"]

    if cfg and hasattr(cfg, "logging"):
        log_cfg = cfg.logging
        level_str = getattr(log_cfg, "level", level_str)
        log_file = getattr(log_cfg, "file", log_file)
        max_bytes = getattr(log_cfg, "max_bytes", max_bytes)
        backup_count = getattr(log_cfg, "backup_count", backup_count)

    level = getattr(logging, level_str.upper(), logging.INFO)

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 기존 핸들러 제거 (중복 방지)
    root_logger.handlers.clear()

    # 1) 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, _DATE_FORMAT))
    root_logger.addHandler(console_handler)

    # 2) 파일 핸들러 (로테이션)
    try:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # 파일에는 DEBUG 이상 모두 기록
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, _DATE_FORMAT))
        root_logger.addHandler(file_handler)
    except OSError as e:
        root_logger.warning("로그 파일 생성 실패 (%s), 콘솔 출력만 사용합니다: %s", log_file, e)

    # 외부 라이브러리 로그 레벨 제한
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    logging.getLogger("livekit").setLevel(logging.WARNING)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """이름 기반 로거를 반환한다.

    setup_logging()이 호출되지 않았으면 기본 설정으로 자동 초기화한다.
    """
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)
