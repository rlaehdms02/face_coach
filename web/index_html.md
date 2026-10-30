# web/index.html — 웹 화면

브라우저에서 보는 화면입니다. 외부 라이브러리 없이 HTML + CSS + JavaScript 한 파일로 되어 있고,
`app.py` 의 API 를 불러서 동작합니다. **반드시 `face_coach/web/` 폴더 안에** 있어야 합니다.

---

## 1. 화면 구성

```
┌──────────────── ① 카메라 연결 ────────────────┐ ┌──── ② 연습하기 ────┐
│ [폰 주소 입력칸] [연결]   상태: ONLINE         │ │ [상태 카드 (색)]    │
│ ┌────────────────────────────────────────────┐ │ │ 응시율 / 연습 시간  │
│ │ 실시간 영상 (/stream.mjpg)                  │ │ │ 고개 정면 / 눈 맞춤 │
│ │  - 얼굴·눈 박스, 눈동자 점                  │ │ │ [시작] [종료]       │
│ │  - 왼쪽 아래 눈 확대 화면                    │ │ │ ── 또는 찍어 둔 영상으로 ── │
│ └────────────────────────────────────────────┘ │ │ [영상 선택][올려서 분석]   │
└────────────────────────────────────────────────┘ │ 진행 막대           │
                                                   │ ▸ 판정 기준 설정     │
                                                   │ ▸ 사용 방법          │
                                                   └────────────────────┘
┌──────────────────────── ③ 연습 결과 ───────────────────────────────┐
│ 응시율(크게) · 연습 시간 · 얼굴 미검출 · 고개 정면 · 눈 맞춤         │
│ 전체 코멘트                                                        │
│ [결과 영상 + 박스 겹쳐 그리기]  ☑ 얼굴·눈 박스 표시  색 설명          │
│ 이탈 구간 목록 (태그: 고개/눈/얼굴, 누르면 영상 이동)                 │
│ 그래프 (4칸)                                                       │
└───────────────────────────────────────────────────────────────────┘
```

화면 폭이 좁으면(860px 이하, 휴대폰 등) 한 줄로 쌓입니다.

---

## 2. 주요 동작

### 카메라 연결

- 입력한 주소를 `/api/camera` 로 보냄. 지난번 주소는 브라우저에 기억해 둠 (localStorage).
- Enter 키로도 연결.

### 실시간 연습

- 0.3초마다 `/api/status` 를 조회해서 상태 카드 색·문구, 응시율 막대, 고개·눈 비율, 버튼 상태를 바꿈.
- **시작**: 판정 기준(`getSettings()`)과 함께 `/api/session/start`
- **종료하고 결과 보기**: `/api/session/stop` → 결과 표시

### 영상 파일 올리기

- **영상 선택** → 파일 이름·크기 표시 → **올려서 분석**
- `XMLHttpRequest` 로 보냄 (fetch 는 올리는 진행률을 알 수 없어서). 판정 기준은 주소 뒤 `?calib_sec=...` 로.
- 올린 뒤 0.5초마다 `/api/upload/status` 조회 → "얼굴·눈 분석 중 N%" → 완료되면 결과 표시
- 페이지를 새로고침해도 분석 중이던 작업은 이어서 표시

### 결과 표시 (`showResult()`)

- 실시간 결과와 영상 결과가 같은 함수로 표시됨. 제목 옆에 출처(실시간 연습 / 파일 이름).
- 영상 결과면 영상 플레이어 표시. 브라우저가 재생 못 하는 형식이면 영상만 숨김.
- 이탈 구간을 누르면 **그 구간 1초 전으로 이동해 재생** ("▶ 이 구간 보기").
- 구간 앞 태그: `고개` / `눈` / `얼굴`

### 결과 영상 위 박스 (`drawOverlay()`)

- `overlay_url` 의 박스 데이터를 받아, 영상 위에 겹친 `<canvas>` 에 그림 (영상을 새로 만들지 않음).
- 지금 재생 시간과 가장 가까운 기록을 **이진 탐색**으로 찾음 (0.5초 넘게 차이 나면 안 그림).
- 영상의 검은 여백(가로·세로 비율 차이)을 계산해서 실제 영상 영역에 맞춰 그림.
- 얼굴 박스 + 상태 이름(한글), 눈 박스(눈만 벗어나면 상태 색), 눈동자 초록 점, 얼굴이 없으면 "얼굴 없음" 표시.
- 재생 중에는 매 화면마다, 멈춤·이동·창 크기 변경 때도 다시 그림. 고해상도 화면(devicePixelRatio)도 선명하게.
- "얼굴·눈 박스 표시" 체크를 끄면 박스가 사라짐.

