# macOS 엣지 시뮬레이터 실행 가이드

> 라즈베리파이 없이 macOS 노트북에서 edge-IoT 시뮬레이터를 실행하여
> 화재 감지 → LiveKit 스트리밍 → 서버 이벤트 발행을 수행하는 가이드

---

## 전체 흐름

```
[macOS 웹캠 / 샘플 영상]
    → [simulator.py + YOLO .pt 모델]
        → 화재/연기 감지
        → POST /embedded/fire-event/publish (EC2 서버)
        → LiveKit WebRTC 스트리밍 (LiveKit Cloud)
        → 모바일 앱에서 실시간 시청
```

---

## 1. 환경 설정

### 1.1 레포 클론 및 가상환경

> **⚠️ Python 버전**: numpy, ultralytics 등 과학 계산 패키지는 **Python 3.12 또는 3.13**을 권장합니다.
> Python 3.14(프리릴리즈)는 사전 빌드된 wheel이 없어 C++ 소스 컴파일이 필요하며, 빌드 실패할 수 있습니다.

```bash
cd /Users/jefflee/Projects
git clone https://github.com/JeffKM/edge-IoT.git  # 이미 클론한 경우 생략
cd edge-IoT

# Python 버전 확인 (3.12.x 또는 3.13.x 권장)
python3 --version

# pyenv를 사용하는 경우 (Python 3.14가 기본인 환경)
# pyenv install 3.12.8
# pyenv local 3.12.8

# Python 가상환경 생성
python3 -m venv venv
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

**핵심 패키지**: ultralytics, opencv-python, livekit, pyyaml, requests

### 1.2 YOLO 모델 준비

macOS에서는 **NCNN 대신 PyTorch(.pt) 모델을 직접 사용**합니다.
NCNN은 Raspberry Pi ARM Linux 전용이므로 macOS에서는 `.pt` 모델이 권장됩니다.

```bash
# 방법 A: Google Drive에서 학습된 모델 다운로드 (권장)
# → experiments/yolov11n/weights/best.pt 에 배치
mkdir -p experiments/yolov11n/weights/
# 다운로드한 best.pt를 위 경로에 복사

# 방법 B: Ultralytics 기본 모델 다운로드 (화재 감지 미학습, 파이프라인 테스트용)
python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"
```

### 1.3 macOS 호환성 검증

```bash
# YOLO 모델 로딩 테스트
python -c "
from ultralytics import YOLO
model = YOLO('experiments/yolov11n/weights/best.pt')
print('모델 로딩 성공')
"

# LiveKit Python SDK 테스트 (M1/M2 ARM64 호환)
python -c "import livekit; print('LiveKit SDK 로딩 성공')"

# 웹캠 접근 테스트
# 시스템 설정 → 개인 정보 보호 → 카메라 → 터미널 앱 허용 필요
python -c "
import cv2
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
print('웹캠 접근:', '성공' if ret else '실패')
if ret:
    print('프레임 크기:', frame.shape)
cap.release()
"
```

---

## 2. 프로덕션 설정

### 2.1 config.production.yaml 설정

프로젝트 루트의 `config.production.yaml`을 실제 값으로 수정합니다:

```yaml
server:
  api_url: "http://<YOUR_SERVER_IP>:8080"
  fire_event_endpoint: "/embedded/fire-event/publish"

livekit:
  url: "wss://<YOUR_LIVEKIT_URL>"

device:
  uuid: "<서버에 등록한 디바이스 UUID>"    # 아래 3절 참조
  api_key: "<서버에서 발급받은 API 키>"

yolo:
  model_path: "./experiments/yolov11n/weights/best.pt"
  confidence: 0.4
  image_size: 640

camera:
  index: 0      # macOS 내장 웹캠
  width: 640
  height: 480

stream:
  max_duration: 30    # 최대 스트리밍 시간 (초)
  restart_delay: 60   # 스트리밍 재시작 딜레이 (초)
  alert_cooldown: 10  # 감지 로그 쿨다운 (초)

logging:
  level: "INFO"
  file: "logs/simulator.log"
  max_bytes: 5242880   # 5MB
  backup_count: 3

retry:
  max_attempts: 3
  base_delay: 1.0
  max_delay: 10.0
```

> **디바이스 UUID와 API 키**: ember-sentinel 레포의 `scripts/e2e-verify.sh`를 사용하여
> 서버에 카메라 디바이스를 등록한 후 얻을 수 있습니다.

### 2.2 기본 config.yaml과의 차이점

| 항목 | config.yaml (개발) | config.production.yaml |
|------|---------------------|------------------------|
| `server.api_url` | `http://localhost:8080` | `http://<YOUR_SERVER_IP>:8080` |
| `livekit.url` | `ws://localhost:7880` | `wss://<YOUR_LIVEKIT_URL>` |
| `yolo.model_path` | `./experiments/.../best_ncnn_model` | `./experiments/.../best.pt` |

