# 06. 심화 — CTF (전도열전달함수) 상세

> [`02-계산엔진.md`](02-계산엔진.md) 2-5절의 CTF 항목을 확장한 문서입니다.
> 소스는 **v26.2.0 태그** 실측, 수식은 공식 `EngineeringReference` §3.1 (p63~72) 기준입니다.

## 6-1. 왜 필요한가 — 해결하려는 문제

벽체를 지나는 열은 **지연되고 감쇠됩니다.** 콘크리트 벽 외부가 정오에 뜨거워져도
실내 표면은 몇 시간 뒤에 반응합니다. 이 축열 거동을 계산하려면 원래 벽체 내부를
여러 층으로 쪼개 각 지점의 온도를 매 타임스텝 풀어야 합니다(유한차분법).
정확하지만 느립니다.

CTF는 다른 접근입니다. **벽체 내부 온도를 아예 계산하지 않습니다.**
대신 "과거 표면온도와 과거 열流의 가중합"으로 현재 열流를 바로 뽑아냅니다.

```
현재 열流 = (과거 외부 표면온도들 × 가중계수)
          + (과거 내부 표면온도들 × 가중계수)
          + (과거 열流들       × 가중계수)
```

이 가중계수가 CTF 계수입니다. 핵심은 **계수를 시뮬레이션 시작 전에 단 한 번만
계산한다**는 점입니다. 공식 문서 표현:

> *"determining the series of CTF coefficients is a one-time calculation,
> making the method much faster than finite difference calculations."*

연간 52,560 타임스텝 동안 벽체 내부 해석을 반복하지 않으니 압도적으로 빠릅니다.

## 6-2. 수식

내부 표면 열流:

$$q''_{ki}(t) = -Z_o T_{i,t} - \sum_{j=1}^{nz} Z_j T_{i,t-j\delta} + Y_o T_{o,t} + \sum_{j=1}^{nz} Y_j T_{o,t-j\delta} + \sum_{j=1}^{nq} \Phi_j q''_{ki,t-j\delta}$$

외부 표면 열流:

$$q''_{ko}(t) = -Y_o T_{i,t} - \sum_{j=1}^{nz} Y_j T_{i,t-j\delta} + X_o T_{o,t} + \sum_{j=1}^{nz} X_j T_{o,t-j\delta} + \sum_{j=1}^{nq} \Phi_j q''_{ko,t-j\delta}$$

| 기호 | 이름 | 의미 | 소스 배열 |
|---|---|---|---|
| $X_j$ | Outside CTF | 외부 표면온도 이력의 가중계수 | `CTFOutside` |
| $Z_j$ | Inside CTF | 내부 표면온도 이력의 가중계수 | `CTFInside` |
| $Y_j$ | Cross CTF | 반대편 표면온도의 영향 (열이 벽을 건너오는 항) | `CTFCross` |
| $\Phi_j$ | Flux CTF | 과거 열流 자체의 가중계수 | `CTFFlux` |

(배열 정의: `src/EnergyPlus/Construction.hh:159~163`)

**눈여겨볼 점 두 가지.**

- $Y$(Cross) 항이 양쪽 식에 똑같이 나타납니다. **상호성(reciprocity)** — 벽을 통한
  열전달이 방향에 무관하게 같은 계수를 쓴다는 물리 법칙이 수식에 박혀 있습니다.
- $\Phi$(Flux) 항이 **과거 열流를 다시 입력으로 쓰는 재귀 구조**입니다. 적은 항으로
  긴 축열 이력을 표현할 수 있게 해주지만, 동시에 **수치 불안정의 원인**입니다(6-5절).

## 6-3. 계수를 구하는 절차 — 상태공간법

진입점: `src/EnergyPlus/Construction.cc:59` `calculateTransferFunction()`
함수 헤더 주석에 5단계 절차가 명시되어 있습니다.

```
1. 재료 정보가 충분히 입력됐는지 확인
2. 이 구성체가 (a) 전부 저항성인가, (b) 이전에 계산한 구성체의 역순인가,
   (c) 둘 다 아닌가를 판정
3. (a)이면 → 전체 R값만 계산해 정상상태 전도로 처리 (CTF 계산 안 함)
4. (b)이면 → 기존 CTF를 가져와 내부/외부 항만 뒤집어 복사
5. (c)이면 → 상태공간법으로 CTF 계산
```

