# A股基本面量化筛选引擎 Spec v0.1

> **项目代号：** A-Value-Screen  
> **版本：** 0.1.0  
> **文档状态：** 可进入实现  
> **目标用户：** A股基本面研究与价值投资研究  
> **核心目标：** 从A股上市公司中筛选出商业质量较好、财务稳健、盈利质量可靠、内在价值显著高于当前价格，并有证据支持“市场可能过度悲观”的公司。

---

## 0. 实施摘要

本系统不是低PE/低PB选股器，也不是单纯的财务质量排名器。它由两个相互独立、逐层收窄的引擎组成：

1. **基本面质量引擎（Quantitative Fundamental Filter）**：根据历史财务数据计算商业质量、财务健康、盈利质量与初步估值指标，执行硬性淘汰和基础评分。
2. **错杀与内在价值引擎（Mispricing & Intrinsic Value Engine）**：结合股价变化、经营基本面背离、市场隐含预期、事件证据和情景DCF，判断低估是否可能来自短期冲击或过度悲观，而不是永久性价值损伤。

系统必须严格区分以下结果：

- `PASS`：数据与规则满足，可以进入下一阶段。
- `REVIEW_REQUIRED`：发现风险或证据不足，需要人工复核；不得自动标记为投资候选。
- `REJECT`：触发明确的硬性排除条件。
- `INSUFFICIENT_DATA`：关键字段缺失、历史长度不足或数据质量不合格，不能作出可靠判断。

**重要：高分不能覆盖硬性淘汰；数据不足不能按0分处理后继续排名；“股价跌得多”不能单独构成错杀证据。**

### 0.1 v0.1范围

- 支持沪深北交易所普通A股，默认排除银行、保险、证券及其他金融类公司；金融企业需要独立的金融机构估值模型，不套用工业企业ROIC/FCF规则。
- 支持按指定 `as_of_date` 进行当前筛选或历史时点筛选。
- 以季度财务数据为底层，派生TTM、年度和多年度指标。
- 支持五年财务质量窗口；至少三年数据可生成“观察级”结果，但不能进入最高评级。
- 支持标准化盈利、FCF Yield、EV/EBIT、三情景DCF和反向DCF。
- 输出可解释的公司档案、指标明细、淘汰原因、评分依据、数据质量报告和候选名单。

### 0.2 v0.1不做的事情

- 不自动形成最终买卖指令或仓位建议。
- 不把LLM生成的行业判断、竞争优势判断或事件解释直接当作事实字段。
- 不在缺少历史版本数据时伪造历史PIT财务数据。
- 不把所有行业强行放在同一套ROIC、FCF、估值阈值下比较。
- 不将管理层指引、券商预测或新闻叙述当成已实现财务事实。

---

## 1. 核心设计原则

1. **先质量，后价格。** 先判断公司是否具有长期价值，再判断价格是否足够便宜。
2. **每股价值优先。** 关注每股标准化盈利、每股FCF和每股内在价值，显式处理增发、回购和股权稀释。
3. **现金流验证利润。** 净利润必须与经营现金流、资本开支和营运资本变化交叉验证。
4. **标准化周期盈利。** 周期股不能机械使用景气高点或低点的单年利润估值。
5. **错杀必须有证据。** 价格下跌只是触发研究的信号；必须证明基本面长期价值没有同比例受损，并识别市场悲观预期。
6. **估值使用情景分布。** 不输出单一“精确内在价值”，至少保留Bear/Base/Bull三种情景和关键假设。
7. **PIT优先，版本可追溯。** 历史时点只能使用当时已经公开的信息版本。
8. **风险与缺失分开。** 风险是公司问题；缺失是数据问题。两者必须使用不同状态码。
9. **可解释、可复算。** 每个指标必须保留原始字段、计算公式、数据版本、窗口和异常处理记录。
10. **行业适配。** v0.1先覆盖非金融企业；行业特殊规则通过配置与独立模型扩展，不使用隐式例外。

---

## 2. 运行模式与配置

### 2.1 运行模式

```yaml
run_mode: current_screen       # current_screen | historical_pit_backtest | final_vintage_research
as_of_date: "YYYY-MM-DD"
financial_data_mode: strict_pit # strict_pit | final_vintage
universe: cn_a_share_all
```

| 模式 | 允许用途 | 必须满足的条件 |
|---|---|---|
| `current_screen` | 以当前可获得的最新财报筛选当前候选股 | 财报公告日期不晚于筛选日；使用当前可见版本 |
| `historical_pit_backtest` | 历史日期模拟筛选结果与后续收益 | 必须有公告时点、版本号/修订记录和历史可见版本；不得使用最终重述数据替代历史版本 |
| `final_vintage_research` | 使用数据库当前保存的最终版财报做横截面研究 | 输出必须标记 `NON_PIT`；禁止据此声称历史策略可交易或回测无前视偏差 |

**关键数据约束：** 如果数据源只有最终版财务报表，没有历史公告版本或修订历史：

- 可以运行当前筛选；
- 可以做最终版历史财务特征研究；
- 不得运行严格历史PIT回测；
- 结果中必须显示 `pit_status = NON_PIT_FINAL_VINTAGE`；
- 不得将最终版数据回填到历史公告时点。

### 2.2 默认参数

```yaml
screening:
  min_listing_years_for_full_grade: 5
  min_listing_years_for_observation: 3
  financial_history_years: 5
  ttm_quarters: 4
  exclude_st: true
  exclude_delisting_risk: true
  exclude_financial_sectors: true
  exclude_suspended_on_as_of_date: true
  min_daily_turnover_cny: 10000000   # 可配置；只作流动性门槛，不作为企业质量分

quality_gates:
  min_5y_positive_roic_years: 4
  min_5y_adjusted_net_income_sum: 0
  min_5y_fcf_sum: 0                 # 作为质量门槛/复核项，不能单独机械淘汰成长型企业
  max_net_debt_to_ebitda_for_A: 2.0
  hard_net_debt_to_ebitda_review: 4.0
  min_interest_coverage_review: 1.5
  min_interest_coverage_preferred: 4.0
  min_cash_conversion_preferred: 0.8
  min_cash_conversion_strong: 1.0

valuation:
  forecast_years: 5
  risk_free_rate_source: cn_gov_10y_yield_pit
  equity_risk_premium: 0.055
  beta_fallback: 1.0
  wacc_floor: 0.08
  wacc_cap: 0.14
  terminal_growth_default: 0.02
  terminal_growth_cap: 0.025
  terminal_spread_min: 0.03        # WACC至少高于永续增长率3个百分点
  scenario_probabilities:
    bear: 0.25
    base: 0.50
    bull: 0.25
  conservative_value_weights:
    bear: 0.60
    base: 0.40

mispricing:
  price_drawdown_trigger_12m: -0.20
  price_drawdown_strong_12m: -0.30
  max_fundamental_decline_for_strong_divergence: 0.10
```

所有阈值必须放在配置文件中，代码中禁止散落硬编码。默认阈值是v0.1初始研究参数，不代表跨行业普适真理；上线前必须通过行业分组与历史样本检验。区间统一采用“左闭右开”口径，例如`12–15%`表示`12% <= x < 15%`；最高档包含上限边界，最低档包含其余更低值。

### 2.3 股票池规则

默认纳入：沪深北交易所普通A股，包括主板、创业板、科创板和北交所；是否纳入北交所可配置。

默认排除：

- B股、基金、ETF、债券、优先股、存托凭证等非普通A股证券；
- `ST`、`*ST`、退市整理期及已确定退市风险证券；
- 筛选时点停牌且无法取得可交易价格的股票；
- 银行、保险、证券及其他金融行业；
- 上市不满3年的公司不进入正式候选池；上市3–5年的公司仅允许标记为 `PROVISIONAL`，最高评级为B类；
- 历史回测不得因为股票后来退市而将其从历史股票池删除，必须保留当时可见的退市公司，避免幸存者偏差。

