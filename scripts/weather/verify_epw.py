#!/usr/bin/env python3
"""EPW 기상파일 품질 검증.

확인 항목
  1. 데이터 행이 8,760행인가 (연간 완전)
  2. 일사량(전일사·직달법선·확산수평) 결측이 있는가
     → EnergyPlus 는 일사 결측을 0 으로 대체하고 오류를 내지 않는다.
       해가 없는 것으로 계산되므로 냉방부하가 과소평가되고 PV 발전량은 0 이 된다.
  3. 건구온도·일사 적산값이 상식적 범위인가

사용법
  python3 scripts/weather/verify_epw.py <epw파일_또는_디렉터리>
종료코드
  0 전부 통과 / 1 하나라도 실패
"""
import sys, os, glob

MISSING = 9999.0          # EPW 일사 필드의 결측 센티넬
ROWS_EXPECTED = 8760

# EPW 데이터 레코드 열 번호(0-base): 6 건구온도, 13 전일사, 14 직달법선, 15 확산수평
COL_DRYBULB, COL_GLOBAL, COL_DIRECT, COL_DIFFUSE = 6, 13, 14, 15


def verify(path):
    rows = 0
    missing = 0
    dbmin, dbmax = 99.0, -99.0
    acc = {COL_GLOBAL: 0.0, COL_DIRECT: 0.0, COL_DIFFUSE: 0.0}

    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i < 8:                      # 헤더 8줄
                continue
            cols = line.rstrip("\n").split(",")
            if len(cols) < 20:
                continue
            rows += 1
            try:
                t = float(cols[COL_DRYBULB])
                if t < 99.0:
                    dbmin, dbmax = min(dbmin, t), max(dbmax, t)
            except ValueError:
                pass
            for c in acc:
                try:
                    v = float(cols[c])
                except ValueError:
                    continue
                if v >= MISSING:
                    missing += 1
                else:
                    acc[c] += v

    problems = []
    if rows != ROWS_EXPECTED:
        problems.append(f"행수 {rows} (기대 {ROWS_EXPECTED})")
    if missing:
        problems.append(f"일사 결측 {missing}개 — EnergyPlus 가 0 으로 대체한다")
    if not (-40.0 < dbmin < 40.0) or not (-40.0 < dbmax < 60.0):
        problems.append(f"건구온도 범위 이상 {dbmin}~{dbmax}℃")
    ghi = acc[COL_GLOBAL] / 1000
    if not (600.0 < ghi < 2600.0):
        problems.append(f"연간 전일사 {ghi:.0f} kWh/m2 — 상식 범위를 벗어난다")

    return {
        "rows": rows,
        "solar_missing": missing,
        "drybulb_c_min": round(dbmin, 1),
        "drybulb_c_max": round(dbmax, 1),
        "global_horiz_kwh_m2": round(ghi, 1),
        "direct_normal_kwh_m2": round(acc[COL_DIRECT] / 1000, 1),
        "diffuse_horiz_kwh_m2": round(acc[COL_DIFFUSE] / 1000, 1),
        "problems": problems,
    }


def main(target):
    files = (sorted(glob.glob(os.path.join(target, "*.epw")))
             if os.path.isdir(target) else [target])
    if not files:
        print(f"EPW 파일이 없다: {target}", file=sys.stderr)
        return 1

    print(f"{'파일':<50}{'행수':>7}{'결측':>6}{'건구℃':>14}"
          f"{'전일사':>9}{'직달':>8}{'확산':>8}  판정")
    print("-" * 118)
    failed = 0
    for p in files:
        r = verify(p)
        ok = not r["problems"]
        failed += 0 if ok else 1
        print(f"{os.path.basename(p)[:48]:<50}{r['rows']:>7}{r['solar_missing']:>6}"
              f"{r['drybulb_c_min']:>7.1f}/{r['drybulb_c_max']:<6.1f}"
              f"{r['global_horiz_kwh_m2']:>9.0f}{r['direct_normal_kwh_m2']:>8.0f}"
              f"{r['diffuse_horiz_kwh_m2']:>8.0f}  {'통과' if ok else '실패'}")
        for msg in r["problems"]:
            print(f"{'':<50}  └ {msg}")

    print("-" * 118)
    print(f"총 {len(files)}개 / 실패 {failed}개")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