**실무상 의미가 있는 건 4번입니다.** 외벽과 그 역순 구성체(예: 인접 존 사이
칸막이의 양면)는 계수를 재사용합니다. 모델에 구성체가 많아도 실제 CTF 계산 횟수는
그보다 적습니다.

5번의 상태공간법은 벽체를 유한차분 격자로 나눠 행렬 방정식을 세우고 선형대수로 풉니다.

$$\dot{\mathbf{x}} = [\mathbf{A}]\mathbf{x} + [\mathbf{B}]\mathbf{u}, \qquad \begin{bmatrix} q''_i \\ q''_o \end{bmatrix} = [\mathbf{C}]\begin{bmatrix} T_1 \\ \vdots \\ T_n \end{bmatrix} + [\mathbf{D}]\begin{bmatrix} T_i \\ T_o \end{bmatrix}$$

출처는 **Seem, J.E., "Modeling of Heat Transfer in Buildings",
University of Wisconsin-Madison, 1987** (박사논문)입니다.
EnergyPlus는 원래 Hittle(1980) 방식을 쓰다가 짧은 타임스텝에서 불안정해 교체했습니다.

> 이 함수는 **1990년 6월 Russ Taylor**가 작성한 코드입니다. EnergyPlus보다 오래된
> BLAST/IBLAST 유산이고, 주석에 개발자가 직접
> *"may be a little difficult to decipher until another major revision is done"*
> 라고 적어 놨습니다.

## 6-4. 격자 분할 규칙

`Construction.cc:501~516` 의 노드 간격 결정식:

$$\Delta x_{\text{목표}} = \sqrt{2 \alpha \Delta t_{\text{CTF}}}, \qquad \alpha = \frac{k}{\rho c_p}$$

```cpp
dxn = std::sqrt(2.0 * (rk(Layer) / rho(Layer) / cp(Layer)) * this->CTFTimeStep);
int ipts1 = int(dl(Layer) / dxn);        // 노드 수 = 두께 / 간격
if (ipts1 > Construction::MaxCTFTerms)   Nodes(Layer) = Construction::MaxCTFTerms;  // 19
else if (ipts1 < MinNodes)               Nodes(Layer) = MinNodes;                   // 6
else                                     Nodes(Layer) = ipts1;
```

| 제약 | 값 | 출처 |
|---|---|---|
| 재료층당 최소 노드 | **6** | `Construction.cc:126` (IBLAST 경험치) |
| 재료층당 최대 노드 | **19** | `Construction.hh:68` `MaxCTFTerms` |
| 구성체당 최대 층 수 | **11** | `Construction.hh:67` `MaxLayersInConstruct` |
| 중간 저항성 층 | **1 노드** | `Construction.cc:498` |

층 경계에는 별도 노드가 놓이고, 그 노드는 **양쪽 층의 반 노드씩을 합친 열용량**을 갖습니다.

> **문서와 코드의 불일치 사례.** 공식 문서는
> *"divide each material layer into between 6 and 18 nodes"* 라고 적지만
> 실제 코드 상한은 `MaxCTFTerms` = **19** 입니다. 결과에 큰 영향은 없지만,
> [`02-계산엔진.md`](02-계산엔진.md) 2-6절의 "문서와 구현이 어긋난 경우도 있다"의
> 구체적 사례입니다. **알고리즘 검증 시 문서만 믿으면 안 되는 이유.**

안정성 기준의 계수 `2.0`에 대해 코드가 솔직하게 적어 놓은 주석:

> *"This criteria was chosen not because it is viewed to be correct,
> but rather for lack of any better criteria at this time."*

## 6-5. ★ 타임스텝 자동 조정 — ZEB 실무의 핵심 함정

**이 절이 가장 중요합니다.**

CTF는 계산 시 쓴 타임스텝에 고정됩니다. 그런데 축열체가 두꺼우면 안정적인 CTF를
얻기 위해 **더 긴 타임스텝이 필요**합니다. (`Construction.cc:584~600`)

```cpp
dtn = rho(Layer) * cp(Layer) * pow_2(dx(Layer)) / rk(Layer);   // Fourier 수 = 1
if (dtn > this->CTFTimeStep) this->CTFTimeStep = dtn;
```

$$\Delta t_{\text{필요}} = \frac{\rho c_p \Delta x^2}{k}$$

이 값이 IDF `Timestep` 객체의 존 타임스텝보다 **10% 넘게 크면**,
EnergyPlus는 CTF 타임스텝을 **존 타임스텝의 정수배로 늘립니다.**

```cpp
this->NumHistories = int((this->CTFTimeStep / state.dataGlobal->TimeStepZone) + 0.5);
this->CTFTimeStep  = state.dataGlobal->TimeStepZone * double(this->NumHistories);
```

그러면 그 벽체는 **존보다 긴 주기로만 열流를 갱신**하고, 중간 시점은 보간으로 채웁니다.
(공식 문서의 "Master history with interpolation" 방식.
런타임은 `HeatBalanceSurfaceManager.cc` 의 `UpdateThermalHistories()` 가 담당)

> **실무 결론 — 외워 두실 만합니다**
>
> 콘크리트 구조·대형 축열체·지중 접지 바닥이 있는 ZEB 모델에서 `Timestep` 을
> 과도하게 짧게(예: 60 = 분당 1회) 주면 정밀도가 올라가지 않습니다.
> 그 벽체들은 **조용히 긴 타임스텝 + 보간으로 전환**되고 계산 시간만 늘어납니다.
> 심한 경우 CTF 급수가 불안정해져 시뮬레이션이 발산합니다.

공식 문서가 구체적 수치를 줍니다.

> *"Heavy constructions, such as slab-on-grade floors (12" heavyweight concrete over
> 18" dirt), have accuracy and stability problems at time steps as large as 0.5 hours"*

즉 **30분 타임스텝에서도 문제가 생기는 구성체가 존재합니다.**

안전장치로 상한도 걸려 있습니다 (`Construction.cc:132`).

```cpp
constexpr Real64 MaxAllowedTimeStep = 7.0;   // 7시간
```

주석에 *"2 meters of concrete will result in a time step of slightly more than 3 hours"*
라고 적혀 있습니다. 7시간을 넘으면 입력이 잘못됐다고 보고 오류를 냅니다.

## 6-6. 수치해석 — 지수행렬

`Construction.cc:1100` `calculateExponentialMatrix()` 가 $e^{[\mathbf{A}]\Delta t}$ 를
계산합니다. **Taylor 급수 전개 + 스케일링·제곱법**(Seem 논문 부록 A)을 씁니다.

최적화 두 가지:

- `AMat` 은 처음에 **삼중대각 행렬**인데, 거듭제곱할 때마다 0이 아닌 대각선이
  2줄씩 늘어납니다. 코드가 이 구조를 알고 불필요한 곱셈을 건너뜁니다.
- 수렴 판정을 `DPLimit = 1.0e-20` 으로 두고, 더 높은 거듭제곱이 합에 영향을 못 주는
  시점에 루프를 끊습니다(배정도 실수 유효숫자 16자리 + 안전계수 기반).

재미있는 구현 세부 — 함수 헤더 주석:

> *"A conversion from SI units to English units is made due to concerns about
> round off problems noted in earlier version of this subroutine."*

**CTF 계산 내부에서만 SI → 야드파운드 단위로 변환**합니다. 반올림 오차 때문입니다.
EnergyPlus는 전체가 SI 단위인데 이 한 곳만 예외이고, `DataConversions::CFL` 같은
변환 상수가 쓰입니다(`Construction.cc:389`). 1990년대 수치 문제의 화석입니다.

## 6-7. 계산 결과 자기검증

```cpp
constexpr Real64 MaxAllowedCTFSumError(0.01);   // 1%
```

$X$, $Y$, $Z$ 급수의 총합(`SumXi`, `SumYi`, `SumZi`)은 물리적으로 모두
**정상상태 열관류율(1/R)과 같아야 합니다.** 세 합의 차이가 1%를 넘으면 계수가 유효한
정상상태 해를 주지 못한다고 보고 경고합니다.

이것이 **CTF 계수의 자기검증 장치**입니다.
결과 검증 로직을 만들 때 `.err` 또는 DB `Errors` 테이블에서 이 경고를 체크하십시오.

## 6-8. CTF를 쓰면 안 되는 경우

CTF는 **선형 시불변 시스템**을 가정합니다. 재료 물성($k$, $\rho$, $c_p$)이 **상수**여야
합니다. 그래서 다음은 CTF로 풀 수 없습니다.

| 상황 | 왜 안 되나 | 대안 |
|---|---|---|
| **상변화재료(PCM)** | 상변화 시 비열이 온도에 따라 급변 | `ConductionFiniteDifference` |
| **온도 의존 열전도율** | 물성이 상수가 아님 | `ConductionFiniteDifference` |
| **습기 이동 동시 해석** | 열-습기 연립 문제 | `CombinedHeatAndMoistureFiniteElement` |
| 벽체 내부 온도분포가 필요할 때 | CTF는 내부 온도를 계산하지 않음 | `ConductionFiniteDifference` |

