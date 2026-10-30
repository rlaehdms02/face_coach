"""
발표 연습 코치 - 시선 분석 (고개 방향 + 눈동자 시선)

두 가지를 함께 본다.
  - 고개 방향: 얼굴 전체가 어디를 향하는지 (yaw: 좌우, pitch: 상하, 단위: 도)
  - 눈 시선 : 고개는 그대로인데 눈동자만 위/아래/옆을 보는지
              (홍채 중심이 두 눈꼬리 사이 가운데에서 얼마나 벗어났는지,
               단위: 눈 가로 길이 대비 비율. 0.10 = 눈 폭의 10%만큼 이동)

처음 몇 초를 "카메라 정면" 기준으로 잡고(캘리브레이션), 기준에서 많이 벗어나면
이탈로 판정한다. 고개와 눈이 모두 기준 안에 있어야 "카메라를 보고 있음"이다.

사용법 (영상 파일)
  python gaze_analysis.py 내발표.mp4
  python gaze_analysis.py 내발표.mp4 --preview          # 분석하면서 화면 보기
  python gaze_analysis.py 내발표.mp4 --eye-v-th 0.06    # 눈 상하 기준을 더 엄격하게

사용법 (실시간: 폰 IP Webcam 앱)
  python gaze_analysis.py http://192.168.0.10:8080      # 폰 화면에 나온 주소
  - 처음 3초는 카메라 정면을 보고 있으면 기준이 잡힌다.
  - 창에서 q 를 누르면(창이 없으면 Ctrl+C) 끝나고 결과가 저장된다.

설치
  pip install opencv-python mediapipe matplotlib numpy
"""

import argparse
import csv
import math
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_PATH = Path(__file__).with_name("face_landmarker.task")

# 판정 기준 기본값 (테스트 영상으로 조정하세요)
DEFAULT_TH = dict(
    yaw_th=20.0,    # 고개 좌우 (도)
    pitch_th=15.0,  # 고개 상하 (도)
    eye_h_th=0.10,  # 눈동자 좌우 이동 (눈 폭 대비 비율)
    eye_v_th=0.08,  # 눈동자 상하 이동 (눈 폭 대비 비율, 위아래는 움직임이 작아서 더 작게)
)

# MediaPipe 얼굴 랜드마크(478개) 중 눈·홍채 번호
EYE_POINTS = [  # (눈꼬리 1, 눈꼬리 2, 윗눈꺼풀, 아랫눈꺼풀)
    (33, 133, 159, 145),
    (362, 263, 386, 374),
]
IRIS_POINTS = [list(range(468, 473)), list(range(473, 478))]  # 홍채 중심 + 테두리 4점
EYE_CONTOURS = [  # 눈 테두리 점들 (눈 박스 그리기용), EYE_POINTS 와 같은 순서
    [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],
    [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398],
]
EYE_CLOSED_RATIO = 0.09  # 눈 뜬 정도(눈꺼풀 간격/눈 폭)가 이보다 작으면 감은 것으로 보고 눈 판정 생략

# 상태 이름과 설명 (CLI·웹 공용)
LABELS = {
    "looking": "카메라를 보고 있음",
    "head_updown": "고개가 위/아래를 향함",
    "head_side": "고개가 옆을 향함",
    "eye_updown": "눈만 위/아래를 봄",
    "eye_side": "눈만 옆을 봄",
    "no_face": "얼굴이 화면에서 사라짐",
}


# ---------------------------------------------------------------------------
# 1. 얼굴 모델 준비
# ---------------------------------------------------------------------------
def ensure_model():
    if MODEL_PATH.exists():
        return MODEL_PATH
    print(f"[준비] 얼굴 모델을 다운로드합니다 → {MODEL_PATH.name} (약 3.6MB, 최초 1회)")
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as e:
        print("[오류] 모델 다운로드 실패:", e)
        print("브라우저로 아래 주소를 열어 받은 파일을 이 스크립트와 같은 폴더에 두세요.")
        print("  ", MODEL_URL)
        sys.exit(1)
    return MODEL_PATH


def create_landmarker():
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(ensure_model())),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        output_facial_transformation_matrixes=True,  # 고개 방향 (눈 시선은 랜드마크의 홍채 점으로 계산)
    )
    return mp, vision.FaceLandmarker.create_from_options(options)


# ---------------------------------------------------------------------------
# 2. 검출 결과 → 특징값 (고개 각도 + 눈 시선)
# ---------------------------------------------------------------------------
def matrix_to_angles(m):
    """4x4 얼굴 변환행렬에서 (yaw, pitch, roll) 각도(도)를 계산한다.
    부호(위/아래, 왼/오른쪽)는 카메라 환경에 따라 다를 수 있어서
    판정에는 '기준값과의 차이 크기'만 사용한다."""
    r = np.asarray(m)[:3, :3]
    pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
    yaw = math.degrees(math.asin(float(np.clip(-r[2, 0], -1.0, 1.0))))
    roll = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    return yaw, pitch, roll


