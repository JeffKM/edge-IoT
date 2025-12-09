import asyncio
import cv2
import requests
import numpy as np
import sys
from livekit import rtc

# ---------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------
LIVEKIT_URL = 'ws://54.187.131.131:7880'
TOKEN_API_URL = 'http://ec2-35-94-89-39.us-west-2.compute.amazonaws.com:8080/media/test/stream'

# Required for Token Generation (Prevents 500 Error)
ROOM_NAME = "usb_cam_room"
PARTICIPANT_NAME = "usb_webcam_user"

async def main():
    # ---------------------------------------------------------
    # 2. Get Token (Sending JSON Payload)
    # ---------------------------------------------------------
    print(f"Requesting token from {TOKEN_API_URL}...")
    token = None 
    try:
        # Define the payload
        payload = {
            "roomName": ROOM_NAME,
            "participantName": PARTICIPANT_NAME
        }
        
        # Send POST request with JSON data
        response = requests.post(TOKEN_API_URL, json=payload)
        response.raise_for_status() 
        
        data = response.json()
        token = data.get("token")
        
        if not token:
            print("Error: No token received.")
            return
        print("Token received.")
        
    except Exception as e:
        print(f"Token Error: {e}")
        return

    # ---------------------------------------------------------
    # 3. Connect to LiveKit
    # ---------------------------------------------------------
    print(f"Connecting to {LIVEKIT_URL}...")
    ctx = rtc.Room()
    try:
        await ctx.connect(LIVEKIT_URL, token)
        print(f"Connected: {ctx.name}")
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # ---------------------------------------------------------
    # 4. Setup Video Track
    # ---------------------------------------------------------
    WIDTH, HEIGHT = 640, 480
    
    source = rtc.VideoSource(WIDTH, HEIGHT)
    track = rtc.LocalVideoTrack.create_video_track("usb_cam_track", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_CAMERA
    await ctx.local_participant.publish_track(track, options)
    print("Video track published.")

    # ---------------------------------------------------------
    # 5. USB Camera Loop (Standard OpenCV)
    # ---------------------------------------------------------
    # Index 0 is usually the USB webcam.
    # If you still have the Pi Camera connected, USB might be at index 1 or 2.
    # Try changing to cv2.VideoCapture(1) if 0 does not work.
    cap = cv2.VideoCapture(0)
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)

    if not cap.isOpened():
        print("Error: Cannot open USB Camera.")
        print("Check if the USB camera is plugged in.")
        await ctx.disconnect()
        return

    print("Streaming from USB Webcam... Press Ctrl+C to stop.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to grab frame (retrying...)")
                await asyncio.sleep(0.1)
                continue

            # Safety: Ensure resolution matches exactly
            if frame.shape[1] != WIDTH or frame.shape[0] != HEIGHT:
                frame = cv2.resize(frame, (WIDTH, HEIGHT))

            # Convert BGR (OpenCV default) to RGBA (LiveKit default)
            frame_rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)

            # Send to LiveKit
            # type: ignore
            video_frame = rtc.VideoFrame(
                WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, frame_rgba.tobytes()
            )

            source.capture_frame(video_frame)
            await asyncio.sleep(0)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        cap.release()
        await ctx.disconnect()
        print("Disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
