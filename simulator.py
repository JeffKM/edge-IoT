"""Ember Sentinel 엣지 시뮬레이터.

라즈베리파이 없이 노트북에서 엣지 디바이스 동작을 시뮬레이션한다.
- 입력 소스: 웹캠 또는 비디오 파일
- YOLO 추론으로 화재/연기 감지
- 감지 시 로컬 Docker 서버 API 호출
- LiveKit 실시간 스트리밍 지원
- BLE는 항상 비활성 (시뮬레이터 전용)

사용법:
    python simulator.py                              # 웹캠 입력
    python simulator.py --source samples/test.mp4    # 비디오 파일 입력
    python simulator.py --headless                   # GUI 없이 실행
    python simulator.py --config config.yaml         # 설정 파일 지정
"""

import cv2
import time
import asyncio
import argparse
import requests
import json
import sys
import numpy as np
from ultralytics import YOLO
from livekit import rtc
from pathlib import Path

from config_loader import load_config, add_config_arg

# --- ANSI 컬러 코드 ---
class Color:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"


def log_info(msg: str):
    print(f"{Color.GREEN}[INFO]{Color.RESET} {msg}")


def log_warn(msg: str):
    print(f"{Color.YELLOW}[WARN]{Color.RESET} {msg}")


def log_error(msg: str):
    print(f"{Color.RED}[ERROR]{Color.RESET} {msg}")


def log_detect(msg: str):
    print(f"{Color.RED}{Color.BOLD}[FIRE]{Color.RESET} {msg}")


def log_stream(msg: str):
    print(f"{Color.BLUE}[STREAM]{Color.RESET} {msg}")


def log_api(msg: str):
    print(f"{Color.MAGENTA}[API]{Color.RESET} {msg}")


# --- CLI 인자 파싱 ---
parser = argparse.ArgumentParser(
    description="Ember Sentinel 엣지 시뮬레이터 — 노트북에서 화재 감지 시뮬레이션"
)
add_config_arg(parser)
parser.add_argument(
    "--source",
    type=str,
    default="webcam",
    help="입력 소스: 'webcam' 또는 비디오 파일 경로 (기본값: webcam)",
)
parser.add_argument(
    "--headless",
    action="store_true",
    help="GUI 없이 실행 (cv2.imshow 비활성화)",
)
args = parser.parse_args()

# --- 설정 로드 ---
cfg = load_config(args.config)

LIVEKIT_URL = cfg.livekit.url
API_BASE_URL = cfg.server.api_url
FIRE_EVENT_ENDPOINT = cfg.server.fire_event_endpoint
TOKEN_API_URL = f"{API_BASE_URL}{FIRE_EVENT_ENDPOINT}"
DEVICE_UUID = cfg.device.uuid
DEVICE_API_KEY = cfg.device.api_key
API_HEADERS = {"X-Device-API-Key": DEVICE_API_KEY}

MODEL_PATH = cfg.yolo.model_path
CONF_THRESHOLD = cfg.yolo.confidence
IMG_SIZE = cfg.yolo.image_size
ALERT_COOLDOWN = cfg.stream.alert_cooldown

MAX_STREAM_DURATION = cfg.stream.max_duration
STREAM_RESTART_DELAY = cfg.stream.restart_delay

CAMERA_WIDTH = cfg.camera.width
CAMERA_HEIGHT = cfg.camera.height


