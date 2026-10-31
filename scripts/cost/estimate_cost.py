#!/usr/bin/env python3
"""시뮬레이션 결과(.sql)의 자동 사이징 용량에 신재생 장비단가를 곱해 투자비를 낸다.

왜 EnergyPlus 밖에서 계산하는가
  ComponentCost:LineItem 의 Line Item Type 선택지는 11개뿐이고, 신재생 중에는
  Generator:Photovoltaic 하나만 있다(IDD 실측). 지열 히트펌프·연료전지·ESS·
  태양열·풍력은 키가 없다. General 타입으로 넣을 수는 있으나 용량을 사람이
  직접 적어야 해서 파라메트릭 자동화가 깨진다.

  자동 사이징 결과는 .sql 의 ComponentSizes 테이블에 들어간다. 거기서 용량을
  읽어 단가를 곱하면 케이스마다 용량이 자동으로 반영된다.

단가는 energinno-DB 한 곳에서만 관리한다. 이 저장소에 복제하지 않는다.
  energinno-DB/data/renewable-equipment/장비단가_2026.json

사용법
  python3 scripts/cost/estimate_cost.py <결과.sql> <장비단가.json> [옵션]

옵션
  --fuelcell pafc|pemfc|sofc   연료전지 종류. EnergyPlus 의 Generator:FuelCell 은
                               종류를 구분하지 않으므로 지정하지 않으면 제외한다.
  --geo-sum                    지열 난방·냉방 용량을 합산한다. 기본은 큰 쪽(max).
                               한 기기가 냉난방을 겸하는 보통의 경우는 기본값이 맞다.
  --json                       JSON 으로 출력

전제
  모델 IDF 에 Output:SQLite(SimpleAndTabular) 가 있어야 .sql 이 생성된다.
  입력 처리 단계에서 실패하면 .sql 이 아예 만들어지지 않는다(docs/04 참조).
종료코드
  0 정상 / 1 실패·미매칭 있음
"""
import json
import os
import sqlite3
import sys

# 장비단가 slug  ->  .sql ComponentSizes 에서 찾을 규칙
#
# CompType 은 IDF 객체 타입 그대로, Description 은 EnergyPlus 가 적는 사이징 항목명이다.
# 매칭은 부분일치로 한다. 모델 구성에 따라 CompType 이 달라질 수 있어 후보를 여럿 둔다.
#
# aggregate 는 후보가 여러 건 잡혔을 때 어떻게 합칠지다.
#   "max"  같은 기기의 여러 모드가 각각 보고될 때 (냉방/난방 분리 보고 등)
#   "sum"  물리적으로 별개인 기기가 여러 대일 때
MATCH = {
    "geothermal-heatpump": {
        "candidates": [
            ("HeatPump:PlantLoop:EIR:Heating", "Nominal Capacity"),
            ("HeatPump:PlantLoop:EIR:Cooling", "Nominal Capacity"),
            ("HeatPump:WaterToWater:EquationFit:Heating", "Nominal Capacity"),
            ("HeatPump:WaterToWater:EquationFit:Cooling", "Nominal Capacity"),
        ],
        # 지열 히트펌프는 보통 한 기기가 냉난방을 겸한다. 난방·냉방 용량이 각각
        # 보고되므로 더하면 2배 계상이 된다. 기본을 큰 쪽(max)으로 둔다.
        # 냉방기·난방기가 물리적으로 별개인 모델이면 --geo-sum 으로 바꿀 것.
        "aggregate": "max",
    },
    "fuelcell": {
        "candidates": [("Generator:FuelCell", "Nominal Electrical Power")],
        "aggregate": "sum",
        # EnergyPlus 의 Generator:FuelCell 은 PAFC/PEMFC/SOFC 를 구분하지 않는다.
        # 성능 파라미터로만 표현되므로 종류를 자동 판별할 수 없다.
        # --fuelcell 로 사용자가 지정해야 한다. 지정하지 않으면 제외한다.
        "needs_choice": {"pafc": "fuelcell-pafc",
                         "pemfc": "fuelcell-pemfc",
                         "sofc": "fuelcell-sofc"},
    },
    "ess-battery": {
        "candidates": [("ElectricLoadCenter:Storage", "Maximum Storage Capacity")],
        "aggregate": "sum",
    },
    # 태양광은 EnergyPlus 내장 비용 산정이 처리한다(idf/cost/ 참조).
    # 여기서 중복 계산하지 않도록 일부러 비워 둔다.
}

UNIT_DIVISOR = {"원/kW": 1000.0, "원/MWh": 1.0e6}   # W -> kW / Wh -> MWh


def load_prices(path):
    d = json.load(open(path, encoding="utf-8"))
    return {it["slug"]: it for it in d["items"]}, d.get("meta", {})


