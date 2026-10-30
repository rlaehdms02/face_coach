# 발표 연습 코치 · 시선 분석 (face_coach)

발표하는 모습을 카메라로 보면서 **고개 방향**과 **눈동자 시선**을 분석하고,
"0:09 ~ 0:12 고개는 정면인데 눈만 아래를 봤어요"처럼 **구간별로 코칭**해 주는 프로그램입니다.

- 실시간: 스마트폰 **IP Webcam** 앱 영상을 웹 화면에서 바로 분석
- 영상 파일: 찍어 둔 발표 영상(mp4 등)을 웹 화면에 올려서 분석
- 얼굴·눈 박스, 눈동자 위치, 판정 상태를 영상 위에 표시
- 결과 리포트: 카메라 응시율, 고개 정면 비율, 눈 맞춤 비율, 이탈 구간 + 코칭 문장, 그래프

> 음성(말 속도, "음·그" 같은 군더더기) 분석은 범위에서 제외했습니다. 시선 처리만 다룹니다.

---

## 1. 폴더 구조

```
face_coach/
├── app.py               # FastAPI 웹 서버 (실시간 카메라 + 영상 업로드 + 결과 리포트)
├── gaze_analysis.py     # 시선 분석 핵심 모듈 (터미널에서 단독 실행도 가능)
├── requirements.txt     # 설치할 패키지 목록
├── README.md            # 이 문서
├── docs/                # 파일별 설명서
│   ├── app.md
│   ├── gaze_analysis.md
│   └── index_html.md
├── web/
│   └── index.html       # 웹 화면 (반드시 web 폴더 안에!)
├── face_landmarker.task # 얼굴 모델 (처음 실행할 때 자동 다운로드, 약 3.6MB)
├── results/             # 분석 결과 (요약 txt, CSV, 그래프 PNG, 박스 데이터 JSON) - 자동 생성
└── uploads/             # 웹에서 올린 영상 - 자동 생성 (용량 커지면 지워도 됨)
```

---

## 2. 설치 (처음 한 번만, 한 줄씩 실행)

### 2-1. WSL 우분투로 들어가기

PowerShell 창이라면 먼저 우분투로 들어갑니다.

```powershell
wsl
```

프롬프트가 `user1@DESKTOP-...:~$` 처럼 바뀌면 리눅스 안입니다.

### 2-2. 시스템 패키지

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libgl1 libglib2.0-0 libgles2 libegl1
```

- `libgl1`, `libglib2.0-0` : OpenCV에 필요
- `libgles2`, `libegl1` : MediaPipe 얼굴 모델에 필요 (없으면 `libGLESv2.so.2` 에러)
- `sudo` 비밀번호를 칠 때 화면에 아무것도 안 보이는 게 정상입니다.

### 2-3. 프로젝트 폴더와 가상환경

```bash
mkdir -p ~/face_coach/web
cd ~/face_coach
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

`(.venv)` 가 프롬프트 앞에 붙으면 가상환경이 켜진 것입니다.

### 2-4. 파이썬 패키지

```bash
pip install -r requirements.txt
```

`requirements.txt` 가 없으면 직접 설치해도 됩니다.

```bash
pip install opencv-python mediapipe matplotlib numpy fastapi uvicorn
```

> ⚠️ `sudo apt install uvicorn` 으로 설치하지 마세요. 가상환경 밖에 설치되어 mediapipe 를 못 찾습니다.

### 2-5. 파일 넣기

```bash
explorer.exe .
```

열린 탐색기 창에 파일을 넣습니다.

| 파일 | 넣을 위치 |
|---|---|
| `app.py`, `gaze_analysis.py`, `requirements.txt` | `face_coach` 바로 아래 |
| `index.html` | `face_coach/web` 안 |

확인:

```bash
ls        # app.py  gaze_analysis.py  requirements.txt  web ...
ls web    # index.html
```

---

## 3. 실행

### 3-1. 매번 실행할 때