# --- LiveKit Manager ---
class LiveKitManager:
    """LiveKit 스트리밍 매니저."""

    def __init__(self):
        self.room = None
        self.video_source = None
        self.is_connected = False
        self.is_connecting = False
        self.width = CAMERA_WIDTH
        self.height = CAMERA_HEIGHT

    async def connect(self, detection_type="UNKNOWN"):
        if self.is_connected or self.is_connecting:
            return

        self.is_connecting = True
        log_api(f"토큰 요청 중 (type: {detection_type})...")

        try:
            payload = {"deviceUuid": DEVICE_UUID, "detectionType": detection_type}
            log_api(f"Payload: {payload}")

            response = requests.post(TOKEN_API_URL, json=payload, headers=API_HEADERS, timeout=5)
            response.raise_for_status()

            data = response.json()
            token = data.get("token")
            fire_event_id = data.get("fireEventId")

            if not token:
                log_error("토큰을 받지 못했습니다.")
                return

            log_stream("LiveKit 서버에 연결 중...")
            self.room = rtc.Room()

            await asyncio.wait_for(
                self.room.connect(LIVEKIT_URL, token), timeout=10.0
            )

            try:
                metadata_dict = {
                    "type": "PUBLISHER",
                    "fireEventId": fire_event_id,
                    "status": detection_type,
                    "updated_at": time.time(),
                }
                new_meta_str = json.dumps(metadata_dict)
                if self.room.local_participant:
                    await self.room.local_participant.update_metadata(new_meta_str)
            except Exception as meta_error:
                log_warn(f"메타데이터 업데이트 실패: {meta_error}")

            self.video_source = rtc.VideoSource(self.width, self.height)
            track = rtc.LocalVideoTrack.create_video_track(
                "fire_cam_track", self.video_source
            )

            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_CAMERA
            options.video_codec = rtc.VideoCodec.VP8

            await self.room.local_participant.publish_track(track, options)

            self.is_connected = True
            log_stream("연결 완료! 스트리밍 시작.")

        except requests.exceptions.ConnectionError:
            log_warn(f"서버 연결 실패 ({API_BASE_URL}). 서버가 실행 중인지 확인하세요.")
            self.is_connected = False
            if self.room:
                await self.room.disconnect()

        except Exception as e:
            log_error(f"LiveKit 연결 실패: {e}")
            self.is_connected = False
            if self.room:
                await self.room.disconnect()

        finally:
            self.is_connecting = False

    def send_frame(self, frame):
        if not self.is_connected or self.video_source is None:
            return

        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height))

        frame_bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
        frame_argb = frame_bgra[:, :, [3, 2, 1, 0]]

        video_frame = rtc.VideoFrame(
            self.width, self.height, rtc.VideoBufferType.ARGB, frame_argb.tobytes()
        )
        self.video_source.capture_frame(video_frame)

    async def disconnect(self):
        if self.room:
            await self.room.disconnect()

        self.is_connected = False
        self.is_connecting = False
        log_stream("연결 해제.")