---

## 3. 서버에 카메라 디바이스 등록

시뮬레이터가 서버에 화재 이벤트를 발행하려면, 먼저 서버에 카메라 디바이스를 등록해야 합니다.

```bash
# ember-sentinel 레포의 E2E 검증 도구 사용
cd /Users/jefflee/Projects/ember-sentinel
./scripts/e2e-verify.sh
# 메뉴 2) 카메라 디바이스 등록 선택

# 등록 후 출력된 UUID를 config.production.yaml에 설정
```

또는 수동으로:

```bash
ACCESS_TOKEN="<앱 로그인 후 받은 JWT>"
ROOM_ID=1

curl -X POST http://<YOUR_SERVER_IP>:8080/room/${ROOM_ID}/camera-edge \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "deviceUuid": "<UUID>",
    "cameraEdgeAlias": "macOS-Webcam-Simulator"
  }'
```

---

## 4. 샘플 화재 영상 준비

웹캠 대신 샘플 영상을 사용하면 재현 가능한 테스트가 가능합니다.

```bash
# samples/ 디렉토리에 화재/연기 영상 배치
ls samples/

# 영상 소스 예시:
# - FASDD_CV 데이터셋의 테스트 영상
# - YouTube Creative Commons 화재 영상
# - ember-sentinel-ai 레포의 테스트 데이터
```

---

## 5. 시뮬레이터 실행

### 5.1 웹캠 입력

```bash
cd /Users/jefflee/Projects/edge-IoT
source venv/bin/activate

# 프로덕션 서버 + LiveKit Cloud 연동
python simulator.py --config config.production.yaml
```

### 5.2 비디오 파일 입력

```bash
python simulator.py --config config.production.yaml \
  --source samples/fire-sample.mp4
```

### 5.3 Headless 모드 (GUI 없이)

```bash
python simulator.py --config config.production.yaml --headless
```

### 동작 확인

시뮬레이터가 정상 동작하면 다음 로그가 순서대로 출력됩니다:

**웹캠 입력 시:**

```
INFO  YOLO 모델 로드 완료.
INFO  카메라 0번 열기 성공.
INFO  BLE: 비활성 (시뮬레이터 모드)
INFO  API 서버: http://<YOUR_SERVER_IP>:8080
INFO  LiveKit: wss://<YOUR_LIVEKIT_URL>
INFO  모니터링 시작.
WARNING 🔥 FIRE 감지! (신뢰도: 0.78)
INFO  FIRE 감지! 서버에 이벤트 발행 + 스트리밍 시작...
INFO  LiveKit 연결 완료. 스트리밍 시작.
```

**비디오 파일 입력 시 (검증 완료된 실제 로그):**

```
2026-05-23 22:04:35 [INFO ] simulator — Ember Sentinel — Edge IoT Simulator
2026-05-23 22:04:35 [INFO ] simulator — YOLO 모델 로드 완료.
2026-05-23 22:04:35 [INFO ] simulator — 입력 소스: 비디오 파일 (samples/fire-sample.mp4)
2026-05-23 22:04:35 [INFO ] simulator — BLE: 비활성 (시뮬레이터 모드)
2026-05-23 22:04:35 [INFO ] simulator — API 서버: http://<YOUR_SERVER_IP>:8080
2026-05-23 22:04:35 [INFO ] simulator — LiveKit: wss://<YOUR_LIVEKIT_URL>
2026-05-23 22:04:35 [INFO ] simulator — 모니터링 시작. Ctrl+C로 종료
2026-05-23 22:04:36 [WARNING] simulator — 🔥 SMOKE 감지! (신뢰도: 0.52) [Frame #1]
2026-05-23 22:04:36 [INFO ] simulator — SMOKE 감지! 서버에 이벤트 발행 + 스트리밍 시작...
2026-05-23 22:04:36 [INFO ] simulator — 토큰 요청 중 (type: SMOKE)...
2026-05-23 22:04:40 [INFO ] simulator — LiveKit 서버에 연결 중...
2026-05-23 22:04:44 [INFO ] simulator — LiveKit 연결 완료. 스트리밍 시작.
2026-05-23 22:04:46 [WARNING] simulator — 🔥 SMOKE 감지! (신뢰도: 0.51) [Frame #7]
2026-05-23 22:04:56 [WARNING] simulator — 🔥 SMOKE 감지! (신뢰도: 0.49) [Frame #52]
2026-05-23 22:05:06 [INFO ] simulator — 최대 스트리밍 시간(30s) 도달. 중단.
2026-05-23 22:05:06 [INFO ] simulator — LiveKit 연결 해제.
```

