# astrbot_plugin_ai_review

基于 AstrBot 已接入大语言模型的群聊 AI 审核插件。

AI 分析群成员聊天记录，为管理员生成审核建议（风险值、违规类型、证据、建议处罚）。
**AI 仅负责辅助审核，不直接处罚用户；所有处罚行为必须由管理员确认后执行。**

## 功能特性

- **主动审核**：`/review @成员`、`/review <uid>`、`/review recent`
- **被动审核**：收到群消息后后台自动分析（可配置触发模式）
- **审核队列**：待审核列表 / 详情 / 通过 / 拒绝，超时自动失效
- **处罚策略**：warn / mute / kick / ban / blacklist，流水线模式，可配置扩展
- **皮梦云黑库同步**：通过皮梦云黑库插件同步，未安装时自动跳过
- **全异步**：被动审核以后台任务执行，不阻塞消息响应
- **配置热加载**：所有配置修改后即时生效

## 安装

将本插件目录放入 AstrBot 的插件目录即可。插件无第三方依赖。

要求：Python 3.11+，AstrBot >= 4.13.0，已配置可用的大语言模型。

## 配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `history_count` | int | 50 | 每个群缓存最近聊天条数 |
| `review_mode` | string | both | active / passive / both |
| `risk_threshold` | int | 80 | AI 风险值低于该值视为不违规 |
| `review_timeout` | int | 300 | 审核任务超时（秒） |
| `cooldown` | int | 300 | 同一用户两次自动审核最小间隔（秒） |
| `enable_blacklist` | bool | false | 是否启用黑库同步 |
| `enable_history` | bool | true | 是否启用聊天记录缓存 |
| `prompt_path` | string | 空 | 自定义 Prompt 目录，留空使用内置 |
| `whitelist` | list | [] | 白名单用户不参与自动审核 |
| `min_msg_len` | int | 2 | 过短消息不触发审核 |
| `llm_max_concurrency` | int | 3 | 同时进行的模型请求数上限 |
| `mute_duration` | int | 600 | mute 处罚禁言时长（秒） |
| `admin_qq` | list | [] | AI 调用异常时向其发送告警私聊 |
| `max_chat_chars` | int | 3000 | 发送给 AI 的聊天记录总字符预算 |
| `max_msg_chars` | int | 200 | 单条消息发送给 AI 的字符上限 |
| `punish_pipeline` | object | {} | 处罚流水线映射（见下） |

可通过 `/reviewconfig` 命令或 AstrBot 管理面板修改。

## 命令（管理员）

```
/review @成员        审核指定成员
/review <uid>        审核指定 QQ
/review recent       审核最近聊天记录
/review list         查看待审核任务
/review detail <id>  查看任务详情
/review pass <id>    通过并执行处罚
/review reject <id>  拒绝任务
/reviewconfig        查看配置
/reviewconfig <key> <value>   修改配置
```

## Prompt 自定义

Prompt 文本独立存放于 `data/prompts/`：

- `system.txt`：系统审核规则（含 `{threshold}` 占位符）
- `user.txt`：聊天记录模板（含 `{records}`、`{target}` 占位符）
- `output.txt`：输出 JSON 格式约束
- `reason.txt`：审核原因模板（`reason` 字段的格式化要求）

可配置 `prompt_path` 指向自定义目录，修改文件后自动生效。

## AI 返回格式

AI 必须返回如下 JSON，解析失败自动重试一次：

```json
{
  "illegal": true,
  "risk": 92,
  "type": "辱骂",
  "reason": "...",
  "evidence": ["...", "..."],
  "suggestion": "mute"
}
```

`risk` 为 0~100 的整数；`suggestion` 只能取 `warn` / `mute` / `kick` / `ban` / `blacklist`。

## 皮梦云黑库同步

当检测到 `astrbot_plugin_pimeng_blacklist` 插件已加载时，`/review pass` 执行
`blacklist` 处罚会自动调用其接口同步黑库；插件未安装时自动跳过，不影响插件运行。

## 处罚流水线

处罚采用流水线模式：每种建议处罚对应一个有序阶段列表，依次执行：

| 建议处罚 | 默认流水线 |
|----------|-----------|
| warn     | `warn` |
| mute     | `warn` → `mute` |
| kick     | `warn` → `kick` |
| ban      | `warn` → `ban` |
| blacklist| `warn` → `blacklist` |

可通过配置 `punish_pipeline` 覆盖，例如 `{"mute": ["warn", "mute"]}`。
阶段取值为 `warn` / `mute` / `kick` / `ban` / `blacklist`，可按需组合扩展。

## 目录结构

```
main.py              插件入口（模块装配、消息监听、命令注册）
config.py            配置中心（热加载、持久化）
models.py            数据模型（dataclass）
prompt.py            Prompt 构建
review/
  history.py         聊天记录缓存（deque）
  workflow.py        审核工作流（过滤/调用/解析/入队）
  queue.py           审核任务队列（超时失效）
  punishment.py      处罚策略（流水线）
commands/
  review.py          /review 命令
  config.py          /reviewconfig 命令
adapters/
  blacklist.py       黑库适配器接口
  pimeng.py          皮梦云黑库插件适配器
utils/
  logger.py          统一日志
  llm.py             LLM 调用客户端（限流、异常通知）
  parser.py          JSON 解析（重试）
data/prompts/        默认 Prompt 文件
```

## 免责声明

本插件仅提供审核建议，所有处罚操作均由管理员人工确认后执行。
请遵守所在群聊规则与法律法规，合理使用审核能力。
