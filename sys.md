# 欧洲银行信用卡活动采集系统 — 系统说明（sys.md）

> 用途：需求、架构、数据模型、验证规则与当前状态的单一事实来源，供后续校对。
> 最后更新：2026-09-25

---

## 1. 需求

### 1.1 目标
自动采集欧洲银行**卡类活动/促销**（开卡奖励、返现、积分加成、免年费、0% 分期/余额转移、推荐奖励、商户优惠），经**多信源交叉验证**后存入 SQLite，必须包含**活动有效期**并跟踪其生命周期。

### 1.2 收录范围（2026-09-23 确认）
| product_scope | 含义 | 例子 |
|---|---|---|
| `credit_card` | 与信用卡/签账卡绑定的活动 | Amex Gold 5 万积分开卡奖励、HSBC 0% 余额转移 |
| `card_bundle` | 开户/转户/套餐类奖励，套餐内含支付卡（多为借记卡） | Crédit Agricole“开户最高送 100 €” |

两类都收录并打标签，查询时用 `list --scope` 过滤。原因：法国、西班牙等市场的“开卡奖励”大多是开户+借记卡套餐，只收信用卡会导致 CA/BBVA 几乎无数据。
不收录：贷款、房贷、储蓄、纯保险产品的活动，以及非促销性质的产品功能。

### 1.3 功能需求
| 编号 | 需求 | 实现位置 |
|---|---|---|
| F1 | 抓取银行官网（静态/JS 页面、PDF） | `crawler/fetch.py`, `crawler/runner.py` |
| F2 | 抓取第三方信源（比价站、评测站、新闻） | 同上，`source_type=third_party` |
| F3 | AI 参与采集：selector 失效时自愈 | `crawler/ai_fallback.py` |
| F4 | AI 发现官网候选页 + 二阶段内容验证 + source recommendation（最终人工确认） | `crawler/ai_discovery.py`, `crawler/content_validator.py`, `main.py discover/validate-leads/leads` |
| F5 | AI 结构化提取活动字段，日期归一化为 ISO，标注 product_scope | `extractor/` |
| F6 | 多信源交叉验证（verified / unverified / conflict） | `validator/` |
| F7 | 有效期与生命周期（upcoming/active/expiring/expired/removed） | `validator/rules.py::lifecycle` |
| F8 | 全链路可追溯：每条结论可回溯到原始快照 | `raw_snapshots` + `data/snapshots/` |

### 1.4 非功能需求
- **合规**：控制频率（默认每请求间隔 3s）；**不绕过反爬保护**（遇到 WAF 403 则禁用该源，改用第三方信源）；只采集公开营销信息，不涉及个人数据。
- **成本**：内容 hash 未变化则不产生新快照、不调用 AI；AI 并发默认 1。
- **可移植**：本地与云端同一入口 `python main.py run`，仅触发方式不同。
- **密钥**：proxy 地址与 API key 只存在 `.env`（权限 600，已 gitignore），不写入代码或文档。

---

## 2. 技术栈
- Python 3.14（`.venv`）
- 采集：`requests` + `Playwright`（Chromium headless）+ BeautifulSoup + pypdf
- AI：自建 **Codex CLI proxy（CLIProxyAPI，OpenAI 兼容）**，局域网 `192.168.178.106:8317/v1`，通过 `openai` SDK 调用
  - 当前/默认优先模型：`gpt-5.6-sol`（`.env` 中 `AI_MODEL` 可切换）
  - proxy 可用模型（2026-09-23）：gpt-6-luna / gpt-6-sol / gpt-6-astra / gpt-5.6-luna / gpt-5.6-sol / gpt-5.6-terra / gpt-5.5 等；抽样测试 gpt-5.5、gpt-5.6-*、gpt-6-* 均能输出合法 JSON 并正确解析德语日期，gpt-6 系列最快（~2s/次）
  - 注意：proxy 的客户端 key 必须在其 `config.yaml` 的 `api-keys` 中；`remote-management.secret-key` 不能用于 `/v1` 接口
