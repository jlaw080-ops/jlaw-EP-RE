#!/usr/bin/env python3
"""상위 배포본의 변경을 감시한다. 파일을 바꾸지는 않는다.

두 가지를 본다.
  1. 고정한 지점 파일의 ETag / Last-Modified / Content-Length 가 달라졌는가
  2. TMYx 에 새 기간판(예: 2013-2027)이 생겼는가

기준년(Reference Year)은 재현성이 목적이므로 자동 교체하지 않는다.
변경이 보이면 사람이 품질을 검증한 뒤 의식적으로 갱신한다.

사용법
  python3 scripts/weather/check_updates.py              사람이 읽는 출력
  python3 scripts/weather/check_updates.py --github     GITHUB_OUTPUT 에 결과 기록
종료코드
  0 변경 없음 / 10 변경 감지 / 1 점검 실패(판정 불가)
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "weather", "manifest.json")
INDEX = ("https://climate.onebuilding.org/WMO_Region_2_Asia"
         "/KOR_South_Korea/index.html")

# 고정 시점에 존재하던 TMYx 기간판. 이보다 새 기간이 보이면 알린다.
KNOWN_TMYX_PERIODS = {"2004-2018", "2007-2021", "2009-2023", "2011-2025"}


def check_stations(manifest):
    changed, unreachable = [], []
    for s in manifest["stations"]:
        h = _http.head(s["source_url"])
        if not h:
            unreachable.append(s["station"])
            continue
        up = s["upstream"]
        diffs = []
        if up.get("etag") and h.get("etag") and up["etag"] != h["etag"]:
            diffs.append(f"ETag {up['etag']} → {h['etag']}")
        if up.get("last_modified") and h.get("last-modified") \
                and up["last_modified"] != h["last-modified"]:
            diffs.append(f"Last-Modified {up['last_modified']} → {h['last-modified']}")
        if up.get("content_length") and h.get("content-length") \
                and int(up["content_length"]) != int(h["content-length"]):
            diffs.append(f"크기 {up['content_length']} → {h['content-length']} bytes")
        if diffs:
            changed.append((s["station"], diffs))
    return changed, unreachable


def check_new_tmyx():
    """TMYx 기간판 목록을 긁어 알려진 것보다 새 기간이 있는지 본다.

    돌려주는 값: 신규 기간 목록 / 읽지 못하면 None
    """
    for _ in range(3):
        r = subprocess.run(["curl", "-sS", "--max-time", "120",
                            "-w", "\n%{http_code}", INDEX],
                           capture_output=True, text=True)
        body = r.stdout or ""
        lines = body.strip().splitlines()
        status = lines[-1].strip() if lines else ""
        if status == "200":
            found = {f"{a}-{b}" for a, b in
                     re.findall(r"TMYx\.(\d{4})-(\d{4})", body)}
            return sorted(found - KNOWN_TMYX_PERIODS)
    return None


def main(argv):
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    meta = manifest["meta"]
    print(f"고정 시점: {meta['pinned_at']}  /  지점 {meta['station_count']}개\n")

    changed, unreachable = check_stations(manifest)
    new_periods = check_new_tmyx()

    if changed:
        print(f"[변경 감지] {len(changed)}개 지점")
        for name, diffs in changed:
            print(f"  · {name}")
            for d in diffs:
                print(f"      {d}")
        print()
    else:
        print("[지점 파일] 변경 없음\n")

    if new_periods:
        print(f"[신규 TMYx 기간판] {', '.join(new_periods)}\n")
    elif new_periods is None:
        print("[신규 TMYx 기간판] 목록 페이지를 읽지 못했다 — 판정 불가\n")
    else:
        print("[신규 TMYx 기간판] 없음\n")

    if unreachable:
        print(f"[점검 불가] {len(unreachable)}개 — {', '.join(unreachable)}")
        print("  상위 서버 레이트 리밋(429)일 수 있다. 잠시 뒤 다시 실행할 것.\n")

    has_change = bool(changed or new_periods)
    if has_change:
        print("자동 교체는 하지 않는다. 다음 순서로 사람이 판단할 것:")
        print("  1. scripts/weather/fetch.py 로 받아 체크섬 불일치를 확인")
        print("  2. scripts/weather/verify_epw.py 로 품질(8760행·일사 결측) 확인")
        print("  3. 기존 파일과 연간 적산값을 비교해 차이의 크기를 파악")
        print("  4. 채택을 결정했으면 weather/manifest.json 을 갱신하고")
        print("     '왜 바꿨는지'를 커밋 메시지에 남길 것")

    if "--github" in argv and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if has_change else 'false'}\n")
            f.write(f"changed_count={len(changed)}\n")
            f.write(f"changed_stations={','.join(n for n, _ in changed)}\n")
            f.write(f"new_periods={','.join(new_periods or [])}\n")
            f.write(f"unreachable={','.join(unreachable)}\n")

    if has_change:
        return 10
    # 변경이 없는데 판정조차 못 한 항목이 있으면 '점검 실패'로 알린다.
    if unreachable or new_periods is None:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