# --- 메인 시뮬레이터 ---
async def run_simulator():
    cap = None
    streamer = LiveKitManager()
    headless = args.headless
    source = args.source

    # 배너 출력
    print(f"\n{Color.CYAN}{Color.BOLD}{'=' * 50}")
    print("  Ember Sentinel — Edge IoT Simulator")
    print(f"{'=' * 50}{Color.RESET}\n")

    # 1. YOLO 모델 로드
    log_info(f"YOLO 모델 로드: {MODEL_PATH}")
    try:
        model = YOLO(MODEL_PATH, task="detect")
    except Exception as e:
        log_error(f"모델 로드 실패: {e}")
        return

    # 2. 입력 소스 설정
    if source == "webcam":
        log_info(f"입력 소스: 웹캠 (index={cfg.camera.index})")
        cap = cv2.VideoCapture(cfg.camera.index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

        if not cap.isOpened():
            log_info(f"카메라 {cfg.camera.index}번 실패, {cfg.camera.index + 1}번 시도...")
            cap = cv2.VideoCapture(cfg.camera.index + 1)
            if not cap.isOpened():
                log_error("카메라를 열 수 없습니다.")
                return
    else:
        video_path = Path(source)
        if not video_path.exists():
            log_error(f"비디오 파일을 찾을 수 없습니다: {source}")
            return
        log_info(f"입력 소스: 비디오 파일 ({source})")
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            log_error(f"비디오 파일을 열 수 없습니다: {source}")
            return

    log_info(f"BLE: 비활성 (시뮬레이터 모드)")
    log_info(f"API 서버: {API_BASE_URL}")
    log_info(f"LiveKit: {LIVEKIT_URL}")
    log_info(f"Headless: {'Yes' if headless else 'No'}")
    log_info(f"모니터링 시작. {'Ctrl+C로 종료' if headless else 'q 키로 종료'}\n")

    last_alert_time = 0
    last_fire_seen_time = 0
    stream_start_time = 0
    last_stream_end_time = 0
    last_trigger_time = 0
    frame_count = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                if source != "webcam":
                    log_info("비디오 끝. 처음부터 반복 재생합니다.")
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                log_error("프레임 드롭.")
                break

            frame_count += 1

            # YOLO 추론
            results = model(frame, imgsz=IMG_SIZE, conf=CONF_THRESHOLD, verbose=False)
            annotated_frame = results[0].plot()

            boxes = results[0].boxes
            fire_detected = len(boxes) > 0
            current_time = time.time()

            # --- 화재/연기 감지 시 ---
            if fire_detected:
                last_fire_seen_time = current_time

                cls_id = int(boxes.cls[0].item())
                detected_label = model.names[cls_id].upper()
                confidence = float(boxes.conf[0].item())

                # 쿨다운 경과 시 알람 로깅
                if current_time - last_alert_time > ALERT_COOLDOWN:
                    log_detect(
                        f"{detected_label} 감지! (신뢰도: {confidence:.2f}) "
                        f"[Frame #{frame_count}]"
                    )
                    last_alert_time = current_time

                # 스트리밍 시작
                if not streamer.is_connected and not streamer.is_connecting:
                    time_since_last_stream = current_time - last_stream_end_time
                    time_since_trigger = current_time - last_trigger_time

                    if (
                        time_since_last_stream > STREAM_RESTART_DELAY
                        and time_since_trigger > STREAM_RESTART_DELAY
                    ):
                        log_stream(
                            f"{detected_label} 감지! 서버에 이벤트 발행 + 스트리밍 시작..."
                        )
                        last_trigger_time = current_time
                        stream_start_time = current_time

                        await streamer.connect(detection_type=detected_label)

            # --- 스트리밍 중단 조건 ---
            if streamer.is_connected:
                elapsed = current_time - stream_start_time

                if elapsed > MAX_STREAM_DURATION:
                    log_stream(
                        f"최대 지속 시간({MAX_STREAM_DURATION}초) 도달. 스트리밍 중단."
                    )
                    await streamer.disconnect()
                    last_stream_end_time = current_time

                elif current_time - last_fire_seen_time > 10.0:
                    log_stream("10초간 미감지. 스트리밍 중단.")
                    await streamer.disconnect()
                    last_stream_end_time = current_time

            # --- 프레임 전송 ---
            if streamer.is_connected:
                streamer.send_frame(frame)

            # --- GUI 표시 ---
            if not headless:
                status_text = "Monitoring..."
                color = (0, 255, 0)

                if streamer.is_connected:
                    remaining = int(
                        MAX_STREAM_DURATION - (current_time - stream_start_time)
                    )
                    status_text = f"STREAMING ON ({remaining}s)"
                    color = (0, 0, 255)
                elif streamer.is_connecting:
                    status_text = "Connecting..."
                    color = (255, 165, 0)
                elif fire_detected:
                    wait_time = int(
                        STREAM_RESTART_DELAY - (current_time - last_stream_end_time)
                    )
                    if 0 < wait_time < STREAM_RESTART_DELAY:
                        status_text = f"Cooldown: {wait_time}s"
                        color = (0, 255, 255)

                cv2.putText(
                    annotated_frame,
                    status_text,
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    color,
                    2,
                )

                cv2.imshow("Ember Sentinel Simulator", annotated_frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    log_info("'q' 키 입력으로 종료합니다.")
                    break

            await asyncio.sleep(0.01)

    except KeyboardInterrupt:
        log_info("Ctrl+C 입력으로 종료합니다.")

    except Exception as e:
        log_error(f"시스템 오류: {e}")

    finally:
        if cap is not None:
            cap.release()
        if not headless:
            cv2.destroyAllWindows()
        await streamer.disconnect()
        print(f"\n{Color.CYAN}[INFO] 시뮬레이터 종료.{Color.RESET}")


if __name__ == "__main__":
    asyncio.run(run_simulator())