---

## 3. 数据模型与字段字典

### 3.1 统一字段约定

每个原始字段至少保存：

```text
security_id             证券唯一标识，不以股票简称作为主键
symbol                  股票代码
exchange                交易所
report_period           财报期末日期
announcement_datetime   公告时间（含时区，优先精确到时间）
available_date          信息可用于交易/筛选的日期
statement_type          ANNUAL | Q1 | H1 | Q3 | Q4
version_id              数据版本/修订版本
is_restatement          是否重述
source                  数据来源
source_field             来源字段名
currency                 CNY
unit                     yuan | thousand_yuan | ten_thousand_yuan | share | percent | day
value                    标准化数值
```

金额统一转换为人民币元；股数统一为股；比率使用小数存储（10%存为0.10）。不得混用“元、万元、百万元”。

### 3.2 利润表原始字段

| 标准字段 | 中文口径 | 用途/说明 |
|---|---|---|
| `revenue` | 营业收入 | 收入规模、增长、利润率分母 |
| `cost_of_revenue` | 营业成本 | 毛利率、存货周转 |
| `selling_expense` | 销售费用 | 费用率分析 |
| `admin_expense` | 管理费用 | 费用率分析 |
| `rd_expense` | 研发费用 | 费用率及研发投入分析；不得未经核验直接与资本化开发支出混算 |
| `finance_expense` | 财务费用 | 辅助分析；不等于利息费用 |
| `interest_expense_gross` | 利息费用/利息支出（总额） | 利息保障倍数、EBIT调整；必须与净财务费用区分 |
| `interest_income` | 利息收入 | EBIT调整时使用 |
| `operating_profit` | 营业利润 | EBIT派生的基础字段 |
| `non_operating_income_net` | 营业外收支净额 | 识别非经营项目 |
| `profit_before_tax` | 利润总额 | 税率、EBIT交叉检查 |
| `income_tax_expense` | 所得税费用 | 有效税率 |
| `net_income` | 净利润 | 合并口径利润 |
| `net_income_parent` | 归母净利润 | 股东盈利 |
| `net_income_ex_nonrecurring_parent` | 扣非归母净利润 | 核心盈利质量指标 |
| `nonrecurring_profit_parent` | 归母非经常性损益净额 | 与扣非归母净利润核对 |
| `depreciation_amortization` | 折旧与摊销 | EBITDA、FCFF |
| `investment_income` | 投资收益 | 识别非主营收益；行业规则可覆盖 |
| `fair_value_change` | 公允价值变动收益 | 识别非主营/非现金项目 |
| `asset_disposal_gain` | 资产处置收益 | 识别一次性收益 |

若数据供应商已提供标准化 `EBIT_adjusted`，必须保留供应商定义和来源；若没有，则按3.6节派生。不得把“利润总额”直接当作EBIT。

### 3.3 资产负债表原始字段

| 标准字段 | 中文口径 | 用途/说明 |
|---|---|---|
| `cash_and_equivalents` | 货币资金/现金及现金等价物 | 净负债、短债覆盖；受限资金需单列 |
| `restricted_cash` | 受限货币资金 | 不得全部视为可自由偿债现金 |
| `trading_financial_assets` | 交易性金融资产 | 可配置是否作为超额现金扣减；默认不直接全部扣减 |
| `accounts_receivable` | 应收账款 | 营运资本、回款质量 |
| `notes_receivable` | 应收票据 | 应收款项扩展口径 |
| `accounts_receivable_financing` | 应收款项融资 | 按可回收性纳入扩展应收款口径 |
| `contract_assets` | 合同资产 | 收入确认与回款质量 |
| `inventory` | 存货 | 库存质量、周转天数 |
| `accounts_payable` | 应付账款 | 营运资本 |
| `notes_payable` | 应付票据 | 是否属于有息融资需按具体票据性质映射 |
| `contract_liabilities` | 合同负债 | 客户预收款，营运资本 |
| `total_assets` | 总资产 | 资产结构检查 |
| `total_liabilities` | 总负债 | 杠杆辅助分析 |
| `total_equity` | 所有者权益合计（含少数股东权益） | ROIC投入资本 |
| `equity_parent` | 归母股东权益 | ROE、每股指标 |
| `minority_interest` | 少数股东权益 | 估值归属调整 |
| `short_term_borrowings` | 短期借款 | 有息债务 |
| `current_portion_long_term_debt` | 一年内到期的非流动负债中的有息部分 | 有息债务 |
| `long_term_borrowings` | 长期借款 | 有息债务 |
| `bonds_payable` | 应付债券 | 有息债务 |
| `lease_liabilities_current` | 一年内到期租赁负债 | 是否计入债务需统一口径，默认计入 |
| `lease_liabilities_noncurrent` | 非流动租赁负债 | 默认计入债务 |
| `goodwill` | 商誉 | 商誉风险 |
| `intangible_assets` | 无形资产 | 资产质量辅助分析 |
| `development_expenditure_capitalized` | 资本化开发支出 | 研发资本化风险 |

**扩展应收款：** `trade_receivables = accounts_receivable + notes_receivable + accounts_receivable_financing + contract_assets`。如果某字段在数据源中已包含于其他字段，必须通过映射配置去重，禁止重复加总。

**有息债务：** `interest_bearing_debt = short_term_borrowings + current_portion_long_term_debt + long_term_borrowings + bonds_payable + lease_liabilities_current + lease_liabilities_noncurrent + other_explicit_interest_bearing_debt`。应付账款等经营性负债不计入有息债务。

### 3.4 现金流量表原始字段

| 标准字段 | 中文口径 | 用途/说明 |
|---|---|---|
| `cash_flow_operating` | 经营活动产生的现金流量净额（CFO） | 现金转化能力 |
| `cash_paid_for_capex_ppe` | 购建固定资产、无形资产和其他长期资产支付的现金 | 资本开支 |
| `cash_received_from_asset_disposal` | 处置长期资产收回的现金 | 不冲减维持性CapEx；单独记录 |
| `cash_paid_for_acquisitions` | 企业并购支付现金 | 并购投资单独分析，不与日常CapEx混为一谈 |
| `cash_paid_for_interest` | 偿付利息支付的现金 | 现金利息覆盖辅助分析 |
| `cash_paid_for_taxes` | 支付的各项税费 | 现金税负辅助分析 |
| `cash_flow_investing` | 投资活动现金流净额 | 资本分配分析 |
| `cash_flow_financing` | 筹资活动现金流净额 | 融资依赖度分析 |

`capex = abs(cash_paid_for_capex_ppe)`。数据源若将购建长期资产支付的现金以负数表达，先转换为正数金额。并购支出单列，不自动加入日常CapEx。

### 3.5 市场与公司行为字段

