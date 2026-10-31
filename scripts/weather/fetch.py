#!/usr/bin/env python3
"""매니페스트에 고정된 기상파일을 내려받아 sha256 으로 대조한다.

weather/manifest.json 에 적힌 sha256 과 다른 파일은 채택하지 않는다.
상위 배포본이 조용히 바뀌어도 그 사실이 여기서 드러난다.

파일은 두 곳에서 찾는다.
  1순위  weather/archive/  — 저장소에 커밋된 고정 보관본(네트워크 불필요)
  2순위  상위 배포 서버     — 보관본이 없거나 해시가 다를 때만

다운로드 실패와 체크섬 불일치를 구분해 보고한다.
상위 서버는 429 Too Many Requests 를 자주 돌려주므로 재시도·간격을 둔다
(자세한 이유는 _http.py 주석 참조).

사용법
  python3 scripts/weather/fetch.py [출력디렉터리]      기본값 weather/epw
종료코드
  0 전부 일치 / 1 불일치 또는 실패 있음
"""
import hashlib
import json
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "weather", "manifest.json")
ARCHIVE = os.path.join(ROOT, "weather", "archive")   # 저장소에 커밋된 고정 보관본


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(outdir):
    m = json.load(open(MANIFEST, encoding="utf-8"))
    archive_dir = os.path.join(outdir, "_archive")
    os.makedirs(archive_dir, exist_ok=True)

    ok, mismatched, failed = 0, [], []
    from_archive = 0

    for s in m["stations"]:
        zpath = os.path.join(archive_dir, s["archive"])

        # 1순위: 저장소에 커밋된 고정 보관본.
        #        네트워크도 상위 서버 상태도 필요 없고, 상위 배포본이 바뀌어도 영향이 없다.
        if not (os.path.exists(zpath) and sha256(zpath) == s["sha256_zip"]):
            pinned = os.path.join(ARCHIVE, s["archive"])
            if os.path.exists(pinned) and sha256(pinned) == s["sha256_zip"]:
                shutil.copy2(pinned, zpath)
                from_archive += 1

        # 2순위: 상위 서버에서 내려받기.
        if not (os.path.exists(zpath) and sha256(zpath) == s["sha256_zip"]):
            got_ok, reason = _http.download(s["source_url"], zpath)
            if not got_ok:
                print(f"[받기 실패] {s['station']} — {reason}")
                failed.append(s["station"])
                continue

        got = sha256(zpath)
        if got != s["sha256_zip"]:
            print(f"[체크섬 불일치] {s['station']}")
            print(f"         기대 {s['sha256_zip']}")
            print(f"         실제 {got}")
            mismatched.append(s["station"])
            continue

        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                if name.lower().endswith((".epw", ".ddy", ".stat")):
                    z.extract(name, outdir)
        ok += 1

    total = len(m["stations"])
    print(f"\n일치 {ok} / 체크섬 불일치 {len(mismatched)} / 받기 실패 {len(failed)}"
          f"  (총 {total}지점)")
    if from_archive:
        print(f"그중 {from_archive}개는 저장소 보관본(weather/archive)에서 가져왔다.")
    print(f"출력: {outdir}")

    if failed:
        print(f"\n받기 실패: {', '.join(failed)}")
        print("  일시적 실패일 수 있다. 잠시 뒤 다시 실행할 것.")
        print("  (상위 서버는 짧은 시간에 많이 요청하면 429 를 돌려준다)")
    if mismatched:
        print(f"\n체크섬 불일치: {', '.join(mismatched)}")
        print("  상위 배포본이 바뀌었다는 뜻이다. 자동 교체하지 않는다.")
        print("  1. scripts/weather/check_updates.py 로 ETag·수정일 변경을 확인")
        print("  2. scripts/weather/verify_epw.py 로 품질(8760행·일사 결측) 확인")
        print("  3. 기존 파일과 연간 적산값을 비교해 차이의 크기를 파악")
        print("  4. 채택을 결정했으면 weather/manifest.json 을 갱신하고")
        print("     '왜 바꿨는지'를 커밋 메시지에 남길 것")

    return 1 if (mismatched or failed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else os.path.join(ROOT, "weather", "epw")))
