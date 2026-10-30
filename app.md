# app.py — FastAPI 웹 서버

수업 실습(병뚜껑 실시간 검사)의 **IP Webcam → OpenCV → FastAPI 웹 화면** 구조를 그대로 가져와서,
ROI 검사 대신 **시선 분석**을 하도록 바꾼 서버입니다. 분석 자체는 `gaze_analysis.py` 를 가져다 씁니다.

```bash
uvicorn app:app --host 0.0.0.0 --port 8000     # 실행 → 브라우저에서 http://localhost:8000
```

---

## 1. 전체 구조

```
브라우저 (web/index.html)
   │  주소 입력·시작·종료·영상 업로드          ▲ 0.3초마다 상태 조회 / 실시간 영상(MJPEG)
   ▼                                           │
app.py ─ LiveState (카메라 스레드 1개)  ── 폰 IP Webcam 영상 읽기 → 얼굴·눈 분석 → 박스 그린 JPEG
       ─ FileJob   (분석 스레드)        ── 올린 영상 파일 분석 → 결과 + 박스 데이터
       ─ analyze_and_save()            ── 최종 분석·파일 저장·결과 JSON (실시간·파일 공용)
```

- 카메라는 **스레드 하나에서만** 읽고, 최신 JPEG를 여러 브라우저에 나눠 줍니다 (수업 코드와 같은 방식).
- 얼굴 모델(`FaceLandmarker`)은 카메라 스레드에서 한 번만 만들고, 영상 파일 분석은 따로 하나 더 만듭니다 (타임스탬프가 섞이지 않게).

---

## 2. 코드 구역 (주석 번호)

| 번호 | 내용 |
|---|---|
| ① | 라이브러리, 경로 상수 (`ROOT`, `HTML_FILE`, `RESULT_DIR`, `UPLOAD_DIR`, `ALLOWED_EXT`, `MAX_UPLOAD_MB=1024`, `MAX_WIDTH=960`) |
| ② | 카메라 연결 전 보여 줄 대기 화면 JPEG |
| ③ | `LiveState` : 실시간 카메라·연습 세션 상태 |
| ④ | 카메라 스레드 시작·종료 |
| ⑤ | `set_camera()` : 주소 저장 (바꾸면 진행 중 세션 취소) |
| ⑥ | `start_session()` / `stop_session()` : 연습 시작(새 `LiveJudge`) / 종료(최종 분석) |
| ⑦ | `get_status()` : 연결 상태, 단계(대기·기준 잡기·연습 중), 실시간 판정, 비율 |
| ⑧~⑫ | `camera_loop()` : 연결·재연결 → 프레임 읽기 → 얼굴·눈 분석 → 판정 → **눈 확대 + 박스 + 정보 막대** 그려서 JPEG 저장 |
| ⑬ | `COACHING` 코칭 문장, `analyze_and_save()` |
| ⑬-2 | `FileJob` : 올린 영상 백그라운드 분석 |
| ⑭ | 서버 시작·종료 (lifespan) |
| ⑮ | 요청 검증 (`CameraRequest`, `SessionRequest`) |
| ⑯~㉔ | API (아래 표) |

---

## 3. API

| 방식 | 주소 | 하는 일 |
|---|---|---|
| GET | `/` | 웹 화면 (`web/index.html`) |
| POST | `/api/camera` | `{"url": "192.168.0.10:8080"}` 카메라 연결. `http://` 와 `/video` 자동 추가, **사설 IP만 허용**, 아이디·비밀번호 있는 주소 거부 |
| POST | `/api/session/start` | 연습 시작. 판정 기준을 함께 보냄 (카메라가 ONLINE 일 때만) |
| POST | `/api/session/stop` | 연습 종료 → 최종 분석 → 결과 JSON |
| GET | `/api/status` | 실시간 상태 (화면이 0.3초마다 조회) |
| GET | `/api/result` | 마지막 결과 (실시간·영상 공용) |
| POST | `/api/upload?calib_sec=3&yaw_th=20&...` | 영상 파일 올리기 → 백그라운드 분석 시작 |
| GET | `/api/upload/status` | 영상 분석 진행률·결과 |
| GET | `/results/{파일}` | 그래프 PNG, 박스 데이터 JSON 등 (results 폴더 안 파일만) |
| GET | `/uploads/{파일}` | 올린 영상 (영상 구간 이동을 위한 Range 요청 지원) |
| GET | `/stream.mjpg` | 실시간 영상 (MJPEG, 최대 약 10장/초) |

`/results/..`, `/uploads/..` 는 `../app.py` 같은 경로로 폴더 밖 파일을 못 읽게 막아 두었습니다.

### 판정 기준 (`SessionRequest`, 업로드에도 같은 값 사용)

| 키 | 기본값 | 허용 범위 |
|---|---|---|
| `calib_sec` | 3.0 | 1 ~ 10 초 |
| `yaw_th` | 20.0 | 5 ~ 60 도 |
| `pitch_th` | 15.0 | 5 ~ 60 도 |
| `eye_h_th` | 0.10 | 0.02 ~ 0.5 |
| `eye_v_th` | 0.08 | 0.02 ~ 0.5 |

