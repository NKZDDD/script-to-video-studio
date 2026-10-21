# Studio 登录服务 API v1

域名示例：`https://auth.example.com`。所有 POST 使用 `Content-Type: application/json`。所有时间字段为 Unix 秒；请求体上限 16 KiB。不要把密码或 Token 放在 URL 查询参数里。

由 Studio Python 后端请求；本服务不开放跨域浏览器调用。下面的密码和凭证均为占位示例，不是可用账号。

## 1. 登录

`POST /api/v1/auth/login`

```json
{
  "username": "customer01",
  "password": "由用户输入的密码",
  "device_id": "c019fd05-bace-4436-a17a-f64a3442d981",
  "device_name": "剪辑电脑"
}
```

成功 HTTP 200：

```json
{
  "token_type": "Bearer",
  "access_token": "随机访问凭证",
  "refresh_token": "随机续期凭证",
  "access_expires_at": 1800000900,
  "refresh_expires_at": 1802592000,
  "session_id": "服务端会话 UUID",
  "server_time": 1800000000,
  "check_after_seconds": 60,
  "user": {
    "id": "内部用户 UUID",
    "username": "customer01",
    "expires_at": null,
    "max_devices": 0
  }
}
```

`expires_at=null` 表示账号无到期日；会话仍有有效期。设备 ID 必须 16–128 位字母数字或 `_-`；建议首次安装生成 UUID 并保留。设备名称仅用于管理员查看，不用于认证。

登录限流：每 IP 每 5 分钟最多 30 次，每账号每 5 分钟最多 10 次，成功和失败都计数。限流写入数据库，重启不会清零；达到上限返回 429 和 `Retry-After` 秒数。

## 2. 在线校验

`POST /api/v1/auth/verify`

请求头：`Authorization: Bearer <access_token>`

```json
{"device_id": "c019fd05-bace-4436-a17a-f64a3442d981"}
```

成功 HTTP 200：包含 `valid:true`、`user`、`session_id`、`access_expires_at`、`server_time`、`check_after_seconds`。

只有实际成功且 `valid` 严格为 `true` 才允许通过。不能拿本地缓存的账号状态或“曾经登录过”代替在线检查。客户端建议 60 秒检查一次，在启动新生产批次前额外校验。

## 3. 续期

`POST /api/v1/auth/refresh`

```json
{
  "refresh_token": "上次登录或续期返回的凭证",
  "device_id": "c019fd05-bace-4436-a17a-f64a3442d981"
}
```

成功返回与登录相同的结构。每次续期都会更换 access_token 和 refresh_token，**旧的两个凭证立即失效**。会话最长截止时间保持不变。

同一个 refresh_token 只能使用一次，重复使用会撤销整个会话。客户端必须锁住续期操作，避免多个生产线程同时续期；新 Token 要作为一对更新。

如果续期请求超时，服务端可能已经成功轮换，不要自动重复使用旧 refresh_token；清除本地登录状态并提示重新登录。此行为不会取消或重提任何生产任务。

续期每 IP 每 5 分钟最多 120 次。访问凭证到期返回 401 `access_expired` 时，客户端可以续期一次并重新校验。

## 4. 退出

`POST /api/v1/auth/logout`

请求体同续期，成功 `{"ok":true}`。退出可重复调用，撤销对应会话。客户端无论退出请求成功与否，都应清除本地凭证；网络失败时不能声称服务端已撤销。

## 5. 健康检查

`GET /health` 返回服务名、版本和数据库可访问状态，不返回账号、配置路径或密钥。本机 HTTP 健康检查允许；认证接口默认要求 HTTPS。

## 6. 错误格式

```json
{"error":{"code":"account_expired","message":"账号使用期限已到，请联系管理员"}}
```

| HTTP / code | 客户端处理 |
|---|---|
| 401 `invalid_credentials` | 提示账号或密码错误 |
| 401 `access_expired` | 串行续期一次，再校验 |
| 401 `invalid_session` / `refresh_reused` | 清除凭证，重新登录 |
| 403 `account_disabled` / `account_expired` | 禁止新生产，提示联系管理员；不要重新提交旧任务 |
| 403 `device_limit` | 退出其他设备或由管理员撤销旧登录 |
| 429 `rate_limited` | 按 Retry-After 等待，不紧密循环 |
| 503 `auth_busy` | 稍后重试登录，不跳过校验 |
| 400 `https_required` | 检查 HTTPS 与反向代理头 |
| 413 / 415 / 422 | 修正请求体大小、类型或字段 |
| 网络失败 / 500 | 提示登录服务不可用，不能视为授权成功 |

