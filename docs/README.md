# cmcc-cloud-alive

移动云电脑协议级桌面保活工具（不限于“畅享版月包”等特定 SKU，任意已列出的云电脑桌面均可选择/保活）。

> 项目根：`/home/demo/restore/cmcc-cloud-alive`
> 蓝本(只读)：`/home/demo/cloud-computer-keepalive`（Go 成品）
> 测试：264 tests，全绿
> 状态：40 分钟真实环境 live 验证已通过（关机/非运行次数=0）

## 安装

```bash
cd /home/demo/restore/cmcc-cloud-alive
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 快速开始

```bash
# 1. 登录（凭据缓存到 state 文件，支持 24h 自动重登）
.venv/bin/python bin/cmcc_cloud_alive.py login <手机号> <密码> --save-password

# 2. 查看云电脑列表
.venv/bin/python bin/cmcc_cloud_alive.py list

# 3. 选择默认云电脑
.venv/bin/python bin/cmcc_cloud_alive.py select <userServiceId>

# 4. 协议级保活（自动路由 ZTE / SCG）
.venv/bin/python bin/cmcc_cloud_alive.py product-keepalive <userServiceId>
```

## product-keepalive（核心命令）

协议级桌面保活入口。自动通过 firmAuth 分类路由：

- **ZTE 路线** → Python 实现（material → CAG → mux → raw SPICE → keepalive）
- **SCG 路线** → Go binary（`scg_go/cmcc_keepalive`）via Python subprocess shim

> 注意：`product-keepalive` 没有 `--route` 参数。路由由
> `product_router.classify_firm_auth_route()` 根据 firmAuth 自动判断。

### 用法

```bash
python3 bin/cmcc_cloud_alive.py product-keepalive [options] <userServiceId>
```

### 参数

| 参数 | 说明 |
| --- | --- |
| `--duration N` | SCG 连接持续秒数（默认 120；0 = 直到中断） |
| `--forever` | 持续运行 SCG keepalive binary |
| `--user-service-id` | 覆盖目标 userServiceId |
| `--vm-id` | 覆盖目标 vmId |
| `--binary` | 覆盖 SCG keepalive binary 路径 |
| `--config-dir` | 覆盖 SCG 配置目录 |

### 示例

```bash
# ZTE 路线 120s short keepalive（自动检测路由）
.venv/bin/python bin/cmcc_cloud_alive.py product-keepalive 2663816

# SCG 路线持续运行
.venv/bin/python bin/cmcc_cloud_alive.py product-keepalive --forever 2663816

# SCG 路线指定时长
.venv/bin/python bin/cmcc_cloud_alive.py product-keepalive --duration 2400 2663816
```

## 全部 CLI 子命令

以下子命令均来自 `cmcc_cloud_alive/main.py` argparse 定义（照实，共 42 个）。

### 账号与状态

| 命令 | 说明 |
| --- | --- |
| `login` | 密码登录（`--save-password` 缓存凭据） |
| `set-profile` | 设置 profile |
| `list` | 云电脑列表 |
| `select` | 选择默认云电脑 |
| `status` | 查询云电脑状态 |
| `token-check` | 检查 token 有效性 |
| `account-keepalive` | 账号级保活 |
| `logout` | 登出 |
| `state` | 查看/管理本地 state 文件 |

### 保活与验证

| 命令 | 说明 |
| --- | --- |
| `product-keepalive` | 协议级保活入口（自动路由 ZTE/SCG） |
| `product-route-check` | 路由分类检查（L0） |
| `verified-run` | verified-run 验证框架 |
| `power-monitor` | 电源状态独立监控 |
| `boot` | 开机 |
| `keepalive-once` | 单次保活 |
| `keepalive` | 持续保活 |
| `cag-keepalive-once` | 单次 CAG 保活（旧路线） |
| `cag-keepalive` | 持续 CAG 保活（旧路线） |
| `cag-verify` | CAG 验证（旧路线） |

### 协议探针与分析

| 命令 | 说明 |
| --- | --- |
| `probe-base` | 基础探针 |
| `spice-offline-proof` | 离线 SPICE codec 验证 |
| `analyze-zime-probe` | 分析 ZIME 探针输出 |
| `extract-zime-sequence` | 提取 ZIME 序列 |
| `analyze-rap-zime` | 分析 RAP/ZIME |
| `analyze-rap-zime-pcap` | 分析 RAP/ZIME pcap |
| `check-rap-zime-runner-input` | 检查 runner 输入 |
| `rap-zime-udp-probe` | RAP/ZIME UDP 探针 |
| `rap-zime-kcp-sync-probe` | RAP/ZIME KCP 同步探针 |
| `rap-zime-kcp-auth-from-cag` | RAP/ZIME KCP 认证（from CAG） |
| `check-rap-zime-auth-gate-report` | 检查 auth gate 报告 |
| `zime-native-bridge` | ZIME native bridge |
| `trace-timeline` | trace 时序提取 |

### HTTP 会话（旧路线，已证伪）

| 命令 | 说明 |
| --- | --- |
| `http-session-replay` | HTTP 会话重放 |
| `http-session-verify` | HTTP 会话验证 |

### 通用

| 命令 | 说明 |
| --- | --- |
| `run` | 通用运行 |
| `protocol-check` | 协议检查 |
| `protocol-run` | 协议运行 |
| `api-probe` | API 探针 |
| `analyze-session-capture` | 分析会话捕获 |
| `source-audit` | 源码审计 |
| `legacy` | 旧命令入口 |

## 测试

```bash
cd /home/demo/restore/cmcc-cloud-alive
.venv/bin/python -m pytest -q
```

258 测试，分布：

| 测试文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `test_python_modules.py` | 127 | 核心模块单测 |
| `test_zte_cag_mux_proxy.py` | 35 | CAG mux/proxy (L10) |
| `test_zte_cag.py` | 34 | CAG TCP/TLS (L9) |
| `test_zte_raw_spice.py` | 22 | raw SPICE main/display (L11/L12) |
| `test_scg_route.py` | 17 | SCG subprocess shim |
| `test_cli.py` | 13 | CLI 子命令 (L0) |
| `test_e2e_zte_keepalive.py` | 6 | 端到端 ZTE keepalive (L12/L13) |
| `test_zte_keepalive_session.py` | 4 | 120s keepalive session (L13) |
| **合计** | **258** | |

## 项目结构

详见 `docs/delivery-handoff.md` §4。

## 文档

- `docs/delivery-handoff.md` — 交付与接手文档
- `docs/final-acceptance-report.md` — L0-L14 验收报告
- `docs/protocol-keepalive.md` — 保活协议说明
- `docs/no-spin-rules.md` — 治理规则
- `docs/plan-zte-evidence-matrix.md` — ZTE 证据矩阵
- `docs/research-notes.md` — 研究笔记