```bash
wsl                              # PowerShell 이라면 먼저
cd ~/face_coach
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

`Uvicorn running on http://0.0.0.0:8000` 이 나오면 성공입니다. 이 터미널은 켜 둡니다.
(아래에 나오는 `WARNING`, `W0000`, `XNNPACK` 줄은 MediaPipe 시작 안내라서 무시해도 됩니다.)

### 3-2. 브라우저로 열기

윈도우 브라우저에서 **http://localhost:8000** 을 엽니다. 코드를 바꾼 뒤에는 **Ctrl+F5** 로 새로고침하세요.

### 3-3. 끄기

서버 터미널에서 `Ctrl+C`.

### 3-4. (선택) 한 번에 실행하는 run.sh 만들기

```bash
cd ~/face_coach
printf '#!/bin/bash\ncd ~/face_coach\nsource .venv/bin/activate\nuvicorn app:app --host 0.0.0.0 --port 8000\n' > run.sh
chmod +x run.sh
```

다음부터는 `wsl` 후 `~/face_coach/run.sh` 만 입력하면 됩니다.

---

## 4. 사용 방법

### 4-1. 실시간 연습 (스마트폰 IP Webcam)

1. 폰에서 **IP Webcam** 앱 → 비디오 설정에서 **전면 카메라** 선택 (해상도 1280x720 권장) → 맨 아래 **서버 시작**
2. 폰을 눈높이, 50~80cm 거리에 세웁니다. **PC와 폰은 같은 와이파이**여야 합니다.
3. 웹 화면 ① 칸에 폰에 나온 주소(예: `192.168.0.10:8080`)를 입력하고 **연결** (`/video` 는 자동으로 붙음)
4. 상태가 **ONLINE** 이 되면 ② **시작**
5. **처음 3초는 폰 카메라 렌즈를 정면으로** 봅니다 (노란색 "기준 잡는 중")
6. 발표 연습 → **종료하고 결과 보기**

### 4-2. 찍어 둔 영상으로 분석

1. 영상도 **처음 3초는 카메라 렌즈를 정면으로** 보고 시작해야 합니다 (그 3초가 기준).
2. ② 카드의 "또는 찍어 둔 영상으로" → **영상 선택** → **올려서 분석**
3. "올리는 중 → 얼굴·눈 분석 중 → 분석 완료" 후 아래에 결과가 나옵니다.
4. 결과의 영상을 재생하면 **얼굴·눈 박스가 따라 움직이고**, 이탈 구간을 누르면 그 시간으로 이동합니다.

- 가능한 형식: mp4, mov, m4v, avi, mkv, webm (1GB 이하). 카메라 연결 없이도 됩니다.
- 아이폰 기본 설정(.mov, HEVC) 영상은 분석은 되지만 윈도우 브라우저에서 재생이 안 될 수 있습니다. 이때는 결과에서 영상만 빠집니다.

### 4-3. 터미널에서 단독 실행 (웹 서버 없이)

```bash
python gaze_analysis.py test.mp4 --preview          # 영상 파일 (분석 화면 보기)
python gaze_analysis.py test.mp4                    # 화면 없이 분석만
python gaze_analysis.py http://192.168.0.10:8080    # IP Webcam 실시간 (창에서 q = 종료·저장)
python gaze_analysis.py http://192.168.0.10:8080 --no-window   # 창 없이 (Ctrl+C = 종료·저장)
```

결과는 `results/` 폴더에 저장됩니다.

---

## 5. 화면 읽는 법

### 판정 상태와 색

| 색 | 상태 | 뜻 |
|---|---|---|
| 🟢 초록 | 카메라 응시 | 고개와 눈 모두 정면 |
| 🔴 빨강 | 고개 위·아래 | 고개를 숙이거나 들었음 |
| 🟠 주황 | 고개 옆 | 고개가 옆으로 돌아감 |
| 🟣 보라 | 눈만 위·아래 | 고개는 정면인데 눈동자만 위/아래 (예: 메모 곁눈질) |
| 🔵 파랑 | 눈만 옆 | 고개는 정면인데 눈동자만 옆 |
| ⚪ 회색 | 얼굴 없음 | 화면 밖으로 나감 |
| 🟡 노랑 | 기준 잡는 중 | 처음 3초 |