未知字段会被拒绝，包括 `api_key`。不要发送服务商密钥、模型名或项目内容。

## 7. 客户端示例

```python
from studio_auth_client import StudioAuthClient, LoginError

# 实际代码首次安装时生成并保存 device_id，后续一直复用。
auth = StudioAuthClient("https://你的登录域名", saved_device_id, "剪辑电脑")
auth.login(username, password)

try:
    user = auth.verify()  # 执行新生产前，失败则不启动新生产
except LoginError as exc:
    show_login_error(exc.code, str(exc))
else:
    start_local_production()  # 服务商 Key 和调用继续留在本机
```

示例 SDK 只提供接口调用。Studio 源码已接入登录页、启动拦截和心跳；旧 EXE 需要更新客户端，部署本服务不会自动修改旧 EXE。
# 网页管理接口（1.1.0）

## 1.2.0 配置下发与用量对账补充

`POST /api/v1/auth/verify` 验证成功时新增 `provider_config` 对象，按服务商 ID 返回已分配的 Key 字段、`base_url`、`assignment_id`。只向该有效登录账号下发；禁止浏览器 Origin 调用，不提供跨账号查询。响应必须只保留在客户端后端内存，不写入本机配置。

- `GET /admin/api/users/{name}/providers`：列出服务商、已配置字段名和版本，不返回 Key 值。
- `POST /admin/api/users/{name}/providers`：独立管理员权限、Origin 和 CSRF 校验；字段为 `provider`、`base_url`、`api_key`、`image_1k_api_key`、`image_4k_api_key`、`video_api_key`、`remove`。Key 留空保留；`remove:true` 移除整家分配。地址必须为无认证参数的 HTTPS URL。
- `POST /api/v1/auth/usage`：普通账号 Bearer 凭证，正文 `{device_id, events:[...]}`，每批最多 20 条且整个请求不超过 16 KiB。服务端由登录身份确定账号，不接受客户端指定其他用户。
- `GET /admin/api/users/{name}/usage`：独立管理员权限；支持 `page`、`provider`、`source`、`since`、`until`。时间为 Unix 秒，按客户端报告的开始时间筛选。`export=true` 导出最多 10000 条 CSV，超过限制明确报错。

用量事件字段：`event_id`（UUID）、`sequence`（递增版本）、`provider`、`model`、`kind`（image/video）、`source`（managed/personal/unknown）、`assignment_id`、`task_id`、`status`（attempted/submitted/completed/failed/pending/unknown）、`resumed`、`started_at`、`updated_at`。不接受额外字段。`user_id + event_id` 唯一；较旧版本忽略、同 ID 核心归属字段变化拒绝。提交时间、来源及状态均为客户端声明，不构成可信账单。已提交 ID 的取回沿用原事件；无历史事件的旧任务 `resumed:true, source:unknown` 单独展示。

网页入口 `GET /admin/`。管理员由终端 `add-admin` 创建，和下列 Studio 普通账号认证接口完全独立。

管理接口前缀 `/admin/api/`，要求 HTTPS。所有 POST 须为 JSON、携带与站点相同的 Origin；除登录外还须携带 HttpOnly Cookie 对应的 `X-CSRF-Token`。登录与 `GET me` 返回 CSRF 值，凭证本身只在 Secure / SameSite=Strict Cookie 中返回，8 小时绝对过期。

| 方法与路径 | 用途 |
|---|---|
| POST login | `{username,password}` 管理员登录 |
| GET me | 当前管理员和 CSRF token |
| POST logout | 撤销管理会话 |
| GET users?q=&page=1 | 普通账号列表，每页 50 条，按账号搜索 |
| POST users | 创建普通账号；username、password、expires_at（UTC 秒/null）、max_devices（0 不限） |
| POST users/{name}/settings | 按提交字段更新 enabled、expires_at、max_devices |
| POST users/{name}/password | `{password}` 重置密码并撤销旧登录 |
| POST users/{name}/revoke | 撤销普通账号的全部登录 |
| GET users/{name}/sessions | 最近 100 条设备登录记录；不返回凭证或哈希 |

管理员重置密码仅通过终端 `password-admin`，会撤销旧管理会话。普通账号和客户端 Bearer 凭证不具有管理权限。所有接口继续使用本文的统一错误结构，页面不会返回密码哈希。

