"""발표 연습 코치 - 스마트폰 IP Webcam → MediaPipe 시선 분석(고개 + 눈) → FastAPI 실시간 웹 화면.

실행:  uvicorn app:app --host 0.0.0.0 --port 8000
접속:  브라우저에서 http://localhost:8000
"""

# ① 라이브러리 가져오기 · 프로젝트 경로 지정
# FastAPI, OpenCV, Pydantic 및 시선 분석 모듈(gaze_analysis.py)을 불러옵니다.
from contextlib import asynccontextmanager
import ipaddress
import json
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlsplit

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

import gaze_analysis as ga

ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "web" / "index.html"
RESULT_DIR = ROOT / "results"
UPLOAD_DIR = ROOT / "uploads"  # 웹에서 올린 영상이 저장되는 폴더 (필요 없으면 지워도 됨)
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_UPLOAD_MB = 1024
MAX_WIDTH = 960  # 분석·전송 속도를 위해 큰 영상은 줄여서 처리


# ② 대기 화면 이미지 준비
# 스마트폰 영상이 연결되기 전 보여 줄 JPEG를 만듭니다.
def placeholder_jpg() -> bytes:
    frame = np.full((480, 640, 3), (38, 43, 50), dtype=np.uint8)
    cv2.putText(frame, "IP WEBCAM - WAITING", (150, 227),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (230, 230, 230), 2)
    cv2.putText(frame, "enter the phone address above", (155, 265),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (170, 170, 170), 1)
    return cv2.imencode(".jpg", frame)[1].tobytes()


WAITING_JPG = placeholder_jpg()