### 영상 위 표시

- **얼굴 박스**: 판정 상태 색 + 상태 이름
- **눈 박스 2개**: 평소 흰색, 눈만 벗어나면 상태 색
- **초록 점**: 눈동자(홍채 중심)
- **왼쪽 아래 눈 확대 화면** (실시간): 흰 점 = 눈꼬리, `+` = 눈 가운데, 초록 원 = 눈동자.
  눈만 움직였을 때 초록 원이 따라가면 눈동자를 제대로 잡고 있는 것입니다.
- 위쪽 숫자: `head y/p` = 고개 좌우/상하 각도 변화(도), `eye h/v` = 눈동자 좌우/상하 이동량(눈 폭 대비)

### 결과 리포트

- **카메라 응시율**: 고개 + 눈 모두 정면인 시간 비율
- **고개 정면 비율 / 눈 맞춤 비율**: 얼굴이 보인 시간 중 각각 정면인 비율
- **이탈 구간**: 1초 이상 이어진 구간만 표시, 종류별 코칭 문장
- **그래프**: 고개 상하·좌우, 눈동자 상하·좌우 4칸

---

## 6. 판정 기준 조절

웹 화면 ② 카드의 **"판정 기준 설정"** 에서 바꿀 수 있습니다. **숫자가 작을수록 엄격**합니다.

| 항목 | 기본값 | 단위 | 언제 바꾸나 |
|---|---|---|---|
| 처음 기준 잡는 시간 | 3 | 초 | 시작할 때 자세 잡는 게 오래 걸리면 5로 |
| 고개 좌우 기준 | 20 | 도 | 청중을 둘러보는 자연스러운 움직임까지 잡히면 25로 |
| 고개 상하 기준 | 15 | 도 | 살짝 고개 숙인 것도 잡고 싶으면 10으로 |
| 눈동자 좌우 기준 | 0.10 | 눈 폭 대비 비율 | 정면인데도 "눈만 옆"이 자주 뜨면 0.12~0.15로 |
| 눈동자 상하 기준 | 0.08 | 눈 폭 대비 비율 | 눈만 아래를 봐도 안 잡히면 0.04~0.06 으로 낮추기 |

**기준 맞추는 법**: 시작 → 3초 정면 → 고개 고정하고 **눈만** 폰 아래쪽 2~3초 → 눈만 옆 2~3초.
이때 화면 위 `v`, `h` 값이 얼마까지 올라가는지 보고, 그 값보다 조금 작게 기준을 정합니다.

---

## 7. 코드 업데이트하는 법

새 파일 3개를 다운로드한 뒤, 아래를 **통째로** 붙여넣으면 다운로드 폴더의 **가장 최근 파일**을 제자리에 복사합니다.

```bash
cd ~/face_coach
newest() { find /mnt/c/Users/*/Downloads -maxdepth 1 -iname "$1" -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-; }
cp "$(newest 'app*.py')" app.py && cp "$(newest 'gaze_analysis*.py')" gaze_analysis.py && cp "$(newest 'index*.html')" web/index.html && echo "복사 완료"
```

제대로 들어갔는지 확인:

```bash
head -2 gaze_analysis.py   # "발표 연습 코치 - 시선 분석 (고개 방향 + 눈동자 시선)"
head -1 app.py             # "발표 연습 코치 - 스마트폰 IP Webcam → ..."
grep -q "def draw_boxes" gaze_analysis.py && grep -q "overlay_url" app.py && grep -q "rCanvas" web/index.html && echo "세 파일 모두 OK"
```

---

## 8. 문제 해결

