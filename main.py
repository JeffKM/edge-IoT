import cv2
import time
import asyncio
import requests
import numpy as np
import json
from ultralytics import YOLO
from bleak import BleakClient
from livekit import rtc

# --- [1. Configuration] ---

# [BLE Configuration]
ARDUINO_MAC_ADDRESS = "90:9F:4D:1A:35:A1" 
CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef1"

# [LiveKit Server Configuration]
LIVEKIT_URL = 'ws://54.187.131.131:7880'
TOKEN_API_URL = 'http://ec2-35-94-89-39.us-west-2.compute.amazonaws.com:8080/embedded/fire-event/publish'
DEVICE_UUID = '067e6162-3b6f-4ae2-a171-2470b63dff00'
ROOM_NAME = "fire_emergency_room"

# [YOLO Model Configuration]
MODEL_PATH = "./experiments/yolov11n/weights/best_ncnn_model"
CONF_THRESHOLD = 0.4 
IMG_SIZE = 640
ALERT_COOLDOWN = 10 

# [Stream Control Configuration]
MAX_STREAM_DURATION = 30   
STREAM_RESTART_DELAY = 60  

# --- [2. LiveKit Manager Class] ---
class LiveKitManager:
    def __init__(self):
        self.room = None
        self.video_source = None
        
        # [Core] State management variables
        self.is_connected = False   
        self.is_connecting = False  

        self.width = 640
        self.height = 480

    async def connect(self, detection_type="UNKNOWN"):
        # Prevent duplicate requests
        if self.is_connected or self.is_connecting: 
            return
        
        self.is_connecting = True 
        print(f"[LiveKit] Requesting Token for type: {detection_type}...")

        try:
            payload = {"deviceUuid": DEVICE_UUID, "detectionType": detection_type} 
            print(f"[DEBUG] Sending Payload: {payload}")
            
            # 1. Request Token from Spring Boot
            # Note: requests is synchronous (blocking), but fast enough usually.
            response = requests.post(TOKEN_API_URL, json=payload, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            token = data.get("token")
            fire_event_id = data.get("fireEventId") # Get Event ID for metadata
            
            if not token:
                print("[LiveKit] Error: No token.")
                return 

            print("[LiveKit] Connecting to Server...")
            self.room = rtc.Room()
            
            # 2. Connect to LiveKit
            # [TIMEOUT FIX] Set a timeout to prevent hanging
            await asyncio.wait_for(self.room.connect(LIVEKIT_URL, token), timeout=10.0)

            # 3. [CRITICAL] Metadata Update Logic
            # Explicitly set metadata so the Webhook can recognize the Publisher
            try:
                metadata_dict = {
                    "type": "PUBLISHER",
                    "fireEventId": fire_event_id,
                    "status": detection_type,
                    "updated_at": time.time()
                }
                new_meta_str = json.dumps(metadata_dict)
                
                print(f"[LiveKit] Updating Metadata: {new_meta_str}")
                
                # Try both methods to ensure server receives it
                if self.room.local_participant:
                    await self.room.local_participant.update_metadata(new_meta_str)
            except Exception as meta_error:
                print(f"[LiveKit] Metadata Update Failed: {meta_error}")
            
            # 4. Video Track Setup
            self.video_source = rtc.VideoSource(self.width, self.height)
            track = rtc.LocalVideoTrack.create_video_track("fire_cam_track", self.video_source)
            
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_CAMERA
            options.video_codec = rtc.VideoCodec.VP8 # Ensure VP8 for compatibility
            
            await self.room.local_participant.publish_track(track, options)
            
            self.is_connected = True
            print(f"[LiveKit] Connected! Streaming Started.")

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

        # Resize if needed
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height))
            
        # [COLOR FIX] Convert BGR -> ARGB
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
        