- 存储：SQLite（`data/bank_campaigns.db`），原始网页/文本落盘在 `data/snapshots/<source_id>/<hash>.{html,pdf,txt}`

---

## 3. 架构

```
config/banks.yaml ──sync──▶ banks / sources
        │
        ▼
[采集层 crawler]  requests / Playwright
   ├─ selector 抽取文本 ──(文本 < 200 字)──▶ AI 自愈 selector（验证命中后回写 sources.selector）
   ├─ 仍失败 ──▶ 回退整页文本
   └─ sha256(text) 与最近快照相同 ──▶ unchanged（跳过后续所有步骤）
        │ new_snapshot
        ▼
[提取层 extractor]  Codex proxy → JSON（按 24k 字符分块）
   └─ 清洗：类型/范围枚举校验、日期 ISO 校验、start>end 纠正、
            evidence_quote 必须是原文子串，否则 confidence 压到 ≤0.3（防幻觉）
        │ campaign_extractions
        ▼
[验证层 validator]
   ├─ 只使用每个源「最新快照」的提取结果（代表网页当前状态）
   ├─ 官网结果优先作为种子创建 campaign，第三方结果挂靠
   ├─ 匹配：见第 5 节
   └─ 重算每个 campaign 的字段、验证状态、生命周期
        │
        ▼
     campaigns  ←→  campaign_sources（记录支撑信源与匹配方式 seed/rule/ai）
```

### 3.1 AI 调用点（全部经过 `ai_client.py` 的并发闸门）
| 调用点 | 触发条件 | 频率 |
|---|---|---|
| selector 自愈 | 配置的 selector 抽出文本 < 200 字 | 低（页面改版时） |
| 结构化提取 | 新快照（内容变化） | 中（首轮 18 页 ≈ 21 次调用，约 4.5 分钟） |
| 实体仲裁 | 相似度 0.35–0.75，或卡名非完全一致 | 低（首轮 7 次） |
| 线索发现 | 手动/每周执行 `discover` | 低 |

AI 发现是**有依据的**：只让 AI 对页面上真实存在的链接打分，不允许其凭空生成 URL。

---

## 4. 数据模型（SQLite）

| 表 | 作用 | 关键字段 |
|---|---|---|
| `banks` | 银行 | `code`（唯一，如 `amex_de`）, `name`（**只写银行名，不写卡产品名**，见 5.2） |
| `cards` | 卡产品（提取时自动创建） | `bank_id`, `name` |
| `sources` | 信源 | `source_type` official/third_party/discovered, `domain`, `fetcher`, `selector`, `enabled`, `last_error` |
| `raw_snapshots` | 原始快照（仅内容变化时新增） | `content_hash`, 文件路径, `extracted` |
| `campaign_extractions` | 单次 AI 提取结果 | `product_scope` + 活动字段 + `start_date`/`end_date` + `evidence_quote` + `confidence` |
| `campaigns` | 聚合后的对外活动实体（派生数据，可重建） | `product_scope`, `verification_status`, `lifecycle_status`, `start_date`, `end_date`, `verification_notes`, `first_seen_at`/`last_seen_at`/`last_verified_at` |
| `campaign_sources` | 活动 ↔ 提取记录 多对多 | `match_method` seed/rule/ai |
| `discovery_leads` | AI 发现的候选页 | `score`, `status` pending/accepted/rejected |

- 活动类型枚举：`welcome_bonus, cashback, points_multiplier, fee_waiver, interest_free, referral, merchant_offer, other`
- Schema 迁移：`storage/db.py::MIGRATIONS`，`init` 时自动为旧库补列。

---

## 5. 交叉验证规则（`validator/`）

### 5.1 匹配（提取记录 → 活动）
1. 银行或活动类型不同 → 不匹配。
2. **同一页面的两条记录永不合并**（一页中每个活动只列一次，同页两条 = 两个活动，例如 Gold 与 Gold Rosé 并列）。
3. 相似度 = 0.4×标题相似度 + 卡名关系分 + 奖励数值关系分 ± 结束日期（±0.1）
   - 卡名关系：same +0.3 / unknown +0.1 / similar +0.1 / different → 直接不匹配
   - 奖励数值（数字集合比较，支持 `85,000`/`85.000`/`1 060`）：有交集 +0.3 / 未知 +0.1 / 无交集 −0.3