> fire-sample.mp4(6초 클립)은 자동 반복 재생되며, 약 4초 만에 SMOKE 감지 → 서버 이벤트 발행 → LiveKit 연결 → 30초 스트리밍 → 자동 중단까지 전체 E2E 플로우가 동작합니다.

---

## 6. E2E 검증 시나리오

### 시나리오 1: 화재 감지 → 푸시 알림 → 실시간 시청

1. 시뮬레이터 실행 (웹캠 앞에 화재 이미지 보여주기 또는 샘플 영상)
2. YOLO가 화재/연기 감지 (confidence >= 0.4)
3. `POST /embedded/fire-event/publish` 로 서버에 이벤트 발행
4. 서버: LiveKit Room 생성 + Egress(녹화) 시작 + FCM 푸시 알림
5. 서버로부터 LiveKit 토큰 수령
6. LiveKit에 WebRTC로 영상 퍼블리시 (최대 30초)
7. **Android 앱**: FCM 알림 수신 → 알림 탭 → CCTV 실시간 시청

### 시나리오 2: 자동 중단

- 화재 미감지 10초 지속 시 자동 스트리밍 중단
- 또는 최대 30초 후 자동 중단
- 중단 후 60초 쿨다운 (재시작 딜레이)

### 검증 결과 (2026-05-23)

| 단계 | 내용 | 결과 |
|:---:|------|:---:|
| 1 | 시뮬레이터 실행 (`fire-sample.mp4`, headless) | PASS |
| 2 | YOLO 감지 (SMOKE, 신뢰도 0.52, Frame #1) | PASS |
| 3 | `POST /embedded/fire-event/publish` → HTTP 201 | PASS |
| 4 | LiveKit Room 생성 (`fire_event_6`) | PASS |
| 5 | LiveKit 토큰 수령 (Publisher) | PASS |
| 6 | WebRTC 영상 퍼블리시 (30초) | PASS |
| 7 | 최대 스트리밍 시간 도달 → 자동 중단 | PASS |

> 수동 화재 이벤트 발행(`curl`)도 HTTP 201 정상 응답 확인됨.

---

## 7. 트러블슈팅

| 문제 | 원인 | 해결 |
|------|------|------|
| `모델 로드 실패` | .pt 파일 경로 오류 | `ls experiments/yolov11n/weights/best.pt` 확인 |
| `카메라 열기 실패` | 카메라 권한 미부여 | 시스템 설정 → 개인 정보 → 카메라 → 터미널 허용 |
| `서버 연결 실패` | EC2 서버 중단 | `curl http://<YOUR_SERVER_IP>:8080` 으로 확인 |
| `LiveKit 연결 타임아웃` | 네트워크 또는 토큰 문제 | LiveKit Cloud 대시보드에서 API 키 확인 |
| `import livekit 오류` | ARM64 호환성 | `pip install livekit --upgrade` |
| `numpy 빌드 실패 (metadata-generation-failed)` | Python 3.14 프리릴리즈 사용 | Python 3.12/3.13으로 전환: `pyenv install 3.12.8 && pyenv local 3.12.8` 후 venv 재생성 |
| `NCNN 모델 사용 불가` | macOS 미지원 | `.pt` 모델로 변경 (`config.production.yaml`) |
| `메타데이터 업데이트 실패` | LiveKit Python SDK 버전 차이 | 기능에 영향 없음 (무시 가능). `pip install livekit --upgrade`로 해결 가능 |
| `HTTP 429 (Rate Limit)` | 1분 내 중복 이벤트 발행 | Rate Limit 해제 대기 (1분) 또는 서버 설정 조정 |
| `h264 mmco: unref short failure` | 샘플 영상 인코딩 경고 | 동작에 영향 없음 (OpenCV 내부 경고, 무시 가능) |

---

## 8. 참고 파일

| 파일 | 역할 |
|------|------|
| `simulator.py` | 시뮬레이터 메인 (웹캠/비디오 + YOLO + LiveKit) |
| `main.py` | 라즈베리파이용 메인 (카메라 + YOLO + BLE + LiveKit) |
| `config.yaml` | 개발 환경 설정 (localhost) |
| `config.production.yaml` | 프로덕션 설정 (EC2 + LiveKit Cloud) |
| `config_loader.py` | YAML → Config 객체 변환 유틸리티 |
| `logger.py` | 로깅 설정 유틸리티 |
