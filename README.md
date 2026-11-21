# 统计区改划 · 新旧区人数精确换算服务

区划调整后，历年按旧区公布的人数需要换算到新区。本服务用两个独立的
FastAPI 实例完成这件事，全程使用有理数精确运算（`fractions.Fraction`），
不使用浮点数。

服务同时承载两代工作流：

- **v1（既有）**：构包端 `/build` 产出代数关系交接包，换算端 `/convert`
  依据当期有数的旧区人数精确推算新区人数；
- **v2（溯源）**：迁库后每期同时保留旧界、新界两套汇总，且整批共用的
  区划版号丢失。构包端 `/trace` 按版号产出“溯源包”，换算端 `/identify`
  用 2～6 个候选包与 1～6 期报表，列出能解释整批报表的全部版号。

## 数学模型

设街块人数为未知量 x，0/1 关联矩阵：

- `A`（街块 × 旧区）：旧区人数 `p = A·x`
- `B`（街块 × 新区）：新区人数 `q = B·x`
- 同一街块可同时属于多个区（矩阵列允许重叠）

由此得到两类精确代数关系：

1. **旧区之间的等式**：`ker(A)` 中的每个向量 k 满足 `k·p = 0`；
2. **新区对旧区的表达式**：对每个新区列 bⱼ 解 `A·c = bⱼ`，
   有解则 `qⱼ = c·p`，系数为精确分数（如 `1/2`、`-3`）。

### v2 的整数可行性判别

溯源包不保留任何街块编号，只记录街块**实际出现的覆盖组合**——每个组合
是一对（旧区号集合，新区号集合）。于是某一期报表能否被某版号解释，等价
于如下非负整数线性方程组是否有解：

```
A·y = b,   y_k ≥ 0 且为整数
```

其中每个变量 `y_k` 对应该版的一个覆盖组合（即呈现该组合的街块人数之
和），`A` 是组合对各上报区的 0/1 关联矩阵，`b` 是该期旧、新两侧实际上
报的合计。各期街块人数可以不同，因此逐期独立判别；缺报的区不进入方程、
不设约束。本服务用精确有理数两阶段单纯形求解线性松弛，再以分支定界
（floor/ceil 分支 + 整数界传播）判定整数可行性，全程无浮点数。

## 两个实例

| 实例 | 端口 | 接口 | 职责 |
|---|---|---|---|
| package-builder | 8001 | `POST /build`、`POST /trace`，文档 `/docs` | `/build` 输出 v1 代数关系包；`/trace` 按版号输出 v2 溯源包 |
| converter | 8002 | `POST /convert`、`POST /identify`，文档 `/docs` | `/convert` 消费 v1 包；`/identify` 用候选溯源包与多期报表识别版号 |

### v1 交接包内容（不含任何街块编号或逐块归属）

```json
{
  "schema_version": 1,
  "old_districts": ["O1", "O2"],
  "new_districts": ["N1"],
  "old_relations": [{"O1": 1, "O2": -1}],
  "new_district_relations": [
    {"code": "N1", "derivable": true, "coefficients": {"O1": "1/2", "O2": "1/2"}}
  ]
}
```

- `old_relations`：旧区人数必须满足的精确线性等式（加权和为 0），
  整数系数，已约分为本原向量；
- `new_district_relations`：每个新区一个条目，`derivable=false` 表示
  覆盖关系无法用旧区精确表达，`coefficients` 为 `"p/q"` 形式的精确分数
  （整数写作 `"2"`）。

### v2 溯源包内容（不含任何街块编号或逐块归属）

```json
{
  "schema_version": 2,
  "version_code": "2024-Q3",
  "old_districts": ["O1", "O2", "O3"],
  "new_districts": ["N1", "N2"],
  "coverage_combinations": [
    {"old_codes": ["O1"], "new_codes": ["N1"]},
    {"old_codes": ["O1", "O2"], "new_codes": ["N1"]},
    {"old_codes": ["O2"], "new_codes": ["N1", "N2"]},
    {"old_codes": ["O3"], "new_codes": ["N2"]}
  ]
}
```

- `version_code`：该溯源包对应的区划版号；
- `coverage_combinations`：街块实际出现的（旧区号集合，新区号集合）
  覆盖组合，去重后保留，每版最多 64 种；
- 包内只有两侧区号与覆盖组合，没有街块编号，也没有逐块归属。

#### `/trace` 的输入与限制

请求体在 v1 的新旧区街块清单之外增加 `version_code`，街块清单格式与
`/build` 完全相同。构包端按街块聚合出覆盖组合后即丢弃街块编号。约束：

- 旧区与新区合计最多 12 个；
- 每版覆盖组合最多 64 种。

### `/identify` 的输入、限制与结论

请求体：