4. 相似度 ≥0.75 **且卡名完全一致** → 规则匹配；否则相似度 ≥0.35 → AI 仲裁（最多比较 3 个候选）；否则新建活动。

### 5.2 卡名归一化
去掉重音、大小写，移除通用词（card/karte/carte/tarjeta/credit…）和**银行名中的词**后比较词集合：相等 = same，子集 = similar（交给 AI），无关 = different。
因此 `banks.name` 里不能写卡产品名（曾把 Advanzia 写成“Advanzia Bank (Gebührenfrei Mastercard Gold)”，导致卡名被全部剥离）。

### 5.3 验证状态
1. **独立性按域名计**：同一域名的多个页面只算 1 个信源。
2. **最低置信度**：confidence < 0.5 的提取不参与验证（包括 evidence_quote 不在原文中的）。
3. **verified**：≥2 个独立域名支持，且结束日期、奖励数值无分歧。
4. **conflict**：`end_date` 不一致，或任意两个信源的奖励数字集合无交集 → 人工复核；展示字段仍以官网为准。
5. **unverified**：仅 1 个域名支持。
6. **主记录选择**：官网 > 高置信度 > 最新提取。
7. **removed**：此前存在的活动在所有信源的最新快照中都消失（已过期的保持 `expired`）。
8. **生命周期**：`start_date > 今天` → upcoming；`end_date < 今天` → expired；距结束 ≤14 天 → expiring；否则 active（无结束日期视为 active）。

---

## 6. 首批目标银行与信源状态（2026-09-23 实测）

| 银行 | code | 官网 | 第三方 | 备注 |
|---|---|---|---|---|
| American Express DE | `amex_de` | ✅ 2 页（Playwright） | ✅ meilenoptimieren, travel-dealz, reisetopia | 2026 年起开卡奖励与消费额挂钩且**长期有效、无官方截止日** → `end_date` 为空属正常 |
| HSBC UK | `hsbc_uk` | ✅ 3 页 | ✅ finder.com, ✅ MoneySavingExpert（需 Playwright） | 主要为 0% 余额转移/购物期，多为“up to”报价 |
| Advanzia（Gebührenfrei Mastercard Gold） | `advanzia` | ✅ gebuhrenfrei.com（requests 403，Playwright 可） | ✅ kreditkarten360, goldgebuhrenfrei | advanzia.com 为集团站无卡片信息 |
| BBVA España | `bbva_es` | ❌ Akamai 403（requests 与 headless 均被拒，**已禁用，不做绕过**） | ✅ helpmycash（Playwright）；❌ elEconomista 403 | **只有 1 个可用信源，无法达到 verified** |
| Crédit Agricole FR | `ca_fr` | ✅ 2 页（Playwright；comparatif 页会跳转到 ouvrir-un-compte） | ✅ selectra, moneyvox | 活动多为 `card_bundle`；各地区 Caisse 价格不同 |

### 6.1 首轮真实结果（gpt-6-sol，2026-09-23）
18 个快照 → 51 条提取 → 51 条中 14 条合并到已有活动（7 条经 AI 仲裁），共 37 个活动，**6 个 verified，0 conflict**：

| 银行 | 活动 | 范围 | 有效期 | 支撑信源 |
|---|---|---|---|---|
| amex_de | Platinum 开卡奖励 最高 85,000 MR | credit_card | 无截止 | americanexpress.com, meilenoptimieren, travel-dealz |
| amex_de | Gold 开卡奖励 最高 50,000 MR | credit_card | 无截止 | americanexpress.com, meilenoptimieren, reisetopia |
| amex_de | Gold Rosé 开卡奖励 最高 50,000 MR | credit_card | 无截止 | americanexpress.com, travel-dealz, reisetopia（两站均明确提到 Rosé） |
| advanzia | 推荐好友各得 40 € | credit_card | 无截止 | gebuhrenfrei.com, kreditkarten360, goldgebuhrenfrei |
| hsbc_uk | 余额转移 0% 最长 36 个月 | credit_card | 无截止 | hsbc.co.uk, finder, MoneySavingExpert |
| ca_fr | 开户最高送 100 € | card_bundle | 至 2026-12-31 | credit-agricole.fr, selectra, moneyvox |

其余值得关注：HSBC “首年年费等额返现”至 2026-09-28（expiring，仅官网）；Amex × RIMOWA 抽奖至 2026-09-30（expiring，仅 meilenoptimieren）。

### 6.2 回归用例
- BBVA 开户奖励 PLAN1060 截止日：官网 2026-10-20 vs elEconomista（6 月）2026-07-22（官方延期）→ 预期 `conflict`，以官网为准（规则层已验证）。
- Amex Gold vs Gold Rosé：同页并列 → 必须是两个活动。
- CA“最高 100 €”：官网 + 2 个第三方 → 预期 `verified`, `card_bundle`, 2026-12-31。

---

## 7. 运行方式

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env        # 填写 AI_BASE_URL / AI_API_KEY / AI_MODEL

.venv/bin/python main.py init                  # 建表/迁移 + 同步 banks.yaml
.venv/bin/python main.py run [--bank X]        # crawl → extract → validate
.venv/bin/python main.py list [--all] [--scope credit_card|card_bundle]
.venv/bin/python main.py sources               # 信源健康状况 / 最近错误
.venv/bin/python main.py health                # health level + 连续失败次数 + HTTP/延迟
.venv/bin/python main.py health --bank amex_de # 按银行查看 health
.venv/bin/python main.py discover              # AI 发现新页面线索
.venv/bin/python main.py validate-leads        # 抓取候选正文并做第二阶段 content validation
.venv/bin/python main.py validate-leads --redo # 重跑仍为 pending 的已验证 lead
.venv/bin/python main.py leads --recommended   # 只看自动推荐 source
.venv/bin/python main.py leads --review        # 只看需人工判断的边界项
.venv/bin/python main.py leads --validator-reject # 查看自动过滤项
.venv/bin/python main.py leads [--accept ID | --reject ID] # 最终人工闸门

# 修改提取提示词或验证规则后：
.venv/bin/python main.py extract --redo        # 丢弃并重新提取所有快照
.venv/bin/python main.py validate --rebuild    # 由提取记录重建 campaigns

# 离线回归测试（不访问网络、不调用 AI、不修改真实数据库）
.venv/bin/python -m unittest discover -s tests -v

# Amex GPT-5.6 Sol live evaluation（读真实 snapshot，不写正式数据库）
.venv/bin/python tests/eval_amex_gpt56.py

