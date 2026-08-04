# 版本历史

版本号规范：`大版本.PR序号`（自 v1.20 起）。此前版本固定为 1.0.0，直至 PR #20 确立规范。

## v1.21.1 

- 完善 `CHANGELOG.md`，补充自 v1.0.0 以来的完整版本历史。

## v1.21.0 

- 新增**二次审核**功能：考虑单一模型的局限性，初次 AI 判定违规后由第二个模型复核同一批聊天记录，两者均判违规才生成审核任务，提高审核准确性与可靠性（降低误报）。
- 二次审核为**可选**能力，默认关闭；二次审核调用/解析失败时自动回退初次判定，不中断审核。

> config / _conf_schema：新增 `enable_second_review`（默认关）+ `second_review_provider_id`（select_provider 下拉）

> models：`ReviewTask` 新增 `second_llm_provider` 字段（序列化兼容旧数据）

> llm / parser：`chat` 与 `parse_with_llm_retry` 支持按指定 provider 调用

> workflow：`_run_second_review` 初次违规后用第二模型复核；两者都违规才入队，二次判定无违规放弃，调用/解析失败回退初次判定不中断；修复初次判定模型被二次调用覆盖

> commands：`/review detail` / `list` / `pass` / 主动审核消息展示二次模型

版本 1.20 → 1.21；新增 5 项二次审核测试，共 157 项通过（9 项跳过）。

## v1.20

- **问题整改 W1-W5 + S2/S4 + 二轮 F1/死代码清理 + 版本规范**：确立版本号 = `大版本.PR序号`（本版本为第 20 个 PR）。
- W1 冷却提前：审核开始（LLM 调用前）即记录冷却起点，避免调用窗口内并发触发多次模型调用。
- W2 Punisher 全局竞争：按群配置改局部返回值，多群交错执行互不覆盖禁言时长/流水线。
- W3 群主/群管免审：`should_skip` 增加 OneBot 群主/群管判定（查询失败不豁免，宁审勿漏）。
- W4 观察期污染：`_run_review` 返回 `(task, outcome)` 六分类，LLM 失败/解析失败/入队拒绝不再计入规则判定一致率。
- W5 ReDoS 防护：`sre_parse` 嵌套量词校验，拒绝 `(a+)+` 类灾难性回溯正则。
- F1 群管免审缓存缺陷：缓存键由「群」改为「(群, 用户)」。
- S2 `_notify_admin` 检查发送返回值；S4 违规类型键上限 20 归 `other`。
- 死代码清理 D1-D11。
- 单元测试 152/152 + 冒烟测试 25/25。

## v1.19

- **group_admin 模式下群管可审批审核任务（pass/reject）+ 全链路冒烟测试**：补全群管审批闭环，本群群主/群管可审批违规任务并执行处罚流水线。
- 入口级鉴权：普通成员 pass 被拒，群主经真实入口放行并执行处罚。
- 修复 #15 test_push_command 在 #16 校验合入后失效的回归。
- 单元测试 144/144 + 冒烟测试 25/25。

## v1.18

- （未合并：`/review` 命令改本群群主/群管鉴权，已关闭，功能并入 v1.19。）

## v1.17

- `/review detail` 应用群级生效的 `regex_forward_threshold`：低于阈值或为 0 时用纯文本，达到阈值才打包合并转发。

## v1.16

- **群级审批授权**：新增群级生效配置 `regex_approval_permission`（默认 `astrbot_admin`，可配 `group_admin`）。
- `group_admin` 模式下本群群主/群管可审批**规则候选**（rule approve/deny），仅限其当前管理的群；其余命令仍限 AstrBot 管理员。
- 规则候选按平台与群分组，私聊审批绑定候选平台与群。

## v1.15

- 修复 `/review push` 命令参数在 AstrBot 前缀剥离后丢失的问题，保留 group/off/view 子模式回退。

## v1.14

- **推送目标配置命令 + 合并转发打包 + 审批命令内联**：
  - `/review push` 命令：本群设置沉淀推送方式（group / admin / off / view），复用按群覆盖 KV 持久化。
  - 合并转发打包：`/review detail` 与规则候选推送达 `regex_forward_threshold` 阈值时打包为合并转发（Nodes），失败自动降级文本。
  - 审批命令内联：`/review list` 与 `/review rule pending` 每条记录下附同意/不同意命令。
  - 推送文本首行带 `📬 [群 xxx]` 前缀。
- 新增轻量 fake astrbot 模块使 main/commands 可在无 AstrBot 环境导入，测试 89/89。

## v1.13

- **插件生命周期清理**：实现 `terminate()`，取消并等待全部受管后台任务，卸载开始后拒绝迟到协程；候选沉淀改用受管 `_spawn`。

## v1.12

- 主动发送（`Context.send_message`）结果检查：找不到匹配平台时上报错误，不再静默丢失。

## v1.11

- 修复 `/review @成员` 无法识别 AstrBot `At` 提及组件，兼容旧小写组件并忽略广播提及。

## v1.10

- **日志器 AstrBot 兼容修复**：补 `plugin_tag` 等字段，修复 `KeyError: plugin_tag` 导致插件加载崩溃（AstrBot `LogQueueHandler` 严格 Formatter）。

## v1.9

- **构建日志体系**：上下文追踪（request_id/群/用户/任务/模型 自动前缀）、结构化事件日志（`log_event`）、异常带栈；改用 AstrBot 插件专属 logger 名。

## v1.8

- **审核任务记录判定模型**：`ReviewTask` 记录实际判定模型，`/review detail` / `list` / 通过处罚时可确认；`llm_provider_id` 面板改为 `select_provider` 下拉选择。

## v1.7

- 新增 `llm_provider_id` 配置项：固定审核使用的 AstrBot 模型 Provider（支持按群覆盖）；`/review provider` 查看已接入模型。

## v1.6

- **正则规则引擎**：规则预筛省 token、候选池 + 管理员审批、按群推送目标；观察期 → 激活 → 熔断三态机制。
- 修复 `_conf_schema.json` 中 `punish_pipeline` 缺少 `items` 导致 AstrBot 加载失败（KeyError: 'items'）。
- 修复 `/review` 与 `/reviewconfig` 命令入口 `__module__` 不匹配导致指令不生效。

## v1.5

- **批次1+2 大更新**：Prompt 优化、LLM 调用加固、配置校验、KV 持久化、队列治理、按群配置覆盖、违规统计。

## v1.4

- 修复 README 中 Mermaid 图在 GitHub 渲染失败的问题。

## v1.3

- 新增被动自主审核开关（配置项 `enable_passive_review` + `/review auto on|off` 命令）。

## v1.2

- 重写 README，补充工作原理（Mermaid 图）与后端配置操作说明。

## v1.1

- 修复插件无法加载等关键 Bug，增强健壮性。

## v1.0.0

- **插件初版交付**（十阶段开发）：利用 AstrBot 已接入的大语言模型对群成员聊天记录进行分析，为管理员生成审核建议。
  - 主动审核（`/review @成员 | uid | recent`）与被动审核（自动分析）。
  - 审核队列（查看 / 详情 / 通过 / 拒绝 / 超时失效）。
  - 处罚策略（warn / mute / kick / ban / blacklist，策略模式 + 流水线）。
  - 皮梦云黑库 Adapter（弱依赖，存在即同步、缺失自动跳过）。
  - 聊天记录内存缓存（deque 自动淘汰）、过滤与冷却。
  - Prompt 独立外置（system / user / output / reason），配置热加载。
  - 全程 AGPL-3.0，作者 Ni-ShuWu & kelai141。