# ③ 실시간 분석 상태를 관리하는 클래스
# 카메라 주소, 연습 세션(판정기), 최신 프레임 등을 보관합니다.
class LiveState:
    """한 대의 카메라를 단일 스레드에서 읽고 여러 브라우저에 공유합니다."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.url: str | None = None
        self.camera_status = "NOT_CONNECTED"
        self.message = "스마트폰 IP Webcam 주소를 연결하세요."
        self.model_error = ""
        self.latest_jpg: bytes = WAITING_JPG
        self.last_frame_time = 0.0
        self.frame_size: list[int] | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        # 연습 1회(세션) 관련: 판정은 gaze_analysis.LiveJudge 가 담당
        self.judge: ga.LiveJudge | None = None
        self.session_t0 = 0.0
        self.settings = dict(calib_sec=3.0, **ga.DEFAULT_TH)
        self.last_face = False
        self.last_result: dict | None = None

    # ④ 카메라 작업 시작·종료
    # 별도의 스레드를 시작하고 서버 종료 시 정리합니다.
    def start(self) -> None:
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.camera_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    # ⑤ 카메라 주소 저장
    # 카메라를 바꾸면 진행 중인 세션은 취소합니다.
    def set_camera(self, url: str) -> None:
        with self.lock:
            self.url = url
            self.camera_status = "CONNECTING"
            self.message = "카메라 연결 중입니다."
            self.latest_jpg = WAITING_JPG
            self.last_frame_time = 0.0
            self.judge = None

    # ⑥ 연습 세션 시작·종료
    # 시작: 새 판정기를 만들고 처음 calib_sec초를 '정면 기준'으로 잡습니다.
    # 종료: 기록한 값으로 gaze_analysis.analyze()를 실행해 결과를 저장합니다.
    def start_session(self, settings: dict) -> None:
        with self.lock:
            self.settings = settings
            th = {k: v for k, v in settings.items() if k in ga.DEFAULT_TH}
            self.judge = ga.LiveJudge(settings["calib_sec"], th)
            self.session_t0 = time.monotonic()

    def stop_session(self) -> dict:
        with self.lock:
            if self.judge is None:
                raise ValueError("진행 중인 연습이 없습니다.")
            judge, self.judge = self.judge, None
            rows, settings = list(judge.rows), dict(self.settings)

        name = time.strftime("session_%Y%m%d_%H%M%S")
        result = analyze_and_save(rows, settings, name, source="실시간 연습")  # 분석은 lock 밖에서
        with self.lock:
            self.last_result = result
        return result

    # ⑦ 현재 상태 조회
    # 카메라 연결, 세션 단계, 실시간 판정, 응시율 등을 반환합니다.
    def get_status(self) -> dict:
        with self.lock:
            elapsed = (time.monotonic() - self.last_frame_time
                       if self.last_frame_time else None)
            camera_status = self.camera_status
            if camera_status == "ONLINE" and elapsed is not None and elapsed > 5:
                camera_status = "DISCONNECTED"
            j = self.judge
            t = time.monotonic() - self.session_t0 if j else 0.0
            live_state = j.state if j else "idle"
            r = lambda v, n: None if v is None else round(v, n)
            return {
                "camera_status": camera_status,
                "message": self.model_error or self.message,
                "session_active": j is not None,
                "phase": "idle" if not j else "calib" if live_state == "calib" else "live",
                "calib_left": round(max(0.0, j.calib_sec - t), 1) if j else None,
                "elapsed": round(t, 1),
                "live_state": live_state if camera_status == "ONLINE" else "idle",
                "face_detected": self.last_face,
                # 실시간 비율은 연습 중에만 (종료 후에는 결과 리포트의 값이 정답)
                "gaze_ratio": r(j.gaze_ratio, 1) if j else None,
                "head_ratio": r(j.head_ratio, 1) if j else None,
                "eye_ratio": r(j.eye_ratio, 1) if j else None,
                "d": {k: r(v, 2) for k, v in j.d.items()} if j else None,
                "settings": self.settings,
                "has_result": self.last_result is not None,
            }

    # ⑧ IP Webcam 연결 및 연결 실패 처리
    # OpenCV로 스마트폰 영상 URL에 접속하고 재연결을 시도합니다.
    def camera_loop(self) -> None:
        try:
            mp, landmarker = ga.create_landmarker()  # 얼굴 모델은 이 스레드에서 한 번만 생성
        except SystemExit:
            with self.lock:
                self.model_error = ("얼굴 모델(face_landmarker.task)을 받지 못했습니다. "
                                    "터미널 안내에 따라 파일을 app.py와 같은 폴더에 두고 서버를 다시 켜세요.")
            return
        except Exception as exc:  # 예: 리눅스에 libGLESv2 등 라이브러리가 없을 때
            print("[오류] 얼굴 분석기를 시작하지 못했습니다:", exc)
            with self.lock:
                self.model_error = f"얼굴 분석기 시작 실패: {exc} (터미널 메시지를 확인하세요)"
            return
        last_ts = -1

        while not self.stop_event.is_set():
            with self.lock:
                url = self.url
            if not url:
                self.stop_event.wait(0.1)
                continue

            # 한 스레드에서만 VideoCapture를 엽니다.
            cap = cv2.VideoCapture(url)
            if not cap.isOpened():
                cap.release()
                with self.lock:
                    if self.url == url:
                        self.camera_status = "DISCONNECTED"
                        self.message = "IP Webcam 연결 실패: 주소와 Wi-Fi를 확인하세요."
                self.stop_event.wait(1.0)
                continue

            try:
                while not self.stop_event.is_set():
                    with self.lock:
                        if self.url != url:
                            break

                    # ⑨ 실시간 Frame 읽기
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        with self.lock:
                            if self.url == url:
                                self.camera_status = "DISCONNECTED"
                                self.message = "프레임 수신 중단: 재연결을 시도합니다."
                                self.latest_jpg = WAITING_JPG
                        break
                    if frame.shape[1] > MAX_WIDTH:
                        s = MAX_WIDTH / frame.shape[1]
                        frame = cv2.resize(frame, None, fx=s, fy=s)
                    h, w = frame.shape[:2]

                    # ⑩ 고개 각도 + 눈 시선 계산 (MediaPipe Face Landmarker)
                    ts = max(int(time.monotonic() * 1000), last_ts + 1)  # 반드시 증가
                    last_ts = ts
                    img = mp.Image(image_format=mp.ImageFormat.SRGB,
                                   data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    feat, geo = ga.extract_features(landmarker.detect_for_video(img, ts), w, h)

                    # ⑪ 판정 (세션 중일 때만 LiveJudge 에 기록)
                    with self.lock:
                        if self.url != url:
                            break
                        self.last_face = feat is not None
                        j = self.judge
                        if j is not None:
                            t = time.monotonic() - self.session_t0
                            state = j.update(t, feat)
                            calib_left, ratio, d = max(0.0, j.calib_sec - t), j.gaze_ratio, dict(j.d)
                        else:
                            state, t, calib_left, ratio, d = "idle", None, None, None, None

                    # ⑫ 화면 표시 · JPEG 변환 · 최신 결과 저장
                    image = frame.copy()
                    ga.draw_eye_debug(image, geo)  # 눈 확대 화면 (눈동자 추적 확인용)
                    ga.draw_boxes(image, geo, state)  # 얼굴·눈 박스 + 눈동자 점
                    ga.draw_overlay(image, state, t, calib_left, ratio, d)
                    if state == "idle":
                        msg = "face OK" if feat is not None else "no face detected"
                        cv2.putText(image, msg, (16, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                    (255, 255, 255), 1)
                    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 77])
                    if success:
                        with self.lock:
                            if self.url == url:
                                self.latest_jpg = encoded.tobytes()
                                self.camera_status = "ONLINE"
                                self.message = "영상 수신 중"
                                self.frame_size = [w, h]
                                self.last_frame_time = time.monotonic()
            finally:
                cap.release()
            self.stop_event.wait(0.1)
        landmarker.close()


# ⑬ 코칭 문장 (구간 종류별 / 전체 비율별)
# 기준 % 값은 임시값입니다. 테스트 영상으로 조정하세요.
COACHING = {
    "head_updown": "고개가 위나 아래로 향했어요. 메모나 화면을 보느라 고개를 숙였다면, 이 구간의 핵심 문장을 외워서 고개를 들고 다시 말해보세요.",
    "head_side": "고개가 옆으로 돌아갔어요. 얼굴과 몸을 카메라(청중) 쪽으로 향한 채 이 구간을 다시 말해보세요.",
    "eye_updown": "고개는 정면인데 눈만 위나 아래를 봤어요. 아래쪽 메모를 곁눈질하거나 생각할 때 눈을 굴리는 습관일 수 있어요. 이 구간은 카메라 렌즈를 보며 다시 말해보세요.",
    "eye_side": "고개는 정면인데 눈만 옆을 봤어요. 화면 옆의 자료를 보고 있었다면 위치를 카메라 가까이로 옮기고 다시 연습해보세요.",
    "no_face": "얼굴이 화면 밖으로 나갔어요. 카메라 화면 안에서 움직이도록 위치를 잡아보세요.",
}


def analyze_and_save(rows: list, settings: dict, name: str, source: str,
                     video_url: str | None = None, track: list | None = None) -> dict:
    """기록한 프레임별 값 → 최종 분석 → 파일 저장 → 웹에 보낼 결과(dict).
    실시간 연습과 영상 파일 분석이 같은 함수를 씁니다.
    track(프레임별 박스 위치)이 있으면 결과 영상 위에 박스를 그릴 데이터도 저장합니다."""
    th = {k: v for k, v in settings.items() if k in ga.DEFAULT_TH}
    frames, segs, stats = ga.analyze(rows, settings["calib_sec"], th)
    summary, _, png_path = ga.save_outputs(frames, segs, stats, RESULT_DIR, name)
    overlay_url = None
    if track:
        overlay_path = RESULT_DIR / f"{name}_overlay.json"
        overlay_path.write_text(json.dumps(ga.build_overlay(frames, track), separators=(",", ":")),
                                encoding="utf-8")
        overlay_url = f"/results/{overlay_path.name}"
    return {
        "overlay_url": overlay_url,
        "name": name,
        "source": source,
        "summary": summary,
        "gaze_ratio": round(stats["gaze_ratio"], 1),
        "head_ratio": round(stats["head_ratio"], 1),
        "eye_ratio": round(stats["eye_ratio"], 1) if stats["eye_available"] else None,
        "no_face_ratio": round(stats["no_face_ratio"], 1),
        "total_sec": round(stats["total_sec"], 1),
        "overall": overall_comment(stats),
        "segments": [dict(start=ga.mmss(s["start"]), end=ga.mmss(s["end"]),
                          start_sec=round(s["start"], 2),
                          dur=round(s["dur"], 1), kind=s["kind"], label=s["label"],
                          coaching=COACHING[s["kind"]]) for s in segs],
        "plot_url": f"/results/{png_path.name}" if png_path else None,
        "video_url": video_url,
    }


# ⑬-2 영상 파일 분석 작업 (업로드한 영상을 백그라운드 스레드에서 분석)
# 한 번에 하나만 분석하고, 진행률·결과를 /api/upload/status 로 알려줍니다.
class FileJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"  # idle / running / done / error
        self.progress = 0.0
        self.message = ""
        self.filename = ""
        self.result: dict | None = None

    def is_running(self) -> bool:
        with self.lock:
            return self.status == "running"

    def start(self, path: Path, original_name: str, settings: dict) -> None:
        with self.lock:
            self.status, self.progress, self.result = "running", 0.0, None
            self.filename = original_name
            self.message = "분석 준비 중"
        threading.Thread(target=self._run, args=(path, original_name, settings), daemon=True).start()

    def _set_progress(self, frac: float) -> None:
        with self.lock:
            self.progress = frac
            self.message = "얼굴·눈 분석 중"

    def _run(self, path: Path, original_name: str, settings: dict) -> None:
        try:
            track: list = []
            rows = ga.extract_angles(path, step=2, progress=self._set_progress, track=track)
            with self.lock:
                self.message = "결과 정리 중"
            result = analyze_and_save(rows, settings, f"video_{path.stem}", source=original_name,
                                      video_url=f"/uploads/{path.name}", track=track)
        except SystemExit:
            self._fail("얼굴 모델(face_landmarker.task)을 받지 못했습니다. 터미널 메시지를 확인하세요.")
            return
        except ValueError as exc:  # 영상을 못 열었거나 얼굴을 못 찾음
            self._fail(f"{exc} 얼굴이 잘 보이는 영상인지, 처음 몇 초에 정면을 보는지 확인하세요.")
            return
        except Exception as exc:
            print("[오류] 영상 분석 실패:", repr(exc))
            self._fail(f"영상 분석 중 오류가 났습니다: {exc}")
            return
        with self.lock:
            self.status, self.progress, self.result = "done", 1.0, result
            self.message = "분석 완료"
        with state.lock:
            state.last_result = result

    def _fail(self, msg: str) -> None:
        with self.lock:
            self.status, self.message = "error", msg

    def get(self) -> dict:
        with self.lock:
            return {"status": self.status, "progress": round(self.progress * 100, 1),
                    "message": self.message, "filename": self.filename, "result": self.result}


def overall_comment(stats: dict) -> str:
    g, h, e = stats["gaze_ratio"], stats["head_ratio"], stats["eye_ratio"]
    if g >= 80:
        return "좋아요! 대부분의 시간 동안 고개와 눈 모두 카메라를 향하고 있었어요."
    if stats["eye_available"] and h >= 80 and e < h - 10:
        return "고개 방향은 좋은데 눈이 자주 다른 곳을 봐요. 눈 시선 구간부터 다시 연습해보세요."
    if g >= 60:
        return "괜찮아요. 아래 구간들만 고치면 훨씬 자연스러워져요."
    return "카메라를 보는 시간이 부족해요. 아래 구간부터 하나씩 다시 연습해보세요."


# ⑭ 서버 실행 준비 및 종료 처리(Lifespan)
# FastAPI가 시작되면 카메라 스레드를 시작하고, 종료 시 중지합니다.
state = LiveState()
file_job = FileJob()


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.start()
    yield
    state.stop()


app = FastAPI(title="발표 연습 코치 - 시선 분석", lifespan=lifespan)


# ⑮ HTTP 요청 Body 검증(Pydantic)
class CameraRequest(BaseModel):
    url: str


class SessionRequest(BaseModel):
    calib_sec: float = Field(default=3.0, ge=1.0, le=10.0)
    yaw_th: float = Field(default=ga.DEFAULT_TH["yaw_th"], ge=5.0, le=60.0)
    pitch_th: float = Field(default=ga.DEFAULT_TH["pitch_th"], ge=5.0, le=60.0)
    eye_h_th: float = Field(default=ga.DEFAULT_TH["eye_h_th"], ge=0.02, le=0.5)
    eye_v_th: float = Field(default=ga.DEFAULT_TH["eye_v_th"], ge=0.02, le=0.5)


# ⑯ GET / : 브라우저에 HTML 화면 전달
@app.get("/")
def index():
    return FileResponse(HTML_FILE, media_type="text/html")


# ⑰ POST /api/camera : 카메라 URL 확인 후 연결 시도
# HTTP 형식, 사설 IP 주소, 포트를 검사합니다. 주소만 넣으면 /video 를 붙여줍니다.
@app.post("/api/camera")
def connect_camera(body: CameraRequest):
    url = body.url.strip()
    if url and "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "http://IP:PORT 형식으로 입력하세요. (예: http://192.168.0.10:8080)")
    if parts.username or parts.password:
        raise HTTPException(400, "사용자 인증이 없는 IP Webcam 주소만 사용하세요.")
    try:
        addr = ipaddress.ip_address(parts.hostname)
        if not (addr.is_private or addr.is_loopback):
            raise ValueError("사설 IP 주소가 아닙니다")
        _ = parts.port  # 숫자가 아닌 포트는 거부
    except ValueError:
        raise HTTPException(400, "스마트폰의 사설 IP 주소(예: 192.168.x.x)를 사용하세요.")
    url = ga.normalize_stream_url(url)
    state.set_camera(url)
    return {"ok": True, "url": url, "message": "연결을 시도합니다."}


# ⑱ POST /api/session/start : 연습 시작 (처음 몇 초는 정면 기준 잡기)
@app.post("/api/session/start")
def start_session(body: SessionRequest):
    if state.get_status()["camera_status"] != "ONLINE":
        raise HTTPException(400, "카메라 영상이 들어온 뒤에 시작하세요.")
    state.start_session(body.model_dump())
    return {"ok": True}


# ⑲ POST /api/session/stop : 연습 종료 → 분석 결과 저장·반환
@app.post("/api/session/stop")
def stop_session():
    try:
        return state.stop_session()
    except ValueError as exc:
        raise HTTPException(400, f"{exc} 얼굴이 보이는 상태로 몇 초 이상 연습한 뒤 종료하세요.")


# ⑳ GET /api/status · /api/result : 최신 상태 / 마지막 결과
@app.get("/api/status")
def get_status():
    return state.get_status()


@app.get("/api/result")
def get_result():
    with state.lock:
        if state.last_result is None:
            raise HTTPException(404, "아직 결과가 없습니다.")
        return state.last_result


# ㉑ GET /results/{파일} · /uploads/{파일} : 그래프 이미지 / 올린 영상 전달 (해당 폴더 안 파일만)
def _safe_file(folder: Path, filename: str) -> Path:
    path = (folder / filename).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise HTTPException(404, "파일이 없습니다.")
    return path


@app.get("/results/{filename}")
def get_result_file(filename: str):
    return FileResponse(_safe_file(RESULT_DIR, filename))


@app.get("/uploads/{filename}")
def get_upload_file(filename: str):
    return FileResponse(_safe_file(UPLOAD_DIR, filename))  # 영상 구간 이동(Range 요청) 지원


# ㉒ POST /api/upload : 영상 파일 올리기 → 백그라운드 분석 시작
# 파일 내용을 그대로 받아(추가 설치 없이) uploads 폴더에 조금씩 저장합니다.
# 판정 기준은 주소 뒤 ?calib_sec=3&yaw_th=20... 로 받습니다.
@app.post("/api/upload")
async def upload_video(request: Request):
    if file_job.is_running():
        raise HTTPException(409, "이미 다른 영상을 분석 중입니다. 끝난 뒤 다시 올려주세요.")
    try:
        settings = SessionRequest.model_validate(dict(request.query_params)).model_dump()
    except ValidationError:
        raise HTTPException(400, "판정 기준 값이 올바르지 않습니다.")

    original = unquote(request.headers.get("x-filename", "video.mp4"))[:200]
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"영상 파일만 올릴 수 있습니다. ({', '.join(sorted(ALLOWED_EXT))})")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / (time.strftime("upload_%Y%m%d_%H%M%S") + ext)  # 한글·특수문자 없는 이름으로 저장
    size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"파일이 너무 큽니다. ({MAX_UPLOAD_MB}MB 이하)")
                f.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "빈 파일입니다.")

    file_job.start(path, original, settings)
    return {"ok": True, "saved_as": path.name, "size_mb": round(size / 1024 / 1024, 1)}


# ㉓ GET /api/upload/status : 영상 분석 진행률 / 결과
@app.get("/api/upload/status")
def upload_status():
    return file_job.get()


# ㉔ GET /stream.mjpg : HTTP MJPEG 실시간 영상 전송
@app.get("/stream.mjpg")
def stream_video():
    def frames():
        while True:
            with state.lock:
                image_bytes = state.latest_jpg
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Cache-Control: no-cache\r\n\r\n"
                   + image_bytes + b"\r\n")
            time.sleep(0.10)  # 브라우저에 최대 약 10회/초 전송

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )