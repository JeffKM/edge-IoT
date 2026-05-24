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
from ultralytics import YOLO
from livekit import rtc
from pathlib import Path

from config_loader import load_config, add_config_arg
from logger import setup_logging, get_logger

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

# --- 설정 및 로깅 초기화 ---
cfg = load_config(args.config)
setup_logging(cfg)
logger = get_logger("simulator")

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

# 재시도 설정
RETRY_MAX = getattr(getattr(cfg, "retry", None), "max_attempts", 3)
RETRY_BASE_DELAY = getattr(getattr(cfg, "retry", None), "base_delay", 1.0)
RETRY_MAX_DELAY = getattr(getattr(cfg, "retry", None), "max_delay", 10.0)


def retry_sync(fn, description="작업", max_attempts=RETRY_MAX):
    """지수 백오프로 동기 작업을 재시도한다."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == max_attempts:
                logger.error("%s 최종 실패 (%d회 시도): %s", description, max_attempts, e)
                raise
            delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
            logger.warning(
                "%s 실패 (시도 %d/%d): %s — %.1f초 후 재시도",
                description, attempt, max_attempts, e, delay,
            )
            time.sleep(delay)


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
        """서버에 토큰을 요청하고 LiveKit에 연결한다."""
        if self.is_connected or self.is_connecting:
            return

        self.is_connecting = True
        logger.info("토큰 요청 중 (type: %s)...", detection_type)

        try:
            payload = {"deviceUuid": DEVICE_UUID, "detectionType": detection_type}
            logger.debug("API Payload: %s", payload)

            def request_token():
                resp = requests.post(TOKEN_API_URL, json=payload, headers=API_HEADERS, timeout=5)
                resp.raise_for_status()
                return resp.json()

            data = retry_sync(request_token, "토큰 요청")

            token = data.get("token")
            fire_event_id = data.get("fireEventId")

            if not token:
                logger.error("서버가 토큰을 반환하지 않았습니다.")
                return

            logger.info("LiveKit 서버에 연결 중...")
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
                logger.warning("메타데이터 업데이트 실패: %s", meta_error)

            self.video_source = rtc.VideoSource(self.width, self.height)
            track = rtc.LocalVideoTrack.create_video_track(
                "fire_cam_track", self.video_source
            )

            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_CAMERA
            options.video_codec = rtc.VideoCodec.VP8

            await self.room.local_participant.publish_track(track, options)

            self.is_connected = True
            logger.info("LiveKit 연결 완료. 스트리밍 시작.")

        except requests.exceptions.ConnectionError:
            logger.error("서버 연결 실패 (%s). 서버가 실행 중인지 확인하세요.", API_BASE_URL)
            self.is_connected = False
            if self.room:
                await self.room.disconnect()

        except asyncio.TimeoutError:
            logger.error("LiveKit 연결 타임아웃 (10초).")
            self.is_connected = False
            if self.room:
                await self.room.disconnect()

        except Exception as e:
            logger.error("LiveKit 연결 실패: %s", e)
            self.is_connected = False
            if self.room:
                await self.room.disconnect()

        finally:
            self.is_connecting = False

    def send_frame(self, frame):
        """프레임을 LiveKit으로 전송한다."""
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
        """LiveKit 연결을 해제한다."""
        if self.room:
            await self.room.disconnect()

        self.is_connected = False
        self.is_connecting = False
        logger.info("LiveKit 연결 해제.")


# --- 카메라 유틸리티 ---
def open_camera(index: int, width: int, height: int) -> cv2.VideoCapture:
    """카메라를 열고, 실패 시 대체 인덱스를 시도한다."""
    for cam_index in [index, index + 1]:
        logger.info("카메라 %d번 열기 시도...", cam_index)
        cap = cv2.VideoCapture(cam_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        if cap.isOpened():
            logger.info("카메라 %d번 열기 성공.", cam_index)
            return cap

        logger.warning("카메라 %d번 열기 실패.", cam_index)
        cap.release()

    raise RuntimeError("사용 가능한 카메라가 없습니다.")


# --- 메인 시뮬레이터 ---
async def run_simulator():
    cap = None
    streamer = LiveKitManager()
    headless = args.headless
    source = args.source
    consecutive_frame_drops = 0
    max_frame_drops = 10

    logger.info("=" * 50)
    logger.info("Ember Sentinel — Edge IoT Simulator")
    logger.info("=" * 50)

    # 1. YOLO 모델 로드
    logger.info("YOLO 모델 로드: %s", MODEL_PATH)
    try:
        model = YOLO(MODEL_PATH, task="detect")
        logger.info("YOLO 모델 로드 완료.")
    except Exception as e:
        logger.critical("모델 로드 실패: %s", e)
        return

    # 2. 입력 소스 설정
    if source == "webcam":
        try:
            cap = open_camera(cfg.camera.index, CAMERA_WIDTH, CAMERA_HEIGHT)
        except RuntimeError as e:
            logger.critical("%s", e)
            return
    else:
        video_path = Path(source)
        if not video_path.exists():
            logger.critical("비디오 파일을 찾을 수 없습니다: %s", source)
            return
        logger.info("입력 소스: 비디오 파일 (%s)", source)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.critical("비디오 파일을 열 수 없습니다: %s", source)
            return

    logger.info("BLE: 비활성 (시뮬레이터 모드)")
    logger.info("API 서버: %s", API_BASE_URL)
    logger.info("LiveKit: %s", LIVEKIT_URL)
    logger.info("Headless: %s", "Yes" if headless else "No")
    logger.info("모니터링 시작. %s", "Ctrl+C로 종료" if headless else "'q' 키로 종료")

    last_alert_time = 0
    last_fire_seen_time = 0
    stream_start_time = 0
    last_stream_end_time = 0
    last_trigger_time = 0
    frame_count = 0

    try:
        while True:
            ret, frame = cap.read()

            # 프레임 드롭 처리
            if not ret:
                if source != "webcam":
                    logger.info("비디오 끝. 처음부터 반복 재생합니다.")
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue

                consecutive_frame_drops += 1
                logger.warning("프레임 드롭 (%d/%d)", consecutive_frame_drops, max_frame_drops)

                if consecutive_frame_drops >= max_frame_drops:
                    try:
                        if cap is not None:
                            cap.release()
                        time.sleep(1.0)
                        cap = open_camera(cfg.camera.index, CAMERA_WIDTH, CAMERA_HEIGHT)
                        consecutive_frame_drops = 0
                        logger.info("카메라 재연결 성공.")
                    except RuntimeError:
                        logger.critical("카메라 복구 실패. 시스템을 종료합니다.")
                        break

                await asyncio.sleep(0.1)
                continue

            consecutive_frame_drops = 0
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

                if current_time - last_alert_time > ALERT_COOLDOWN:
                    logger.warning(
                        "🔥 %s 감지! (신뢰도: %.2f) [Frame #%d]",
                        detected_label, confidence, frame_count,
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
                        logger.info(
                            "%s 감지! 서버에 이벤트 발행 + 스트리밍 시작...",
                            detected_label,
                        )
                        last_trigger_time = current_time
                        stream_start_time = current_time

                        await streamer.connect(detection_type=detected_label)

            # --- 스트리밍 중단 조건 ---
            if streamer.is_connected:
                elapsed = current_time - stream_start_time

                if elapsed > MAX_STREAM_DURATION:
                    logger.info(
                        "최대 스트리밍 시간(%ds) 도달. 중단.",
                        MAX_STREAM_DURATION,
                    )
                    await streamer.disconnect()
                    last_stream_end_time = current_time

                elif current_time - last_fire_seen_time > 10.0:
                    logger.info("10초간 미감지. 스트리밍 중단.")
                    await streamer.disconnect()
                    last_stream_end_time = current_time

            # --- 프레임 전송 (YOLO 바운딩 박스 포함) ---
            if streamer.is_connected:
                streamer.send_frame(annotated_frame)

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
                    logger.info("'q' 키 입력으로 종료합니다.")
                    break

            await asyncio.sleep(0.01)

    except KeyboardInterrupt:
        logger.info("Ctrl+C 입력으로 종료합니다.")

    except Exception as e:
        logger.critical("시스템 오류: %s", e, exc_info=True)

    finally:
        if cap is not None:
            cap.release()
        if not headless:
            cv2.destroyAllWindows()
        await streamer.disconnect()
        logger.info("시뮬레이터 종료.")


if __name__ == "__main__":
    asyncio.run(run_simulator())
