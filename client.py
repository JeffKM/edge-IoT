"""LiveKit 스트리밍 테스트 클라이언트.

YOLO/BLE 없이 순수 웹캠 스트리밍만 수행하는 테스트용 클라이언트.
설정은 config.yaml에서 로드한다.

사용법:
    python client.py                        # 기본 config.yaml 사용
    python client.py --config config.yaml   # 설정 파일 지정
"""

import asyncio
import argparse
import cv2
import requests
from livekit import rtc

from config_loader import load_config, add_config_arg
from logger import setup_logging, get_logger

# --- CLI 인자 파싱 ---
parser = argparse.ArgumentParser(description="LiveKit 스트리밍 테스트 클라이언트")
add_config_arg(parser)
args = parser.parse_args()

# --- 설정 및 로깅 초기화 ---
cfg = load_config(args.config)
setup_logging(cfg)
logger = get_logger("client")

LIVEKIT_URL = cfg.livekit.url
TOKEN_API_URL = f"{cfg.server.api_url}/media/test/stream"
DEVICE_UUID = cfg.device.uuid
DEVICE_API_KEY = cfg.device.api_key
API_HEADERS = {"X-Device-API-Key": DEVICE_API_KEY}

ROOM_NAME = "usb_cam_room"
PARTICIPANT_NAME = "usb_webcam_user"

CAMERA_WIDTH = cfg.camera.width
CAMERA_HEIGHT = cfg.camera.height


async def main():
    # 1. 토큰 발급
    logger.info("토큰 요청: %s", TOKEN_API_URL)
    try:
        payload = {"roomName": ROOM_NAME, "participantName": PARTICIPANT_NAME}
        response = requests.post(TOKEN_API_URL, json=payload, headers=API_HEADERS, timeout=10)
        response.raise_for_status()

        data = response.json()
        token = data.get("token")

        if not token:
            logger.error("토큰을 받지 못했습니다.")
            return
        logger.info("토큰 발급 완료.")

    except requests.exceptions.ConnectionError:
        logger.error("서버 연결 실패 (%s). 서버가 실행 중인지 확인하세요.", cfg.server.api_url)
        return
    except requests.exceptions.Timeout:
        logger.error("토큰 요청 타임아웃.")
        return
    except Exception as e:
        logger.error("토큰 발급 실패: %s", e)
        return

    # 2. LiveKit 연결
    logger.info("LiveKit 서버에 연결 중: %s", LIVEKIT_URL)
    ctx = rtc.Room()
    try:
        await asyncio.wait_for(ctx.connect(LIVEKIT_URL, token), timeout=10.0)
        logger.info("LiveKit 연결 완료: %s", ctx.name)
    except asyncio.TimeoutError:
        logger.error("LiveKit 연결 타임아웃 (10초).")
        return
    except Exception as e:
        logger.error("LiveKit 연결 실패: %s", e)
        return

    # 3. 비디오 트랙 설정
    source = rtc.VideoSource(CAMERA_WIDTH, CAMERA_HEIGHT)
    track = rtc.LocalVideoTrack.create_video_track("usb_cam_track", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_CAMERA
    await ctx.local_participant.publish_track(track, options)
    logger.info("비디오 트랙 발행 완료.")

    # 4. 카메라 스트리밍
    cap = cv2.VideoCapture(cfg.camera.index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)

    if not cap.isOpened():
        logger.error("USB 카메라를 열 수 없습니다. 연결 상태를 확인하세요.")
        await ctx.disconnect()
        return

    logger.info("USB 웹캠 스트리밍 시작. Ctrl+C로 종료.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.warning("프레임 읽기 실패, 재시도 중...")
                await asyncio.sleep(0.1)
                continue

            if frame.shape[1] != CAMERA_WIDTH or frame.shape[0] != CAMERA_HEIGHT:
                frame = cv2.resize(frame, (CAMERA_WIDTH, CAMERA_HEIGHT))

            frame_rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            video_frame = rtc.VideoFrame(
                CAMERA_WIDTH, CAMERA_HEIGHT, rtc.VideoBufferType.RGBA, frame_rgba.tobytes()
            )
            source.capture_frame(video_frame)
            await asyncio.sleep(0)

    except KeyboardInterrupt:
        logger.info("Ctrl+C로 종료합니다.")
    finally:
        cap.release()
        await ctx.disconnect()
        logger.info("연결 해제 완료.")


if __name__ == "__main__":
    asyncio.run(main())
