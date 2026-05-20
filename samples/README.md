# 샘플 영상 파일

이 디렉토리에 화재/연기 감지 테스트용 영상 파일을 배치합니다.

## 사용법

```bash
# 샘플 영상으로 시뮬레이터 실행
python simulator.py --source samples/test.mp4
```

## 영상 파일 준비

영상 파일은 용량이 크므로 Git에 포함하지 않습니다. 아래 방법 중 하나로 준비하세요:

### 방법 1: 직접 촬영
스마트폰이나 웹캠으로 화재/연기 영상을 촬영하여 이 폴더에 저장합니다.

### 방법 2: 공개 데이터셋에서 추출
- [FASDD_CV Dataset](https://github.com/FASDD/FASDD_CV) — 프로젝트 학습에 사용한 데이터셋
- YouTube에서 화재 영상을 다운로드 (`yt-dlp` 활용)

### 방법 3: 테스트용 영상 생성
```bash
# OpenCV로 빈 테스트 영상 생성 (10초, 640x480)
python -c "
import cv2, numpy as np
out = cv2.VideoWriter('samples/test.mp4', cv2.VideoWriter_fourcc(*'mp4v'), 30, (640, 480))
for _ in range(300):
    out.write(np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8))
out.release()
print('test.mp4 생성 완료')
"
```

## 권장 파일명

| 파일명 | 설명 |
|--------|------|
| `test.mp4` | 일반 테스트 영상 |
| `fire_sample.mp4` | 실제 화재 장면 포함 영상 |
| `smoke_sample.mp4` | 연기 장면 포함 영상 |
| `normal.mp4` | 화재 없는 일반 환경 영상 |
