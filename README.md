# 🔌 Edge-IoT

> **Raspberry Pi 기반 화재·연기 감지 엣지 디바이스 시스템**

[![CI](https://github.com/JeffKM/edge-IoT/actions/workflows/ci.yml/badge.svg)](https://github.com/JeffKM/edge-IoT/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)](https://www.python.org/)
[![YOLOv11](https://img.shields.io/badge/YOLOv11n-NCNN-FF6F00)](https://docs.ultralytics.com/)
[![LiveKit](https://img.shields.io/badge/LiveKit-WebRTC-5A67D8)](https://livekit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

인하대학교 캡스톤 디자인 프로젝트(팀: `inha-capstone-04`) — [Ember Sentinel](https://github.com/JeffKM/ember-sentinel) 시스템의 엣지 디바이스 소프트웨어입니다. USB 카메라로 촬영한 영상을 YOLOv11n 모델로 실시간 추론하고, 화재/연기 감지 시 BLE 경보 + 서버 이벤트 발행 + WebRTC 실시간 스트리밍을 수행합니다.

---

## 목차

- [시스템 아키텍처](#시스템-아키텍처)
- [하드웨어 구성](#하드웨어-구성)
- [소프트웨어 구성](#소프트웨어-구성)
- [시작하기](#시작하기)
- [실행 모드](#실행-모드)
- [설정](#설정)
- [동작 로직](#동작-로직)
- [프로젝트 구조](#프로젝트-구조)
- [관련 레포지토리](#관련-레포지토리)

---

## 시스템 아키텍처

```
USB 카메라 (640×480)
    │
    ▼
YOLOv11n NCNN 추론 (conf ≥ 0.4)
    │
    ├── 화재/연기 감지 시:
    │   ├── BLE → Arduino Nano 33 BLE → 부저 경보 (10초)
    │   ├── HTTP POST → 서버 화재 이벤트 발행 → FCM 푸시 알림
    │   └── WebRTC → LiveKit SFU → 모바일 앱 실시간 시청
    │
    └── 미감지 시:
        └── BLE 하트비트 전송 (1초 간격)
```

---

## 하드웨어 구성

| 장치 | 모델 | 역할 |
|------|------|------|
| 메인 보드 | Raspberry Pi 5 (8GB) | YOLO 추론 + 스트리밍 |
| 카메라 | USB 웹캠 | 영상 캡처 (640×480) |
| 경보 장치 | Arduino Nano 33 BLE | BLE 수신 → 부저 경보 |
| 부저 | 능동 부저 (GPIO 12) | 0.5초 간격 ON/OFF 패턴 |

---

## 소프트웨어 구성

| 파일 | 역할 |
|------|------|
| `main.py` | **핵심 시스템** — 카메라 캡처 → YOLO 추론 → BLE 경보 → 서버 발행 → LiveKit 스트리밍 |
| `simulator.py` | **macOS 시뮬레이터** — Raspberry Pi 없이 웹캠 또는 영상 파일로 E2E 테스트 |
| `client.py` | **테스트 클라이언트** — YOLO/BLE 없이 순수 LiveKit 스트리밍 테스트 |
| `arduino.ino` | **Arduino 펌웨어** — BLE로 명령 수신 → 부저 10초간 ON/OFF |
| `config_loader.py` | YAML 설정 파싱 유틸리티 |
| `logger.py` | 로그 로테이션 + 외부 라이브러리 로그 억제 |

### 기술 스택

| 분류 | 기술 | 버전 |
|------|------|------|
| Language | Python | 3.11+ |
| Computer Vision | OpenCV | 4.8.0+ |
| AI 추론 | YOLOv11n (Ultralytics) | 8.3.0+ |
| 스트리밍 | LiveKit Python SDK | 0.11.0+ |
| BLE | bleak | 0.21.0+ |
| 설정 | PyYAML | 6.0+ |
| 코드 품질 | Ruff + Mypy | 0.8.0+, 1.13.0+ |

---

## 시작하기

### Raspberry Pi (프로덕션)

```bash
git clone https://github.com/JeffKM/edge-IoT.git
cd edge-IoT
python -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt

# NCNN 모델 배치 (ember-sentinel-ai에서 학습)
# → experiments/yolov11n/weights/best_ncnn_model

# 실행
python main.py --config config.yaml
```

### macOS (시뮬레이터)

```bash
git clone https://github.com/JeffKM/edge-IoT.git
cd edge-IoT
python -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt

# PyTorch 모델 배치 (NCNN은 ARM Linux 전용)
# → experiments/yolov11n/weights/best.pt

# 웹캠으로 실행
python simulator.py --config config.production.yaml

# 영상 파일로 실행
python simulator.py --config config.production.yaml --source samples/fire-sample.mp4

# GUI 없이 실행 (CI/E2E 테스트용)
python simulator.py --config config.production.yaml --source samples/fire-sample.mp4 --headless
```

### Arduino 펌웨어 업로드

Arduino IDE에서 `arduino.ino`를 Arduino Nano 33 BLE에 업로드합니다.

- **BLE 서비스 UUID**: `12345678-1234-5678-1234-56789abcdef0`
- **BLE 특성 UUID**: `12345678-1234-5678-1234-56789abcdef1`
- **광고 이름**: `Nano33BLE-Fire`

---

## 실행 모드

| 모드 | 명령어 | YOLO | BLE | 스트리밍 | 용도 |
|------|--------|------|-----|---------|------|
| 프로덕션 | `python main.py` | ✓ NCNN | ✓ | ✓ | RPi5 실제 배포 |
| BLE 없이 | `python main.py --no-ble` | ✓ | Mock | ✓ | BLE 하드웨어 없이 테스트 |
| 시뮬레이터 | `python simulator.py` | ✓ PyTorch | ✗ | ✓ | macOS 개발/테스트 |
| 헤드리스 | `python simulator.py --headless` | ✓ | ✗ | ✓ | CI/E2E 자동화 |
| 스트림 테스트 | `python client.py` | ✗ | ✗ | ✓ | LiveKit 인프라 검증 |

---

## 설정

### config.yaml (개발/로컬)

```yaml
server:
  api_url: "http://localhost:8080"
  fire_event_endpoint: "/embedded/fire-event/publish"

livekit:
  url: "ws://localhost:7880"

device:
  uuid: "cam-uuid-001"
  api_key: "dev-api-key-001"

ble:
  arduino_mac: "90:9F:4D:1A:35:A1"
  characteristic_uuid: "12345678-1234-5678-1234-56789abcdef1"

yolo:
  model_path: "./experiments/yolov11n/weights/best_ncnn_model"
  confidence: 0.4
  image_size: 640

camera:
  index: 0
  width: 640
  height: 480

stream:
  max_duration: 30        # 최대 스트리밍 시간 (초)
  restart_delay: 60       # 재시작 대기 시간 (초)
  alert_cooldown: 10      # 경보 쿨다운 (초)
```

### config.production.yaml (프로덕션/EC2)

```yaml
server:
  api_url: "http://<EC2_IP>:8080"

livekit:
  url: "wss://<LIVEKIT_CLOUD_URL>"

yolo:
  model_path: "./experiments/yolov11n/weights/best.pt"  # macOS: PyTorch
```

---

## 동작 로직

### 상태 머신

```
[모니터링] ──감지──→ [경보 발생] ──LiveKit 연결──→ [스트리밍 중]
     ↑                    │                            │
     │                    │                     30초 경과 또는
     │              BLE 쿨다운 10초              미감지 10초 지속
     │                    │                            │
     └────────────────────┘                    [스트리밍 종료]
                                                       │
                                                 60초 대기 후
                                                       │
                                               [모니터링] 복귀
```

### 주요 타이머

| 타이머 | 기본값 | 용도 |
|--------|--------|------|
| 경보 쿨다운 | 10초 | BLE 경보 중복 방지 |
| 최대 스트리밍 | 30초 | 세션당 최대 송출 시간 |
| 재시작 대기 | 60초 | 스트리밍 종료 후 재시작까지 대기 |
| 하트비트 | 1초 | BLE 연결 유지 |
| 프레임 드롭 임계값 | 10회 | 연속 드롭 시 카메라 재연결 |

### 에러 처리

- **API 호출**: 지수 백오프 재시도 (1초 → 2초 → 4초 → 최대 10초, 3회)
- **카메라 장애**: 연속 프레임 드롭 감지 → 자동 재연결
- **BLE 미연결**: `--no-ble` 플래그로 Mock BLE 사용
- **네트워크 장애**: 타임아웃 보호 (5초), 연결 실패 시 graceful degradation

---

## 프로젝트 구조

```
edge-IoT/
├── main.py                    # Raspberry Pi 핵심 시스템
├── simulator.py               # macOS 시뮬레이터
├── client.py                  # LiveKit 스트리밍 테스트 클라이언트
├── config_loader.py           # YAML 설정 파싱
├── logger.py                  # 로깅 (로테이션 + 외부 라이브러리 억제)
├── arduino.ino                # Arduino Nano 33 BLE 펌웨어
├── config.yaml                # 개발 설정 (localhost)
├── config.production.yaml     # 프로덕션 설정 (EC2 + LiveKit Cloud)
├── pyproject.toml             # Ruff + Mypy 설정
├── requirements.txt           # 런타임 의존성
├── requirements-dev.txt       # 개발 의존성 (Ruff, Mypy)
├── .github/workflows/
│   └── ci.yml                 # GitHub Actions (Ruff lint + Mypy 타입 체크)
├── docs/
│   └── macos-simulator-guide.md  # macOS 시뮬레이터 가이드
├── samples/
│   └── README.md              # 테스트 영상 준비 가이드
└── experiments/               # YOLO 모델 (다운로드 필요)
    └── yolov11n/weights/
        ├── best_ncnn_model/   # RPi5용 NCNN 모델
        └── best.pt            # macOS용 PyTorch 모델
```

---

## 관련 레포지토리

| 레포지토리 | 역할 | 기술 스택 |
|------------|------|-----------|
| [ember-sentinel](https://github.com/JeffKM/ember-sentinel) | 모바일 앱 | React Native 0.81, Expo 54 |
| [ember-sentinel-server](https://github.com/JeffKM/ember-sentinel-server) | 백엔드 API | Java 17, Spring Boot 3.5 |
| [ember-sentinel-ai](https://github.com/JeffKM/ember-sentinel-ai) | AI 모델 학습 | Python, YOLOv11n, NCNN |
| [Terraform-Bastion-Server](https://github.com/JeffKM/Terraform-Bastion-Server) | AWS 인프라 IaC | Terraform, AWS |

---

<div align="center">

**인하대학교 캡스톤 디자인 — 팀 `inha-capstone-04`**

</div>
