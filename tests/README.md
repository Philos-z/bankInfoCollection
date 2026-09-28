# Tests — 业务规则回归说明

这组测试的目标不是证明“所有网页抓取和 AI 结果都正确”，而是固定当前系统已经确认过的
**业务规则与边界行为**，防止后续修改 prompt、schema、entity matching 或 verification 规则时产生静默回归。

## 如何运行

```bash
.venv/bin/python -m unittest discover -s tests -v
```

这些测试是离线的：

- 不访问网络；
- 不调用 AI / Codex proxy；
- 不读写 `data/bank_campaigns.db`；
- 不修改真实抓取数据。

因此适合作为每次修改验证逻辑后的第一道检查。

### GPT-5.6 Sol 真实 Amex evaluation

`tests/eval_amex_gpt56.py` 会读取数据库里已保存的前 5 个 Amex snapshot，使用当前 extraction prompt
重新调用 `gpt-5.6-sol`，但**不会写回数据库**：

```bash
.venv/bin/python tests/eval_amex_gpt56.py
```

它重点报告：`card_name` 空值率、近似重复组、Gold/Gold Rosé 是否同时被区分，以及每个 snapshot
抽出的卡名。该脚本属于 live evaluation，不放进普通 unit test，因为它依赖 proxy、模型输出也不是完全确定性的。

已执行的评估结果会以摘要形式保存在 `tests/eval_reports/`，用于后续比较 prompt/model 变化。

AI link discovery 的真实运行评估也记录在 `tests/eval_reports/discovery_2026-09-23.md`。该评估区分
“发现与促销相关的页面”与“适合作为稳定 campaign source 的页面”，避免只用 AI score 判断质量。

Discovery 第二阶段最终验收见 `tests/eval_reports/discovery_stage2_2026-09-24.md`。

`tests/fixtures/discovery_golden.json` 保存 30 条人工复核过的 discovery 正/负/边界样本；
`tests/eval_discovery_golden.py` 用当前数据库里的第二阶段结果计算 recommendation precision、recall
以及 status+role 的精确匹配率，不访问网络或 AI。

### `test_content_validator.py`

保护 discovery 第二阶段：application/legal URL 预过滤、AI evidence quote 防幻觉、HTML/PDF 脚注噪声、
standing-feature 排除、page type 清洗，以及 `recommended / review / reject` 的 deterministic policy。
最终是否推荐 source 由代码规则决定，而不是直接采用模型 confidence。

## 测试文件与保护范围

### `test_extractor_schema.py`

保护 `extractor/schema.py` 的输入清洗规则：

- 只接受合法 ISO 日期；
- 起止日期反转时自动纠正；
- 非法 `product_scope` / `campaign_type` 使用安全 fallback；
- confidence 限制在 0~1；
- `evidence_quote` 必须能在原文中找到，否则 confidence 压到 `<= 0.3`；
- 缺少活动标题时不生成 extraction。

如果这里失败，通常意味着 **AI 输出进入数据库前的防幻觉/规范化逻辑发生了变化**。

### `test_validator_rules.py`

保护 `validator/rules.py` 的业务判断规则：

- 欧洲/美国数字格式统一，例如 `85,000`、`85.000`、`85 000`；
- card name normalization；
- 生命周期 `upcoming / active / expiring / expired`；
- 独立信源按 domain 计数；
- confidence `< 0.5` 的 extraction 不参与 verification；
- reward / end date 不一致触发 `conflict`；
- primary source 选择时 official 优先。

其中包含两个真实业务回归：

1. **BBVA PLAN1060**：官网截止日 `2026-10-20` 与旧第三方截止日 `2026-07-22` 不一致，必须为 `conflict`。
2. **Crédit Agricole 100 €**：官网 + Selectra + Moneyvox 三个独立域一致时，必须为 `verified`。

另外有一个“当前语义锁定”测试：两个独立第三方 domain 目前也允许得到 `verified`。
如果未来决定“verified 必须包含官网源”，应主动修改该测试和业务规则，而不是直接删除测试。

### `test_validator_matching.py`

保护 `validator/validate.py::_match` 的实体合并边界：

- 同一 snapshot 不会仅因为“来自同一页”就自动合并；
- 同一 snapshot 中若卡名归一化后完全一致且 offer 高匹配，可直接去重（例如 `Blue Card` / `American Express Blue Card`）；
- Gold / Gold Rosé 这类非完全一致的卡变体即使 offer 相同，也不能绕过 AI 仲裁；
- `card_name = null` 但 reward/title 足够接近时，应进入 AI 仲裁，而不是直接创建 duplicate campaign。

这里对应真实的 **Amex Gold vs Gold Rosé** 回归案例。