| 증상 / 메시지 | 원인 | 해결 |
|---|---|---|
| `externally-managed-environment` | 가상환경 없이 pip 사용 | `source .venv/bin/activate` 후 설치 |
| `Command 'uvicorn' not found` | 가상환경이 꺼짐 | `source .venv/bin/activate` (apt 로 설치 ❌) |
| `No module named mediapipe` | 가상환경이 꺼짐 | 위와 같음 |
| `libGLESv2.so.2: cannot open shared object file` | 그래픽 라이브러리 없음 | `sudo apt install -y libgles2 libegl1` |
| `libGL.so.1` 에러 | OpenCV 라이브러리 없음 | `sudo apt install -y libgl1` |
| `\\wsl.localhost\...: command not found` | 탐색기 주소를 터미널에 입력함 | 터미널에서는 `explorer.exe .` |
| `APP.py`, `Index.html` 처럼 대문자 | 다운로드 시 이름 바뀜 | `mv APP.py app.py`, `mv Index.html web/index.html` |
| `app.py:Zone.Identifier` 파일 | 윈도우 다운로드 꼬리표 | 무시하거나 `rm -f *Zone.Identifier` |
| `partially initialized module 'gaze_analysis' ... circular import` | `gaze_analysis.py` 안에 `app.py` 코드가 붙여넣어짐 | 7장 방법으로 파일 다시 복사, `head -2` 로 확인 |
| `address already in use` | 8000번 포트를 다른 서버(수업 실습 등)가 사용 중 | 그 서버를 끄거나 `--port 8001` 로 실행 후 `localhost:8001` |
| 웹 화면이 예전 모습 / 업로드 버튼 없음 | `web/index.html` 이 예전 버전 | `grep -c uploadBtn web/index.html` 이 0 이면 다시 복사, Ctrl+F5 |
| 주소 입력했는데 NOT_CONNECTED | 연결 버튼을 안 누름 / 서버 재시작 | **연결** 버튼 다시 누르기 |
| DISCONNECTED | 폰·PC 와이파이가 다름, IP Webcam 서버 꺼짐 | 브라우저에서 폰 주소가 직접 열리는지 먼저 확인 |
| 얼굴 모델 다운로드 실패 | 인터넷 차단 | 터미널에 나온 주소를 브라우저로 받아 `face_landmarker.task` 를 `face_coach` 폴더에 넣기 |
| 눈만 움직였는데 안 잡힘 | 눈동자가 작게 찍힘 / 기준이 둔함 | 폰을 가까이, 해상도 올리기, 눈동자 기준 낮추기 (6장) |
| 결과에 영상이 안 나옴 | 브라우저가 재생 못 하는 코덱(HEVC 등) | 분석 결과는 정상. mp4(H.264)로 다시 찍기 |

---

## 9. 동작 원리 요약

1. **MediaPipe Face Landmarker** 가 프레임마다 얼굴 점 478개(홍채 포함)와 얼굴 회전행렬을 찾습니다.
2. **고개 방향**: 회전행렬 → yaw(좌우)·pitch(상하) 각도
3. **눈동자 시선**: 두 눈꼬리 사이 가운데에서 홍채 중심이 얼마나 벗어났는지 (눈 폭 대비 비율)
4. **캘리브레이션**: 처음 3초의 중앙값을 "정면" 기준으로 잡고, 이후 기준과의 차이로 판정
5. **판정 순서**: 얼굴 없음 → 고개 이탈 → 눈 이탈 → 카메라 응시
6. **떨림 제거**: 약 0.5초 이동 중앙값, 눈 깜빡임(0.6초 이하)은 직전 눈 위치로 채움
7. **구간 묶기**: 0.5초 이내 끊김은 이어 붙이고, 1초 미만 이탈은 무시

자세한 내용은 `docs/` 폴더의 파일별 설명서를 보세요.

---

## 10. 참고 자료

파일별 자세한 출처는 각 `docs/*.md` 의 "참고 자료"에 있습니다. 수업 자료 출처는 이 문서 맨 아래 **출처 (수업 자료)** 에 모았습니다.

### 공식 문서

