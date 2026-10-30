# gaze_analysis.py — 시선 분석 핵심 모듈

얼굴을 찾아 **고개 방향**과 **눈동자 시선**을 계산하고, 정면 기준과 비교해 이탈 구간을 찾는 모듈입니다.
웹 서버(`app.py`)가 가져다 쓰고, 터미널에서 단독으로 실행할 수도 있습니다.

---

## 1. 전체 흐름

```
영상 프레임
  └─ MediaPipe Face Landmarker ── 얼굴 점 478개 + 얼굴 회전행렬
        ├─ matrix_to_angles()  → 고개 yaw(좌우), pitch(상하), roll  [도]
        └─ eye_geometry()      → 눈동자 h(좌우), v(상하), 눈 뜬 정도 [눈 폭 대비 비율]
              └─ extract_features() → 특징값 (yaw, pitch, roll, eye_h, eye_v) + 박스용 위치(geo)

실시간 화면 판정 : LiveJudge.update()   (최근 7프레임 중앙값으로 즉시 판정)
최종 결과 판정   : analyze()            (전체 기록을 다듬어서 다시 판정 → 구간·비율 계산)
저장             : save_outputs()       (요약 txt, 프레임별 CSV, 그래프 PNG)
결과 영상 박스   : overlay_sample() / build_overlay()  (브라우저에서 영상 위에 그릴 데이터)
```

---

## 2. 측정값과 단위

| 값 | 의미 | 단위 | 계산 방법 |
|---|---|---|---|
| `yaw` | 고개 좌우 | 도 | 얼굴 회전행렬에서 계산 |
| `pitch` | 고개 상하 | 도 | 얼굴 회전행렬에서 계산 |
| `eye_h` | 눈동자 좌우 이동 | 눈 폭 대비 비율 | 두 눈꼬리를 잇는 선 방향으로 홍채 중심이 가운데에서 벗어난 거리 ÷ 눈 폭 |
| `eye_v` | 눈동자 상하 이동 | 눈 폭 대비 비율 | 눈꼬리 선에 수직인 방향으로 벗어난 거리 ÷ 눈 폭 |
| `open` | 눈 뜬 정도 | 눈 폭 대비 비율 | 윗눈꺼풀~아랫눈꺼풀 거리 ÷ 눈 폭 |

- 비율로 계산하기 때문에 **얼굴 크기·폰과의 거리와 상관없습니다.**
- 눈꼬리 선 기준이라 **고개를 갸웃(roll)해도 눈동자 값이 거의 변하지 않습니다.**
- 부호(위/아래, 왼/오른쪽)는 카메라·거울 모드에 따라 다를 수 있어서, 판정에는 **기준과의 차이 크기**만 씁니다.
- 두 눈 값의 평균을 씁니다.

### 사용하는 얼굴 점 번호 (MediaPipe 478점 기준)

| 상수 | 점 번호 | 용도 |
|---|---|---|
| `EYE_POINTS` | (33, 133, 159, 145), (362, 263, 386, 374) | 눈꼬리 2개, 윗·아랫눈꺼풀 |
| `IRIS_POINTS` | 468~472, 473~477 | 홍채 중심 + 테두리 4점 (평균해서 눈동자 위치) |
| `EYE_CONTOURS` | 눈마다 16점 | 눈 박스 그리기 |

홍채는 눈 가운데와 가까운 쪽을 짝으로 정해서, 번호 순서가 바뀌어도 맞게 짝지어집니다.

---

## 3. 판정 규칙

### 기본 기준값 (`DEFAULT_TH`)

| 키 | 기본값 | 의미 |
|---|---|---|
| `yaw_th` | 20.0 | 고개 좌우 이탈 기준 (도) |
| `pitch_th` | 15.0 | 고개 상하 이탈 기준 (도) |
| `eye_h_th` | 0.10 | 눈동자 좌우 이탈 기준 (눈 폭의 10%) |
| `eye_v_th` | 0.08 | 눈동자 상하 이탈 기준 (위아래 움직임은 작게 잡혀서 더 작게) |