```json
{
  "packages": [ /* 2~6 个 v2 溯源包，版号各异 */ ],
  "reports": [
    {
      "period": "2021",
      "old_populations": {"O1": 30, "O2": 50, "O3": 40},
      "new_populations": {"N1": 60, "N2": 70}
    }
  ]
}
```

限制与口径：

- 候选包 2～6 个、报表 1～6 期；
- 各包版号必须不同；所有包的旧区号集合必须相同，新区号集合同理；
- 两侧合计最多 12 个区，每版最多 64 种覆盖组合；
- 实报人数为 0～1,000,000 的整数；缺报区不出现在映射中，不设约束；
- 同一期旧、新两侧实报合计必须能由**同一套**非负整数街块人数得到，
  各期街块人数可以不同。

响应给出每个候选版号是否相容及不相容期的零基下标，并给出整批结论：

```json
{
  "status": "unique_compatible",
  "compatible_versions": ["2024-Q3"],
  "results": [
    {"version_code": "2024-Q3", "compatible": true, "incompatible_periods": []},
    {"version_code": "2024-Q1", "compatible": false, "incompatible_periods": [0]}
  ]
}
```

`status` 取值：`unique_compatible`（唯一相容）、`multiple_compatible`
（多版相容）、`no_compatible_version`（无相容版）。输入不合规或计算失败
时只返回错误体，不返回任何部分结果。

### v1 换算规则（converter `/convert`）

- **可算范围只按本次提交的旧区数据判定**：仅当新区表达式能（借助交接包
  内的旧区关系）改写为只含本次有数旧区时才计算，其余新区列入 `missing`，
  并注明 `not_expressible_from_old_districts` 或
  `insufficient_known_old_districts`；
- 本次有数旧区所支撑的每条旧区等式都会实际校验，结果放在
  `checked_relations`；等式涉及无数据旧区时自动跳过；
- 旧区人数必须是非负整数（请求体 schema 强制）；
- **整次失败**（HTTP 400，只返回错误体）的情形：
  - 旧区人数违背覆盖关系推出的等式；
  - 任一新区换算结果为负数；
  - 任一新区换算结果不是整数（分数系数导致的半整数等）。

成功响应示例：

```json
{
  "available_old_districts": ["O1", "O2"],
  "checked_relations": [],
  "computed": [
    {"code": "N1", "status": "computed", "population": 8,
     "coefficients": {"O1": "1/2", "O2": "1/2"},
     "expression": "1/2*O1 + 1/2*O2", "missing_reason": null}
  ],
  "missing": [],
  "results": [ /* computed 与 missing 的完整列表，顺序与新区一致 */ ]
}
```

## Docker Compose 启动

```bash
docker compose up --build
```

- 交接包服务（含 Swagger 交互文档）：<http://localhost:8001/docs>
- 换算服务（含 Swagger 交互文档）：<http://localhost:8002/docs>
- 健康检查：`GET /health`

## 本地运行与测试

```bash
pip install -r requirements.txt
pip install pytest httpx
uvicorn app.builder_app:app --port 8001
uvicorn app.converter_app:app --port 8002
pytest -q
```

## 典型调用流程

### v1（既有包，继续可用）

1. 在 `http://localhost:8001/docs` 的 `POST /build` 提交新旧区及街块覆盖，
   保存返回的交接包 JSON；
2. 在 `http://localhost:8002/docs` 的 `POST /convert` 提交该交接包与
   当期已有旧区人数（非负整数），当次得到可精确推出的新区人数、
   带分数系数的算式，以及缺资料新区列表。

### v2（版号溯源）

1. 对每个候选区划版号，在 `http://localhost:8001/docs` 的 `POST /trace`
   提交 `version_code` 与原格式的新旧区街块清单，当次得到该版的溯源包
   （只含覆盖组合，不含街块编号）；
2. 在 `http://localhost:8002/docs` 的 `POST /identify` 提交 2～6 个候选
   溯源包与 1～6 期报表，当次得到能解释整批报表的全部版号，以及
   唯一相容 / 多版相容 / 无相容版的整批结论。

## 目录结构

```
app/
  rationals.py      # 精确有理线性代数（RREF、零空间、线性方程组，v1）
  expressions.py    # 分数与算式格式化（v1）
  models.py         # Pydantic 请求/响应模型与校验（v1 + v2）
  engine.py         # v1 交接包生成与换算的纯领域逻辑
  ilp.py            # 精确整数可行性：界传播 + 两阶段单纯形 + 分支定界（v2）
  trace_engine.py   # v2 溯源包生成与版号识别的纯领域逻辑
  builder_app.py    # 实例 1：/build 与 /trace
  converter_app.py  # 实例 2：/convert 与 /identify
tests/              # 端到端测试（TestClient），含 v1 与 v2 两套
Dockerfile
docker-compose.yml
```