def read_sizes(sql_path):
    if not os.path.exists(sql_path):
        print(f"[실패] .sql 이 없다: {sql_path}")
        print("  Output:SQLite 를 넣었는지, 입력 처리 단계에서 실패하지 않았는지 확인할 것.")
        print("  (입력 오류 시 .sql 은 아예 생성되지 않는다 — docs/04-자동화.md 참조)")
        return None
    con = sqlite3.connect(f"file:{sql_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT CompType, CompName, Description, Value, Units FROM ComponentSizes"
        ).fetchall()
    except sqlite3.Error as e:
        print(f"[실패] ComponentSizes 를 읽을 수 없다: {e}")
        return None
    finally:
        con.close()
    return rows


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    sql_path, price_path = argv[0], argv[1]
    as_json = "--json" in argv
    geo_sum = "--geo-sum" in argv

    fuelcell = None
    for i, a in enumerate(argv):
        if a == "--fuelcell" and i + 1 < len(argv):
            fuelcell = argv[i + 1].lower()

    prices, meta = load_prices(price_path)
    rows = read_sizes(sql_path)
    if rows is None:
        return 1

    results, unmatched, notes = [], [], []

    for key, rule in MATCH.items():
        # --- 연료전지: 종류를 사용자가 지정해야 한다 ---
        if "needs_choice" in rule:
            choices = rule["needs_choice"]
            if fuelcell is None:
                # 설비가 모델에 있는지 먼저 보고, 있을 때만 알린다.
                present = any((ct or "").upper().startswith(c[0].upper())
                              and c[1].upper() in (de or "").upper()
                              for c, (ct, cn, de, v, u) in
                              ((c, r) for c in rule["candidates"] for r in rows))
                if present:
                    unmatched.append(
                        "연료전지가 모델에 있으나 종류를 지정하지 않아 제외했다. "
                        f"--fuelcell {{{'|'.join(choices)}}} 로 지정할 것. "
                        "(EnergyPlus 의 Generator:FuelCell 은 종류를 구분하지 않는다)")
                continue
            if fuelcell not in choices:
                unmatched.append(f"--fuelcell '{fuelcell}' 은 "
                                 f"{{{'|'.join(choices)}}} 중 하나여야 한다")
                continue
            slug = choices[fuelcell]
        else:
            slug = key

        price = prices.get(slug)
        if not price:
            unmatched.append(f"{slug} — 단가 JSON 에 없다")
            continue
        div = UNIT_DIVISOR.get(price["price_unit"])
        if div is None:
            unmatched.append(f"{slug} — 단위 '{price['price_unit']}' 환산 규칙이 없다")
            continue

        found = []
        for comp_type, desc in rule["candidates"]:
            for ct, cn, de, val, un in rows:
                if (ct or "").upper().startswith(comp_type.upper()) \
                        and desc.upper() in (de or "").upper():
                    found.append((ct, cn, de, float(val or 0.0), un))
        if not found:
            continue

        agg = rule["aggregate"]
        if slug == "geothermal-heatpump" and geo_sum:
            agg = "sum"
        raw = sum(f[3] for f in found) if agg == "sum" else max(f[3] for f in found)
        qty = raw / div

        if slug == "geothermal-heatpump" and len(found) > 1 and agg == "max":
            notes.append(
                f"지열: 사이징 항목 {len(found)}건을 '큰 쪽(max)' 으로 집계했다. "
                f"합산하면 {sum(f[3] for f in found) / div:,.1f} "
                f"{price['price_unit'].split('/')[1]} 이 된다. "
                "냉방기·난방기가 물리적으로 별개면 --geo-sum 을 쓸 것.")

        results.append({
            "slug": slug,
            "name": price["name"],
            "quantity": round(qty, 3),
            "quantity_unit": price["price_unit"].split("/")[1],
            "aggregate": agg,
            "unit_price": price["unit_price"],
            "price_unit": price["price_unit"],
            "cost": round(qty * price["unit_price"]),
            "matched": [{"comp_type": f[0], "comp_name": f[1],
                         "description": f[2], "value": f[3], "units": f[4]}
                        for f in found],
        })

    total = sum(r["cost"] for r in results)
    base_notes = [
        "태양광은 EnergyPlus 내장 비용 산정(ComponentCost:LineItem)이 처리하므로 "
        "여기서 제외했다. 총 투자비는 Construction Cost Estimate Summary 와 합산할 것.",
        "단가의 부가가치세 포함 여부는 확인되지 않았다(energinno-DB 기록).",
    ]

    if as_json:
        print(json.dumps({
            "source_sql": sql_path,
            "price_source": price_path,
            "price_base_year": meta.get("base_year"),
            "options": {"fuelcell": fuelcell, "geo_sum": geo_sum},
            "items": results,
            "total_cost": total,
            "notes": base_notes + notes,
            "unmatched": unmatched,
        }, ensure_ascii=False, indent=2))
        return 1 if unmatched else 0

    print(f"결과 파일 : {sql_path}")
    print(f"단가 출처 : {price_path}  (기준연도 {meta.get('base_year', '?')})")
    print()
    if not results:
        print("사이징 결과에서 해당 설비를 찾지 못했다.")
        print("모델에 지열·연료전지·ESS 가 없거나, CompType 이름이 예상과 다를 수 있다.")
        print('확인: sqlite3 <결과.sql> "select distinct CompType from ComponentSizes;"')
    else:
        print(f"{'설비':<26}{'용량':>13}{'집계':>6}{'단가':>17}{'투자비':>19}")
        print("-" * 84)
        for r in results:
            print(f"{r['name'][:24]:<26}"
                  f"{r['quantity']:>9,.1f} {r['quantity_unit']:<3}"
                  f"{r['aggregate']:>6}"
                  f"{r['unit_price']:>12,} {r['price_unit'].split('/')[0]:<4}"
                  f"{r['cost']:>17,} 원")
        print("-" * 84)
        print(f"{'합계':<26}{'':>13}{'':>6}{'':>17}{total:>17,} 원")
        print()
        for r in results:
            print(f"  · {r['name']} 매칭 근거 (집계: {r['aggregate']})")
            for m in r["matched"]:
                print(f"      {m['comp_type']} / {m['comp_name']}")
                print(f"        {m['description']} = {m['value']:,.1f} {m['units']}")

    print()
    print("주의")
    for n in base_notes + notes:
        print(f"  · {n}")
    if unmatched:
        print()
        print("미매칭 / 지정 필요")
        for u in unmatched:
            print(f"  · {u}")
    return 1 if unmatched else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