def eye_geometry(landmarks, w, h):
    """두 눈꼬리를 잇는 선을 기준으로 홍채 중심이 어디 있는지 계산한다.
      h: 눈꼬리 선 방향 이동 (화면 오른쪽 +), v: 수직 방향 이동 (화면 아래쪽 +)
      둘 다 눈 가로 길이로 나눈 비율이라 얼굴 크기·거리와 상관없고,
      눈꼬리 선 기준이라 고개를 갸웃(roll)해도 값이 크게 변하지 않는다.
    랜드마크가 478개(홍채 포함)가 아니면 None."""
    if len(landmarks) < 478:
        return None
    P = lambda i: np.array([landmarks[i].x * w, landmarks[i].y * h], dtype=float)
    irises = [np.mean([P(i) for i in idx], axis=0) for idx in IRIS_POINTS]
    eyes = []
    for (a, b, up, lo), contour in zip(EYE_POINTS, EYE_CONTOURS):
        cpts = np.array([P(i) for i in contour])
        box = (*cpts.min(0), *cpts.max(0))  # 눈 박스 (x1, y1, x2, y2)
        A, B = P(a), P(b)
        if A[0] > B[0]:
            A, B = B, A  # 항상 화면 왼쪽 → 오른쪽 방향으로
        width = float(np.linalg.norm(B - A))
        if width < 1e-6:
            return None
        e = (B - A) / width               # 눈꼬리 선 방향
        n = np.array([-e[1], e[0]])       # 수직 방향 (화면 아래쪽 +)
        mid = (A + B) / 2
        iris = min(irises, key=lambda c: float(np.linalg.norm(c - mid)))  # 가까운 홍채를 짝으로
        d = iris - mid
        eyes.append(dict(A=A, B=B, mid=mid, iris=iris, width=width, box=box,
                         h=float(d @ e / width), v=float(d @ n / width),
                         open=float(np.linalg.norm(P(up) - P(lo)) / width)))
    return dict(eyes=eyes,
                h=float(np.mean([x["h"] for x in eyes])),
                v=float(np.mean([x["v"] for x in eyes])),
                open=float(np.mean([x["open"] for x in eyes])))


def extract_features(res, w=1, h=1):
    """FaceLandmarker 결과 → (특징값, 눈 위치 정보).
    특징값 = (yaw, pitch, roll, eye_h, eye_v). 얼굴이 없으면 (None, None).
    눈을 감았거나 홍채 점이 없으면 eye_h, eye_v는 None (그 프레임은 고개만으로 판정).
    w, h 는 프레임 크기(픽셀). 가로세로 비율을 맞추려고 필요하다."""
    if not res.facial_transformation_matrixes:
        return None, None
    yaw, pitch, roll = matrix_to_angles(res.facial_transformation_matrixes[0])
    geo = None
    if getattr(res, "face_landmarks", None):
        lm = res.face_landmarks[0]
        geo = eye_geometry(lm, w, h) or {"eyes": []}
        xs, ys = [p.x * w for p in lm], [p.y * h for p in lm]
        geo["face_box"] = (min(xs), min(ys), max(xs), max(ys))  # 얼굴 박스 (x1, y1, x2, y2)
        geo["size"] = (w, h)
    eye_h = eye_v = None
    if geo is not None and "open" in geo and geo["open"] >= EYE_CLOSED_RATIO:
        eye_h, eye_v = geo["h"], geo["v"]
    return (yaw, pitch, roll, eye_h, eye_v), geo


# ---------------------------------------------------------------------------
# 3. 판정 규칙 (CLI·웹 서버 공용)
# ---------------------------------------------------------------------------
def head_state(dy, dp, th):
    if abs(dp) > th["pitch_th"] and abs(dp) / th["pitch_th"] >= abs(dy) / th["yaw_th"]:
        return "head_updown"
    if abs(dy) > th["yaw_th"]:
        return "head_side"
    return None


def eye_state(deh, dev, th):
    if deh is None or dev is None:
        return None  # 눈 점수 없음 → 눈 판정 생략
    if abs(dev) > th["eye_v_th"] and abs(dev) / th["eye_v_th"] >= abs(deh) / th["eye_h_th"]:
        return "eye_updown"
    if abs(deh) > th["eye_h_th"]:
        return "eye_side"
    return None


def classify(dy, dp, deh, dev, th):
    """기준 대비 차이로 최종 상태를 정한다. 우선순위: 얼굴 없음 > 고개 > 눈."""
    if dy is None or dp is None:
        return "no_face"
    return head_state(dy, dp, th) or eye_state(deh, dev, th) or "looking"