# --- [3. Main Execution Function] ---
async def run_system():
    cap = None
    streamer = LiveKitManager()

    # 1. Load Model
    print(f"[INFO] Loading YOLO model: {MODEL_PATH}")
    try:
        model = YOLO(MODEL_PATH, task='detect')
    except Exception as e:
        print(f"[ERROR] Model load failed: {e}")
        return

    # 2. Camera Setup
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    if not cap.isOpened():
        print("[INFO] Trying camera index 1...")
        cap = cv2.VideoCapture(1)
        if not cap.isOpened():
            print("[ERROR] Camera failed.")
            return

    print(f"[INFO] Connecting to Arduino ({ARDUINO_MAC_ADDRESS})...")
    
    try:
        async with BleakClient(ARDUINO_MAC_ADDRESS) as client:
            print("[INFO] Connected to Arduino via BLE!")
            print("[INFO] System Active. Press 'q' to exit.")

            last_alert_time = 0
            last_heartbeat_time = 0
            last_fire_seen_time = 0 
            
            # [Timer Variables]
            stream_start_time = 0      
            last_stream_end_time = 0    
            last_trigger_time = 0 # Added missing variable
            
            while True:
                ret, frame = cap.read()
                if not ret: 
                    print("[ERROR] Frame drop.")
                    break

                # YOLO Inference
                results = model(frame, imgsz=640, conf=CONF_THRESHOLD, verbose=False)
                annotated_frame = results[0].plot()
                
                boxes = results[0].boxes
                fire_detected = (len(boxes) > 0)
                current_time = time.time()
                
                # --- [Logic A] Fire/Smoke IS Detected ---
                if fire_detected:
                    last_fire_seen_time = current_time 
                    
                    cls_id = int(boxes.cls[0].item())
                    detected_label = model.names[cls_id].upper() 

                    # A-1. BLE Alarm
                    if (current_time - last_alert_time > ALERT_COOLDOWN):
                        print(f"?? {detected_label} DETECTED! Triggering Alarm...")
                        try:
                            await client.write_gatt_char(CHARACTERISTIC_UUID, bytes([1]))
                            last_alert_time = current_time
                        except Exception as e:
                            print(f"[BLE Error] {e}")

                    # A-2. Start Stream (FIXED LOGIC)
                    # [FIX] Use await instead of create_task for connection
                    # This pauses the YOLO loop to give full CPU to networking
                    if not streamer.is_connected and not streamer.is_connecting:
                        
                        time_since_last_stream = current_time - last_stream_end_time
                        time_since_trigger = current_time - last_trigger_time
                        
                        if time_since_last_stream > STREAM_RESTART_DELAY and time_since_trigger > STREAM_RESTART_DELAY:
                            print(f"?? {detected_label} found! Requesting Stream...")
                            
                            last_trigger_time = current_time
                            stream_start_time = current_time 
                            
                            # [KEY FIX] Blocking call to ensure connection succeeds without CPU starvation
                            await streamer.connect(detection_type=detected_label)
                        else:
                            pass
                            
                # --- [Logic B] Stop Conditions ---
                if streamer.is_connected:
                    elapsed = current_time - stream_start_time
                    
                    if elapsed > MAX_STREAM_DURATION:
                        print(f"?? Max duration ({MAX_STREAM_DURATION}s) reached. Stopping.")
                        await streamer.disconnect()
                        last_stream_end_time = current_time

                    elif (current_time - last_fire_seen_time > 10.0):
                        print("?? Situation clear for 10s. Stopping Stream...")
                        await streamer.disconnect()
                        last_stream_end_time = current_time

                # --- [Logic C] Heartbeat (Arduino) ---
                if not fire_detected and (current_time - last_heartbeat_time > 1.0):
                    try:
                        await client.write_gatt_char(CHARACTERISTIC_UUID, bytes([0]))
                        last_heartbeat_time = current_time
                    except: pass

                # --- [Logic D] Send Frame ---
                if streamer.is_connected:
                    streamer.send_frame(frame)

                # --- [Logic E] Display Status ---
                status_text = "Monitoring..."
                color = (0, 255, 0)

                if streamer.is_connected:
                    remaining = int(MAX_STREAM_DURATION - (current_time - stream_start_time))
                    status_text = f"STREAMING ON ({remaining}s)"
                    color = (0, 0, 255)
                elif streamer.is_connecting:
                     status_text = "Connecting..."
                     color = (255, 165, 0) 
                elif fire_detected:
                    # Only show cooldown if we really wanted to stream but couldn't
                    wait_time = int(STREAM_RESTART_DELAY - (current_time - last_stream_end_time))
                    if wait_time > 0 and wait_time < STREAM_RESTART_DELAY: 
                        status_text = f"Cooldown: {wait_time}s"
                        color = (0, 255, 255) 

                cv2.putText(annotated_frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                
                # [Optional] Comment out imshow if running headless to save CPU
                cv2.imshow("Fire Detection & Streaming", annotated_frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                
                # [KEY FIX] Yield control to event loop
                # 10ms sleep allows asyncio tasks (like network keep-alives) to run
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
