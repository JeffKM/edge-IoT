import cv2
import time
import asyncio
import argparse
import requests
import numpy as np
import json
from ultralytics import YOLO
from livekit import rtc

from config_loader import load_config, add_config_arg

# --- [1. CLI 인자 파싱] ---
parser = argparse.ArgumentParser(description="Ember Sentinel 엣지 디바이스")
add_config_arg(parser)
parser.add_argument(
    "--no-ble",
    action="store_true",
    help="BLE 없이 실행 (Arduino 연결 생략)",
)
args = parser.parse_args()

# --- [2. 설정 로드] ---
cfg = load_config(args.config)

LIVEKIT_URL = cfg.livekit.url
TOKEN_API_URL = f"{cfg.server.api_url}{cfg.server.fire_event_endpoint}"
DEVICE_UUID = cfg.device.uuid
DEVICE_API_KEY = cfg.device.api_key
API_HEADERS = {"X-Device-API-Key": DEVICE_API_KEY}
ROOM_NAME = "fire_emergency_room"

MODEL_PATH = cfg.yolo.model_path
CONF_THRESHOLD = cfg.yolo.confidence
IMG_SIZE = cfg.yolo.image_size
ALERT_COOLDOWN = cfg.stream.alert_cooldown

MAX_STREAM_DURATION = cfg.stream.max_duration
STREAM_RESTART_DELAY = cfg.stream.restart_delay

ARDUINO_MAC_ADDRESS = cfg.ble.arduino_mac
CHARACTERISTIC_UUID = cfg.ble.characteristic_uuid

CAMERA_INDEX = cfg.camera.index
CAMERA_WIDTH = cfg.camera.width
CAMERA_HEIGHT = cfg.camera.height

NO_BLE = args.no_ble


# --- [3. Mock BLE Client] ---
class MockBLEClient:
    """BLE 없이 실행 시 사용하는 더미 클라이언트."""

    def __init__(self, address: str):
        self.address = address
        print(f"[MockBLE] BLE 모킹 활성화 (대상: {address})")

    async def __aenter__(self):
        print("[MockBLE] 가상 BLE 연결 완료")
        return self

    async def __aexit__(self, *_):
        print("[MockBLE] 가상 BLE 연결 해제")

    async def write_gatt_char(self, uuid: str, data: bytes):
        label = "ALARM ON" if data == bytes([1]) else "heartbeat"
        print(f"[MockBLE] write_gatt_char({uuid[:8]}..., {label})")


# --- [4. LiveKit Manager Class] ---
class LiveKitManager:
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
        print(f"[LiveKit] Requesting Token for type: {detection_type}...")

        try:
            payload = {"deviceUuid": DEVICE_UUID, "detectionType": detection_type}
            print(f"[DEBUG] Sending Payload: {payload}")

            response = requests.post(TOKEN_API_URL, json=payload, headers=API_HEADERS, timeout=5)
            response.raise_for_status()

            data = response.json()
            token = data.get("token")
            fire_event_id = data.get("fireEventId")

            if not token:
                print("[LiveKit] Error: No token.")
                return

            print("[LiveKit] Connecting to Server...")
            self.room = rtc.Room()

            await asyncio.wait_for(self.room.connect(LIVEKIT_URL, token), timeout=10.0)

            try:
                metadata_dict = {
                    "type": "PUBLISHER",
                    "fireEventId": fire_event_id,
                    "status": detection_type,
                    "updated_at": time.time(),
                }
                new_meta_str = json.dumps(metadata_dict)
                print(f"[LiveKit] Updating Metadata: {new_meta_str}")

                if self.room.local_participant:
                    await self.room.local_participant.update_metadata(new_meta_str)
            except Exception as meta_error:
                print(f"[LiveKit] Metadata Update Failed: {meta_error}")

            self.video_source = rtc.VideoSource(self.width, self.height)
            track = rtc.LocalVideoTrack.create_video_track(
                "fire_cam_track", self.video_source
            )

            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_CAMERA
            options.video_codec = rtc.VideoCodec.VP8

            await self.room.local_participant.publish_track(track, options)

            self.is_connected = True
            print("[LiveKit] Connected! Streaming Started.")

        except Exception as e:
            print(f"[LiveKit] Connection Failed: {e}")
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
        print("[LiveKit] Disconnected.")