### `/api/status` 주요 값

| 키 | 의미 |
|---|---|
| `camera_status` | `NOT_CONNECTED` / `CONNECTING` / `ONLINE` / `DISCONNECTED` (5초 이상 프레임이 없으면 DISCONNECTED) |
| `message` | 안내 문구. 얼굴 분석기 시작 실패 같은 오류도 여기로 |
| `session_active`, `phase` | 연습 중인지, `idle` / `calib` / `live` |
| `calib_left`, `elapsed` | 기준 잡기 남은 시간, 연습 경과 시간 |
| `live_state` | 실시간 상태 (`looking`, `head_updown`, ...) |
| `gaze_ratio`, `head_ratio`, `eye_ratio` | 연습 중 실시간 비율 (종료 후에는 결과 리포트 값이 정답) |
| `d` | 기준 대비 차이 (`d_yaw`, `d_pitch`, `d_eye_h`, `d_eye_v`) |

### 결과 JSON (`analyze_and_save()` 가 만듦)

| 키 | 의미 |
|---|---|
| `source` | "실시간 연습" 또는 올린 파일 이름 |
| `gaze_ratio`, `head_ratio`, `eye_ratio`, `no_face_ratio`, `total_sec` | 비율·길이 |
| `overall` | 전체 한 줄 코멘트 (`overall_comment()`) |
| `segments` | 이탈 구간 목록: `start`, `end`, `start_sec`, `dur`, `kind`, `label`, `coaching` |
| `plot_url` | 그래프 PNG 주소 |
| `video_url` | 올린 영상 주소 (영상 파일일 때만) |
| `overlay_url` | 박스 데이터 JSON 주소 (영상 파일일 때만) |

---

## 4. 영상 업로드 처리 (`/api/upload` + `FileJob`)

1. 브라우저가 파일 내용을 그대로 보냄 (`Content-Type: application/octet-stream`, 파일 이름은 `X-Filename` 헤더)
   → **python-multipart 같은 추가 설치가 필요 없습니다.**
2. 서버는 받은 내용을 조금씩 `uploads/upload_날짜_시간.확장자` 로 저장 (한글·특수문자 없는 이름)
3. 확인: 확장자(mp4, mov, m4v, avi, mkv, webm), 빈 파일, 1GB 초과, 판정 기준 범위, **이미 분석 중이면 409 거절**
4. `FileJob` 스레드가 `ga.extract_angles(..., progress=..., track=...)` 로 분석
   → 진행 메시지: "분석 준비 중" → "얼굴·눈 분석 중 N%" → "결과 정리 중" → "분석 완료"
5. `analyze_and_save()` 가 결과와 함께 `results/video_..._overlay.json` (박스 데이터) 저장
6. 실패 시 이유를 메시지로: 모델 없음 / 영상을 못 엶 / 얼굴을 못 찾음 / 기타 오류

---

## 5. 코칭 문장 (`COACHING`, `overall_comment()`)

| 구간 종류 | 코칭 |
|---|---|
| `head_updown` | 고개를 들고 핵심 문장을 외워서 다시 말해보기 |
| `head_side` | 얼굴과 몸을 카메라(청중) 쪽으로 향하고 다시 |
| `eye_updown` | 메모 곁눈질·눈 굴리는 습관 → 렌즈를 보며 다시 |
| `eye_side` | 옆 자료를 카메라 가까이로 옮기고 다시 |
| `no_face` | 화면 안에서 움직이도록 위치 잡기 |

전체 코멘트: 응시율 80% 이상 칭찬 / 고개는 좋은데 눈이 10%p 이상 낮으면 눈 연습 권유 / 60% 이상 보통 / 그 아래는 부족.
(퍼센트 기준은 임시값이라 테스트 영상으로 조정하면 됩니다.)

---

## 6. 변경 이력

| 단계 | 바뀐 내용 |
|---|---|
| 1. 첫 웹 버전 | 수업 코드 구조로 서버 작성: 주소 입력 연결, 카메라 스레드, MJPEG 전송, 상태 조회. ROI 대신 **연습 시작(3초 기준 잡기) → 실시간 판정 → 종료 시 결과 리포트**. 사설 IP 검사, 결과 폴더 밖 파일 접근 차단 |
| 2. 결과 표시 개선 | 종료 후에는 실시간 응시율을 숨김 (결과 리포트 값과 헷갈리지 않게), 기준 잡기 색 수정 |
| 3. 눈 시선 추가 | 판정을 `ga.LiveJudge` 로 통일, 상태 6가지, 고개·눈 비율, 눈 기준값 설정, 상태별 코칭 문장 |
| 4. 오류 안내 | 얼굴 분석기 시작 실패(예: `libGLESv2` 없음)를 터미널과 웹 화면에 표시 |
| 5. 눈동자 직접 측정 | 프레임 크기를 넘겨 홍채 위치 계산, **눈 확대 화면** 표시, 눈 기준 범위를 비율 단위로 (0.02~0.5) |
| 6. 영상 업로드 | `/api/upload`, `/api/upload/status`, `/uploads/{파일}`, `FileJob`, `analyze_and_save()` 로 실시간·파일 결과 형식 통일, 결과에 `source`, `video_url`, 구간 `start_sec` 추가 |
| 7. 박스 표시 | 실시간 영상에 `ga.draw_boxes()` (얼굴·눈 박스 + 눈동자), 영상 파일은 박스 데이터 JSON 저장 후 `overlay_url` 로 전달 |

