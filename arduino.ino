#include <ArduinoBLE.h>

// 1. 서비스 & 특성 UUID 설정 (파이썬과 맞춰야 할 비밀번호 같은 것)
BLEService ctrlService("12345678-1234-5678-1234-56789abcdef0");
BLEByteCharacteristic cmdChar("12345678-1234-5678-1234-56789abcdef1", BLEWrite | BLERead);

// 2. 부저 핀 설정 (13번 핀)
const int buzzerPin = 12;

// 알람 상태 관리 변수
unsigned long alarmEndTime = 0; // 알람이 언제 끝날지 저장
bool isAlarming = false;        // 현재 알람이 울리는 중인지

void setup() {
  Serial.begin(115200);
  pinMode(buzzerPin, OUTPUT);
  pinMode(LED_BUILTIN, OUTPUT); // 눈으로 확인용 LED

  // BLE 시작
  if (!BLE.begin()) {
    Serial.println("starting BLE failed!");
    while (1);
  }

  // BLE 이름 및 서비스 설정
  BLE.setLocalName("Nano33BLE-Fire");  // 스캔할 때 이 이름이 뜹니다!
  BLE.setAdvertisedService(ctrlService);
  ctrlService.addCharacteristic(cmdChar);
  BLE.addService(ctrlService);

  // 초기값 0
  cmdChar.writeValue(0);

  BLE.advertise();
  Serial.println("Bluetooth device active, waiting for connections...");
  digitalWrite(LED_BUILTIN, HIGH);
}

void loop() {
  // BLE 장치(라즈베리파이)가 연결되었는지 확인
  BLEDevice central = BLE.central();

  if (central) {
    Serial.print("Connected to central: ");
    Serial.println(central.address());

    // 연결되어 있는 동안 계속 실행
    while (central.connected()) {
      
      // 1. 파이썬에서 데이터가 왔는지 확인
      if (cmdChar.written()) {
        byte val = cmdChar.value();
        Serial.print("Command received: ");
        Serial.println(val);

        // '1'을 받으면 알람 시작 (현재시간 + 10초)
        if (val == 1) {
          alarmEndTime = millis() + 10000; 
          isAlarming = true;
          Serial.println("!!! FIRE ALARM STARTED (10s) !!!");
        }
      }

      // 2. 알람 울리기 로직 (Non-blocking)
      if (isAlarming) {
        if (millis() < alarmEndTime) {
          // 10초가 안 지났으면 소리 내기 (삐- 삐- 효과)
          // 0.5초 간격으로 소리 냈다 안 냈다 반복
          if ((millis() / 500) % 2 == 0) {
            digitalWrite(buzzerPin, HIGH);
            digitalWrite(LED_BUILTIN, HIGH);
          } else {
            digitalWrite(buzzerPin, LOW);
            digitalWrite(LED_BUILTIN, LOW);
          }
        } else {
          // 10초 지났으면 끄기
          isAlarming = false;
          digitalWrite(buzzerPin, LOW);
          digitalWrite(LED_BUILTIN, LOW);
          Serial.println("Alarm stopped.");
          
          // 파이썬 쪽 상태도 0으로 돌려놓기 (선택사항)
          cmdChar.writeValue(0); 
        }
      }
    }
    
    // 연결 끊기면
    Serial.println("Disconnected");
    digitalWrite(buzzerPin, LOW); // 안전하게 끄기
  }
}