# --- [5. Main Execution Function] ---
async def run_system():
    cap = None
    streamer = LiveKitManager()

    # 1. YOLO 모델 로드
    print(f"[INFO] Loading YOLO model: {MODEL_PATH}")
    try:
        model = YOLO(MODEL_PATH, task="detect")
    except Exception as e:
        print(f"[ERROR] Model load failed: {e}")
        return

    # 2. 카메라 설정
    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

    if not cap.isOpened():
        print(f"[INFO] Trying camera index {CAMERA_INDEX + 1}...")
        cap = cv2.VideoCapture(CAMERA_INDEX + 1)
        if not cap.isOpened():
            print("[ERROR] Camera failed.")
            return

    # 3. BLE 클라이언트 선택
    if NO_BLE:
        ble_client_cls = MockBLEClient
        print("[INFO] BLE 비활성화 모드 (--no-ble)")
    else:
        from bleak import BleakClient

        ble_client_cls = BleakClient

    print(f"[INFO] Connecting to Arduino ({ARDUINO_MAC_ADDRESS})...")

    try:
        async with ble_client_cls(ARDUINO_MAC_ADDRESS) as client:
            print("[INFO] Connected to Arduino via BLE!")
            print("[INFO] System Active. Press 'q' to exit.")

            last_alert_time = 0
            last_heartbeat_time = 0
            last_fire_seen_time = 0

            stream_start_time = 0
            last_stream_end_time = 0
            last_trigger_time = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    print("[ERROR] Frame drop.")
                    break

                # YOLO 추론
                results = model(frame, imgsz=IMG_SIZE, conf=CONF_THRESHOLD, verbose=False)
                annotated_frame = results[0].plot()

                boxes = results[0].boxes
                fire_detected = len(boxes) > 0
                current_time = time.time()

                # --- [Logic A] 화재/연기 감지 ---
                if fire_detected:
                    last_fire_seen_time = current_time

                    cls_id = int(boxes.cls[0].item())
                    detected_label = model.names[cls_id].upper()

                    # A-1. BLE 알람
                    if current_time - last_alert_time > ALERT_COOLDOWN:
                        print(f"[ALERT] {detected_label} DETECTED! Triggering Alarm...")
                        try:
                            await client.write_gatt_char(
                                CHARACTERISTIC_UUID, bytes([1])
                            )
                            last_alert_time = current_time
                        except Exception as e:
                            print(f"[BLE Error] {e}")

                    # A-2. 스트리밍 시작
                    if not streamer.is_connected and not streamer.is_connecting:
                        time_since_last_stream = current_time - last_stream_end_time
                        time_since_trigger = current_time - last_trigger_time

                        if (
                            time_since_last_stream > STREAM_RESTART_DELAY
                            and time_since_trigger > STREAM_RESTART_DELAY
                        ):
                            print(f"[STREAM] {detected_label} found! Requesting Stream...")

                            last_trigger_time = current_time
                            stream_start_time = current_time

                            await streamer.connect(detection_type=detected_label)

                # --- [Logic B] 스트리밍 중단 조건 ---
                if streamer.is_connected:
                    elapsed = current_time - stream_start_time

                    if elapsed > MAX_STREAM_DURATION:
                        print(
                            f"[STREAM] Max duration ({MAX_STREAM_DURATION}s) reached. Stopping."
                        )
                        await streamer.disconnect()
                        last_stream_end_time = current_time

                    elif current_time - last_fire_seen_time > 10.0:
                        print("[STREAM] Situation clear for 10s. Stopping Stream...")
                        await streamer.disconnect()
                        last_stream_end_time = current_time

                # --- [Logic C] Heartbeat (Arduino) ---
                if not fire_detected and (current_time - last_heartbeat_time > 1.0):
                    try:
                        await client.write_gatt_char(
                            CHARACTERISTIC_UUID, bytes([0])
                        )
                        last_heartbeat_time = current_time
                    except Exception:
                        pass

                # --- [Logic D] 프레임 전송 ---
                if streamer.is_connected:
                    streamer.send_frame(frame)

                # --- [Logic E] 상태 표시 ---
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

                cv2.imshow("Fire Detection & Streaming", annotated_frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

                await asyncio.sleep(0.01)

    except Exception as e:
        print(f"[System Error] {e}")

    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        await streamer.disconnect()
        print("[INFO] System Shutdown.")


if __name__ == "__main__":
    asyncio.run(run_system())