如果这里失败，通常意味着 **不同活动被错误合并，或相同活动被重复创建的风险上升**。

### `test_amex_integration.py`

使用**内存 SQLite + 正式数据库 schema + 正式 `validate()` 流程**做离线 integration regression。
它复现 2026-09-23 Amex 官网真实出现的 5 组同页重复，并同时放入 Gold / Gold Rosé：

- 5 组已确认 duplicate 必须被压缩为 5 个 canonical campaign；
- Gold 与 Gold Rosé 必须继续保持两个独立 campaign；
- 两个独立 domain 的 Gold / Gold Rosé evidence 必须分别挂到正确实体并得到 `verified`；
- BMW Carbon 别名是唯一需要 mock AI arbiter 的模糊案例。

因此它比单纯函数测试更接近真实的 `extractions → campaigns → campaign_sources → verification` 数据链路。

### `test_source_roles.py`

保护 discovery 推荐 source 的下游语义：

- `primary` source 可以 seed 新 campaign；
- `supporting` source 只能附着到已有 campaign；
- unmatched supporting extraction 必须被跳过，不能产生孤立 campaign；
- campaign 主记录优先使用 `primary` role，再比较 official/confidence/recency。

### `test_source_health.py`

保护 source health 状态机与持久化：

- disabled / unknown / OK / degraded / warning / critical 阈值；
- 失败连续累积；
- HTTP status / latency / last error 落库；
- 任意一次成功必须把 `consecutive_failures` 清零并更新 `last_success_at`。

真实全量抓取验收记录见 `tests/eval_reports/source_health_2026-09-24.md`。

重点 source selector 的 DOM/evidence/live-extraction 验收记录见
`tests/eval_reports/selector_optimization_2026-09-24.md`。

### `test_bank_agnostic_core.py`

保护核心逻辑的银行无关性：具体银行名、产品名和真实活动实体只能出现在配置与 regression fixtures，
不能进入 extraction / matching / validation / discovery / health 等通用 production 代码。

去特例重构与真实 rebuild 验收见 `tests/eval_reports/bank_agnostic_refactor_2026-09-24.md`。

BBVA 作为“新银行只改配置、不改核心逻辑”的接入验收见
`tests/eval_reports/bbva_config_only_onboarding_2026-09-24.md`。

### `test_ai_client.py`

保护 AI/proxy 传输层重试策略：连接错误、timeout、429 和 5xx 可以有限重试；普通 4xx 不重试。
默认 `AI_MAX_CONCURRENCY=1`，避免对单订阅 proxy 形成并发压力。

### `test_lifecycle_presence.py`

保护 lifecycle 对 LLM 漏抽的鲁棒性：旧 evidence 如果仍能在该 source 最新 snapshot 中逐字/受控省略匹配，
campaign 不得仅因为本轮 extraction 缺失就变成 `removed`；只有最新页面证据也消失时才允许 removed。

### `test_reporting.py`

保护 V1.1 用户结果层：canonical campaign 聚合、独立 domain 计数、同一 source 多 snapshot 不重复计数、
latest evidence 选择、完整 evidence history / raw snapshot provenance、report filter 与 system summary。

完整 V1 系统验收见 `tests/eval_reports/v1_system_acceptance_2026-09-25.md`。

## 测试失败时怎么处理

先判断失败属于哪一种：

1. **代码 bug**：预期业务规则没变，但结果变了 → 修代码。
2. **有意修改规则**：例如把 `verified` 改成必须包含 official source → 同时修改实现、测试和 `sys.md`。
3. **新增真实案例**：线上发现新的误合并/误判 → 先把它写成 regression test，再修改规则。

不要为了让测试变绿而直接删除失败用例；这些测试本身就是历史业务决策的留痕。

## 当前测试没有覆盖什么

这批测试仍不证明：

- Playwright / requests 对真实网站一定能抓取成功；
- AI 一定能从网页中正确抽取字段；
- proxy 一定在线；
- 所有未来/未见过的网站结构都能稳定抓取；
- 30 条 discovery golden set 之外的未知网页也具有 100% precision / recall；
- LLM 输出在长期模型升级后不会产生新的边界案例。

后续应另外补：

1. 临时 SQLite 的 integration tests；
2. 固定真实网页 snapshot + 人工 ground truth 的 golden dataset；
3. 更高层级的外部告警渠道（如邮件/Slack）——当前 V1 已提供 CLI/log WARNING/CRITICAL。

## 维护原则

每当线上人工复核发现一个真实误判，应优先把该案例固化成测试，再修规则。
这样 `tests/` 会逐步成为系统业务知识的可执行历史记录，而不只是代码覆盖率工具。