class LiveJudge:
    """실시간 판정기. 프레임마다 update()를 부르면
    처음 calib_sec초로 기준을 잡고, 이후 최근 몇 프레임의 중앙값으로 상태를 판정한다.
    (중앙값을 쓰는 이유: 눈 깜빡임 같은 순간적인 튐을 무시하려고)"""

    def __init__(self, calib_sec=3.0, th=None, recent_n=7):
        self.calib_sec = calib_sec
        self.th = dict(DEFAULT_TH, **(th or {}))
        self.rows = []  # (t, yaw, pitch, roll, eye_h, eye_v)
        self.base_head = None
        self.base_eye = None
        self.recent = deque(maxlen=recent_n)
        self.state = "calib"
        self.d = dict(d_yaw=None, d_pitch=None, d_eye_h=None, d_eye_v=None)
        self.judged = self.looking = self.head_ok = self.eye_ok = self.no_face = 0

    def _make_base(self):
        face = [r for r in self.rows if r[1] is not None]
        if not face:
            return
        self.base_head = (float(np.median([r[1] for r in face])),
                          float(np.median([r[2] for r in face])))
        eye = [r for r in face if r[4] is not None]
        if eye:
            self.base_eye = (float(np.median([r[4] for r in eye])),
                             float(np.median([r[5] for r in eye])))

    def update(self, t, feat):
        if feat is None:
            self.rows.append((t, None, None, None, None, None))
            self.recent.clear()
        else:
            self.rows.append((t, *feat))
            self.recent.append(feat)

        if self.base_head is None:
            if t >= self.calib_sec:
                self._make_base()
            if self.base_head is None:
                self.state = "calib"  # 얼굴이 잡힐 때까지 계속 기준 잡기
                return self.state

        self.d = dict(d_yaw=None, d_pitch=None, d_eye_h=None, d_eye_v=None)
        if self.recent:
            med = lambda i: float(np.median([f[i] for f in self.recent]))
            self.d["d_yaw"] = med(0) - self.base_head[0]
            self.d["d_pitch"] = med(1) - self.base_head[1]
            eyes = [f for f in self.recent if f[3] is not None]
            if eyes and self.base_eye is None:
                self.base_eye = (eyes[0][3], eyes[0][4])
            if eyes:
                self.d["d_eye_h"] = float(np.median([f[3] for f in eyes])) - self.base_eye[0]
                self.d["d_eye_v"] = float(np.median([f[4] for f in eyes])) - self.base_eye[1]

        d = self.d
        self.state = classify(d["d_yaw"], d["d_pitch"], d["d_eye_h"], d["d_eye_v"], self.th)
        self.judged += 1
        self.looking += self.state == "looking"
        if self.state == "no_face":
            self.no_face += 1
        else:
            self.head_ok += head_state(d["d_yaw"], d["d_pitch"], self.th) is None
            self.eye_ok += eye_state(d["d_eye_h"], d["d_eye_v"], self.th) is None
        return self.state

    @property
    def gaze_ratio(self):
        """고개+눈 모두 정면인 비율 (얼굴 없음 포함 전체 기준)"""
        return self.looking / self.judged * 100 if self.judged else None

    @property
    def head_ratio(self):
        """얼굴이 보인 시간 중 고개가 정면인 비율"""
        n = self.judged - self.no_face
        return self.head_ok / n * 100 if n > 0 else None

    @property
    def eye_ratio(self):
        """얼굴이 보인 시간 중 눈이 정면인 비율 (눈 점수가 없으면 None)"""
        n = self.judged - self.no_face
        return self.eye_ok / n * 100 if n > 0 and self.base_eye is not None else None