IDF에서 `HeatBalanceAlgorithm` 객체로 선택합니다(IDD 실측 선택지).

| 선택지 | 비고 | 공식 문서 |
|---|---|---|
| `ConductionTransferFunction` | **기본값**. 빠름 | EngRef p63 |
| `MoisturePenetrationDepthConductionTransferFunction` | EMPD. 표면 습기 완충만 간이 반영 | EngRef p85 |
| `ConductionFiniteDifference` | CondFD. PCM 가능, 느림 | EngRef p73 |
| `CombinedHeatAndMoistureFiniteElement` | HAMT. 열+습기 완전 연립 | EngRef p80 → [`08`](08-심화-기밀성능과결로.md) |

> **패시브하우스 검토 주의**: 내단열 구조의 결로·곰팡이 위험을 보려면 CTF로는 부족합니다.
> HAMT나 최소한 EMPD가 필요합니다. → [`08-심화-기밀성능과결로.md`](08-심화-기밀성능과결로.md)

## 6-9. "R값만"(No-Mass) 층 처리

`Material:NoMass` 로 단열재를 R값만 입력하는 경우가 많습니다.
EnergyPlus의 처리가 **위치에 따라 다릅니다.**

| 위치 | 처리 |
|---|---|
| **구성체 중간** | 노드 1개. $\rho = 0$, $c_p = 0$, $k = 1$, 두께 = R값으로 치환. **열용량 없이 저항만 추가** |
| **최내측 또는 최외측** | **공기 물성으로 변환해 가짜 열용량을 부여** |
| no-mass 층이 연달아 있을 때 | 내부적으로 하나로 합침 (0으로 나누기 방지) |

최외측·최내측을 예외 처리하는 이유 — 공식 문서 설명:

> *"A little or no mass layer there could receive intense thermal radiation from internal
> sources or the sun causing the temperature at the inner or outer surface to achieve very
> high levels... likely unrealistic from a real-world perspective."*

열용량이 0인 층이 표면에 있으면 일사나 내부 복사를 받는 순간 온도가 비물리적으로
치솟아 시뮬레이션이 종료될 수 있습니다. 그래서 가짜 열용량을 넣어 막습니다.

> **실무 지침**: 외단열(EIFS) 모델링에서 외측 단열재를 `Material:NoMass` 로 넣으면
> EnergyPlus가 **몰래 공기 물성을 부여**합니다. 외피 표면온도가 중요한 검토
> (하자 분석, 결로)라면 `Material` 로 밀도·비열까지 제대로 입력하십시오.

## 6-10. 직접 확인하는 방법

계산된 CTF 계수를 눈으로 볼 수 있습니다. IDF에 이 객체를 넣으면 `.eio` 에 출력됩니다.

```idf
Output:Constructions,
  Constructions,
  Materials;
```

(근거: `HeatBalanceManager.cc:6429` 의 `ScanForReports(state, "Constructions", ...)`
→ `reportTransferFunction()` 호출)

출력 내용: 구성체별 전체 물성, 재료층 목록, **CTF 계수 $X_j$, $Y_j$, $Z_j$, $\Phi_j$
전체와 CTF 타임스텝·항 수**.

**알고리즘 검증을 하신다면 이것부터 하십시오.** 관심 있는 벽체 하나를 골라 `.eio` 에서
CTF 계수와 타임스텝을 확인하면, 그 벽체가 지정한 타임스텝으로 돌고 있는지(6-5절)
즉시 알 수 있습니다.

---

## 요약 — 기억할 것 3가지

1. **CTF는 시뮬레이션 전에 한 번만 계산**되고, 벽체 내부 온도는 아예 풀지 않습니다.
   그래서 빠릅니다.
2. **두꺼운 축열체는 타임스텝을 조용히 늘립니다.** `Timestep` 을 짧게 줘도 그 벽체는
   긴 주기 + 보간으로 전환됩니다. ZEB·콘크리트 구조에서 `.eio` 로 반드시 확인하십시오.
3. **PCM·습기·가변물성은 CTF로 불가능합니다.** `HeatBalanceAlgorithm` 을 바꿔야 하고,
   입력 부담과 계산 시간이 크게 늘어납니다.