`EYE_CLOSED_RATIO = 0.09` : 눈 뜬 정도가 이보다 작으면 **눈 감음**으로 보고 그 프레임은 눈 판정을 건너뜁니다.

### 상태 6가지 (우선순위 순)

| 상태 키 | 한글 (`LABELS`) | 조건 |
|---|---|---|
| `no_face` | 얼굴이 화면에서 사라짐 | 얼굴을 못 찾음 |
| `head_updown` | 고개가 위/아래를 향함 | \|Δpitch\| > pitch_th (좌우보다 상하가 더 크게 벗어난 경우) |
| `head_side` | 고개가 옆을 향함 | \|Δyaw\| > yaw_th |
| `eye_updown` | 눈만 위/아래를 봄 | 고개는 기준 안, \|Δeye_v\| > eye_v_th |
| `eye_side` | 눈만 옆을 봄 | 고개는 기준 안, \|Δeye_h\| > eye_h_th |
| `looking` | 카메라를 보고 있음 | 위 어디에도 해당 없음 |

상하·좌우가 둘 다 넘으면 **기준 대비 더 많이 벗어난 쪽**으로 판정합니다.

### 관련 함수

- `head_state(dy, dp, th)` → `"head_updown" / "head_side" / None`
- `eye_state(deh, dev, th)` → `"eye_updown" / "eye_side" / None` (눈값이 없으면 None)
- `classify(dy, dp, deh, dev, th)` → 최종 상태 하나

---

## 4. 주요 함수·클래스

### 모델·특징값

| 이름 | 하는 일 |
|---|---|
| `ensure_model()` | `face_landmarker.task` 가 없으면 다운로드. 실패하면 받을 주소를 안내하고 종료 |
| `create_landmarker()` | MediaPipe Face Landmarker 생성 (VIDEO 모드, 얼굴 1개, 회전행렬 출력) |
| `matrix_to_angles(m)` | 4x4 회전행렬 → (yaw, pitch, roll) |
| `eye_geometry(landmarks, w, h)` | 눈꼬리·홍채·눈꺼풀로 눈동자 h, v, 눈 뜬 정도, 눈 박스 계산. 478점이 아니면 None |
| `extract_features(res, w, h)` | 검출 결과 → `(특징값, geo)`. geo 에는 얼굴 박스·눈 박스·눈동자 위치·프레임 크기 |

### 실시간 판정

| 이름 | 하는 일 |
|---|---|
| `LiveJudge(calib_sec, th)` | 실시간 판정기. `update(t, feat)` 를 프레임마다 호출 |
| `LiveJudge.update()` | 처음 `calib_sec` 초는 기준 잡기 → 이후 최근 7프레임 **중앙값**으로 판정 (깜빡임 같은 순간 튐 무시) |
| `gaze_ratio / head_ratio / eye_ratio` | 지금까지의 응시율·고개 정면 비율·눈 맞춤 비율 (%) |

### 영상 입력

| 이름 | 하는 일 |
|---|---|
| `extract_angles(video_path, step, preview, progress, max_width, track)` | 영상 파일을 끝까지 읽어 프레임별 특징값 목록 반환 |
| `is_stream(source)` | `http://`, `rtsp://`, 숫자(웹캠 번호)면 실시간 입력으로 판단 |
| `normalize_stream_url(source)` | `http://IP:8080` 만 넣어도 `/video` 를 붙여 줌 |
| `LatestFrameReader` | 네트워크 영상은 버퍼가 쌓여 점점 늦어지므로, 별도 스레드가 계속 읽고 **가장 최근 프레임만** 넘겨 줌 |
| `extract_angles_live(source, ...)` | IP Webcam 실시간 분석 (터미널 버전) |

`extract_angles()` 옵션:

- `step=2` : 2프레임마다 1번 분석 (속도)
- `max_width=960` : 큰 영상(1080p, 4K)은 가로 960으로 줄여서 분석
- `progress` : 진행률(0~1)을 받는 함수 (웹 진행 막대용)
- `track` : 리스트를 넘기면 프레임별 박스 위치를 채움 (결과 영상 박스용)
- 영상을 못 열면 `ValueError`

### 화면 그리기 (OpenCV, 실시간·미리보기용)

| 이름 | 하는 일 |
|---|---|
| `draw_overlay(frame, state, ...)` | 테두리 색, 위쪽 검은 막대에 상태·시간·응시율·각도 표시 |
| `draw_eye_debug(frame, geo)` | 왼쪽 아래에 **눈 확대 화면** (눈꼬리 흰 점, 가운데 `+`, 눈동자 초록 원) |
| `draw_boxes(frame, geo, state)` | **얼굴 박스**(상태 색 + 이름), **눈 박스 2개**, **눈동자 점** |

호출 순서: `draw_eye_debug` → `draw_boxes` → `draw_overlay` (확대 화면에 다른 표시가 섞이지 않게)

> OpenCV `putText` 는 한글이 깨져서 영상 위 글자는 영어로 씁니다. (웹 결과 화면은 한글)

### 결과 영상 박스 데이터

| 이름 | 하는 일 |
|---|---|
| `STATE_CODES` | 상태 → 번호 (0 looking, 1 head_updown, 2 head_side, 3 eye_updown, 4 eye_side, 5 no_face) |
| `overlay_sample(geo)` | 박스 위치를 0~1 비율로: `[얼굴 x1,y1,x2,y2, (눈 x1,y1,x2,y2, 눈동자 x,y) × 2]` |
| `build_overlay(frames, track)` | **최종 판정 상태** + 박스 위치 → `{"states": [...], "data": [[시간, 상태번호, ...], ...]}` |

0~1 비율로 저장하므로 브라우저에서 영상 크기가 달라도 정확히 겹쳐집니다.

### 최종 분석

`analyze(rows, calib_sec=3.0, th=None, min_dur=1.0, gap_tol=0.5)` → `(frames, segments, stats)`

1. 프레임 간격 계산 (실시간은 간격이 불규칙해서 **중앙값** 사용)
2. `smooth()` : 약 0.5초 이동 중앙값으로 떨림 제거
3. `fill_blinks()` : 얼굴은 보이는데 눈값이 없는 **0.6초 이하** 구간(눈 깜빡임)은 직전 눈값으로 채움
4. 처음 `calib_sec` 초의 중앙값 = 정면 기준 (얼굴이 늦게 잡히면 처음 잡힌 프레임들로)
5. 프레임마다 `classify()`
6. 이탈 프레임을 구간으로 묶기: **0.5초 이내 끊김은 이어 붙이고**, **1초 미만 구간은 버림**
7. 통계: 응시율, 고개 정면 비율, 눈 맞춤 비율, 얼굴 미검출 비율

예전 형식 `(t, yaw, pitch, roll)` 기록도 받습니다 (눈은 판정 생략).

### 저장

`save_outputs(frames, segs, stats, out_dir, name)` → `results/` 에 저장

| 파일 | 내용 |
|---|---|
| `{name}_gaze_summary.txt` | 요약 (길이, 응시율, 고개·눈 비율, 기준값, 이탈 구간 목록) |
| `{name}_gaze_frames.csv` | `time_sec, d_yaw_deg, d_pitch_deg, d_eye_h, d_eye_v, state` |
| `{name}_gaze_plot.png` | 4칸 그래프 (고개 상하·좌우, 눈동자 상하·좌우) + 이탈 구간 색칠 |

---

## 5. 터미널 실행 옵션

```bash
python gaze_analysis.py <영상파일 | http://IP:8080 | 웹캠번호> [옵션]
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--calib-sec` | 3 | 처음 몇 초를 정면 기준으로 |
| `--yaw-th` | 20 | 고개 좌우 기준 (도) |
| `--pitch-th` | 15 | 고개 상하 기준 (도) |
| `--eye-h-th` | 0.10 | 눈동자 좌우 기준 |
| `--eye-v-th` | 0.08 | 눈동자 상하 기준 |
| `--min-dur` | 1.0 | 이보다 짧은 이탈은 무시 (초) |
| `--step` | 2 | (영상 파일) N프레임마다 1번 분석 |
| `--out` | results | 결과 폴더 |
| `--preview` | | (영상 파일) 분석 화면 보기 (눈 확대 + 박스) |
| `--no-window` | | (실시간) 창 없이 터미널에만 표시, Ctrl+C 로 종료·저장 |
| `--max-sec` | 0 | (실시간) N초 후 자동 종료 (0 = 무제한) |