## 1.3.0 Key 库

- `GET /admin/api/keys`：返回可命名 Key 库和服务商中文名称，不含凭据。
- `POST /admin/api/keys`：新建/编辑，字段 `id`（编辑时填写）、`provider`、`name`、`enabled`、`base_url`、`api_key`、可选 `models`（模型 ID 数组）。保留旧分组字段兼容迁移。空密钥保留，`models:[]` 恢复自动拉取；服务商不可在已有记录上修改。
- `GET/POST /admin/api/users/{name}/keys`：读取或替换账号授权，POST 为 `{key_ids:[...]}`；重复或无效 ID 整体拒绝。同模型重叠按列表顺序选择，不做失败换 Key。
- 有效 `verify` 返回 `provider_config[provider].keys`，每项含 `key_id`、`key_name`、`assignment_id`、凭据、地址及可选 `models`。顶层首项字段保留旧协议兼容，但完整多 Key 功能必须更新客户端。停用记录不下发。
- 用量事件增加可选 `key_id`、`key_name`；查询增加 `key_id` 筛选，汇总按具体 Key 分组，CSV 包含这两列。旧事件缺失字段按空值处理，不猜测归属。
- 库最多 2000 条；单账号最多 256 条、48 家服务商、每家 32 条。显式模型绑定最多 100 个、每个 ID 最长 200 字符，仍受请求体大小限制。

所有新增管理接口沿用独立管理员会话、HTTPS、Origin 和 CSRF 校验。修改库中共享 Key 会影响已授权的所有账号；旧批次保持已冻结配置。

## 1.3.1 config 导入

- `POST /admin/api/keys/import/preview`：`{content:"config.json 文件文本"}`，返回不含密钥的 `rows` 和 `warnings`。每行含 `index`、`name`、`provider`、`field`、`base_url`、`models`、`duplicate`。不写入。
- `POST /admin/api/keys/import/commit`：`{content:"同一文件文本",selected:[0,1]}`，重新解析校验、按索引选取，事务新建，返回 `added`、`skipped`、`key_ids`；不覆盖记录或分配账号。
- 仅以上两个路径的请求体上限为 2 MiB（容纳 JSON 转义），`content` 最多 524288 字符；页面文件上限 512 KB。其他接口保持 16 KiB。一次最多 256 条。
- 输入必须为含 `providers` 对象的 Studio 配置，重复 JSON 字段拒绝。只读取支持的凭据字段及 HTTPS 地址、显式模型绑定；错误响应不包含原始值。重复导入不启用已停用 Key。


## 客户端版本策略（1.6.0，兼容 1.5.0 发布请求）

`GET /api/v1/updates/latest?flavor=all` 返回 `release`、`policy_protocol:1`、`client:{allowed,code,message}`。客户端应发送以下 ASCII 请求头，登录、verify、refresh 也必须发送：

```
X-Studio-Version: 2026.10.20.2
X-Studio-Flavor: all
X-Studio-Build: <release.json 中的 build_sha256>
```

管理员 `POST /admin/api/releases` 接收 `version`（推荐版本）、`flavor`、`url`、`sha256`、`bytes`、`notes`、`build_sha256`（构建清单的 SHA256）和可选 `minimum_version`（最低可用版本）。省略最低版本时保留当前值。最低版本不能下降、不能高于推荐版本，且必须为同体系已登记构建或本次推荐版本。同版本构建校验码不能替换，推荐版本不能回退。`enforce_build` 仍由服务端维护，不能在请求中修改。

兼容旧请求 `mandatory:true`，等同最低版本设为本次 `version`；如同时传入不一致的 `minimum_version` 则拒绝。新后台使用独立的最低版本选择框，返回的 `mandatory` 表示最低版本是否等于当前推荐版本。

管理员 `GET /admin/api/releases` 返回 `releases` 和 `builds:[{flavor,version}]`，供最低版本下拉框选择。这些修改均需管理员会话、同源和 CSRF 校验。

安装包 URL 支持公网 HTTPS 的 R2 公开域名、自定义域名、原本站 `/downloads/`，仅接受直接返回 EXE 的地址，不允许临时签名查询串、明文 HTTP、内网 IP、非标准端口、R2 S3 API 域名或路径穿越。认证服务不下载文件，不需要 R2 Key；发布端上传工具核对完整公网下载 SHA256，客户端仍独立检查字节数和 SHA256、不跟随重定向、不向下载域名发送登录凭据。旧安装版 2026.10.20.1 及以前无法直接接收 R2 地址，切换时需手动安装一次新版。

