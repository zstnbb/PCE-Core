# PCE Proxy PoC

本地 AI 交互捕获代理 — PCE 第一版 Proxy PoC（TASK-001）。

## 它做什么

PCE Proxy 位于你和 AI 服务之间，透明地记录请求与响应到本地 SQLite，不修改任何内容。

当前支持的目标域名：
- `api.openai.com`
- `api.anthropic.com`

## 环境要求

- Python 3.10+
- mitmproxy 10+

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动代理

```bash
mitmdump -s pce_proxy/addon.py -p 8080 --set stream_large_bodies=1m
```

代理将监听 `127.0.0.1:8080`。

### 3. 配置客户端使用代理

对于 API 调用（如 curl / Python requests），设置 HTTP 代理：

```bash
# Linux / macOS
export HTTP_PROXY=http://127.0.0.1:8080
export HTTPS_PROXY=http://127.0.0.1:8080

# 如果需要信任 mitmproxy 的 CA 证书
# 首次启动 mitmproxy 后，证书生成在 ~/.mitmproxy/
# 参考: https://docs.mitmproxy.org/stable/concepts-certificates/
```

### 4. 发送测试请求

```bash
# 示例：向 OpenAI 发一个请求（需要你自己的 API key）
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer sk-your-key" \
  --proxy http://127.0.0.1:8080 \
  --cacert ~/.mitmproxy/mitmproxy-ca-cert.pem
```

### 5. 查看捕获记录

```bash
# 查看最近 20 条
python -m pce_proxy

# 查看最近 5 条
python -m pce_proxy --last 5

# 查看统计
python -m pce_proxy --stats

# 查看特定请求/响应对
python -m pce_proxy --pair <pair_id>
```

## 数据存储

- 默认路径: `~/.pce/data/pce.db`
- 可通过环境变量 `PCE_DATA_DIR` 覆盖
- 所有敏感 header（Authorization, Cookie, API Key 等）在落库前已被替换为 `REDACTED`

## 项目结构

```
PCE Core/
├── Docs/                   # 项目文档（决议、架构、ADR、任务单）
├── pce_proxy/
│   ├── __init__.py         # 包定义
│   ├── __main__.py         # python -m pce_proxy 入口
│   ├── addon.py            # mitmproxy addon（核心代理逻辑）
│   ├── config.py           # 配置：allowlist、路径、默认值
│   ├── db.py               # SQLite schema 与读写
│   ├── inspect_cli.py      # 查看捕获记录的 CLI
│   └── redact.py           # header 脱敏工具
├── requirements.txt
├── .gitignore
└── README.md
```

## 设计原则

- **Fail-open**: 记录失败不阻断上游请求
- **Local-first**: 数据默认只在本地
- **Allowlist**: 只捕获目标 AI 域名，其他流量直接放行
- **脱敏**: 敏感认证信息不以明文落库
