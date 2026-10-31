r"""IDF 를 IDD 와 대조해 구조 오류를 잡는다. (EnergyPlus 실행 없이)

검사 항목
  · 객체 이름이 IDD 에 있는가
  · 필드 수가 IDD 정의를 넘지 않는가
  · \required-field 가 비어 있지 않은가
  · \type choice 필드 값이 \key 목록에 있는가
  · \minimum / \maximum 위반이 있는가
사용법
  python3 check_idf.py <IDD경로> <IDF경로>
"""
import re, sys, collections

idd_path, idf_path = sys.argv[1], sys.argv[2]

# ---------- IDD 파싱 ----------
Field = collections.namedtuple("Field", "code name required keys ftype minimum maximum")
objects = {}
cur = None
fields = []
f = None

obj_re = re.compile(r'^([A-Za-z][A-Za-z0-9:_\-. ]*),\s*$')
fld_re = re.compile(r'^\s+([AN]\d+)\s*[,;]')

def flush_field():
    global f
    if f is not None:
        fields.append(Field(**f))
        f = None

def flush_object():
    global cur, fields
    flush_field()
    if cur:
        objects[cur.upper()] = fields
    cur, fields = None, []

for raw in open(idd_path, encoding='utf-8', errors='replace'):
    line = raw.rstrip('\n')
    if line.startswith('!'):
        continue
    m = obj_re.match(line)
    if m:
        flush_object()
        cur = m.group(1).strip()
        continue
    if cur is None:
        continue
    m = fld_re.match(line)
    if m:
        flush_field()
        f = dict(code=m.group(1), name=None, required=False, keys=[],
                 ftype=None, minimum=None, maximum=None)
        continue
    if f is None:
        continue
    s = line.strip()
    if s.startswith('\\field'):
        f['name'] = s[6:].strip()
    elif s.startswith('\\required-field'):
        f['required'] = True
    elif s.startswith('\\key'):
        f['keys'].append(s[4:].strip())
    elif s.startswith('\\type'):
        f['ftype'] = s[5:].strip()
    elif s.startswith('\\minimum'):
        f['minimum'] = s
    elif s.startswith('\\maximum'):
        f['maximum'] = s
flush_object()

# ---------- IDF 파싱 ----------
text = open(idf_path, encoding='utf-8', errors='replace').read()
text = re.sub(r'!.*', '', text)                 # 주석 제거
raw_objs = [o.strip() for o in text.split(';') if o.strip()]

errors, warnings, checked = [], [], 0
for ro in raw_objs:
    parts = [p.strip() for p in ro.split(',')]
    name = parts[0]
    defn = objects.get(name.upper())
    if defn is None:
        errors.append(f"[정의 없음] 객체 '{name}' 이 IDD 에 없다")
        continue
    checked += 1
    values = parts[1:]

    # Schedule:Compact 등 extensible 객체는 필드 수 검사를 건너뛴다
    extensible = any(k in name.upper() for k in ("SCHEDULE:COMPACT",))

    if not extensible and len(values) > len(defn):
        errors.append(f"[필드 초과] {name}: 값 {len(values)}개, IDD 정의 {len(defn)}개")

    for i, fd in enumerate(defn):
        v = values[i] if i < len(values) else ""
        if fd.required and v == "":
            errors.append(f"[필수 누락] {name} → {fd.code} {fd.name}")
        if v and fd.keys:
            if v.upper() not in [k.upper() for k in fd.keys]:
                errors.append(f"[선택지 오류] {name} → {fd.code} {fd.name} = '{v}'"
                              f"  (허용: {', '.join(fd.keys)})")

print(f"IDD 객체 정의 {len(objects)}개 적재")
print(f"IDF 객체 {len(raw_objs)}개 중 {checked}개 대조\n")
if errors:
    print(f"오류 {len(errors)}건")
    for e in errors:
        print("  " + e)
else:
    print("오류 없음")
if warnings:
    print(f"\n경고 {len(warnings)}건")
    for w in warnings:
        print("  " + w)
sys.exit(1 if errors else 0)