# Discovery golden benchmark（不访问网络/AI，读取当前验证结果）
.venv/bin/python tests/eval_discovery_golden.py
```

测试的业务含义、真实回归案例、失败后的处理原则见 `tests/README.md`。`tests/` 被视为
验证规则的“可执行历史记录”：线上发现新的误判时，应先新增 regression test，再修改规则。

调度：本地 `crontab -e` 添加 `0 6 * * * cd <repo> && .venv/bin/python main.py run >> data/run.log 2>&1`（或 launchd）；云端用同一命令配 CronJob/Cloud Scheduler，挂载 `data/` 持久卷。

---

## 8. 当前状态与待办

**已完成（截至 2026-09-25）**
- V1 全部功能已完成；当前 24 个 source 入库，21 个启用 source 全部 health=`OK`，3 个不可访问/历史 source 主动禁用。
- 接通 Codex proxy，完成首轮真实提取与交叉验证（见 6.1）。
- 修正首轮暴露的问题：卡名精确匹配导致漏合并、奖励数值比较粗糙、同页不同卡误合并风险、法国/西班牙开户类活动被提示词排除（新增 `product_scope`）。
- Amex entity resolution 第一轮修复：允许同页重复 offer 在卡身份明确时去重；非完全一致卡变体走保守 AI 仲裁；
  `card_name=null` 且其余证据强匹配时进入 AI 仲裁。GPT-5.6 Sol 对 5 个真实 Amex snapshot 的 live evaluation
  得到 25 条 extraction、`card_name` 空值 0、Gold/Gold Rosé 不再合并成一个 card_name。详见
  `tests/eval_reports/amex_gpt56_2026-09-23.md`。
- 已用新 prompt + entity resolution 正式重建 `amex_de`：28→29 条 extraction，22→17 个 campaign，
  3 个 verified 保持不变；减少的 5 个 campaign 对应 Blue/PAYBACK/BMW 的 5 组已确认重复。
  结果见 `tests/eval_reports/amex_production_rebuild_2026-09-23.md`，并新增内存 SQLite integration regression。
- 已执行首轮全量 AI `discover`：实际持久化 67 个唯一 lead。抽查显示 recall 高但 precision 不足以自动 accept；
  强候选包括 Advanzia Sonderaktionen/Referral、HSBC Rewards 与两个 Summary Box PDF、CA 开户 100€、Amex referral。
  同时发现并修复 discover 重复候选导致“90 new leads”虚高的计数 bug。详见
  `tests/eval_reports/discovery_2026-09-23.md`。
- Discovery 二阶段已完成：URL prefilter → HTML/PDF fetch → GPT-5.6 Sol page classification → evidence quote
  校验 → standing-feature 过滤 → deterministic recommendation policy。历史 URL canonicalize 后共有 **66 个唯一 lead**，
  最终为 **33 recommended / 2 review / 31 reject**；recommended 中 `primary=30`、`supporting=3`。
  `supporting` source 已接入 validator：只能给已有 campaign 补证据，不能 seed 新 campaign。
  30 条人工 golden benchmark 当前 precision=100%、recall=100%、status+role accuracy=100%；最终离线测试基线为 80/80。
  完整验收见 `tests/eval_reports/discovery_stage2_2026-09-24.md`。
- Source Health 已完成：每个 source 持久化最近尝试/成功时间、连续失败次数、HTTP status 和 latency；
  健康等级为 `UNKNOWN/OK/DEGRADED/WARNING/CRITICAL/DISABLED`，默认连续失败 3 次进入 WARNING、5 次进入 CRITICAL，
  成功后自动清零。最终 V1 状态为 **21 OK / 3 DISABLED / 0 DEGRADED/WARNING/CRITICAL**。详见
  `tests/eval_reports/source_health_2026-09-24.md`。
- 重点 source CSS selector 优化已完成：Amex welcome article=`main`、MSE=`article`、HelpMyCash=`main`、
  CA 两个官方页=`main#content`，并在最终幂等性验收中继续补充 Moneyvox=`.fiche-produit`、
  Reisetopia=`.main-content > article`、Advanzia=营销区块组合 selector。动态新闻/相关推荐/申请表单 UI 不再制造
  假 snapshot；最终全量 run 达到 **21 unchanged / 0 new snapshot / 0 error**。完整验收见
  `tests/eval_reports/selector_optimization_2026-09-24.md`。
- Core bank-agnostic 重构已完成：production entity-resolution/extraction/discovery/health 逻辑中不再包含具体
  银行或产品名规则；删除产品 alias 硬编码，模糊产品身份统一通过 token/reward/mechanics/date/candidate
  uniqueness + 保守 AI arbiter 处理，并新增自动 guard 防止具体实体知识重新进入核心代码。真实 Amex rebuild
  得到 **16 active / 3 verified / 0 conflict**，扩展命名正确并回基础实体、独立 edition 仍保持分离。
  详见 `tests/eval_reports/bank_agnostic_refactor_2026-09-24.md`。
- BBVA 配置化接入验收已完成：在不修改任何核心 Python 的前提下，仅通过 `banks.yaml` 新增 Finect、
  iFinanzas、Roams 三个可访问独立域，并将持续 403 的 elEconomista 禁用。三源都由通用 extractor 提取为
  同一个 `card_bundle / welcome_bonus` PLAN1060 活动，统一截止日 `2026-10-20`，最终由 3 个独立 domain
  得到 **verified**；BBVA 正常调度为 **4 OK / 3 DISABLED / 0 error**。详见
  `tests/eval_reports/bbva_config_only_onboarding_2026-09-24.md`。