# ---------------------------------------------------------------------------
# 4. 영상 파일 → 프레임별 특징값
# ---------------------------------------------------------------------------
def extract_angles(video_path, step=2, preview=False, progress=None, max_width=960, track=None):
    """영상 파일을 처음부터 끝까지 읽으며 프레임별 특징값을 모은다.
    progress: 진행률(0~1)을 받는 함수 (웹 서버에서 진행 표시용, 없으면 생략)
    track   : 리스트를 넘기면 프레임별 박스 위치 (t, overlay_sample) 를 채운다 (결과 화면 박스용)
    영상을 열 수 없으면 ValueError."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"영상을 열 수 없습니다: {Path(video_path).name} (mp4 형식인지 확인하세요)")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0 or fps > 240:
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    print(f"[영상] {Path(video_path).name}  {fps:.1f}fps  {total}프레임  약 {total / fps:.1f}초")

    try:
        mp, landmarker = create_landmarker()
    except BaseException:
        cap.release()
        raise
    rows = []  # (t, yaw, pitch, roll, eye_h, eye_v)  얼굴이 없으면 None
    preview_judge = LiveJudge() if preview else None  # 미리보기 박스 색깔용 (최종 결과는 analyze)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step:
            idx += 1
            continue
        if max_width and frame.shape[1] > max_width:  # 큰 영상(1080p, 4K)은 줄여서 빠르게
            s = max_width / frame.shape[1]
            frame = cv2.resize(frame, None, fx=s, fy=s)
        t = idx / fps
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        feat, geo = extract_features(landmarker.detect_for_video(img, int(t * 1000)),
                                     frame.shape[1], frame.shape[0])
        rows.append((t, *feat) if feat else (t, None, None, None, None, None))
        if track is not None:
            track.append((t, overlay_sample(geo) if feat else None))

        if preview:
            draw_eye_debug(frame, geo)
            draw_boxes(frame, geo, preview_judge.update(t, feat))
            if feat is None:
                msg = "NO FACE"
            else:
                msg = f"yaw {feat[0]:+.1f} pitch {feat[1]:+.1f}"
                if feat[3] is not None:
                    msg += f"  eye_h {feat[3]:+.2f} eye_v {feat[4]:+.2f}"
            cv2.putText(frame, f"{t:6.1f}s  {msg}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.imshow("gaze preview (q: quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        if total and idx % (step * 50) == 0:
            print(f"\r[분석] {idx / total * 100:5.1f}%", end="")
        if progress and total and idx % (step * 5) == 0:
            progress(min(1.0, idx / total))
        idx += 1

    print("\r[분석] 100.0%")
    if progress:
        progress(1.0)
    cap.release()
    landmarker.close()
    if preview:
        cv2.destroyAllWindows()
    return rows


# ---------------------------------------------------------------------------
# 5. 실시간 스트림(IP Webcam) → 프레임별 특징값
# ---------------------------------------------------------------------------
def is_stream(source):
    s = str(source)
    return s.startswith(("http://", "https://", "rtsp://")) or s.isdigit()


def normalize_stream_url(source):
    """IP Webcam 앱 화면의 'http://IP:8080' 만 입력해도 영상 주소(/video)로 바꿔준다."""
    s = str(source)
    if s.isdigit():
        return int(s)  # 0, 1 ... = PC에 연결된 웹캠
    if s.startswith("http") and s.rstrip("/").count("/") == 2:
        return s.rstrip("/") + "/video"
    return s


class LatestFrameReader:
    """네트워크 스트림은 버퍼가 쌓여 화면이 점점 늦어지므로,
    별도 스레드에서 계속 읽고 '가장 최근 프레임'만 넘겨준다."""

    def __init__(self, source):
        self.cap = cv2.VideoCapture(source)
        self.frame = None
        self.ok = self.cap.isOpened()
        self.lock = threading.Lock()
        self.running = self.ok
        self.thread = None
        if self.ok:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def _loop(self):
        fails = 0
        while self.running:
            ok, f = self.cap.read()
            if not ok:
                fails += 1
                if fails > 50:
                    self.running = False
                time.sleep(0.02)
                continue
            fails = 0
            with self.lock:
                self.frame = f
        self.cap.release()  # 읽던 스레드가 직접 닫아야 충돌(segfault)이 안 난다

    def read(self):
        with self.lock:
            f = self.frame
            self.frame = None
        return f

    def release(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        else:
            self.cap.release()


# 화면 표시 색(BGR)과 문구 (OpenCV putText는 한글이 깨져서 영어로 표시)
STATE_COLOR = {"looking": (60, 200, 60), "head_updown": (40, 80, 230), "head_side": (20, 160, 240),
               "eye_updown": (200, 80, 160), "eye_side": (200, 140, 40), "no_face": (150, 150, 150),
               "calib": (0, 200, 230), "idle": (200, 200, 200)}
STATE_TEXT = {"looking": "LOOKING AT CAMERA", "head_updown": "HEAD UP/DOWN - face the camera",
              "head_side": "HEAD TURNED - face the camera", "eye_updown": "EYES UP/DOWN - look at camera",
              "eye_side": "EYES SIDE - look at camera", "no_face": "NO FACE",
              "calib": "CALIBRATING - look at camera", "idle": "READY - press START"}


def draw_overlay(frame, state, t=None, calib_left=None, ratio=None, d=None, footer=None):
    """판정 상태를 영상 위에 그린다. (CLI 실시간 창·웹 서버 공용)"""
    h, w = frame.shape[:2]
    c = STATE_COLOR[state]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), c, 8)
    cv2.rectangle(frame, (0, 0), (w, 64), (0, 0, 0), -1)
    title = STATE_TEXT[state] + (f" ({calib_left:.1f}s)" if state == "calib" and calib_left is not None else "")
    cv2.putText(frame, title, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, c, 2)
    info = f"{t:6.1f}s" if t is not None else ""
    if ratio is not None:
        info += f"  gaze {ratio:3.0f}%"
    if d and d.get("d_yaw") is not None:
        info += f"  head y{d['d_yaw']:+.0f} p{d['d_pitch']:+.0f}"
    if d and d.get("d_eye_h") is not None:
        info += f"  eye h{d['d_eye_h']:+.2f} v{d['d_eye_v']:+.2f}"
    cv2.putText(frame, info, (16, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    if footer:
        cv2.putText(frame, footer, (w - 170, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)


def draw_eye_debug(frame, geo):
    """눈 부분을 확대해서 왼쪽 아래에 붙이고, 눈꼬리·가운데·눈동자 위치를 표시한다.
    초록 점(눈동자)이 실제 눈동자를 따라 움직이는지 눈으로 확인하는 용도.
    draw_overlay() 보다 먼저 호출해야 확대 화면에 다른 표시가 섞이지 않는다."""
    if not geo or not geo.get("eyes"):
        return
    fh, fw = frame.shape[:2]
    eyes = geo["eyes"]
    pts = np.array([p for e in eyes for p in (e["A"], e["B"])])
    pad = max(e["width"] for e in eyes) * 0.6
    x1, y1 = np.maximum((pts.min(0) - pad).astype(int), 0)
    x2, y2 = (pts.max(0) + pad).astype(int)
    x2, y2 = min(fw, x2), min(fh, y2)

    if x2 - x1 >= 10 and y2 - y1 >= 4:
        zw = int(fw * 0.32)
        s = zw / (x2 - x1)
        zh = int((y2 - y1) * s)
        if zh > fh * 0.28:  # 너무 크면 줄이기
            s *= fh * 0.28 / zh
            zw, zh = int((x2 - x1) * s), int((y2 - y1) * s)
        x0, y0 = 10, fh - zh - 30
        if y0 > 70 and zw > 20:
            zoom = cv2.resize(frame[y1:y2, x1:x2], (zw, zh), interpolation=cv2.INTER_LINEAR)
            to_z = lambda p: (int((p[0] - x1) * s), int((p[1] - y1) * s))
            for e in eyes:
                cv2.line(zoom, to_z(e["A"]), to_z(e["B"]), (200, 200, 200), 1)
                for p in (e["A"], e["B"]):
                    cv2.circle(zoom, to_z(p), 3, (255, 255, 255), -1)
                cv2.drawMarker(zoom, to_z(e["mid"]), (255, 255, 255), cv2.MARKER_CROSS, 10, 1)
                r = max(4, int(e["width"] * s * 0.18))
                cv2.circle(zoom, to_z(e["iris"]), r, (0, 255, 0), 2)
                cv2.circle(zoom, to_z(e["iris"]), 2, (0, 255, 0), -1)
            frame[y0:y0 + zh, x0:x0 + zw] = zoom
            cv2.rectangle(frame, (x0 - 1, y0 - 1), (x0 + zw, y0 + zh), (255, 255, 255), 1)
            closed = geo["open"] < EYE_CLOSED_RATIO
            label = "EYE ZOOM  green=pupil  +=eye center" + ("  (eyes closed)" if closed else "")
            cv2.putText(frame, label, (x0, y0 + zh + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (255, 255, 255), 1)


BOX_LABEL = {"looking": "LOOKING", "head_updown": "HEAD UP/DOWN", "head_side": "HEAD SIDE",
             "eye_updown": "EYES UP/DOWN", "eye_side": "EYES SIDE", "calib": "CALIBRATING", "idle": ""}


def draw_boxes(frame, geo, state):
    """얼굴 박스(판정 상태 색 + 이름), 눈 박스 2개, 눈동자 점을 그린다. (실시간 화면용)
    눈만 벗어난 상태면 눈 박스도 같은 색으로 강조한다."""
    if not geo or "face_box" not in geo:
        return
    c = STATE_COLOR.get(state, STATE_COLOR["idle"])
    x1, y1, x2, y2 = (int(v) for v in geo["face_box"])
    pad = int((x2 - x1) * 0.06)
    x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
    cv2.rectangle(frame, (x1, y1), (x2, y2), c, 3)
    label = BOX_LABEL.get(state, "")
    if label:
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ly = max(y1, 64 + th + 10)  # 위쪽 정보 막대(64px)에 가려지지 않게
        cv2.rectangle(frame, (x1, ly - th - 10), (x1 + tw + 10, ly), c, -1)
        cv2.putText(frame, label, (x1 + 5, ly - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    eye_c = c if state in ("eye_updown", "eye_side") else (255, 255, 255)
    for e in geo.get("eyes", []):
        bx1, by1, bx2, by2 = e["box"]
        m = (bx2 - bx1) * 0.15
        cv2.rectangle(frame, (int(bx1 - m), int(by1 - m)), (int(bx2 + m), int(by2 + m)), eye_c, 2)
        r = max(2, int(e["width"] * 0.12))
        cv2.circle(frame, (int(e["iris"][0]), int(e["iris"][1])), r, (0, 255, 0), -1)


# 영상 파일 결과 화면용: 프레임별 박스 위치(0~1 비율)를 저장해 두고 브라우저에서 영상 위에 그린다
STATE_CODES = ["looking", "head_updown", "head_side", "eye_updown", "eye_side", "no_face"]


def overlay_sample(geo):
    """geo → [얼굴 x1,y1,x2,y2, (눈 x1,y1,x2,y2, 눈동자 x,y) x 2] (0~1 비율, 소수 4자리)."""
    if not geo or "face_box" not in geo:
        return None
    w, h = geo["size"]
    nx = lambda x: round(float(x) / w, 4)
    ny = lambda y: round(float(y) / h, 4)
    fb = geo["face_box"]
    out = [nx(fb[0]), ny(fb[1]), nx(fb[2]), ny(fb[3])]
    for e in geo.get("eyes", []):
        b = e["box"]
        out += [nx(b[0]), ny(b[1]), nx(b[2]), ny(b[3]), nx(e["iris"][0]), ny(e["iris"][1])]
    return out


def build_overlay(frames, track):
    """analyze() 의 최종 판정(frames) + 프레임별 박스 위치(track) → 브라우저용 데이터.
    각 항목: [시간, 상태 번호, ...overlay_sample 값]"""
    data = []
    for f, (t, sample) in zip(frames, track):
        code = STATE_CODES.index(f["state"]) if f["state"] in STATE_CODES else 5
        data.append([round(t, 3), code] + (sample or []))
    return {"states": STATE_CODES, "data": data}


def extract_angles_live(source, calib_sec=3.0, th=None, window=True, max_sec=0, max_width=960):
    src = normalize_stream_url(source)
    print(f"[실시간] 연결 중: {src}")
    reader = LatestFrameReader(src)
    if not reader.ok:
        print("[오류] 스트림에 연결할 수 없습니다.")
        print(" - 폰과 PC가 같은 와이파이인지, IP Webcam에서 '서버 시작'을 눌렀는지 확인하세요.")
        print(" - 브라우저에서 같은 주소가 열리는지 먼저 확인해 보세요.")
        sys.exit(1)

    mp, landmarker = create_landmarker()
    judge = LiveJudge(calib_sec, th)
    t0 = time.monotonic()
    last_ts = -1
    print(f"[실시간] 시작! 처음 {calib_sec:.0f}초는 카메라를 정면으로 봐 주세요. "
          f"{'창에서 q' if window else 'Ctrl+C'} 를 누르면 종료·저장합니다.")
    try:
        while True:
            frame = reader.read()
            if frame is None:
                if not reader.running:
                    print("\n[실시간] 스트림이 끊겼습니다. 지금까지 결과를 저장합니다.")
                    break
                time.sleep(0.005)
                continue
            if max_width and frame.shape[1] > max_width:
                s = max_width / frame.shape[1]
                frame = cv2.resize(frame, None, fx=s, fy=s)

            t = time.monotonic() - t0
            ts = max(int(t * 1000), last_ts + 1)  # 타임스탬프는 반드시 증가해야 함
            last_ts = ts
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            feat, geo = extract_features(landmarker.detect_for_video(img, ts), frame.shape[1], frame.shape[0])
            state = judge.update(t, feat)

            if window:
                draw_eye_debug(frame, geo)
                draw_boxes(frame, geo, state)
                draw_overlay(frame, state, t, max(0.0, calib_sec - t), judge.gaze_ratio, judge.d,
                             footer="q: quit & save")
                cv2.imshow("presentation coach - gaze (q: quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                r = judge.gaze_ratio
                print(f"\r[실시간] {t:6.1f}s  {state:<12}  응시율 {r if r is not None else 0:5.1f}%", end="")
            if max_sec and t >= max_sec:
                break
    except KeyboardInterrupt:
        print("\n[실시간] 종료합니다.")
    finally:
        reader.release()
        landmarker.close()
        if window:
            cv2.destroyAllWindows()
    print(f"\n[실시간] {len(judge.rows)}프레임 분석 완료")
    return judge.rows


# ---------------------------------------------------------------------------
# 6. 최종 분석: 캘리브레이션 + 판정 + 구간 찾기 (얼굴 모델 없이도 테스트 가능한 순수 로직)
# ---------------------------------------------------------------------------
def smooth(values, win):
    """None은 그대로 두고, 이동 중앙값으로 떨림·깜빡임을 줄인다."""
    out = []
    half = max(1, win // 2)
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        neigh = [x for x in values[max(0, i - half): i + half + 1] if x is not None]
        out.append(float(np.median(neigh)))
    return out


def fill_blinks(times, values, face, max_gap=0.6):
    """얼굴은 보이는데 눈값이 없는 짧은 구간(눈 깜빡임)은 직전 눈값으로 채운다.
    채우지 않으면 깜빡임이 '카메라 봄'으로 계산되어, 눈 이탈 구간이 둘로 쪼개진다."""
    out = list(values)
    i, n = 0, len(out)
    while i < n:
        if out[i] is None and face[i]:
            j = i
            while j < n and out[j] is None and face[j]:
                j += 1
            prev = out[i - 1] if i > 0 else None
            end_t = times[j] if j < n else times[-1]
            if prev is not None and end_t - times[i - 1] <= max_gap:
                for k in range(i, j):
                    out[k] = prev
            i = j
        else:
            i += 1
    return out


def _median_base(pairs):
    return (float(np.median([p[0] for p in pairs])), float(np.median([p[1] for p in pairs])))


def analyze(rows, calib_sec=3.0, th=None, min_dur=1.0, gap_tol=0.5):
    th = dict(DEFAULT_TH, **(th or {}))
    # 예전 형식 (t, yaw, pitch, roll) 도 받기
    rows = [tuple(r) + (None,) * (6 - len(r)) for r in rows]
    times = [r[0] for r in rows]
    if not times:
        raise ValueError("분석된 프레임이 없습니다.")
    # 실시간은 프레임 간격이 일정하지 않아서 중앙값 간격을 사용
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 1 / 15
    win = max(1, int(round(0.5 / dt)))  # 약 0.5초 창
    yaws, pitches = smooth([r[1] for r in rows], win), smooth([r[2] for r in rows], win)
    face = [r[1] is not None for r in rows]
    eye_hs = smooth(fill_blinks(times, [r[4] for r in rows], face), win)
    eye_vs = smooth(fill_blinks(times, [r[5] for r in rows], face), win)

    # 캘리브레이션: 처음 calib_sec초 동안의 중앙값 = "정면" 기준
    head_cal = [(y, p) for t, y, p in zip(times, yaws, pitches) if t <= calib_sec and y is not None]
    if len(head_cal) < 3:
        head_cal = [(y, p) for y, p in zip(yaws, pitches) if y is not None][: max(3, int(calib_sec / dt))]
    if not head_cal:
        raise ValueError("영상 전체에서 얼굴을 찾지 못했습니다.")
    base_head = _median_base(head_cal)

    eye_cal = [(h, v) for t, h, v in zip(times, eye_hs, eye_vs) if t <= calib_sec and h is not None]
    if len(eye_cal) < 3:
        eye_cal = [(h, v) for h, v in zip(eye_hs, eye_vs) if h is not None][: max(3, int(calib_sec / dt))]
    base_eye = _median_base(eye_cal) if eye_cal else None

    frames = []
    for t, y, p, eh, ev in zip(times, yaws, pitches, eye_hs, eye_vs):
        if y is None:
            frames.append(dict(t=t, d_yaw=None, d_pitch=None, d_eye_h=None, d_eye_v=None,
                               state="no_face", head_ok=None, eye_ok=None))
            continue
        dy, dp = y - base_head[0], p - base_head[1]
        deh = dev = None
        if eh is not None and base_eye is not None:
            deh, dev = eh - base_eye[0], ev - base_eye[1]
        frames.append(dict(t=t, d_yaw=dy, d_pitch=dp, d_eye_h=deh, d_eye_v=dev,
                           state=classify(dy, dp, deh, dev, th),
                           head_ok=head_state(dy, dp, th) is None,
                           eye_ok=eye_state(deh, dev, th) is None))

    # 연속된 이탈 프레임을 구간으로 묶기 (짧은 끊김은 이어 붙임)
    segments, cur = [], None
    for f in frames:
        if f["state"] == "looking":
            continue
        if cur and f["t"] - cur["end"] <= gap_tol:
            cur["end"] = f["t"]
            cur["states"].append(f["state"])
        else:
            if cur:
                segments.append(cur)
            cur = dict(start=f["t"], end=f["t"], states=[f["state"]])
    if cur:
        segments.append(cur)

    result_segs = []
    for s in segments:
        dur = s["end"] - s["start"] + dt
        if dur < min_dur:
            continue
        main = max(set(s["states"]), key=s["states"].count)
        result_segs.append(dict(start=s["start"], end=s["end"] + dt, dur=dur,
                                kind=main, label=LABELS[main]))

    n = len(frames)
    face = [f for f in frames if f["state"] != "no_face"]
    stats = dict(
        total_sec=times[-1] + dt,
        gaze_ratio=sum(f["state"] == "looking" for f in frames) / n * 100,
        head_ratio=sum(f["head_ok"] for f in face) / len(face) * 100 if face else 0,
        eye_ratio=sum(f["eye_ok"] for f in face) / len(face) * 100 if face else 0,
        no_face_ratio=(n - len(face)) / n * 100,
        eye_available=base_eye is not None,
        base_yaw=base_head[0], base_pitch=base_head[1],
        base_eye_h=base_eye[0] if base_eye else None, base_eye_v=base_eye[1] if base_eye else None,
        calib_sec=calib_sec, **th,
    )
    return frames, result_segs, stats


# ---------------------------------------------------------------------------
# 7. 저장 (CSV / 그래프 / 요약)
# ---------------------------------------------------------------------------
def mmss(sec):
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _fmt(v, nd):
    return "" if v is None else f"{v:.{nd}f}"


SEG_COLORS = {"head_updown": "#e4572e", "head_side": "#f3a712", "eye_updown": "#a23b72",
              "eye_side": "#2e86ab", "no_face": "#888888"}


def save_outputs(frames, segs, stats, out_dir, name):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"{name}_gaze_frames.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["time_sec", "d_yaw_deg", "d_pitch_deg", "d_eye_h", "d_eye_v", "state"])
        for r in frames:
            w.writerow([f"{r['t']:.3f}", _fmt(r["d_yaw"], 2), _fmt(r["d_pitch"], 2),
                        _fmt(r["d_eye_h"], 3), _fmt(r["d_eye_v"], 3), r["state"]])

    lines = [
        f"[시선 분석 요약] {name}",
        f"- 영상 길이: {mmss(stats['total_sec'])} ({stats['total_sec']:.1f}초)",
        f"- 카메라 응시율(고개+눈 모두 정면): {stats['gaze_ratio']:.1f}%",
        f"- 고개 정면 비율: {stats['head_ratio']:.1f}%",
        (f"- 눈 맞춤 비율: {stats['eye_ratio']:.1f}%" if stats["eye_available"]
         else "- 눈 맞춤 비율: (눈 점수를 받지 못해 고개만 판정)"),
        f"- 얼굴 미검출 비율: {stats['no_face_ratio']:.1f}%",
        f"- 이탈 기준: 고개 좌우 ±{stats['yaw_th']:.0f}° / 상하 ±{stats['pitch_th']:.0f}°, "
        f"눈 좌우 ±{stats['eye_h_th']:.2f} / 상하 ±{stats['eye_v_th']:.2f} (처음 {stats['calib_sec']:.0f}초 기준)",
        "",
        f"[시선 이탈 구간] {len(segs)}개",
    ]
    for s in segs:
        lines.append(f"  {mmss(s['start'])} ~ {mmss(s['end'])} ({s['dur']:.1f}초)  {s['label']}")
    if not segs:
        lines.append("  없음 - 계속 카메라를 잘 보고 있어요.")
    summary = "\n".join(lines)
    (out_dir / f"{name}_gaze_summary.txt").write_text(summary, encoding="utf-8-sig")

    png_path = out_dir / f"{name}_gaze_plot.png"
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        t = [r["t"] for r in frames]
        col = lambda k: [np.nan if r[k] is None else r[k] for r in frames]
        panels = [("d_pitch", stats["pitch_th"], "Head up/down (pitch, deg)"),
                  ("d_yaw", stats["yaw_th"], "Head left/right (yaw, deg)"),
                  ("d_eye_v", stats["eye_v_th"], "Pupil up/down (ratio of eye width)"),
                  ("d_eye_h", stats["eye_h_th"], "Pupil left/right (ratio of eye width)")]
        fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True)
        for ax, (key, th, title) in zip(axes, panels):
            ax.plot(t, col(key), lw=1.2, color="#2b6cb0")
            ax.axhline(th, ls="--", color="gray", lw=0.8)
            ax.axhline(-th, ls="--", color="gray", lw=0.8)
            ax.axhline(0, color="black", lw=0.5)
            for s in segs:
                ax.axvspan(s["start"], s["end"], color=SEG_COLORS[s["kind"]], alpha=0.2)
            ax.set_title(title, fontsize=10, loc="left")
        axes[-1].set_xlabel("time (sec)")
        fig.suptitle(f"Gaze analysis - camera gaze {stats['gaze_ratio']:.0f}% "
                     f"(head {stats['head_ratio']:.0f}%, eyes {stats['eye_ratio']:.0f}%)\n"
                     "red: head up/down, orange: head side, purple: eyes up/down, "
                     "blue: eyes side, gray: no face", fontsize=10)
        fig.tight_layout()
        fig.savefig(png_path, dpi=110)
        plt.close(fig)
    except ImportError:
        png_path = None
        print("[안내] matplotlib이 없어 그래프는 건너뜁니다. (pip install matplotlib)")

    return summary, csv_path, png_path


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="발표 영상 시선 분석 (고개 방향 + 눈 시선)")
    ap.add_argument("video", help="영상 파일, 또는 IP Webcam 주소(http://IP:8080), 또는 PC 웹캠 번호(0)")
    ap.add_argument("--calib-sec", type=float, default=3.0, help="처음 몇 초를 정면 기준으로 쓸지 (기본 3)")
    ap.add_argument("--yaw-th", type=float, default=DEFAULT_TH["yaw_th"], help="고개 좌우 이탈 기준 각도 (기본 20)")
    ap.add_argument("--pitch-th", type=float, default=DEFAULT_TH["pitch_th"], help="고개 상하 이탈 기준 각도 (기본 15)")
    ap.add_argument("--eye-h-th", type=float, default=DEFAULT_TH["eye_h_th"], help="눈동자 좌우 이동 기준, 눈 폭 대비 (기본 0.10)")
    ap.add_argument("--eye-v-th", type=float, default=DEFAULT_TH["eye_v_th"], help="눈동자 상하 이동 기준, 눈 폭 대비 (기본 0.08)")
    ap.add_argument("--min-dur", type=float, default=1.0, help="이 시간(초)보다 짧은 이탈은 무시 (기본 1)")
    ap.add_argument("--step", type=int, default=2, help="(영상 파일) N프레임마다 1번 분석 (기본 2)")
    ap.add_argument("--out", default="results", help="결과 저장 폴더 (기본 results)")
    ap.add_argument("--preview", action="store_true", help="(영상 파일) 분석 중 화면 표시")
    ap.add_argument("--no-window", action="store_true", help="(실시간) 창 없이 터미널에만 표시")
    ap.add_argument("--max-sec", type=float, default=0, help="(실시간) 이 시간(초)이 지나면 자동 종료")
    a = ap.parse_args()
    th = dict(yaw_th=a.yaw_th, pitch_th=a.pitch_th, eye_h_th=a.eye_h_th, eye_v_th=a.eye_v_th)

    if is_stream(a.video):
        rows = extract_angles_live(a.video, a.calib_sec, th, window=not a.no_window, max_sec=a.max_sec)
        name = time.strftime("live_%Y%m%d_%H%M%S")
    else:
        try:
            rows = extract_angles(a.video, step=a.step, preview=a.preview)
        except ValueError as e:
            print("[오류]", e)
            sys.exit(1)
        name = Path(a.video).stem
    try:
        frames, segs, stats = analyze(rows, a.calib_sec, th, a.min_dur)
    except ValueError as e:
        print("[오류]", e, "얼굴이 잘 보이게 다시 찍어주세요.")
        sys.exit(1)

    summary, csv_path, png_path = save_outputs(frames, segs, stats, Path(a.out), name)
    print()
    print(summary)
    print()
    print(f"[저장] {csv_path}")
    if png_path:
        print(f"[저장] {png_path}")


if __name__ == "__main__":
    main()