| 주제 | 출처 |
|---|---|
| 얼굴 점·얼굴 회전행렬 (MediaPipe Face Landmarker) | [Face landmark detection guide for Python](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python) |
| 홍채(눈동자) 점 | [MediaPipe Iris (Google Research)](https://research.google/blog/mediapipe-iris-real-time-iris-tracking-depth-estimation/) |
| FastAPI 응답·서버 시작/종료 | [FastAPI Response classes](https://fastapi.tiangolo.com/reference/responses/), [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/) |
| 업로드 파일 받기 | [Starlette Requests](https://www.starlette.dev/requests/) |
| uvicorn 실행 옵션 | [Uvicorn Settings](https://www.uvicorn.dev/settings) |
| WSL 에서 `explorer.exe .`, `/mnt/c` 로 윈도우 파일 접근 | [Working across Windows and Linux file systems (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/wsl/filesystems) |
| `externally-managed-environment` 에러가 나는 이유 (가상환경 필요) | [PEP 668](https://peps.python.org/pep-0668/) |
| IP Webcam 앱 | [Google Play – IP Webcam](https://play.google.com/store/apps/details?id=com.pas.webcam) |

### 직접 정한 것 (출처 없음, 테스트로 조정 필요)

- 판정 기준값 (고개 20°·15°, 눈동자 0.10·0.08, 눈 감음 0.09)
- 판정 순서, 떨림 제거·깜빡임 채우기·구간 묶기 규칙
- 코칭 문장과 전체 코멘트의 퍼센트 기준
- 화면 디자인, 박스 데이터 형식

---

## 출처 (수업 자료)

| 수업 python 파일 | 쓴 기능 | 이 프로젝트에서 쓴 곳 |
|---|---|---|
| `src/fastapi_cap_app.py` | `LiveState`·`camera_loop()` (카메라 스레드), `placeholder_jpg()`, `lifespan()`, `connect_camera()` (`/api/camera` 사설 IP 검사), `/api/status`, `stream_video()` (`/stream.mjpg`), `index()` | `app.py` 서버 구조 전체 (자세한 건 `docs/app.md`) |
| `src/cap_inspector.py` | `judge_cap()` 측정값을 기준값과 비교해 OK / NG 판정 | `gaze_analysis.py` `classify()`, `head_state()`, `eye_state()` (고개·눈 값을 기준과 비교해 상태 판정) |
| `src/cap_inspector.py` | `inspect_roi()` 대상을 못 찾으면 CHECK, 결과를 dict 로 반환 | `gaze_analysis.py` 얼굴을 못 찾으면 `no_face`, `extract_features()` |
| `src/cap_inspector.py` | `load_rule()` 기준 이미지(cap_on / cap_off)로 기준값 만들기 | `gaze_analysis.py` `analyze()`, `LiveJudge` 의 처음 3초 정면 기준 잡기 |
| `src/fastapi_cap_app.py` | `camera_loop()` 의 `cv2.VideoCapture(url)` 로 IP Webcam(`/video`) 열기 | `gaze_analysis.py` `normalize_stream_url()`, `LatestFrameReader`, `extract_angles_live()` |
| `src/fastapi_cap_app.py` | `/api/camera`, `/api/status`, `/stream.mjpg` API | `web/index.html` 주소 연결 → 상태 반복 조회 → `<img>` 로 실시간 영상 표시 |
| `tests/test_inspector.py` | `FakeCapture` 실제 카메라 대신 가짜 프레임, `synthetic_roi()` 가짜 이미지, `wait_for()` `/api/status` 반복 확인 | 개발 중 테스트 (가짜 카메라 서버 + 가짜 얼굴 점으로 전체 흐름 확인). 배포 파일에는 없음 |
| `src/36_day05_freeze_rule.py` | Calibration 으로 정한 기준값을 JSON 에 저장해 고정 (Rule Freeze) | 가져온 코드 없음. 기준값을 정하고 고정한 뒤 확인하는 순서만 README 6장과 `DEFAULT_TH` 에 반영 |

> 3일차 확장 실습 코드: `src/fastapi_cap_app.py`, `src/cap_inspector.py`, `tests/test_inspector.py` · 5일차 코드: `src/36_day05_freeze_rule.py`