| 标准字段 | 中文口径 | 用途 |
|---|---|---|
| `close_unadjusted` | 未复权收盘价 | 实际价格核对；必须保存对应`price_date` |
| `close_adjusted` | 复权收盘价 | 收益率、回撤计算；必须注明前复权/后复权口径并保存`price_date` |
| `total_return_index` | 含分红再投资总回报指数（如可得） | 12个月总回报 |
| `total_shares` | 总股本 | 市值、每股价值 |
| `diluted_shares` | 稀释后股数 | 每股内在价值；缺失时使用总股本并标记 |
| `free_float_shares` | 流通股本 | 流动性辅助指标 |
| `market_cap` | 总市值 | 估值 |
| `daily_turnover_value` | 日成交额 | 流动性过滤 |
| `risk_warning_flag` | ST/*ST/退市风险标志 | 股票池过滤 |
| `listing_date` | 上市日期 | 历史长度 |
| `delisting_date` | 退市日期 | 历史股票池 |
| `suspension_flag` | 停牌标志 | 可交易性 |

### 3.6 关键派生字段与公式

所有派生指标必须在指标注册表中定义 `formula_id`、`formula_version`、输入字段、窗口和异常处理方法。

#### A. 期间转换与TTM

财报中的利润表和现金流量表常为年初至报告期末累计值，必须先转成单季度值：

- Q1单季 = Q1累计值；
- Q2单季 = H1累计值 − Q1累计值；
- Q3单季 = Q1–Q3累计值 − H1累计值；
- Q4单季 = FY累计值 − Q1–Q3累计值。

`TTM(x) = 最近连续4个单季度x之和`。

要求：同一TTM窗口中的季度必须来自同一可见版本链。若发生重述，应先按as-of日期选择各报告期当时可见版本，再执行期间转换；禁止用一个版本的全年累计值减去另一个版本的前三季度累计值。

资产负债表字段为时点值，不进行TTM求和。平均余额优先使用期初、期末余额的算术平均；季度指标可使用相邻季度均值。

#### B. 标准化经营利润与EBIT

优先级：

1. 使用数据源提供、定义明确且可追溯的 `EBIT_adjusted`；
2. 否则使用：

```text
EBIT_adjusted = operating_profit
                + interest_expense_gross
                - interest_income
                - non_operating_gains_to_exclude
```

`non_operating_gains_to_exclude` 默认包括可明确识别的重大投资收益、公允价值收益、资产处置收益及其他一次性非主营净收益；如为经常性主营业务收入或特定行业正常经营项目，不应机械剔除。每项调整必须保留明细。

若 `interest_expense_gross` 或关键非经常性项目缺失，EBIT只能标记为 `ESTIMATED`，ROIC和EV/EBIT降级，不允许伪装成精确值。

#### C. 标准税率与NOPAT

```text
ETR_t = income_tax_expense_t / profit_before_tax_t
```

仅在 `profit_before_tax_t > 0` 且有效税率处于合理区间时纳入。标准税率：

```text
normalized_tax_rate = median(valid ETR over latest 5 fiscal years)
```

将结果裁剪到配置区间 `[0, 0.35]`。有效样本少于2年时，使用行业/公司配置税率；再缺失则默认25%，并标记 `TAX_RATE_FALLBACK`。

```text
NOPAT = EBIT_adjusted * (1 - normalized_tax_rate)
```

#### D. 投入资本与ROIC

```text
interest_bearing_debt = 短期借款 + 一年内到期有息债务 + 长期借款
                         + 应付债券 + 租赁负债 + 其他明确有息债务

invested_capital = total_equity + interest_bearing_debt
                   - unrestricted_cash_and_equivalents

average_invested_capital_t = (invested_capital_t + invested_capital_t-1) / 2
ROIC_t = NOPAT_TTM_t / average_invested_capital_t
```

`unrestricted_cash_and_equivalents = max(0, cash_and_equivalents - restricted_cash)`。交易性金融资产默认不扣减，只有在确认流动性高、非经营性且未用于质押/经营周转时，才通过显式配置作为超额现金扣减。

若平均投入资本 `<= 0`，该期ROIC标记无效，不得用极端值参与评分。ROIC使用合并口径NOPAT与含少数股东权益的投入资本，避免分子分母口径不一致。

#### E. ROE

```text
ROE_t = net_income_parent_TTM_t / average(equity_parent_t, equity_parent_t-1)
```

ROE是辅助指标，不得替代ROIC。平均归母权益小于等于0时ROE无效。

#### F. EBITDA、净负债与利息保障

```text
EBITDA = EBIT_adjusted + depreciation_amortization
net_debt = interest_bearing_debt - unrestricted_cash_and_equivalents
net_debt_to_ebitda = net_debt / EBITDA
interest_coverage = EBIT_adjusted / interest_expense_gross
cash_short_debt_cover = unrestricted_cash_and_equivalents / interest_bearing_debt_due_within_12m
```

处理规则：

- EBITDA `<= 0` 且净负债 `> 0`：杠杆指标不可正常解释，标记高风险复核；不得把负数比率误判为低杠杆。
- 利息费用为0或接近0时，利息保障倍数记为 `NA_NO_INTEREST`，不是无限高分；同时检查是否存在资本化利息或数据缺失。
- 净现金公司 `net_debt < 0` 可记为净现金状态；评分按净现金处理，不因负数比率产生异常高分。

#### G. 毛利率、营业利润率和费用率

```text
gross_margin = (revenue - cost_of_revenue) / revenue
operating_margin = EBIT_adjusted / revenue
selling_expense_ratio = selling_expense / revenue
admin_expense_ratio = admin_expense / revenue
rd_expense_ratio = rd_expense / revenue
```

`revenue <= 0` 时上述比率无效。零收入或负收入不允许生成虚假的高利润率。

#### H. FCF与现金转化

```text
FCF = cash_flow_operating - capex
cash_conversion_5y = sum(CFO over 5Y) / sum(net_income over 5Y)
fcf_conversion_5y = sum(FCF over 5Y) / sum(net_income_ex_nonrecurring_parent over 5Y)
```

分母绝对值过小、正负跨越或累计扣非净利润不为正时，转换率标记为不可解释，并展示原始累计金额，不用任意epsilon制造比率。非经常性依赖度的累计归母净利润分母接近0或正负跨越时同样标记无效，不得用该比率评分。

FCF是股东现金创造能力的重要代理，但`CFO-CapEx`不是所有行业都可直接比较的完整股权自由现金流。金融企业排除在v0.1之外。

#### I. 应收、存货与营运资本

```text
trade_receivables = accounts_receivable + notes_receivable
                    + accounts_receivable_financing + contract_assets

receivable_days = average(trade_receivables) / TTM_revenue * 365
inventory_days = average(inventory) / TTM_cost_of_revenue * 365

operating_nwc = trade_receivables + inventory
                - accounts_payable - notes_payable_noninterest_bearing
                - contract_liabilities

delta_nwc = operating_nwc_t - operating_nwc_t-1
```

若票据应收/应付、合同资产/负债在供应商字段中存在包含关系，必须通过字段映射配置去重。若成本或收入分母小于等于0，周转天数无效。

#### J. 增长和每股指标

```text
revenue_growth_yoy = revenue_TTM_t / revenue_TTM_t-4q - 1
adjusted_np_growth_yoy = adjusted_np_TTM_t / adjusted_np_TTM_t-4q - 1
EPS_adjusted = net_income_ex_nonrecurring_parent_TTM / diluted_shares
```

CAGR只在起点、终点均为正且期间长度符合要求时计算：

```text
CAGR_n = (value_end / value_start) ** (1/n) - 1
```

出现负值、零值或盈亏转换时，不得用标准CAGR；改用利润率变化、绝对金额变化和`TURNAROUND`状态描述。

#### K. 估值指标

```text
market_cap = close_unadjusted * total_shares
enterprise_value = market_cap + interest_bearing_debt
                   - unrestricted_cash_and_equivalents
                   - eligible_non_operating_financial_assets

normalized_pe = market_cap / normalized_net_income_parent
EV_EBIT = enterprise_value / normalized_EBIT
FCF_yield = normalized_FCF / market_cap
```

分母小于等于0时相应估值倍数记为 `NA_NEGATIVE_DENOMINATOR`，不得显示为负PE后参与排序。

`normalized_net_income_parent` 和 `normalized_FCF` 默认使用最近5个完整年度与最近TTM数据共同估计：优先使用可比、非异常的完整年度中位数；若公司处于明显周期状态，转入周期专项估值，不能简单取最近五年平均。

---

## 4. 财务质量指标窗口与标准化

每家公司至少计算以下时间窗口：

- 最近TTM；
- 最近完整财年；
- 过去3个完整财年；
- 过去5个完整财年；
- 过去5年年度中位数、均值、标准差和正值年份数。

标准化规则：

1. 对毛利率、营业利润率、ROIC、ROE等比率，输出中位数、均值、标准差和趋势斜率。
2. 趋势斜率使用最近5个完整年度的OLS斜率；缺少5年时注明窗口长度。
3. 评分使用中位数和持续性，避免只因某一年极高值加分。
4. 对明确的极端异常值，先保留原始值，再产生winsorized辅助值；原始值不得覆盖。
5. 同时输出年度序列，不能只留下聚合指标。
6. 经营周期较强的行业必须生成周期标签；处于周期高点的公司不能因当前利润和FCF异常高而被评为低估。

---

## 5. 硬性淘汰、复核与数据不足规则

### 5.1 `REJECT`：明确排除

满足任一条件时直接排除，并记录规则ID和证据：

| 规则ID | 条件 | 处理理由 |
|---|---|---|
| `G-001` | as-of时点为ST、*ST、退市整理期或已确定重大退市风险 | 不进入常规价值候选池 |
| `G-002` | 近3个完整财年存在否定意见或无法表示意见的审计报告，且问题未被后续正式审计结论消除 | 财务信息可靠性不足 |
| `G-003` | 已有监管机关/司法机关正式认定的重大财务造假、资金占用或欺诈发行，且未完成可信整改 | 治理和资本永久损失风险 |
| `G-004` | 归母净资产为负，或持续资不抵债 | 股权价值与持续经营风险过高 |
| `G-005` | 被依法暂停上市/终止上市或已进入退市执行流程 | 不符合普通候选池定义 |
| `G-006` | 核心数据校验失败且无法修复，例如股本、市值、报表勾稽存在重大矛盾 | 无法可靠计算 |

### 5.2 `REVIEW_REQUIRED`：不得自动通过

| 规则ID | 条件 | 默认处理 |
|---|---|---|
| `R-001` | 5年累计扣非归母净利润小于等于0 | 评估是否周期低谷/商业模式恶化；默认不得评A/B |
| `R-002` | 5年累计CFO为负且累计扣非归母净利润为正 | 盈利现金转化存在问题 |
| `R-003` | 5年现金转化率低于0.5 | 检查应收、存货、合同资产及资本化 |
| `R-004` | 净负债/EBITDA大于4，或EBITDA非正且净负债为正 | 财务杠杆风险 |
| `R-005` | 最近TTM利息保障倍数低于1.5，或连续两年低于1.5 | 债务偿付能力风险 |
| `R-006` | 应收款或存货增速连续两年比收入增速高出20个百分点以上 | 可能存在回款/需求/库存问题 |
| `R-007` | 商誉/归母净资产大于30%，或商誉减值风险显著 | 并购资产质量风险 |
| `R-008` | 近3年存在保留意见或重大审计强调事项，且影响收入、资产、持续经营或现金流 | 需要阅读审计报告原文 |
| `R-009` | 近3年发生重大诉讼、重大客户流失、核心技术替代、重大安全环保处罚或持续经营不确定事项 | 需要事件级判断 |
| `R-010` | 最近3年总股本累计稀释超过20%，且新增资本未体现为每股盈利/现金流改善 | 股东被稀释风险 |
| `R-011` | 过去5年ROIC持续下降且最近3年下降幅度超过5个百分点 | 竞争优势可能衰退 |
| `R-012` | 当前TTM扣非净利润或FCF为负 | 判断暂时性冲击还是长期损伤 |

`REVIEW_REQUIRED`不是一律排除。人工复核完成后，需写入`review_decision`、`reviewer`、`review_date`、`evidence_refs`和`reason`。无复核结论时，最高状态为观察，不得进入A类。

### 5.2.1 状态优先级

同一公司可同时触发多个状态条件，最终状态按以下顺序确定：

1. 已确认的`REJECT`优先级最高；
2. 未触发REJECT，但关键数据不足时为`INSUFFICIENT_DATA`；
3. 数据足够但存在未关闭风险时为`REVIEW_REQUIRED`；
4. 其余情况为`PASS`。

系统必须同时保留所有命中的规则列表，不能因为最终状态只显示一个标签而丢失其他原因。

### 5.3 `INSUFFICIENT_DATA`：数据不足

满足任一条件时不参与正式评分：

- 少于3年可比财务历史；
- TTM关键流量字段无法由连续4个单季值构造；
- ROIC所需EBIT、投入资本或折旧摊销字段缺失且不能可靠派生；
- 现金流量表或资产负债表关键字段覆盖不足；
- 股本、价格或复权数据无法与公司行为对齐；
- 历史回测模式缺少公告日期/历史版本信息；
- 报表单位、币种或合并范围无法确认。

对于缺失字段，不允许默认填0。仅允许对明确可推导的字段进行公式补全，并留下`imputation_flag`。

---

## 6. 基本面评分体系（100分）

### 6.1 评分总表

评分区间统一采用左闭右开口径（`[lower, upper)`）；相邻区间不得重叠或留空。对于需要同时满足多个条件的定性子项，评分规则必须由结构化状态字段驱动，不能由自由文本自动推断。

| 维度 | 权重 | 评价目标 |
|---|---:|---|
| A. 商业质量 | 25 | 长期资本回报与竞争力 |
| B. 财务健康 | 20 | 杠杆、流动性、资产负债表质量 |
| C. 盈利质量 | 15 | 利润现金转化、非经常性与稀释 |
| D. 估值吸引力 | 25 | 保守内在价值、FCF收益率、反向DCF |
| E. 错杀证据 | 15 | 基本面与价格背离、短期冲击、市场预期 |
| **合计** | **100** | |

评分仅对有效字段计算。维度内关键字段缺失超过该维度权重的20%时，该维度标记`INCOMPLETE`，公司不得得到最终A/B/C评级，只能输出临时分数和数据缺口。

### 6.2 A. 商业质量（25分）

| 子项 | 分值 | 规则 |
|---|---:|---|
| 5年ROIC中位数 | 8 | `>=15%:8`; `12–15%:6`; `10–12%:4`; `8–10%:2`; `<8%:0` |
| ROIC持续性 | 5 | 5年中ROIC为正且有效年份：`5年:5`; `4年:4`; `3年:2`; `<3年:0` |
| 利润率质量 | 4 | 毛利率/EBIT利润率5年趋势稳定或改善且波动可控：4；轻微下滑：2；持续明显下滑：0 |
| 收入与盈利韧性 | 4 | 5年收入和扣非利润总体增长且无连续恶化：4；一项稳定、一项波动：2；持续衰退：0 |
| 现金再投资效率 | 4 | FCF长期为正且ROIC稳定：4；FCF有周期波动但累计为正：2；长期消耗现金且回报下降：0 |

说明：ROIC高但依赖一次性收益、并购会计或极低账面投入资本时，必须触发复核；不能仅凭数值给满分。

### 6.3 B. 财务健康（20分）

| 子项 | 分值 | 规则 |
|---|---:|---|
| 净负债/EBITDA | 5 | 净现金或`<=1x:5`; `1–2x:4`; `2–3x:2`; `3–4x:1`; `>4x`或EBITDA<=0且净负债为正：0并复核 |
| 利息保障倍数 | 4 | `>=8x:4`; `4–8x:3`; `2–4x:2`; `1.5–2x:1`; `<1.5x:0并复核` |
| 短期债务现金覆盖 | 3 | `>=1.5x:3`; `1.0–1.5x:2`; `0.5–1.0x:1`; `<0.5x:0` |
| 营运资本质量 | 4 | 应收/存货增速不高于收入增速且周转稳定：4；轻微恶化：2；连续显著恶化：0 |
| 审计与治理 | 4 | 无重大异常且信息披露可靠：4；有需跟踪事项：2；重大未解决问题：0并复核/排除 |

无有息负债或无利息费用的公司不自动得到满分：相应子项依据净现金、现金覆盖和字段完整性评分；若利息字段缺失而非确实无利息，计为缺失而不是0或满分。

### 6.4 C. 盈利质量（15分）

| 子项 | 分值 | 规则 |
|---|---:|---|
| 5年现金转化率 CFO/净利润 | 5 | `>=1.0:5`; `0.8–1.0:4`; `0.5–0.8:2`; `<0.5:0并复核` |
| FCF/扣非净利润 | 4 | `>=0.8:4`; `0.5–0.8:3`; `0.2–0.5:1`; `<0.2:0`；若处于明确成长投资期可人工复核，不自动加分 |
| 非经常性损益依赖 | 3 | 5年累计非经常性损益占累计归母净利润比例`<=10%:3`; `10–20%:2`; `20–35%:1`; `>35%:0` |
| 每股盈利与股本变化 | 3 | EPS增长且股本稳定/回购注销：3；股本适度增长但每股价值仍改善：2；显著稀释且每股盈利不增长：0 |

### 6.5 D. 估值吸引力（25分）

所有估值都以每股口径与筛选日价格比较。若DCF输入关键字段不足，估值维度标记不完整，不得用低PE分数替代。

| 子项 | 分值 | 规则 |
|---|---:|---|
| 相对保守内在价值的安全边际 | 10 | MOS`>=50%:10`; `40–50%:9`; `30–40%:7`; `20–30%:4`; `10–20%:2`; `<10%:0` |
| 标准化FCF收益率 | 5 | `>=8%:5`; `6–8%:4`; `4–6%:3`; `2–4%:1`; `<2%:0` |
| 标准化EV/EBIT相对合理区间 | 4 | 相对行业/自身合理估值折价`>=30%:4`; `20–30%:3`; `10–20%:2`; `0–10%:1`; 溢价：0 |
| 反向DCF悲观程度 | 6 | 市场隐含FCF增长率比基本面Base假设低`>=5个百分点:6`; `3–5pp:5`; `1–3pp:3`; `0–1pp:1`; 不低于Base：0 |

**估值限制：** 周期性行业必须先识别周期位置并使用中周期盈利；银行、保险、券商等金融企业不进入v0.1。对负标准化利润或负FCF公司，PE/FCF Yield不得产生正向估值分数。

### 6.6 E. 错杀证据（15分）

错杀维度包含结构化证据与人工研究结论，不允许LLM凭空生成“错杀原因”。每条证据必须保留来源、日期、摘要和可信等级。

| 子项 | 分值 | 规则 |
|---|---:|---|
| 价格与基本面背离 | 4 | 12个月总回报`<=-30%`，但标准化盈利/FCF/ROIC未出现同等程度恶化：4；跌幅`-20%至-30%`且基本面相对稳定：2；否则0–1 |
| 短期冲击可逆性 | 4 | 有公告/行业数据/客户或供需证据证明冲击主要为暂时性：4；原因合理但证据不充分：2；结构性损伤：0 |
| 市场隐含预期与经营证据背离 | 4 | 反向DCF隐含预期明显悲观，且实际订单、客户、市场份额、现金流等证据不支持如此悲观：4；部分支持：2；没有背离：0 |
| 永久性价值损伤排查 | 3 | 核心产品、客户、成本位置、行业需求、治理结构未见永久性恶化且有证据：3；仍有不确定性：1；确认重大永久损伤：0 |

错杀证据的人工输入结构：

```json
{
  "evidence_id": "EV-001",
  "security_id": "...",
  "category": "TEMPORARY_SHOCK | MARKET_SENTIMENT | CYCLICAL_DOWNTURN | EVENT_DRIVEN",
  "claim": "待验证的判断",
  "evidence_summary": "事实摘要，不写未经验证的推测",
  "source_name": "公告/行业统计/公司披露/其他",
  "source_url_or_doc_id": "...",
  "published_at": "YYYY-MM-DD",
  "evidence_grade": "A | B | C",
  "supports_reversibility": true,
  "review_status": "PENDING | CONFIRMED | REJECTED",
  "reviewer": "...",
  "reviewed_at": "YYYY-MM-DD"
}
```

A类证据优先为监管公告、公司正式公告、审计报告、政府/行业协会原始统计或可复核经营数据；媒体报道和二手观点不能单独支持关键结论。

### 6.7 最终等级

| 评级 | 分数 | 附加条件 |
|---|---:|---|
| A：重点投资候选 | 85–100 | 商业质量`>=15/25`；财务健康`>=12/20`；错杀证据`>=9/15`；无未解决硬性复核项；MOS建议至少30% |
| B：重点研究 | 75–84 | 商业质量`>=15/25`；财务健康`>=12/20`；没有未解决重大风险；数据质量合格 |
| C：观察名单 | 60–74 | 无REJECT；可存在待确认风险或估值不足 |
| D：低优先级 | 0–59 | 价值或质量证据不足 |
| `REVIEW` | 不限 | 存在未关闭的重大复核项；不授予A/B |
| `INSUFFICIENT_DATA` | 不评分 | 关键数据不足或PIT条件不满足 |

**A类是研究优先级标签，不是自动买入信号。** 买入仍需结合投资期限、组合风险预算、流动性、行业集中度和具体价格。

---

## 7. 内在价值估值模块

### 7.1 估值输入

DCF至少需要：

- 收入历史序列；
- 调整后EBIT及利润率历史序列；
- 标准化税率；
- 折旧摊销；
- 日常资本开支；
- 营运资本及其变化；
- 有息债务、可自由使用现金；
- 少数股东权益；
- 稀释后股数；
- 折现率输入（无风险利率、ERP、Beta）；
- 永续增长率；
- Bear/Base/Bull情景假设及概率。

缺少折旧摊销、资本开支或营运资本字段时，必须输出估值降级标志；不允许将DCF结果标记为高可信。

### 7.2 历史FCFF

```text
FCFF = NOPAT + depreciation_amortization - capex - delta_nwc
```

同时保留现金流口径：

```text
FCF_cash = CFO - capex
```

两者差异用于检查现金流质量。对历史数据，若FCFF与FCF_cash长期显著背离，系统应生成解释任务，不得直接选取较高者作为估值基础。

### 7.3 预测参数生成

v0.1使用可解释的规则化预测起点，不让模型自行任意拟合：

- Base收入增速：最近3年收入CAGR与5年CAGR的加权值（60%/40%），限制在`[-2%, 10%]`；若负值、盈亏或周期标志使CAGR无效，则转人工配置。
- Bear收入增速：Base减5个百分点，最低不低于配置下限；Bull收入增速：Base加3个百分点，最高不超过配置上限。
- Base EBIT利润率：最近5年有效年度EBIT利润率中位数与最近2年中位数加权（50%/50%）。
- Bear EBIT利润率：Base减2个百分点；Bull：Base加1.5个百分点。若利润率对周期极敏感，必须启用行业中周期假设。
- D&A/收入、CapEx/收入和营运资本/收入：使用最近5年中位数，并对异常年份作单独标记。
- 若预测增长对应的再投资需求不合理，必须限制FCF，不能同时假设高增长、低CapEx和低营运资本投入。

以上默认幅度仅为v0.1起点，均需配置化并记录版本。

### 7.4 折现率与终值

```text
Cost_of_equity = risk_free_rate + equity_risk_premium * levered_beta
Cost_of_debt = gross_interest_expense / average_interest_bearing_debt
After_tax_cost_of_debt = Cost_of_debt * (1 - normalized_tax_rate)
E_weight = market_cap / (market_cap + interest_bearing_debt)
D_weight = interest_bearing_debt / (market_cap + interest_bearing_debt)
WACC_raw = E_weight * Cost_of_equity + D_weight * After_tax_cost_of_debt
WACC = clamp(WACC_raw, 8%, 14%)
terminal_growth = min(configured_terminal_growth, 2.5%)
require WACC >= terminal_growth + 3 percentage points
```

如果`average_interest_bearing_debt`为0，则债务权重为0；若债务成本因利息费用缺失无法估算，使用配置的行业债务成本并标记`COST_OF_DEBT_FALLBACK`。若`WACC_raw`落在8%–14%之外，除输出截断值外还必须输出`WACC_CLAMPED`告警和原始值，不得隐藏截断。

- 无风险利率使用筛选日可获得的中国10年期国债收益率，并保留来源和时间戳；历史回测必须使用当时数据。
- Beta默认使用过去3年周收益率相对CSI300计算，设置合理上下限；样本不足时使用行业Beta或1.0回退值并标记。
- 债务成本使用可观测融资成本；税后债务成本按标准税率调整。
- WACC不能低于8%，也不能超过配置上限；若需要超出范围，应触发人工估值复核，而非悄悄截断。
- 终值使用Gordon Growth：`TV = FCFF_(n+1)/(WACC-g)`。
- 终值现值占企业价值超过75%时，标记`TERMINAL_VALUE_DOMINANT`，估值可信度下降。

### 7.5 三情景DCF

每个情景计算：

```text
EV_scenario = sum(FCFF_t / (1 + WACC)^t) + TV / (1 + WACC)^n
EquityValue_parent = EV_scenario
                     - net_debt
                     - minority_interest
                     + eligible_non_operating_assets
IntrinsicValuePerShare = EquityValue_parent / diluted_shares
```

注意：若FCFF为合并口径，股权价值中必须扣除少数股东权益；若使用归母口径模型，则不可再次扣除。二者只能择一，必须在模型元数据中声明。

情景概率：

```text
ExpectedValue = 0.25 * Bear + 0.50 * Base + 0.25 * Bull
ConservativeValue = 0.60 * Bear + 0.40 * Base
```

概率允许人工修改，但修改必须记录理由、证据来源与修改前后值。不得因为希望买入而提高Bull概率。

安全边际：

```text
MOS = 1 - current_price / ConservativeValuePerShare
```

若保守价值小于等于0，MOS无效并判定估值不通过。

### 7.6 反向DCF计算定义

为保证v0.1可复算，反向DCF采用单变量求解，估计“当前市值要求未来FCF以多快速度增长/收缩”。该指标不是市场真实预测，而是在锁定其他假设后的隐含FCF增长率。

1. 起点使用`normalized_FCFF_0`；必须大于0，否则输出`REVERSE_DCF_UNAVAILABLE_NONPOSITIVE_FCF`。
2. 固定WACC、永续增长率、净负债、少数股东权益、非经营性资产和稀释股数。
3. 对每个候选增长率`g`，设`FCFF_t = normalized_FCFF_0 * (1+g)^t`，`t=1..5`；终值使用`FCFF_6 = FCFF_5 * (1+terminal_growth)`。
4. 计算该路径对应的股权价值，并求解使其等于当前总市值的`g_implied`。
5. 使用二分法在配置区间`[-10%, +20%]`求解；若区间内没有根，分别输出`BELOW_SEARCH_RANGE`或`ABOVE_SEARCH_RANGE`，不得硬填边界值作为真实解。
6. `base_fcf_growth`定义为Base情景预测FCFF第1年至第5年的年化复合增长率；仅当各年FCFF均为正时有效。
7. 评分比较`base_fcf_growth - g_implied`。差值越大，表示市场价格要求的FCF路径越悲观。

反向DCF与Base情景必须使用相同的起始FCFF、WACC和终值口径，否则不得比较。若FCFF路径跨越正负，反向DCF评分记为缺失并要求人工检查。

### 7.7 交叉估值

DCF不是唯一估值锚。还需输出：

- 标准化PE；
- 标准化EV/EBIT；
- 标准化FCF Yield；
- 公司自身过去5–10年估值区间（数据可得时）；
- 同行业可比公司估值分位数（行业分类和盈利口径一致时）。

同行比较必须满足：同一行业细分、相近商业模式、相近会计口径；不得简单把不同行业最低PE作为合理估值。

---

## 8. 错杀识别逻辑

### 8.1 候选触发器

满足以下任一条件，进入错杀研究队列，但不自动获得错杀分：

- 过去12个月含分红总回报`<= -20%`；
- 市值较过去3年高点回撤`>=30%`；
- 当前标准化PE/EV-EBIT处于自身历史低分位；
- 当前股价隐含经营预期明显低于历史正常水平；
- 行业整体估值显著压缩，但个股基本面相对稳定。

### 8.2 基本面背离量化

至少比较以下序列：

- 12个月总回报；
- 3年标准化归母净利润变化；
- 3年标准化FCF变化；
- ROIC变化；
- 收入变化；
- 净负债变化。

形成`price_fundamental_divergence_score`。不得仅用股价跌幅与当年净利润同比比较；需要使用标准化盈利和中周期判断。

### 8.3 永久性损伤检查表

每家公司必须逐项给出`YES / NO / UNKNOWN`及证据：

1. 核心产品或服务是否被技术替代？
2. 核心客户是否永久流失或供应商地位下降？
3. 市场份额、渠道或成本优势是否发生结构性恶化？
4. 行业长期需求是否出现不可逆收缩？
5. ROIC是否连续多年低于资本成本且看不到修复路径？
6. 资产负债表是否需要持续外部融资才能维持经营？
7. 是否存在严重治理、关联交易或资本分配问题？
8. 核心资产是否存在重大减值、抵押、冻结或产权争议？

任一项为`YES`且可能影响长期盈利能力时，至少进入`REVIEW_REQUIRED`；若已确认商业模式或持续经营能力受到永久性重大损伤，则不得进入A类。

### 8.4 错杀分类

`TEMPORARY_SHOCK`、`MARKET_SENTIMENT`、`CYCLICAL_DOWNTURN`、`EVENT_DRIVEN`、`STRUCTURAL_DECLINE`五类。前四类只是待验证解释；`STRUCTURAL_DECLINE`是负面分类。分类必须有证据，不得仅由价格序列自动确定。

---

## 9. PIT、公告时点与重述版本控制

这是A股历史研究最关键的数据要求之一。

### 9.1 信息可用日期

- 必须保存财报实际公告时间，而不是只保存报告期末。
- 若公告时间可精确到时分秒：按市场交易时段确定可用日期；收盘后公告通常从下一交易日可用。
- 若只有公告日期而没有时间：保守地从下一交易日开始使用，避免同日收盘价与公告先后顺序不明造成前视偏差。
- 分红、回购、增发、股本变更、审计意见、监管处罚等信息也必须按其公告/生效日期进入历史时点数据。

### 9.2 历史版本选择

对每个`security_id + report_period + field`，在筛选日D仅选择`available_date <= D`的最新可见版本。后续更正或重述只能从其公开日期之后生效。

### 9.3 重述处理

- 重述不是覆盖历史版本，而是新增版本记录。
- 当前筛选可以采用当前最新版本；历史PIT只能采用当时可见版本。
- 期间差分必须在同一版本链中完成。
- 若供应商无法提供版本号，至少保存源文件发布日期、抓取时间和文件哈希；无法恢复旧版本时必须标记为非PIT。

### 9.4 非PIT模式限制

当数据源只有最终版财报时：

- `historical_pit_backtest`必须报错停止；
- `final_vintage_research`可执行，但所有输出写入`pit_status=NON_PIT_FINAL_VINTAGE`；
- 不得报告策略历史收益、命中率或胜率为可交易回测结论；
- 可以用于当前公司质量横截面分析，但应提示历史财务指标可能被后续重述影响。

---

## 10. 数据质量检查（Data QA）

### 10.1 完整性检查

| 检查ID | 检查内容 | 失败处理 |
|---|---|---|
| `DQ-001` | 证券代码、报告期、公告日期、版本字段非空 | `INSUFFICIENT_DATA` |
| `DQ-002` | 关键财务字段覆盖率：收入、利润、资产、负债、CFO、CapEx | 按字段降级；核心缺失则不评分 |
| `DQ-003` | 5年历史季度连续性 | 缺期不能默认为0；重新构造窗口或降级 |
| `DQ-004` | 金额单位与币种统一 | 不一致时停止计算 |
| `DQ-005` | 期末日期与报告类型匹配 | 进入异常记录 |
| `DQ-006` | 公告日期不早于数据实际可获得日期 | PIT失败 |

### 10.2 会计勾稽检查

至少执行：

- `total_assets ≈ total_liabilities + total_equity`；
- 现金流量表期末现金与资产负债表货币资金进行合理性核对（考虑受限资金、汇率和现金等价物范围）；
- `net_income_parent`不应长期大于合并净利润且无法由少数股东损益解释；
- `net_income_ex_nonrecurring_parent + nonrecurring_profit_parent ≈ net_income_parent`；
- 股本变动与公告中的增发、回购、送转、拆并股事件核对；
- 期初余额与上期末余额在同一版本下保持一致；
- TTM累计值与年度累计值差异需要可解释。

容差采用金额绝对值与相对值双阈值，例如：

```text
abs(lhs-rhs) <= max(abs_tolerance, relative_tolerance * max(abs(lhs), abs(rhs)))
```

默认相对容差0.5%，绝对容差由币种单位配置。超差不直接修正原始值，必须记录异常。

### 10.3 异常值检查

- 收入、利润、现金流突然变化超过±100%：生成异常标记，不自动删除。
- 毛利率、ROIC、ROE超出合理区间：核验分母、并购、减值、一次性收益和数据单位。
- 负CapEx、负折旧摊销、负存货或负应收：核验数据源和会计特殊事项。
- 总股本单年变动超过10%：核对送转、增发、回购、可转债转股和股权激励。
- 复权价格跳变：核对分红、拆股、送转和复权因子。

异常值保留原值，并通过`quality_flag`记录，不允许静默winsorize。

### 10.4 数据质量等级

| 等级 | 条件 | 输出限制 |
|---|---|---|
| A | 核心字段完整，公告时点与版本可追溯，勾稽通过 | 可进入正式评分/历史PIT |
| B | 少量非核心字段缺失或可解释异常 | 可评分，但显示置信度折扣 |
| C | 关键指标依赖估算或重述版本不完整 | 仅观察，不得A类 |
| F | 关键字段缺失、重大勾稽失败或PIT不合格 | 不评分/停止历史回测 |

---

## 11. 输出定义

### 11.1 单家公司结果JSON

```json
{
  "security_id": "...",
  "symbol": "...",
  "company_name": "...",
  "as_of_date": "YYYY-MM-DD",
  "run_mode": "current_screen",
  "pit_status": "STRICT_PIT | NON_PIT_FINAL_VINTAGE",
  "data_quality_grade": "A | B | C | F",
  "eligibility_status": "PASS | REVIEW_REQUIRED | REJECT | INSUFFICIENT_DATA",
  "rejection_reasons": [],
  "review_flags": [],
  "scores": {
    "commercial_quality": 0,
    "financial_health": 0,
    "earnings_quality": 0,
    "valuation_attractiveness": 0,
    "mispricing_evidence": 0,
    "total": 0,
    "grade": "A | B | C | D | REVIEW | INSUFFICIENT_DATA"
  },
  "key_metrics": {},
  "valuation": {
    "bear_value_per_share": null,
    "base_value_per_share": null,
    "bull_value_per_share": null,
    "expected_value_per_share": null,
    "conservative_value_per_share": null,
    "current_price": null,
    "margin_of_safety": null,
    "wacc": null,
    "terminal_growth": null,
    "terminal_value_weight": null
  },
  "mispricing": {
    "category": [],
    "price_12m_total_return": null,
    "fundamental_price_divergence": null,
    "permanent_impairment_checks": [],
    "evidence_refs": []
  },
  "explainability": {
    "positive_drivers": [],
    "negative_drivers": [],
    "missing_data": [],
    "formula_versions": {},
    "source_refs": []
  }
}
```

### 11.2 批量输出文件

每次运行至少产生：

1. `screen_summary.csv`：全部股票、状态、总分和五维分数；
2. `candidate_list.csv`：通过质量和估值初筛的候选股票；
3. `rejected_list.csv`：淘汰股票及规则ID；
4. `review_queue.csv`：需人工复核的股票、问题和证据缺口；
5. `insufficient_data.csv`：数据不足股票及缺失字段；
6. `company_profiles/{symbol}.json`：单家公司完整指标、估值和解释；
7. `data_quality_report.json`：数据覆盖率、勾稽异常、PIT状态；
8. `run_manifest.json`：代码版本、参数版本、数据源版本、运行时间、as-of日期和输入文件哈希。

### 11.3 输出排序

默认按以下优先级排序，而不是只按总分：

1. `eligibility_status=PASS`；
2. 评级A优先于B；
3. MOS由高到低；
4. 商业质量分由高到低；
5. 数据质量等级A优先于B；
6. 流动性和行业集中度作为研究排序辅助字段。

`REVIEW_REQUIRED`与`INSUFFICIENT_DATA`不得混入正式候选榜单。

---

## 12. 系统模块与建议目录结构

```text
A-Value-Screen/
├── README.md
├── SPEC.md
├── pyproject.toml
├── configs/
│   ├── default.yaml
│   ├── sector_overrides.yaml
│   └── field_mapping.yaml
├── data_contracts/
│   ├── raw_financial_schema.json
│   ├── normalized_financial_schema.json
│   └── output_schema.json
├── src/avalue_screen/
│   ├── domain/
│   │   ├── models.py
│   │   ├── enums.py
│   │   └── errors.py
│   ├── ingestion/
│   │   ├── loaders.py
│   │   ├── field_mapper.py
│   │   └── unit_normalizer.py
│   ├── pit/
│   │   ├── availability.py
│   │   ├── version_selector.py
│   │   └── period_builder.py
│   ├── features/
│   │   ├── profitability.py
│   │   ├── cashflow.py
│   │   ├── balance_sheet.py
│   │   ├── per_share.py
│   │   └── valuation_inputs.py
│   ├── quality/
│   │   ├── data_quality.py
│   │   ├── accounting_checks.py
│   │   └── outlier_checks.py
│   ├── screening/
│   │   ├── universe.py
│   │   ├── hard_gates.py
│   │   ├── scoring.py
│   │   └── ranking.py
│   ├── valuation/
│   │   ├── normalized_earnings.py
│   │   ├── dcf.py
│   │   ├── reverse_dcf.py
│   │   └── multiples.py
│   ├── mispricing/
│   │   ├── divergence.py
│   │   ├── evidence.py
│   │   └── impairment_checklist.py
│   ├── reporting/
│   │   ├── csv_export.py
│   │   ├── json_export.py
│   │   └── explainability.py
│   └── cli.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── regression/
└── outputs/
```

### 12.1 模块职责

- `ingestion`：读取数据，不做投资判断。
- `pit`：按as-of日期选择可见版本并构造期间，是所有财务特征计算的上游依赖。
- `features`：只负责指标计算，不执行淘汰或评分。
- `quality`：独立检查数据可信度和会计勾稽。
- `screening`：执行股票池、硬性规则和分数。
- `valuation`：标准化盈利、DCF和反向DCF。
- `mispricing`：识别价格/基本面背离并管理证据，不允许用未验证文本替代事实。
- `reporting`：输出字段、公式版本和解释链。

### 12.2 工程约束

- 所有计算函数必须尽量纯函数化；输入数据和配置相同，输出必须一致。
- 所有公式必须有独立单元测试。
- 禁止在特征计算中隐式访问未来日期数据。
- 禁止在代码中静默补零、静默覆盖重述数据或静默删除异常样本。
- 数据源适配器与业务规则隔离；更换数据供应商不应重写筛选逻辑。
- 每个分数都必须返回`score + rationale + source_metrics + rule_version`。
- 所有阈值从配置读取；配置文件必须纳入版本控制。

---

## 13. 测试与验收标准

### 13.1 单元测试

必须覆盖：

1. 累计季度值转换为单季度值；
2. TTM滚动计算；
3. 重述前后版本选择；
4. 公告日期之后才可使用财务字段；
5. 同日盘后公告从下一交易日生效；
6. ROIC、ROE、EBITDA、净负债、利息保障倍数；
7. CFO/净利润、FCF/扣非净利润；
8. 应收周转天数、存货周转天数、营运资本变化；
9. 负利润、负FCF、负投入资本、零分母等边界条件；
10. 股本送转、增发、回购后的每股数据与复权价格；
11. DCF三情景、终值占比、安全边际；
12. 五个评分维度每个阈值的边界值；
13. REJECT、REVIEW_REQUIRED、INSUFFICIENT_DATA状态互斥与优先级。

### 13.2 合成数据测试

至少准备以下合成公司：

- `GOOD_CHEAP`：ROIC高、现金流良好、低杠杆、股价明显低于保守价值，应进入A/B候选；
- `GOOD_EXPENSIVE`：质量高但价格高，不得进入A类；
- `CHEAP_TRAP`：低PE但ROIC连续下降、CFO差、应收存货恶化，应触发复核或淘汰；
- `CYCLICAL_TROUGH`：当前盈利低谷但中周期盈利稳定，不能机械按当前PE淘汰；
- `STRUCTURAL_DECLINE`：收入、利润率、市场份额长期恶化，不得因股价大跌被判错杀；
- `DILUTION_TRAP`：净利润增长但股本大幅增加、EPS不增长，应扣分；
- `RESTATED_DATA`：历史报表后续重述，历史as-of结果不得提前看到重述；
- `MISSING_DATA`：关键字段缺失，状态应为`INSUFFICIENT_DATA`而非填0；
- `NEGATIVE_FCF_GROWTH`：成长性资本开支导致短期FCF为负，必须进入复核而非自动认定造假。

### 13.3 历史PIT回归测试

构造至少一个具有多次财报更正的样本公司：

- 在更正公告日之前，历史筛选结果必须使用旧版本；
- 更正公告日之后，才允许使用新版本；
- 同一`as_of_date`重复运行，结果必须完全一致；
- 将未来版本数据加入数据库，不得改变过去as-of日期的结果。

### 13.4 计算正确性验收

- 关键指标与手工计算样本逐项核对，金额误差不超过配置容差；
- 同一输入、同一配置、同一代码版本的输出哈希一致；
- 所有分数能够反向追溯到子项、指标、原始字段和来源；
- 对缺失字段不会静默产生0分或满分；
- 所有拒绝项有唯一规则ID和可读理由；
- 历史PIT模式在缺少版本信息时必须明确失败，不得降级为最终版数据继续运行。

### 13.5 研究有效性验收（不等于收益承诺）

第一阶段不以跑赢指数为唯一验收目标，先验证筛选结果是否符合设计意图：

- A类公司的5年ROIC、现金转化、资产负债表质量显著优于随机同业样本；
- A类公司的保守估值折价确实存在，且不是由负分母或错误股本造成；
- `GOOD_CHEAP`、`GOOD_EXPENSIVE`、`CHEAP_TRAP`、`STRUCTURAL_DECLINE`等合成测试分类正确；
- 经过人工复核的错杀候选，其关键判断均有来源证据；
- 不得仅通过调整阈值让历史收益变好而牺牲规则可解释性。

后续如进行投资表现验证，应使用严格PIT数据、包含退市股票、可交易价格、交易成本、停牌和涨跌停约束，并单独报告样本外结果。

---

## 14. 实现顺序（Codex执行计划）

### Phase 0：数据合同与配置

交付：字段schema、标准字段映射、单位规则、配置文件、运行模式校验。

验收：能读取一家公司原始财务数据并生成标准化数据；所有字段有来源和单位。

### Phase 1：PIT与财报期间引擎

交付：公告日期过滤、版本选择、累计值转单季、TTM构造、历史版本回归测试。

验收：未来重述数据不会污染历史as-of结果。

### Phase 2：基本面特征与Data QA

交付：盈利能力、现金流、负债、营运资本、每股指标；会计勾稽和异常检查。

验收：合成样本和手工样本计算正确；缺失值不被补零。

### Phase 3：股票池、硬性规则与评分

交付：Universe过滤、REJECT/REVIEW/INSUFFICIENT_DATA状态、100分评分、解释理由。

验收：全部阈值边界测试通过；硬性规则优先于分数。

### Phase 4：标准化盈利与估值

交付：标准化盈利、FCF Yield、EV/EBIT、三情景DCF、反向DCF、安全边际。

验收：估值关键假设可见；终值占比、负FCF和负分母均有明确处理。

### Phase 5：错杀识别与证据管理

交付：价格/基本面背离、错杀分类、永久性损伤清单、人工证据管理。

验收：仅价格大跌不能产生错杀高分；未核实证据不能进入A类。

### Phase 6：批量运行、报告与回归

交付：CLI、批量CSV/JSON、运行清单、全量测试和示例输出。

验收：可对指定as-of日期运行全A股筛选；输出可复现、可审计、可解释。

---

## 15. CLI接口建议

```bash
# 当前筛选
avalue-screen run \
  --as-of 2026-11-10 \
  --mode current_screen \
  --config configs/default.yaml \
  --input data/ \
  --output outputs/2026-11-10/

# 严格历史PIT筛选；若缺少历史版本必须失败
avalue-screen run \
  --as-of 2021-06-30 \
  --mode historical_pit_backtest \
  --config configs/default.yaml \
  --input data/ \
  --output outputs/2021-06-30/

# 最终版财报研究；输出强制标记NON_PIT
avalue-screen run \
  --as-of 2026-11-10 \
  --mode final_vintage_research \
  --config configs/default.yaml \
  --input data/ \
  --output outputs/final-vintage/

# 查看单家公司解释
avalue-screen explain --symbol 000000.SZ --as-of 2026-11-10
```

实际CLI参数可根据仓库现有结构调整，但运行模式和PIT限制不可删除。

---

## 16. v0.1完成定义（Definition of Done）

只有同时满足以下条件，才视为v0.1完成：

- [ ] 字段schema和数据映射可运行，单位与币种统一；
- [ ] 季度期间转换、TTM和公告版本选择通过测试；
- [ ] 非PIT最终版数据不能被误用于历史PIT回测；
- [ ] 核心财务特征可逐项复算；
- [ ] 硬性淘汰、复核、数据不足状态严格区分；
- [ ] 100分评分与所有边界规则实现；
- [ ] DCF、反向DCF和安全边际输出假设与敏感性；
- [ ] 错杀结论有结构化证据引用，不能仅由价格跌幅触发；
- [ ] 所有候选、淘汰和数据不足公司均能解释原因；
- [ ] 输出包含运行配置、代码版本、数据版本和输入哈希；
- [ ] 合成数据、单元测试、PIT回归测试全部通过；
- [ ] README提供数据接入方式、运行方式和已知限制。

**最终交付不是一张股票排名表，而是一套可追溯、可复算、能解释“为什么通过/为什么淘汰/为什么可能被错杀”的A股基本面筛选系统。**
