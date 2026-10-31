"""상위 배포 서버(climate.onebuilding.org)에 대한 공용 HTTP 유틸.

이 서버는 짧은 시간에 여러 번 요청하면 429 Too Many Requests 를 돌려준다.
그런데 curl 은 오류 본문도 -o 파일에 그대로 쓰기 때문에, HTTP 상태를 보지 않으면
227바이트짜리 429 HTML 이 zip 으로 저장되고 그 해시가 "체크섬 불일치"로 보고된다.
즉 일시적 실패가 '상위 배포본이 바뀌었다'는 거짓 경보로 둔갑한다.

그래서 이 모듈을 통과하는 요청은 모두
  · HTTP 상태를 확인하고
  · 429/5xx 는 백오프 재시도하고
  · 요청 사이에 간격을 두고
  · 실패한 응답을 결과 파일로 남기지 않는다.
"""
import os
import subprocess
import tempfile
import time

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
POLITE_DELAY_SEC = 1.0          # 연속 요청 사이 간격
BACKOFF_BASE_SEC = 3.0


def _sleep_backoff(attempt):
    time.sleep(BACKOFF_BASE_SEC * (2 ** attempt))


def head(url, attempts=4):
    """헤더만 받아 dict 로 돌려준다. 실패하면 빈 dict."""
    for i in range(attempts):
        r = subprocess.run(
            ["curl", "-sI", "--max-time", "60", "-w", "\n%{http_code}", url],
            capture_output=True, text=True)
        lines = r.stdout.strip().splitlines()
        status = lines[-1].strip() if lines else ""
        out = {}
        for line in lines[:-1]:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            if k in ("etag", "last-modified", "content-length"):
                out[k] = v.strip()
        if status == "200" and (out.get("etag") or out.get("last-modified")):
            time.sleep(POLITE_DELAY_SEC)
            return out
        if i < attempts - 1 and (status in ("", "000")
                                 or (status.isdigit() and int(status) in RETRY_STATUS)):
            _sleep_backoff(i)
            continue
        break
    return {}


def download(url, dest, attempts=4):
    """dest 로 내려받는다. (성공여부, 사유) 를 돌려준다.

    임시 파일에 받아 HTTP 200 일 때만 dest 로 옮긴다.
    실패한 응답 본문은 dest 에 남지 않는다.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    last = "원인 불명"
    for i in range(attempts):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)),
                                   suffix=".part")
        os.close(fd)
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "180", "-o", tmp, "-w", "%{http_code}", url],
            capture_output=True, text=True)
        status = (r.stdout or "").strip()

        if r.returncode == 0 and status == "200" and os.path.getsize(tmp) > 0:
            os.replace(tmp, dest)
            time.sleep(POLITE_DELAY_SEC)
            return True, "200"

        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        os.unlink(tmp)
        last = (f"HTTP {status or '응답없음'}"
                + (f", curl 종료코드 {r.returncode}" if r.returncode else "")
                + (f", 본문 {size}B" if size else ""))

        if i < attempts - 1 and (status in ("", "000")
                                 or (status.isdigit() and int(status) in RETRY_STATUS)):
            _sleep_backoff(i)
            continue
        break
    return False, last