---

## 6. 변경 이력

| 단계 | 바뀐 내용 |
|---|---|
| 1. 첫 버전 | 영상 파일 → **고개 방향(yaw/pitch)** 만 분석. 처음 3초 캘리브레이션, 이탈 구간 찾기, CSV·그래프·요약 저장 |
| 2. 실시간 | **IP Webcam 실시간 모드** 추가 (`LatestFrameReader`, 주소에 `/video` 자동 추가, 화면 표시). 종료할 때 프로그램이 튕기던(segfault) 문제 수정 → 읽던 스레드가 직접 카메라를 닫도록 |
| 3. 웹 연동 | 판정 함수 `classify()` 를 따로 빼서 웹 서버와 공용으로 사용 |
| 4. 눈 시선 1차 | MediaPipe의 `eyeLook*` 점수로 눈 시선 추가, 상태 6가지, `LiveJudge` 공용 판정기, 그래프 4칸, 고개·눈 비율 따로 계산 |
| 5. 눈동자 직접 측정 | `eyeLook*` 점수가 얼굴 전체 모양에 흔들려 **눈동자 대신 얼굴 움직임처럼 반응**하는 문제 → **홍채 점(468~477)으로 눈동자 위치를 직접 계산**하도록 변경. 눈 감음 걸러내기, 고개 갸웃해도 안정, **눈 확대 화면** 추가. 기준값 단위가 "눈 폭 대비 비율"로 바뀜 (0.10 / 0.08) |
| 6. 영상 업로드 지원 | `extract_angles()` 에 진행률·크기 줄이기 추가, 영상을 못 열면 프로그램 종료 대신 `ValueError`. **버그 수정**: 눈만 벗어난 구간 중간에 눈을 깜빡이면 그 순간이 "카메라 봄"으로 계산돼 구간이 쪼개지고 앞부분이 사라지던 문제 → `fill_blinks()` 로 깜빡임을 직전 눈값으로 채움, 구간 이어 붙이기 허용 0.4 → 0.5초 |
| 7. 박스 표시 | 얼굴 박스·눈 박스 계산 (`EYE_CONTOURS`), `draw_boxes()` (실시간·미리보기), 결과 영상용 `overlay_sample()` / `build_overlay()`, `extract_angles(track=...)`. 눈 확대 화면 크기 축소 (42% → 32%) |

---

## 7. 참고 자료

### 공식 문서에서 가져온 것

| 코드 부분 | 참고한 내용 | 출처 |
|---|---|---|
| `create_landmarker()`, `detect_for_video()`, `RunningMode.VIDEO`, `output_facial_transformation_matrixes` | Face Landmarker 파이썬 사용법, 영상(VIDEO) 모드, 얼굴 회전행렬 출력 옵션 | [Face landmark detection guide for Python](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python) |
| `MODEL_URL`, 얼굴 점 478개, (4단계에서 쓴) `eyeLook*` 블렌드셰이프 점수 | 모델 파일, 출력 형식 | [Face landmark detection guide (개요)](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker) |
| `IRIS_POINTS` (468~477, 홍채 중심 + 테두리 4점) | 홍채 점 5개씩을 얼굴 점에 추가하는 MediaPipe Iris 방식 | [MediaPipe Iris: Real-time Iris Tracking & Depth Estimation (Google Research)](https://research.google/blog/mediapipe-iris-real-time-iris-tracking-depth-estimation/) |
| `EYE_POINTS`, `EYE_CONTOURS` (눈꼬리 33·133·362·263, 눈꺼풀 159·145·386·374, 눈 테두리 16점) | MediaPipe 얼굴 메시 점 번호 | [MediaPipe Face Mesh 문서](https://mediapipe.readthedocs.io/en/latest/solutions/face_mesh.html), [Face Mesh landmark indices 정리](https://hackernoon.com/mediapipe-face-mesh-landmark-indices-cheat-sheet) |
| `cv2.VideoCapture`, `cv2.resize`, `cv2.putText`, `cv2.rectangle`, `cv2.circle`, `cv2.imshow` | OpenCV 기본 함수 | [OpenCV 문서](https://docs.opencv.org/) |
| `save_outputs()` 그래프 | matplotlib 서브플롯, `axvspan` 구간 색칠 | [matplotlib 문서](https://matplotlib.org/stable/) |

> MediaPipe 얼굴 점 번호는 공식 문서가 그림(얼굴 메시 지도)으로만 제공해서, 번호를 정리한 비공식 자료를 함께 참고했습니다.
> 홍채 점의 위치(468~477)와 눈 짝짓기는 번호 순서에 의존하지 않도록 "눈 가운데와 가까운 홍채"로 짝을 짓게 만들었습니다.


### 참고 자료 없이 직접 정한 것 (테스트로 조정 필요)

- **눈동자 h, v 계산식**: 눈꼬리 선 기준으로 홍채 중심이 벗어난 거리를 눈 폭으로 나누는 방식 (일반적으로 쓰이는 "눈 안에서 홍채 위치 비율" 아이디어를 이 프로젝트에 맞게 구현)
- **기준값** `yaw_th=20`, `pitch_th=15`, `eye_h_th=0.10`, `eye_v_th=0.08`, `EYE_CLOSED_RATIO=0.09`: 임시값. 실제 얼굴 영상으로 조정해야 함
- **판정 우선순위** (얼굴 없음 → 고개 → 눈), 최근 7프레임 중앙값, 0.5초 이동 중앙값
- **`fill_blinks()`** (0.6초 이하 깜빡임 채우기), 구간 이어 붙이기 0.5초, 최소 구간 1초
- 박스 데이터 형식 (`overlay_sample()`, `build_overlay()`), 화면 색·문구

---

## 출처 (수업 자료)

| 수업 python 파일 | 쓴 기능 | 이 프로젝트에서 쓴 곳 |
|---|---|---|
| `src/cap_inspector.py` | `judge_cap()` 측정값을 기준값과 비교해 OK / NG 판정 | `gaze_analysis.py` `classify()`, `head_state()`, `eye_state()` (고개·눈 값을 기준과 비교해 상태 판정) |
| `src/cap_inspector.py` | `inspect_roi()` 대상을 못 찾으면 CHECK, 결과를 dict 로 반환 | `gaze_analysis.py` 얼굴을 못 찾으면 `no_face`, `extract_features()` |
| `src/cap_inspector.py` | `load_rule()` 기준 이미지(cap_on / cap_off)로 기준값 만들기 | `gaze_analysis.py` `analyze()`, `LiveJudge` 의 처음 3초 정면 기준 잡기 |
| `src/fastapi_cap_app.py` | `camera_loop()` 의 `cv2.VideoCapture(url)` 로 IP Webcam(`/video`) 열기 | `gaze_analysis.py` `normalize_stream_url()`, `LatestFrameReader`, `extract_angles_live()` |
| `src/36_day05_freeze_rule.py` | Calibration 으로 정한 기준값을 JSON 에 저장해 고정 (Rule Freeze) | 가져온 코드 없음. 기준값을 정하고 고정한 뒤 확인하는 순서만 README 6장과 `DEFAULT_TH` 에 반영 |

> 3일차 확장 실습 코드: `src/fastapi_cap_app.py`, `src/cap_inspector.py`, `tests/test_inspector.py` · 5일차 코드: `src/36_day05_freeze_rule.py`

> `load_rule()` 은 정상·불량 이미지 2장으로 기준값을 만들고, 이 코드의 처음 3초는 사람마다 다른 **정면 자세**를 기준으로 잡는다는 점이 다릅니다.