---

## 7. 참고 자료


### 공식 문서에서 가져온 것

| 코드 부분 | 출처 |
|---|---|
| `FileResponse`, `StreamingResponse` | [FastAPI Reference – Response classes](https://fastapi.tiangolo.com/reference/responses/) |
| `lifespan` (서버 시작·종료 작업) | [FastAPI – Lifespan Events](https://fastapi.tiangolo.com/advanced/events/) |
| `/api/upload` 에서 `request.stream()` 으로 파일을 조금씩 받기 (python-multipart 없이) | [Starlette – Requests (Request Body, stream)](https://www.starlette.dev/requests/) |
| `/uploads/{파일}` 영상 구간 이동 (Range 요청) | Starlette `FileResponse` 의 Range 지원 — [FastAPI Reference – Response classes](https://fastapi.tiangolo.com/reference/responses/) |
| 실행 명령 `uvicorn app:app --host 0.0.0.0 --port 8000` | [Uvicorn – Settings](https://www.uvicorn.dev/settings), [Uvicorn – Deployment](https://uvicorn.dev/deployment/) |

### 참고 자료 없이 직접 정한 것

- `/api/session/start`·`stop`, `/api/upload`·`/api/upload/status` API 설계, `FileJob` 백그라운드 분석, 결과 JSON 형식
- 업로드 제한 (확장자 목록, 1GB, 분석 중 중복 업로드 409)
- `COACHING` 코칭 문장, `overall_comment()` 의 퍼센트 기준 (80% / 60% / 고개-눈 10%p 차이) — 임시값

---

## 출처 (수업 자료)

| 수업 python 파일 | 쓴 기능 | 이 프로젝트에서 쓴 곳 |
|---|---|---|
| `src/fastapi_cap_app.py` | `placeholder_jpg()` 카메라 연결 전 대기 화면 JPEG | `app.py` ② `placeholder_jpg()` |
| `src/fastapi_cap_app.py` | `LiveState` 카메라 1대를 스레드 1개에서 읽고 `lock` 으로 여러 브라우저에 공유, `start()` / `stop()` / `set_camera()` | `app.py` ③~⑤ `LiveState` |
| `src/fastapi_cap_app.py` | `get_status()` 5초 넘게 프레임이 없으면 DISCONNECTED | `app.py` ⑦ `get_status()` |
| `src/fastapi_cap_app.py` | `camera_loop()` `cv2.VideoCapture(url)` 연결·재연결, 프레임 읽기, JPEG(품질 77) 변환, 주소가 바뀌면 이전 결과 버림 | `app.py` ⑧~⑫ `camera_loop()` |
| `src/fastapi_cap_app.py` | `lifespan()` 서버 시작 시 카메라 스레드 시작, 종료 시 정리 | `app.py` ⑭ `lifespan()` |
| `src/fastapi_cap_app.py` | `CameraRequest`, `Field(ge=, le=)` Pydantic 범위 검증 | `app.py` ⑮ `CameraRequest`, `SessionRequest` |
| `src/fastapi_cap_app.py` | `index()` GET `/` HTML 화면 전달 | `app.py` ⑯ `index()` |
| `src/fastapi_cap_app.py` | `connect_camera()` POST `/api/camera` http(s) 형식·아이디/비밀번호 거부·사설 IP만 허용·포트 확인 | `app.py` ⑰ `connect_camera()` |
| `src/fastapi_cap_app.py` | GET `/api/status` 최신 상태 JSON | `app.py` ⑳ `get_status()` |
| `src/fastapi_cap_app.py` | `stream_video()` GET `/stream.mjpg` MJPEG 실시간 영상 (약 10장/초) | `app.py` ㉔ `stream_video()` |
| `tests/test_inspector.py` | `FakeCapture` 실제 카메라 대신 가짜 프레임, `synthetic_roi()` 가짜 이미지, `wait_for()` `/api/status` 반복 확인 | 개발 중 테스트 (가짜 카메라 서버 + 가짜 얼굴 점으로 전체 흐름 확인). 배포 파일에는 없음 |

> 3일차 확장 실습 코드: `src/fastapi_cap_app.py`, `src/cap_inspector.py`, `tests/test_inspector.py` · 5일차 코드: `src/36_day05_freeze_rule.py`