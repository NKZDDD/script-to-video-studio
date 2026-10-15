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

示例 SDK 只提供接口调用。Studio 登录页、启动拦截、心跳、系统凭据保存和批次行为尚需接入。部署本服务不会自动让现有 EXE 要求登录。
