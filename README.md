# A-Value-Screen v0.1

A股基本面量化筛选引擎。它不是低 PE/低 PB 选股器，也不是单纯的财务质量排名器，而是两套相互独立、逐层收窄的引擎：

1. **基本面质量引擎**：商业质量、财务健康、盈利质量与初步估值，硬性淘汰 + 100 分评分。
2. **错杀与内在价值引擎**：价格与基本面背离、市场隐含预期、事件证据、三情景 DCF 与安全边际。

系统严格区分四种状态：`PASS`、`REVIEW_REQUIRED`、`REJECT`、`INSUFFICIENT_DATA`。

**硬规则：** 高分不能覆盖硬性淘汰；数据不足不能按 0 分处理后继续排名；“股价跌得多”不能单独构成错杀证据。

完整规则、公式与完成定义见仓库根目录的 [`SPEC.md`](SPEC.md)。

---

## 安装

需要 Python 3.11+。

```bash
cd A-Value-Screen
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

常用依赖已在 `pyproject.toml` 中钉死版本：`pydantic`、`PyYAML`、`pandas`、`numpy`、`click`、`python-dateutil`；开发依赖为 `pytest`。

安装后提供命令行入口 `avalue-screen`。

```bash
pytest
# 当前仓库应看到 118 passed
```

---

## 如何准备输入数据

v0.1 **不依赖任何实时行情或财务 API**，也不需要同花顺 HiThink 密钥。测试与示例全部走本地 JSON / CSV。数据适配器与筛选规则是隔离的，后续若要接 HiThink / DuckDB，只需实现同样的标准字段，不必改评分或估值逻辑。

### 推荐：单个 `universe.json`

```text
{
  "data_source": "my-local-extract",
  "data_version": "2026-11-10",
  "final_vintage_only": false,
  "historical_versions_available": true,
  "trading_days": [],          # 可选；缺省为工作日减去 holidays
  "holidays": [],
  "risk_free_curve": [
    {"rate_date": "2026-11-01", "yield_10y": 0.025, "source": "cn_gov_10y"}
  ],
  "companies": [ /* 见下 */ ]
}
```

每家公司至少包含：

- 身份字段：`security_id`、`symbol`、`exchange`、`company_name`
- `statements[]`：各报告期的利润表 / 资产负债表 / 现金流量表。金额统一为人民币元，比率用小数（10% 存 `0.10`）。每个报表应带 `report_period`、`announcement_datetime`（含时区，建议精确到时间）、`available_date`、`statement_type`（`Q1` / `H1` / `Q3` / `ANNUAL`）、`version_id`、`version_chain`、`is_restatement`
- `market.prices[]`：含 `price_date`、未复权价、复权价、总回报指数（如有）、成交额、总股本 / 稀释股本
- 可选：`audit_opinions`、`governance_events`、`evidence`（错杀证据，结构见 SPEC 6.6）、`impairment_checks`（永久性损伤八项清单）
- 行业与标记：`industry` / `sector` / `is_financial` / `is_cyclical` / `listing_date` / `levered_beta` / `reasonable_ev_ebit`

字段别名可通过 `configs/field_mapping.yaml` 映射到标准名字。金额单位如 `万元`、`percent` 会在加载时换算；币种必须是 CNY，否则报错停止，不会偷偷混算。

### 目录输入

`--input` 也可以是目录：

- `universe.json`；或
- `companies/*.json` + 可选的 `meta.json` / `risk_free_curve.csv` / `trading_days.csv`；或
- CSV 组合：`securities.csv` + `statements.csv`（长表，每行一个字段）+ 可选 `prices.csv`

合成样本宇宙在 `tests/fixtures/universe/universe.json`，可由 `python tests/fixtures/generate_fixtures.py` 重新生成。

---

## 如何运行

```bash
# 当前筛选（以 as-of 日已公告、已可使用的版本）
avalue-screen run \
  --as-of 2026-11-10 \
  --mode current_screen \
  --config configs/default.yaml \
  --input tests/fixtures/universe/universe.json \
  --output outputs/2026-11-10/

# 严格历史 PIT 回测。若缺少公告时点或历史版本，必须报错停止，
# 不会静默回退到最终版财报。
avalue-screen run \
  --as-of 2021-06-30 \
  --mode historical_pit_backtest \
  --config configs/default.yaml \
  --input data/ \
  --output outputs/2021-06-30/

# 最终版财报研究。所有输出强制标记 pit_status=NON_PIT_FINAL_VINTAGE，
# 不得据此声称历史策略可交易或回测无前视偏差。
avalue-screen run \
  --as-of 2026-11-10 \
  --mode final_vintage_research \
  --config configs/default.yaml \
  --input data/ \
  --output outputs/final-vintage/

# 查看单家公司的完整解释档案（可读已有输出目录，或当场重跑）
avalue-screen explain \
  --symbol 600001.SH \
  --as-of 2026-11-10 \
  --input outputs/2026-11-10/
```

每次运行至少写出：

1. `screen_summary.csv` — 全部股票、状态、五维分数
2. `candidate_list.csv` — 正式候选（仅 `PASS` 且评级达到配置的最低档，默认 A/B）
3. `rejected_list.csv` — 硬性淘汰及规则 ID
4. `review_queue.csv` — 需人工复核
5. `insufficient_data.csv` — 数据不足及缺失字段
6. `company_profiles/{symbol}.json` — 单家完整指标、估值与解释
7. `data_quality_report.json`
8. `run_manifest.json` — 代码版本、参数版本、as-of、输入文件哈希、运行时间

排序优先级为：`PASS` → 评级 A 先于 B → MOS 从高到低 → 商业质量分 → 数据质量等级。`REVIEW_REQUIRED` 与 `INSUFFICIENT_DATA` 不会进入正式候选榜。

所有阈值都在 `configs/default.yaml`（以及可选的 `configs/sector_overrides.yaml`）中，业务逻辑不散落硬编码。默认参数是 v0.1 初始研究值，不是跨行业普适真理。

### 股票池

项目默认只纳入沪深主板普通 A 股（代码前缀 `600/601/603/605/000/001/002`），剔除创业板、科创板和北交所，以对齐纸面账本约束。可用配置控制：

- `universe.main_board_only`：默认 `true`。设为 `false` 时恢复 SPEC 的全市场普通 A 股（主板、创业板、科创板，以及可选北交所）
- `universe.include_bse`：在 `main_board_only` 为 `false` 时，是否纳入北交所（默认 `true`）

金融行业默认排除。ST / 退市风险 / 筛选日停牌且无成交价的证券按配置排除。历史回测不会因为后来退市而从当时的股票池里删掉该公司。

---

## 示例输出

`outputs/examples/current_screen_2026-11-10/` 是对合成样本宇宙、`as_of=2026-11-10`、`current_screen` 的一次完整运行结果，可直接打开查看。其中：

| 证券 | 状态 | 评级 | 说明 |
|---|---|---|---|
| `GOOD_CHEAP` | PASS | A | 质量高、价格显著低于保守价值，进入正式候选 |
| `GOOD_EXPENSIVE` | PASS | C | 质量同样高，但价格偏贵，不得进入 A 类，也不进入正式候选 |
| `CHEAP_TRAP` | REVIEW_REQUIRED | REVIEW | 低 PE 但 ROIC 下滑、现金转化差、应收存货恶化 |
| `STRUCTURAL_DECLINE` | REVIEW_REQUIRED | REVIEW | 基本面长期恶化；股价大跌不被判为错杀 |
| `CYCLICAL_TROUGH` | REVIEW_REQUIRED | REVIEW | 使用中周期盈利，不会只因当期 PE 偏高被硬性淘汰 |
| `DILUTION_TRAP` | REVIEW_REQUIRED | REVIEW | 利润增长但股本大幅扩张、每股价值不改善 |
| `NEGATIVE_FCF_GROWTH` | REVIEW_REQUIRED | REVIEW | 成长性资本开支导致短期负 FCF，进入复核而非自动认定造假 |
| `MISSING_DATA` | INSUFFICIENT_DATA | INSUFFICIENT_DATA | 关键字段缺失，不会被填 0 后排名 |
| `RESTATED_DATA` | PASS | B | 用于 PIT 回归：更正公告前只能看到旧版本 |

---

## 已知限制与实现假设

1. **A 类是研究优先级标签，不是自动买入信号。** 买入仍需结合投资期限、组合风险预算、流动性、行业集中度和具体价格。
2. **`NON_PIT_FINAL_VINTAGE` 的结果不能当作可交易历史回测。** 只有 `historical_pit_backtest` 且数据含公告时点与历史版本时，才是严格 PIT。缺少历史版本时该模式会直接报错停止，绝不静默改用最终版数据。
3. **金融企业（银行、保险、证券及其他金融）不在 v0.1 范围内**，不能套用工业企业的 ROIC / FCF / DCF 规则。
4. **系统不自动给出买卖指令或仓位建议**，也不会把未经核实的文本（包括模型生成的行业判断）当成事实字段。
5. **交易日历**：若输入未提供官方交易日列表，则使用“工作日减去 `holidays`”的近似日历。精确的上交所/深交所休市日需要自行传入 `trading_days`。
6. **盘后公告**：有精确时间时，15:00（Asia/Shanghai）及以后从下一交易日生效；只有公告日期、没有钟点时，保守地从下一交易日开始使用。
7. **反向 DCF 在搜索区间 `[-10%, +20%]` 无根时**，不把边界值当作真实解（`BELOW_SEARCH_RANGE` / `ABOVE_SEARCH_RANGE`）。若市值低于区间下沿，评分仅使用“隐含增长率至少低于搜索下沿”这一不等式下界，不会把 `-10%` 写成已求解的 `g_implied`。
8. **正式候选名单的最低评级**：SPEC 写的是“通过质量和估值初筛的候选”。本实现保守地将 `C`（观察名单）排除在 `candidate_list.csv` 之外，只保留 A/B，可用 `screening.candidate_min_grades` 修改。
9. **沪深主板前缀**：`main_board_only` 默认按用户指定的 `600/601/603/605/000/001/002` 过滤。深圳主板 `003` 未默认计入，如需纳入请改 `universe.main_board_prefixes`。
10. **同行估值**：若公司未提供 `reasonable_ev_ebit` / `peer_ev_ebit_median`，且未打开 `valuation.allow_default_reasonable_ev_ebit`，则 EV/EBIT 折价子项记为缺失，不会发明一个同行样本。
11. **定性子项**（利润率质量、韧性、再投资效率、营运资本质量、审计治理、错杀可逆性等）由配置阈值驱动的结构化状态机打分，不从自由文本推断。没有经确认的错杀证据时，不会仅因股价下跌给出高错杀分，也无法进入 A 类。
12. **周期股中周期盈利**：在 `is_cyclical=true` 或行业命中 `configs/sector_overrides.yaml` 中的周期列表时，标准化盈利使用近五年完整年度中位数，而不是简单地平均或只用当期 TTM。
13. **默认 DCF 预测规则**（收入增速上下限、利润率情景加减、再投资上限等）都是 v0.1 起点，全部来自配置，需要按行业检验后再用于正式研究。
14. **缺少折旧摊销、资本开支或营运资本时，DCF 降级**，不会把结果标成高可信，也不会用低 PE 分数顶替估值维度。
15. **异常值保留原值**，只打标；winsorize 只作为辅助字段，不会覆盖原始序列。缺失字段不会被静默填 0，重述不会覆盖历史版本。

如对 SPEC 某处有两种读法，本仓库取偏保守的解释，并尽量做成可配置项。实现过程中不会编造真实财务数字；示例数据全部是合成夹具。
