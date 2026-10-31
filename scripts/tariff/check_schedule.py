"""IDF 의 Schedule:Compact 가 원본 요금표의 계절·시간대 정의를 정확히 재현하는지
8,760시간 전부 대조한다. EnergyPlus 를 돌리지 않고 변환 오류를 잡는다.

사용법
  python3 scripts/tariff/check_schedule.py <IDF경로> <요금표JSON경로>

요금표 JSON 은 energinno-DB 의 data/electricity/kepco_YYYY-MM-DD.json 이다
(definitions.seasons 와 definitions.time_zones 를 읽는다).

종료코드
  0 전부 일치 / 1 불일치 있음
"""
import json, re, sys, datetime

idf, tariff_json = sys.argv[1], sys.argv[2]

# ---- IDF 의 Schedule:Compact 를 해석해 (월,일,시) -> 값 함수를 만든다 ----
def parse_compact(text, sched_name):
    body = re.search(r'Schedule:Compact,\s*' + re.escape(sched_name) + r'\s*,(.*?);',
                     text, re.S)
    if not body:
        sys.exit(f"스케줄을 못 찾음: {sched_name}")
    toks = [t.strip() for t in body.group(1).split(',') if t.strip()]
    blocks, cur = [], None
    for t in toks:
        if t.startswith('Through:'):
            md = t.split(':', 1)[1].strip()
            mm, dd = (int(x) for x in md.split('/'))
            cur = {"through": (mm, dd), "rules": []}
            blocks.append(cur)
        elif t.startswith('For:'):
            continue
        elif t.startswith('Until:'):
            hhmm = t.split(':', 1)[1].strip()
            h, m = (int(x) for x in hhmm.split(':')) if ':' in hhmm else (int(hhmm), 0)
            cur["rules"].append([h * 60 + m, None])
        elif cur is None:
            continue          # 스케줄 자료형 이름 등, 첫 Through: 앞의 토큰
        else:
            # Until 다음에 오는 값
            for r in reversed(cur["rules"]):
                if r[1] is None:
                    r[1] = float(t); break
    return blocks

def lookup(blocks, month, day, hour):
    """hour 는 1~24 (EnergyPlus 식: Until: 08:00 은 07:00~08:00 구간 끝)"""
    for b in blocks:
        tm, td = b["through"]
        if (month, day) <= (tm, td):
            minute = hour * 60
            for until, val in b["rules"]:
                if minute <= until:
                    return val
            return b["rules"][-1][1]
    return None

text = open(idf, encoding='utf-8', errors='replace').read()
text = re.sub(r'!.*', '', text)
season_b = parse_compact(text, 'KEPCO_Season')
tou_b    = parse_compact(text, 'KEPCO_TOU_Period')

# ---- 원본 요금표 정의 ----
d = json.load(open(tariff_json, encoding='utf-8'))
defs = d['definitions']
SEASON_MONTHS = {}   # month -> 한전 계절명
for s in defs['seasons']:
    code = s['code']
    for part in s['months'].replace('월', '').split(','):
        part = part.strip()
        if '~' in part:
            a, b = (int(x) for x in part.split('~'))
            rng = range(a, b + 1) if a <= b else list(range(a, 13)) + list(range(1, b + 1))
            for mm in rng: SEASON_MONTHS[mm] = code
        elif part:
            SEASON_MONTHS[int(part)] = code

TZ = defs['time_zones']          # 계절 -> 시간대 -> ["22:00~08:00", ...]
def kepco_zone(season_code, hour):
    """hour 1~24. 구간 "08:00~15:00" 은 08시 초과 15시 이하를 뜻한다."""
    for zone, spans in TZ[season_code].items():
        for sp in spans:
            a, b = sp.split('~')
            ah = int(a.split(':')[0]); bh = int(b.split(':')[0])
            if bh == 0: bh = 24
            if ah < bh:
                if ah < hour <= bh: return zone
            else:                        # 자정을 넘는 구간 (22:00~08:00)
                if hour > ah or hour <= bh: return zone
    return None

E_SEASON = {1: '겨울철', 2: '봄·가을철', 3: '여름철', 4: '봄·가을철'}
E_ZONE   = {3: '경부하', 4: '중간부하', 1: '최대부하'}

mism_season, mism_zone, total = [], [], 0
for month in range(1, 13):
    days = [31,28,31,30,31,30,31,31,30,31,30,31][month-1]
    for day in range(1, days + 1):
        for hour in range(1, 25):
            total += 1
            sv = lookup(season_b, month, day, hour)
            tv = lookup(tou_b, month, day, hour)
            want_season = SEASON_MONTHS[month]
            got_season = E_SEASON.get(int(sv)) if sv else None
            if got_season != want_season:
                mism_season.append((month, day, hour, want_season, got_season))
            want_zone = kepco_zone(want_season, hour)
            got_zone = E_ZONE.get(int(tv)) if tv else None
            if got_zone != want_zone:
                mism_zone.append((month, day, hour, want_season, want_zone, got_zone))

print(f"대조한 시간: {total}  (8760 기대)")
print(f"계절 불일치: {len(mism_season)}")
for r in mism_season[:5]: print("   ", r)
print(f"시간대 불일치: {len(mism_zone)}")
for r in mism_zone[:8]: print("   ", r)
sys.exit(1 if (mism_season or mism_zone or total != 8760) else 0)