一旦任一体系正式发布含构建校验码的版本，缺少/伪造体系、未登记版本、版本低于最低值、指纹不符的 login/verify/refresh 返回 HTTP 426，错误码 `update_required` 或 `client_unregistered`。已登录会话也必须通过验证才能取得 provider_config。logout 和 usage 仍允许已授权会话清理及补传记录；不会借此下发 Key。为便于服务端先升级，尚未正式启用任何体系的构建校验时兼容旧客户端；新版带版本头的客户端必须完成登记才放行。单独登记测试构建不会开启对旧客户端的拦截。

版本和指纹是公开的客户端声明，不能提供防破解或设备可信证明；详见 CLIENT-UPDATE-UPGRADE.md。

## 1.6.1 暂停／恢复版本校验

管理员 `POST /admin/api/releases/enforcement`：`{"enabled":false}` 暂停，`{"enabled":true}` 恢复，沿用管理员认证、同源和 CSRF 校验；返回 `enforcement_enabled`。管理员版本列表也返回此布尔字段，独立于每个体系的发布信息。

默认开启，数据库持久化，所有体系共同生效，重启或发布新版本不会自行恢复。暂停不修改推荐版本、最低版本和构建记录；恢复后重新使用原规则。操作写入管理员审计，命令行操作标记为 `local-cli`。

暂停时公开更新接口返回 `enforcement_enabled:false`、`release:null`、`policy_protocol:1` 和 `client:{allowed:true,code:"version_check_paused",...}`。暂时隐藏推荐是为了让只支持同域下载的旧安装版不被 R2 URL 解析挡住；管理员仍可查看和发布版本。login/verify/refresh 不再检查版本和构建，但密码、会话、停用、到期、设备限制和 Key 分配检查保持正常。不会因公开接口的 `allowed:true` 就取得登录凭据或服务商 Key。

客户端仍需连接登录服务，已安装程序的本地文件完整性检查保持原样；这不是离线模式。

## 1.6.2 单独登记测试构建

管理员 `POST /admin/api/releases/builds` 接收且仅接收 `{"flavor":"all","version":"2026.10.20.3","build_sha256":"64位小写十六进制校验码"}`，需管理员会话、同源和 CSRF 校验。返回 `{"ok":true}`，写入 `register_client_build` 审计。校验码必须取自实际打包清单，不能只填写一个任意版本号。

只写 `client_builds`，不修改推荐版本、下载地址、最低版本或暂停状态，不向公开接口提供测试包下载信息。重复登记同一版本和校验码成功；同体系同版本的校验码不能替换，正式发布也遵守此限制。管理员列表 `builds` 包含全部已登记构建，包括历史正式版和测试版；列表本身不代表更新推荐。

即使尚未发布任何推荐，已登记测试构建也能通过 updates/latest、login、verify、refresh 的版本检查，随后仍执行账号授权。已有最低版本继续生效，低于门槛的测试包仍被拒绝；其他未登记版本和不匹配的体系/校验码仍被拒绝。全局暂停时保持原暂停行为，恢复后测试登记仍有效。测试通过后，用同一清单向原 `POST /admin/api/releases` 正式发布才会改变更新推荐。
## 版本统计（服务端 1.6.4）

`GET /admin/api/releases/statistics` 仅管理员会话可访问，响应禁止缓存。
参数：`days=7|30|90|0`（默认 30，0 为全部）、`flavor=*|all|v34|v61|unknown`、`segment=all|old|required|attention`、`version`、`search`（账号）、`page`（从 1 开始，每页 50 条）。`version=unknown` 筛选未上报有效版本。

返回 `summary` 去重账号计数与覆盖数、`distribution` 按版本/体系/状态的账号和设备记录数、`details` 当前页设备明细及 `total/page/page_size`。版本、账号搜索与 segment 只筛明细；观察期与体系同时筛汇总和明细。`enforcement_enabled` 为 false 时，低于最低版本计数表示恢复规则后涉及的账号。

仅成功登录、verify、refresh 采集；未认证/被门槛提前阻断的请求和匿名更新检查不计入。只保留账号＋设备＋体系的最近版本，人数不可跨版本相加。无历史回填，不提供反破解保证。设备摘要以账号范围内的短标识返回，不返回凭证或原始设备 ID。