### 상태 색 (CSS 변수)

| 변수 | 색 | 상태 |
|---|---|---|
| `--ok` | 초록 | 카메라 응시 |
| `--bad` | 빨강 | 고개 위·아래 |
| `--warn` | 주황 | 고개 옆 |
| `--eye-v` | 보라 | 눈만 위·아래 |
| `--eye-h` | 파랑 | 눈만 옆 |
| `--mute` | 회색 | 얼굴 없음 / 대기 |
| `--calib` | 노랑 | 기준 잡는 중 |

### 판정 기준 설정 (접어 둔 메뉴)

처음 기준 잡는 시간, 고개 좌우·상하(도), 눈동자 좌우·상하(눈 폭 대비). 실시간 연습과 영상 분석 모두에 적용됩니다.

---

## 3. 변경 이력

| 단계 | 바뀐 내용 |
|---|---|
| 1. 첫 버전 | 카메라 연결, 실시간 영상, 상태 카드, 응시율 막대, 시작·종료, 결과 리포트(구간 + 코칭 + 그래프), 판정 기준 설정, 휴대폰 화면 대응 |
| 2. 눈 시선 | 상태 6가지 색, **고개 정면 / 눈 맞춤** 칸, 눈 기준 설정, 구간 태그(고개/눈/얼굴) |
| 3. 눈동자 직접 측정 | 눈 기준 단위를 "눈 폭 대비"로 (0.10 / 0.08), 눈 확대 화면 안내 문구 |
| 4. 영상 업로드 | "또는 찍어 둔 영상으로" 영역, 업로드·분석 진행 막대, 결과 영상 플레이어, **구간 누르면 영상 이동**, 결과 출처 표시 |
| 5. 박스 표시 | 결과 영상 위 `<canvas>` 에 **얼굴·눈 박스 + 눈동자** 그리기, 표시 켜기/끄기, 색 설명 |

---

## 4. 참고 자료


### 공식 문서에서 가져온 것

| 코드 부분 | 출처 |
|---|---|
| 영상 업로드 진행률 (`xhr.upload.onprogress`) | [MDN – XMLHttpRequest progress event](https://developer.mozilla.org/docs/Web/API/XMLHttpRequest/progress_event) |
| 구간 누르면 이동 (`video.currentTime`) | [MDN – HTMLMediaElement: currentTime](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime) |
| 고해상도 화면에서 박스를 선명하게 (`devicePixelRatio`) | [MDN – Window: devicePixelRatio](https://developer.mozilla.org/en-US/docs/Web/API/Window.devicePixelRatio) |
| `<canvas>` 2D 그리기 (`strokeRect`, `arc`, `fillText`) | [MDN – Canvas API](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API) |
| 재생 중 다시 그리기 (`requestAnimationFrame`) | [MDN – Window: requestAnimationFrame()](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame) |
| 지난 주소 기억 (`localStorage`) | [MDN – Window: localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage) |

### 참고 자료 없이 직접 정한 것

- 화면 배치, 색 구성, 상태 문구, 구간 태그
- 박스 데이터에서 지금 시간의 기록을 찾는 이진 탐색, 0.5초 넘게 차이 나면 안 그리는 규칙
- 영상의 검은 여백을 빼고 실제 영상 영역에 맞춰 그리는 계산

---

## 출처 (수업 자료)

| 수업 python 파일 | 쓴 기능 | 이 프로젝트에서 쓴 곳 |
|---|---|---|
| `src/fastapi_cap_app.py` | `/api/camera`, `/api/status`, `/stream.mjpg` API | `web/index.html` 주소 연결 → 상태 반복 조회 → `<img>` 로 실시간 영상 표시 |

> 3일차 확장 실습 코드: `src/fastapi_cap_app.py`, `src/cap_inspector.py`, `tests/test_inspector.py` · 5일차 코드: `src/36_day05_freeze_rule.py`

> 화면 HTML·CSS·JavaScript 는 수업의 `web/cap_inspection.html` 을 복사하지 않고 새로 작성했습니다.