- AI/proxy 韧性已完成：`AI_MAX_CONCURRENCY=1`，连接/timeout/429/5xx 默认有限重试 2 次并指数退避；
  普通 4xx 不重试，避免一个代理异常拖死整条 pipeline。
- Lifecycle presence guard 已完成：当本轮 LLM 漏抽某活动时，先用历史 grounded evidence 检查最新 source snapshot；
  evidence 仍存在则保持 lifecycle，不再产生假 `removed`。真实验收中 3 条误 removed 活动自动恢复 active。
- V1 System Acceptance 已完成：最终全量 run 为 **0 new / 21 unchanged / 0 crawl error，0 extract，0 validate create**；
  33 个 canonical campaigns 中 7 verified、0 conflict、0 removed；75 个 discovery leads 无自动处理积压；
  21 个启用 source 全部 OK；DB integrity 正常；80/80 tests 通过。详见
  `tests/eval_reports/v1_system_acceptance_2026-09-25.md`。

**待办**
- [x] BBVA 补充 3 个可访问独立西班牙信源并完成 config-only onboarding / verified 验收。
- [x] Amex duplicate / `card_name` entity resolution 第一轮修复与 GPT-5.6 Sol 真实 snapshot 评估。
- [x] 对高成本/高噪声 source 做 CSS selector 优化并 live 验收；低收益或会丢 evidence 的 source 保持整页抓取。
- [x] 为 `validator/rules.py` 与 `extractor/schema.py` 补离线单元测试，并覆盖 6.2 的
      BBVA 日期冲突、Amex Gold/Gold Rosé 同页不误合并、CA 多域验证回归用例。
- [x] Source Health + 连续失败告警：3 次 WARNING / 5 次 CRITICAL，可配置阈值，并提供 `health` CLI。
- [x] 跑一次 `discover` 并完成人工/内容抽查；当前策略保持 `pending` 人工复核，不自动 accept。
- [x] Discovery 第二阶段 content validation / source recommendation：过滤 application、通用条款、教育文章、
      personalized hub 与 standing feature；支持 evidence guard、primary/supporting role、golden benchmark 与人工 accept 闸门。
- [x] V1 System Acceptance：全量 run 幂等、增量 discovery、health、lifecycle、duplicate、DB integrity、80-test regression 全部通过。
- [x] V1.1 Reporting MVP：已增加独立 `reporting/` 用户结果层，把 canonical campaign 与多个
      extraction/source 聚合为用户可读对象；默认输出银行/产品、活动名称、summary、reward、conditions、
      start/end date、lifecycle、verification 与 independent source count。支持 bank/type/verified/expiring 过滤。
- [x] V1.1 Evidence / Original Page：每个最终事实可 drill down 到证据来源，至少保留并展示 `source_url`
      （原始网页 live URL）、domain/source type、`evidence_quote`、confidence、fetched_at、snapshot_id；同时提供
      当时抓取的原始 snapshot（raw HTML/PDF + normalized text）入口。即使 live 页面之后修改或下线，也必须能
      回看系统当时用于提取/验证的原始页面版本；同 source 的历史 evidence/snapshot 通过 `evidence_history` 保留。
- [x] V1.1 Report 输出：默认面向最终用户展示当前有效活动；支持 `--verified-only`、`--bank`、`--type`、
      `--expiring`、`--json`，并支持单 campaign 的 evidence/source drill-down。JSON 输出同样必须带 provenance，
      不能把无法回溯到 source/snapshot/evidence 的 AI 总结当作已验证事实。
- [ ] V1.1 Fact Enrichment：把当前较宽的 `conditions` / supporting conditions 进一步语义拆成明确的
      `eligibility[]`、`requirements[]`、`benefit_components[]`，并要求每个聚合 fact 带 extraction/snapshot
      evidence refs；应缓存结果，避免每次 `report` 都重新调用模型。当前 Reporting MVP 不伪造这层结构。
- [ ] 轮换曾在对话中明文出现过的 proxy key。
