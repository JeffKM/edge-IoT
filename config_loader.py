"""설정 파일 로더 유틸리티.

YAML 설정 파일을 로드하고, 점 표기법으로 중첩 키에 접근할 수 있는 Config 객체를 제공한다.

사용법:
    cfg = load_config("config.yaml")
    print(cfg.server.api_url)
    print(cfg.yolo.confidence)
"""

import argparse
import yaml
from pathlib import Path


class Config:
    """중첩 딕셔너리를 속성 접근으로 사용할 수 있는 설정 래퍼."""

    def __init__(self, data: dict):
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, Config(value))
            else:
                setattr(self, key, value)

    def __repr__(self):
        return f"Config({vars(self)})"


def load_config(path: str = "config.yaml") -> Config:
    """YAML 설정 파일을 로드하여 Config 객체로 반환한다."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return Config(data)


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    """argparse 파서에 --config 인자를 추가한다."""
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="설정 파일 경로 (기본값: config.yaml)",
    )
