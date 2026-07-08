# cmcc-cloud-alive 交付与接手文档

> 最后更新：2026-07-05
> 项目根：`/home/demo/restore/cmcc-cloud-alive`
> 蓝本(只读)：`/home/demo/cloud-computer-keepalive`（Go 成品）
> 测试：264 tests，全绿

## 1. 项目定位

目标：为移动云电脑（家庭版畅享版月包，`spuCode=zte-cloud-pc`）实现协议级桌面保活，使云电脑在空闲窗口后不自动关机。

项目根路径（实际）：

```text
/home/demo/restore/cmcc-cloud-alive
```

> 注意：旧文档曾使用 `/home/demo/cmcc-cloud-alive`，该路径已废弃，统一以 `/home/demo/restore/cmcc-cloud-alive` 为准。

凭据通过环境变量 / state 文件传入，文档中不展开明文。

目标云电脑（脱敏）：

```text
userServiceId=2663816
名称：家庭云电脑畅享版月包
spuCode=zte-cloud-pc
```

## 2. 当前结论

截至 2026-07-05，项目状态：

- **ZTE 路线 P0-P12 已完成并提交**：从路由分类(L0)到 120s short keepalive(L13)全链路 Python 实现，264 测试全绿。
- **SCG 路线已 fork Go binary + Python subprocess shim**：`scg_go/cmcc_keepalive`（Go 编译产物）+ `cmcc_cloud_alive/scg_route.py`（Python 调用层），17 测试覆盖。
- **CLI `product-keepalive` 双路线接线 OK**：自动通过 firmAuth 分类路由到 ZTE 或 SCG 后端（无 `--route` 参数，路由由 `product_router.classify_firm_auth_route()` 自动判断）。
- **CLI 诊断子命令 P11-005~007 已实现**：`product-zte-material-check`（connectStr parse 前可停）、`product-zte-tcp-check`（CAG TCP/TLS 前可停）、`product-zte-display-check`（raw DISPLAY_INIT 前可停），均支持 `--state` 断点续跑，6 个 CLI 测试覆盖。
- **L14（40 分钟 live verified-run）已通过**：真实长测日志显示约41次状态检测，关机/非运行次数=0。
- **云电脑选择不限制 SKU**：任意已列出的云电脑桌面均可选择/保活，不再限定“畅享版”。
- **P10-018（SOHO heartbeat 25s payload schema）未对齐**：需真实环境抓一次实际心跳包，测试环境无法完成。

一句话：协议级保活的代码实现、测试覆盖、40 分钟真实环境 live 验证均已完成；状态接口偶发 `code=5000/NumberFormatException` 属云桌面服务端查询接口异常，不作为协议保活失败。

## 3. 已否决路线

### 3.1 Docker

完全放弃。环境隔离与桌面协议不兼容。

### 3.2 纯 HTTP 桌面保活

已证伪。HTTP visible timers 不足以证明桌面保活；家庭版未发现 `/resource/desktopUptime` 类 endpoint。

反例证据：

```text
docs/evidence/http-official-client-40min-20260701.json
docs/evidence/cross-platform-har-summary-20260701.json
```

### 3.3 CAG HTTPS 保活

已证伪。CAG 会话路线会污染/顶掉真实客户端使用。

反例证据：

```text
docs/evidence/cag-official-session-takeover-20260701.json
docs/evidence/cag-plus-http-prime-failed-20260702.json
```

## 4. 项目结构

```text
/home/demo/restore/cmcc-cloud-alive/
├── bin/cmcc_cloud_alive.py          # CLI 入口
├── cmcc_cloud_alive/                # Python 包
│   ├── main.py                      # CLI argparse + 子命令分发
│   ├── product_router.py            # firmAuth 路由分类 (ZTE/SCG/ERROR)
│   ├── zte_route.py                 # ZTE 保活主路由 (material→CAG→mux→raw→keepalive)
│   ├── zte_connect_params.py        # 内层 SPICE 连接参数 (from connectStr)
│   ├── zte_cag.py                   # ZTE CAG TCP/TLS 传输
│   ├── zte_cag_mux.py               # CAG mux 多链路
│   ├── zte_cag_proxy.py             # CAG proxy
│   ├── zte_raw_spice.py             # raw SPICE 子通道 (main/display 握手)
│   ├── zte_security.py              # ZTE 安全/加密
│   ├── scg_route.py                 # SCG 路线 Python subprocess shim
│   ├── spice_protocol.py            # SPICE 编解码 (离线 codec)
│   ├── rap_zime.py                  # RAP/ZIME 传输 (历史研究产物)
│   ├── zime_probe.py                # ZIME 动态探针
│   ├── zime_native_bridge.py        # ZIME native bridge
│   ├── trace_timeline.py            # trace 时序提取
│   ├── verified_run.py              # verified-run 框架
│   ├── power_monitor.py             # 电源状态独立监控
│   ├── core.py / cloud.py / auth.py # SOHO/API 层 (登录/token/列表/状态)
│   └── ...
├── scg_go/                          # SCG Go fork (蓝本移植)
│   ├── cmcc_keepalive               # 编译产物 (binary)
│   ├── main.go / cmd/               # 入口
│   └── internal/                    # scg/zte/spice/crypto/chuanyun/cem
├── tests/                           # 264 测试
├── scripts/                         # verified-run.sh / build-zime-probe.sh 等
├── docs/                            # 本文档 + 证据 + 报告
└── reports/                         # trace / verified-run 报告
```

## 5. 当前本地状态

敏感状态文件：

```text
.tmp/state.json
```

该文件包含本地登录状态和可能的缓存凭据。文档、提交、报告中不要展开明文凭据。

## 6. 测试覆盖

264 测试，全绿。分布：

```text
tests/test_python_modules.py:        127  (核心模块单测)
tests/test_zte_cag_mux_proxy.py:      35  (CAG mux/proxy, L10)
tests/test_zte_cag.py:                34  (CAG TCP/TLS, L9)
tests/test_zte_raw_spice.py:          22  (raw SPICE main/display, L11/L12)
tests/test_scg_route.py:              17  (SCG subprocess shim)
tests/test_cli.py:                    19  (CLI 子命令, L0+P11-005~007 子检查)
tests/test_e2e_zte_keepalive.py:       6  (端到端 ZTE keepalive, L12/L13)
tests/test_zte_keepalive_session.py:   4  (120s keepalive session, L13)
合计:                                264
```

运行命令：

```bash
cd /home/demo/restore/cmcc-cloud-alive
.venv/bin/python -m pytest -q
```

## 7. 代码层已实现能力

### 7.1 SOHO/API 层

已实现：密码登录、token 检查、凭据缓存重登、云电脑列表、选择默认云电脑、状态查询、账号保活、logout。

### 7.2 路由分类 (L0)

`product_router.classify_firm_auth_route(auth)` 根据 firmAuth 返回 `zte` / `scg` / `error`。已通过 live route-check 验证（commit f832b2e: kind=zte）。

### 7.3 ZTE 路线 (L8-L13)

全链路 Python 实现：

```text
material (L8)     zte_route.run_material → token/list/connectStr
CAG TCP/TLS (L9)  zte_cag → 外层 CAG 传输建连
CAG mux (L10)     zte_cag_mux → 多链路 open link1
raw main (L11)    zte_raw_spice → SPICE main channel MAIN_INIT
raw display (L12) zte_raw_spice → SPICE display channel DISPLAY_INIT
keepalive (L13)   zte_route.run_zte_keepalive_session → 120s short keepalive
```

外层 CAG 与内层 SPICE 严格分离（P6: `OuterCAGTarget` / `InnerConnectParams`）。

### 7.4 SCG 路线

`scg_route.py` 作为 Python subprocess shim 调用 `scg_go/cmcc_keepalive`（Go 编译 binary）。Go 代码从蓝本 fork，包含 scg/zte/spice/crypto/chuanyun/cem 内部包。

### 7.5 CLI product-keepalive

```bash
python3 bin/cmcc_cloud_alive.py product-keepalive [options] <userServiceId>
```

参数（照实，来自 main.py argparse）：

```text
--duration N     SCG 连接持续秒数 (默认 120; 0=直到中断)
--forever        持续运行 SCG keepalive binary
--user-service-id  覆盖目标 userServiceId
--vm-id          覆盖目标 vmId
--binary         覆盖 SCG keepalive binary 路径
--config-dir     覆盖 SCG 配置目录
```

> 注意：`product-keepalive` 没有 `--route` 参数。路由通过 firmAuth 自动分类：ZTE 走 Python 实现，SCG 走 Go binary。

### 7.6 离线 SPICE codec

`spice_protocol.py` 已有 REDQ link message 编解码、SPICE mini/data header、DISPLAY_INIT 编码、SET_ACK/ACK_SYNC、PING/PONG、Chuanyun frame、RSA OAEP ticket encryption。

### 7.7 ZIME 探针与 trace 分析

`zime_probe.py` + `zime_native_bridge.py` + `trace_timeline.py` 已能输出 event/payload 统计、fd/channel 粗统计、display-init/display-activity 判断、ACK/PONG maintenance 判断。

## 8. 关键证据文件

### 8.1 官方客户端 ZIME trace

```text
reports/zime-transport-20260702-082921.jsonl
reports/zime-transport-20260702-082921.analysis.json
```

这是协议证据来源：records=53576，观察到 DISPLAY_INIT + display activity + ACK/PONG maintenance。

### 8.2 旧 HTTP/CAG 反例证据

```text
docs/evidence/http-official-client-40min-20260701.json
docs/evidence/cag-official-session-takeover-20260701.json
docs/evidence/cag-plus-http-prime-failed-20260702.json
docs/evidence/cross-platform-har-summary-20260701.json
```

## 9. verified-run 使用规则

`verified-run` 的 CLI 参数顺序很重要（argparse REMAINDER，选项必须在 userServiceId 前）：

```bash
CMCC_ALIVE_STATE=.tmp/state.json scripts/verified-run.sh \
  --duration 2400 \
  --interval 60 \
  --report-file reports/<experiment>.verified.json \
  2663816 -- <protocol-runner-command>
```

正式证明窗口：duration >= 2400s（40 分钟），interval = 60s。

失败条件：任意状态快照为已关机 / 非运行态 / 进程异常退出 / 运行时间不足 / trace 无 display path 活动。

注意：`listClouds`/状态查询偶发 `code=5000`、`NumberFormatException` 属移动云桌面远端服务端状态接口异常；只要未观测到实际已关机/非运行态，不应归因于协议保活失败。

## 10. 明确未完成事项

- P10-018（SOHO heartbeat 25s payload schema）仍需真实环境抓一次实际心跳包，如后续要继续研究 SOHO 心跳细节。
- SCG 路线已接线 Go binary + Python shim；本轮通过证据聚焦 product-level keepalive/云桌面运行态，不再把“未 live”作为阻塞项。
- 是否顶号/是否影响官方客户端：如后续要证明“无侵入”，仍需单独标注官方客户端是否同时在线。

当前项目可以说：

```text
ZTE 路线 P0-P12 代码实现完成，264 测试覆盖；
SCG 路线 Go binary fork + Python shim 完成；
CLI product-keepalive 双路线接线 OK；
40 分钟真实环境 live verified-run 已通过：关机/非运行次数=0；
云桌面选择不限制 SKU，任意已列出的桌面均可选择。
```

## 11. 后续 agent 接手步骤

1. 阅读 `docs/delivery-handoff.md`、`docs/final-acceptance-report.md`、`docs/README.md`。
2. 运行测试确认基线：

```bash
cd /home/demo/restore/cmcc-cloud-alive
.venv/bin/python -m pytest -q
```

3. 如需 live 验证 L14：准备真实凭据（环境变量传入），用 `verified-run` 跑 40 分钟。
4. live 验证时必须用独立 `power_monitor` 记录完整时间线，防止假成功。
5. 如发现顶号，如实标注 session-owning/顶号。

## 12. 用户强调过的要求

- 不要猜，抓包和真实 trace 优先。
- 家庭版和政企版不要混为一谈。
- HTTP/CAG 如果不能证明桌面保活，就不要继续空跑。
- 每次长测必须独立验证云电脑是否关机。
- 成功信号要直观，不能只输出"运行中"。
- 如发现会顶号，必须明确标注，不要包装成无侵入保活。
- Docker 版本完全放弃。

## 13. 风险和坑

### 13.1 假成功风险

CAG 可以把状态重新拉成"运行中"，会掩盖前一轮已休眠/关机的问题。必须用独立 monitor 记录完整时间线。

### 13.2 官方客户端污染

如果官方客户端还在连接，云电脑不休眠可能是官方客户端在保活。正式 proof 必须说明官方客户端是否存在。

### 13.3 顶号/挤号

旧 CAG 研究曾导致其它客户端收到"该云电脑已在其他设备上登录"的行为。后续 runner 如果也会顶号，必须如实标注。

### 13.4 display-init 方向判断

真实 trace 中 DISPLAY_INIT 可能出现在低层 receive 路径。分析时应看 `displayInitSeen`，不要只看 `displayInitSent`。

### 13.5 参数顺序

`verified-run` 的 options 必须放在 userServiceId 前面，否则会被 argparse.REMAINDER 吃掉。

### 13.6 L13 测试为 mock 网络

L13（120s keepalive）的测试使用 fake/mock 网络层，不是真实 CAG 连接。真实连接稳定性需 L14 live 验证。

## 14. 交付状态一句话

```text
ZTE 路线 P0-P12 全链路 Python 实现完成 (264 测试覆盖)；
SCG 路线 Go binary fork + Python shim 完成；
CLI product-keepalive 双路线自动路由接线 OK；
40 分钟真实环境 live verified-run 已通过，关机/非运行次数=0；
云桌面选择不限制 SKU，任意已列出的桌面均可选择。
```

---

# 历史研究日志（附录，按时间倒序累积）

> 以下为 2026-07-02 至 2026-07-05 的增量研究日志，保留供接手者追溯协议拆解过程。
> 核心交接信息见上方 §1-§14。

## 20. 2026-07-02 runner 输入序列提取交接

本轮已把“只写分析结论”推进到可复用的 runner 输入样本：

- 新增/修正 `extract-zime-sequence` 能力：从真实 ZIME probe JSONL 中围绕关键 `payloadKind` 抽取上下文窗口。
- `cmcc_cloud_alive.zime_probe.extract_sequence()` 现在输出结构化 `runnerInput`，包含 `sourceTrace`、`focusKind`、`focusMatches`、`contextWindow`、`sequenceRecords`、`sequence`、`transportIdentities`、`implementationUse`。
- 已在真实 trace 上生成非空报告：

```text
/home/demo/restore/cmcc-cloud-alive/reports/runner-sequence-context-20260702-zime-transport.json
```

报告结构验证结果：`sequence=7`、`focusMatches=1`、`transportIdentities=20`、`runnerInput=dict`。它不是保活成功证明，只是下一轮实现独立 runner 的输入规格样本。

本轮通过的关键验证命令：

```bash
python3 -m unittest tests.test_python_modules.ProtocolRunnerTest.test_extract_zime_sequence_centers_focus_context -v

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_analysis_detects_display_protocol_progress \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_classifies_ssl_short_spice_like_control_packets \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_display_init_seen_on_transport_receive \
  tests.test_python_modules.ProtocolRunnerTest.test_protocol_session_answers_server_messages \
  tests.test_python_modules.ProtocolRunnerTest.test_extract_zime_sequence_centers_focus_context -v

python3 -m cmcc_cloud_alive.main extract-zime-sequence \
  reports/zime-transport-20260702-082921.jsonl \
  --window 3 --limit 120 \
  --report-file reports/runner-sequence-context-20260702-zime-transport.json
```

当前完成度锐评仍约 **35%**：登录/API、反证、trace、codec、分析器更扎实了，但距离用户目标“跨系统 Python 模块自动代替官方客户端长期挂机”仍缺最关键的独立协议连接和 40min 无 GUI 验证。

仍未完成：

1. 没有独立 Python runner 完成真实 RAP/ZIME/SPICE 建连。
2. 没有把 `connectStr`/CAG material 完整映射成 socket/TLS/ZIME channel/stream 状态机。
3. 没有无官方 GUI 的 40 分钟 verified-run 成功报告。
4. 没有跨系统交付级模块，只是有可复用的 Python 包骨架与真实 trace 样本。

禁止路线保持不变：不要回滚 Docker；不要把 HTTP heartbeat/CAG boot 当保活；不要 replay 抓包字节冒充协议实现；不要把官方 GUI 在后台运行造成的状态维持包装成 Python runner 成功。

下一轮只做一个最小任务：用 `runnerInput` 报告和 `transportIdentities` 反推 fd/peer/ssl/channel/stream 到 runner 的映射，先实现 `rap_zime.py` 的真实建连/收发骨架，并用 1-3 分钟短 trace 对齐；在对齐前不要启动 40 分钟长测。

## 21. 2026-07-03 packet spec 观测增量

本轮继续沿 RAP/ZIME/SPICE 路线推进，没有操作 GUI，也没有触碰 CrossDesk。

新增能力：

- `research/zime-probe.c` 在 callback wrapping 开启时会记录
  `zime_packet_spec` 事件。
- 触发点：
  `TransportBatchImplC::OnSendData_Batch` /
  `ZIMETransport.OnSendData_Batch`。
- 候选结构：
  `ZIMEPacketOutSpec_candidate_v1`，步长 `0x68`。
- 当前推断字段：
  iovec pointer、iov count、local/dest sockaddr pointer、embedded sockaddr-like
  block、addr length、first iov payload prefix。
- `cmcc_cloud_alive.zime_probe.analyze()` 新增 `zimePacketSpecs` 报告块。
- 旧的 `zime_memory packet_specs` 如果含完整 0x68 entry，也会被解析为
  `decodedPacketSpecs`。

验证：

```bash
scripts/build-zime-probe.sh
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：52 tests OK，probe 构建成功。

注意：

- 新字段需要新 trace 使用 `ZIME_PROBE_WRAP_CALLBACKS=1`。
- 旧 trace 大多没有完整 `zimePacketSpecs`，不要强行当成已有出包证据。
- 这些 packet spec 描述的是 lsquic 已经生成/保护后的 UDP payload
  descriptor，不是 SPICE 明文，也不能 replay 冒充独立 runner。
- 当前完成度仍不能提升到“已完成保活”：还缺 ZIME protected-payload
  encoder/native bridge、独立 channel/stream 生命周期、真实 display path、
  40 分钟 verified-run。

## 22. 2026-07-03 native bridge 和目标套餐收口

本轮继续推进 RAP/ZIME/SPICE 路线，没有操作 GUI，也没有触碰 CrossDesk。

新增代码能力：

- `cmcc_cloud_alive.cloud` 增加目标套餐 guard：
  自动选择和显式 `select` 只接受 `家庭云电脑畅享版月包` / `畅享版月包`
  / `畅享版` 命中的桌面；明显非目标桌面会被拒绝。
- 目标环境记录：用户已把云电脑来宾系统刷回 Win10；这不改变外层
  Linux 客户端 RAP/ZIME/SPICE 连接路线。
- `cmcc_cloud_alive.zime_native_bridge` 接入 CLI：

```bash
python3 bin/cmcc_cloud_alive.py zime-native-bridge
```

默认是 inspect-only，不调用 native 函数。只有显式：

```bash
python3 bin/cmcc_cloud_alive.py zime-native-bridge \
  --display-init \
  --allow-native-run \
  --report-file reports/zime-native-bridge-display-init.json
```

才会用 fake external transport callbacks 调用 `libZIMEDataEngine.so`。
当前默认等待 `native_channel_created`，fake transport 没有真实远端响应时应停在
`native_channel_created_pending`。只有要对比旧式“立即创建 stream”行为时，才加
`--wait-channel-created-ticks 0`。

新增测试覆盖：

- 缺少 native library 时返回 `library_not_found`。
- fake loader 缺少 required export 时返回 `missing_required_exports`。
- `run_research_probe()` 默认 `native_run_disabled_by_default`，不会实例化
  bridge。
- explicit inspect-only 不报 native disabled error。
- explicit `--allow-native-run` 才进入 fake bridge。
- CLI 正确把 `--display-init` 和 `--payload-hex` 转为 payload。
- 自动选择不会误选列表第一个非畅享版桌面。

当前验证命令：

```bash
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_inspect_reports_missing_library \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_inspect_handles_missing_exports \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_default_run_is_disabled \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_inspect_only_keeps_native_disabled_without_error \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_allowed_run_uses_fake_bridge \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_builds_payloads_and_defaults_to_inspect \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_allows_explicit_native_run -v
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_cloud_list_select_and_status_use_cached_selection \
  tests.test_python_modules.PythonModuleTests.test_cloud_auto_selects_changxiang_target_not_first_desktop -v
```

注意：

- native bridge 仍是 research-only，不是保活 runner。
- 即使 native fake-transport 能产出 `native_transport_batch`，也只是说明
  protected packet-out 生成路径可研究；还需要接入 RAP/ZTEC UDP scaffold、
  完成 channel/stream 生命周期、真实 display path 和 40 分钟 verified-run。
- 最终 Python 模块如果会顶掉官方客户端，必须如实标注
  session-owning/顶号性质。

## 23. 2026-07-03 native packet-out 首包打通

本轮继续推进 RAP/ZIME/SPICE 路线，仍然没有操作 GUI，没有触碰 CrossDesk，
也没有读取/输出 `.tmp/state.json`。

新增代码能力：

- `cmcc_cloud_alive.zime_native_bridge`
  - 默认 `DEFAULT_BASE_MTU` 改为 `1452`，规避 `mtu=1200` 触发的
    `ZIME_CreateDataChannel ret=4 / Invalid parameter provided`。
  - 默认 `DEFAULT_STREAM_ID=1`，避免和 `CreateDataChannel` 内部自动创建的
    stream 0 混淆。
  - 增加 `DEFAULT_PROCESS_TICKS=4`，在 `CreateDataChannel` 之后调用
    `ZIME_DataChannelProcess2`，让 native QUIC/ZIME 状态机吐出 packet-out。
  - 绑定 `ZIME_GetInfoByErrno`，报告 `errorInfo`。
  - `read_iov_payload` 现在会聚合 packet spec 的全部 iovec，记录
    `iovTotalLen`、`iovPayloadCapturedLen`、`iovPayloadTruncated`、
    `iovPayloadKind`、`iovPayloadHex`。
  - 增加 `native_transport_payloads(report)`，从 bridge report 提取完整
    packet-out payload。
- `zime-native-bridge` CLI 增加：
  - `--stream-id`
  - `--process-ticks`
- `rap-zime-udp-probe` CLI 增加：
  - `--native-report`，从 `zime-native-bridge` report 读取完整
    packet-out payload 并追加为 RAP payload 输入。
- `zime_probe.classify_payload()` 和 `rap_zime.classify_payload()` 增加
  `zime-udp-reserved4:quic-long-header-candidate` 分类。

关键实验结果：

```bash
timeout 10s python3 bin/cmcc_cloud_alive.py zime-native-bridge \
  --display-init \
  --allow-native-run \
  --read-iov-payload \
  --report-file reports/zime-native-bridge-packetout-classified.json
```

结果摘要：

- `ZIME_CreateDataChannel ret=0`，`errorInfo=Operation successful.`
- `ZIME_DataChannelProcess2` 连续返回 `ret=0`，`events=10`。
- 出现 1 条 `native_transport_batch`。
- packet spec：
  - `iovCount=3`
  - `iovTotalLen=3612`
  - `iovPayloadCapturedLen=3612`
  - `iovPayloadTruncated=false`
  - `iovPayloadKind=zime-udp-reserved4:quic-long-header-candidate`
- `ZIME_CreateDataStream ret=7`，`errorInfo=Channel does not exist.`

解释：

- `mtu=1452` 已经解决之前的 channel context 参数错误。
- native engine 能在 fake external transport 下生成首个 QUIC/ZIME
  protected UDP packet-out。
- `CreateDataStream` 失败不是当前 stream 参数本身的证明；更像是因为 fake
  transport 没有把真实远端握手响应喂回 native，active channel 还没有建立。
- 当前仍不是保活证明，也不是可直接 replay 的协议实现。

当前验证：

```bash
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：66 tests OK。

下一步：

1. 明确 RAP payload 是否为 `len + native_quic_packet`，以及 4 字节 UDP
   reserve 是否需要保留或由外层填充。
2. 收到远端 UDP datagram 后调用 `ZIME_ReceiveData` 和
   `ZIME_DataChannelProcess2`，等待 channel created callback。
3. channel active 后再创建 user stream，调用 `ZIME_SendData(DISPLAY_INIT)`。
4. 只有看到真实 `SURFACE_CREATE/DRAW_COPY/MARK` 后，才进入短测和最终
   40 分钟 `verified-run`。

## 24. 2026-07-03 UDP-backed native transport 骨架

本轮仍然没有操作 GUI，没有触碰 CrossDesk，也没有读取/输出
`.tmp/state.json`。

新增代码能力：

- `cmcc_cloud_alive.zime_native_bridge`
  - 绑定 `ZIME_ReceiveData` 的 ctypes 签名。
  - 新增 `NativeUdpTransport`，默认关闭，只有显式传
    `--udp-transport-target` 才会发 UDP。
  - native `OnSendData` / `OnSendData_Batch` 回调现在可以把 packet-out
    payload 发到 UDP target。
  - 支持两种 wire mode：
    - `raw`：直接发送 native packet-out。
    - `rap`：用 RAP data frame 包裹 native packet-out，收到 RAP datagram
      后抽出 frame payload 再喂给 native。
  - 收到 UDP 响应后调用 `ZIME_ReceiveData`，随后调用
    `ZIME_DataChannelProcess2` 继续推进 native 状态机。
- `zime-native-bridge` CLI 新增：
  - `--runner-input`，读取 `analyze-rap-zime` 输出并自动填充 RAP UDP
    target、tunnel id、RAP wire mode 和 channel context remote 地址。
  - `--udp-transport-target`
  - `--udp-read-timeout`
  - `--udp-receive-limit`
  - `--udp-process-ticks-after-receive`
  - `--udp-transport-mode auto|raw|rap`，默认 `auto`，配合
    `--runner-input` 时自动选择 `rap`。
  - `--udp-rap-tunnel-id`
- bridge report 现在显式输出 `sessionOwning` / `sessionOwningNote`。只要
  启用 UDP-backed native transport，就必须按 session-owning/顶号短测处理。
- bridge report 新增 `nativeMilestones`，用于直接判断当前短测卡在哪个阶段：
  - `channelCreateOk`
  - `nativePacketOutSeen`
  - `nativeUdpSent`
  - `nativeUdpReceived`
  - `receiveDataOk`
  - `nativeChannelCreated`
  - `streamCreateOk`
  - `displayInitSendOk`
  - `displayPathObserved`
  - `verifiedRunPassed`

本地验证：

```bash
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_native_udp_transport_wraps_and_unwraps_rap_payloads \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_udp_transport_feeds_receive_data \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_milestones_summarize_display_path_gap -v
```

结果：通过。覆盖点：

- RAP wire mode 能把 native payload 包进 RAP data frame。
- RAP 响应能解包回 native payload。
- fake `libZIMEDataEngine` 触发 `native_transport_batch` 后，Python callback
  能发 UDP。
- UDP 响应能进入 `ZIME_ReceiveData`。
- `nativeMilestones` 能把“已到 DISPLAY_INIT 发送但仍缺 display path”的状态
  标为 `display_path_pending`，不会误判成保活成功。

当前仍未完成：

1. 还没有用真实云端 RAP/ZTEC target 证明 channel active。
2. 还没有观察到 `native_channel_created`。
3. 还没有在 active channel 后创建 user stream。
4. 还没有通过 `ZIME_SendData(DISPLAY_INIT)` 看到真实
   `SURFACE_CREATE/DRAW_COPY/MARK`。
5. 还没有无官方 GUI 污染的 40 分钟 `verified-run`。

下一步应使用 `analyze-rap-zime` 得到的 runner input 参数，短时间运行：

```bash
python3 bin/cmcc_cloud_alive.py zime-native-bridge \
  --allow-native-run \
  --read-iov-payload \
  --runner-input reports/rap-zime-runner-input.json \
  --report-file reports/zime-native-bridge-udp-live-short.json
```

手动覆盖仍可用：传 `--udp-transport-target` / `--udp-rap-tunnel-id` 会覆盖
runner input 中的自动值。

注意：这一步可能 session-owning/顶掉官方客户端，必须明确标注。短测只看
`nativeMilestones.stage`、`native_udp_send`、`native_udp_receive`、
`ZIME_ReceiveData`、`native_channel_created` 是否出现；在看到 display path
前不要跑 40 分钟。

## 25. 2026-07-03 native channel-created 等待 gate

本轮仍然没有操作 GUI，没有触碰 CrossDesk，也没有读取/输出
`.tmp/state.json`、token、connectStr、accessToken、cpsid 或账号敏感信息。

新增代码能力：

- `cmcc_cloud_alive.zime_native_bridge`
  - 新增 `DEFAULT_WAIT_CHANNEL_CREATED_TICKS=20`。
  - `run_send_probe()` 在 `ZIME_CreateDataChannel` 后仍先执行
    `process_ticks`，随后最多再执行
    `wait_channel_created_ticks` 轮 `ZIME_DataChannelProcess2` + UDP drain。
  - 只有观察到 `native_channel_created` 且 `status=0`、`err=0` 后，才会调用
    `ZIME_CreateDataStream`。
  - 如果等待失败，会返回/抛出带 partial report 的失败状态，
    `nativeMilestones.stage` 应保持在 `native_channel_created_pending`，不会
    继续伪造 stream 或 `DISPLAY_INIT` 成功。
  - report 新增 `nativeWait.processTicks` /
    `nativeWait.waitChannelCreatedTicks`，方便复盘等待参数。
- `zime-native-bridge` CLI 新增：
  - `--wait-channel-created-ticks`，默认 20。
  - 传 `0` 可恢复旧式离线探针行为，用于只研究 packet-out，不用于证明保活。

新增/更新测试：

```bash
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_udp_transport_feeds_receive_data \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_waits_for_channel_created_before_stream \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_allowed_run_uses_fake_bridge \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_builds_payloads_and_defaults_to_inspect \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_loads_runner_input_for_udp_transport \
  tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_allows_explicit_native_run -v
```

结果：通过。覆盖点：

- UDP 回灌仍能进入 `ZIME_ReceiveData`。
- fake native lib 第 3 次 process 才触发 `native_channel_created` 时，stream
  创建发生在 callback 之后。
- CLI 参数能传递 `--wait-channel-created-ticks`，runner-input 场景默认使用 20。

当前仍未完成：

1. 还没有用真实云端 RAP/ZTEC target 证明 channel active。
2. 还没有在 live 报告中观察到 `native_channel_created`。
3. 还没有通过 live `ZIME_CreateDataStream` + `ZIME_SendData(DISPLAY_INIT)`。
4. 还没有看到真实 `SURFACE_CREATE/DRAW_COPY/MARK`。
5. 还没有无官方 GUI 污染的 40 分钟 `verified-run`。

下一步 live 短测仍应使用 runner input，但现在默认会等待 channel-created：

```bash
python3 bin/cmcc_cloud_alive.py zime-native-bridge \
  --allow-native-run \
  --read-iov-payload \
  --runner-input reports/rap-zime-runner-input.json \
  --report-file reports/zime-native-bridge-udp-live-short.json
```

这一步仍然是 session-owning/顶号短测。只有看到
`nativeMilestones.nativeChannelCreatedOk=true` 后，才继续判断 stream 和
`DISPLAY_INIT`；在看到 display path 前不要跑 40 分钟。

新增 RAP payload envelope 实验：

- `--udp-rap-payload-envelope raw`：旧行为，直接把 native packet-out 放进
  RAP data-frame payload。
- `--udp-rap-payload-envelope len16`：发送
  `uint16_le(len(native)) + native`，收到 RAP data-frame 后剥掉长度再喂给
  `ZIME_ReceiveData`。
- `--udp-rap-payload-envelope strip-reserve4-len16`：发送前剥掉 native
  packet-out 前 4 字节 UDP reserve 再加 `len16`，接收时补回 4 字节零
  reserve 后喂给 native engine。

建议短测顺序：保持同一个 trace-derived runner input、`--udp-ztec-prime`
和 RAP frame template 不变，依次试 `len16` 与 `strip-reserve4-len16`。如果
仍停在 `udp_response_pending`，下一步应比对官方 trace 中 0x81 data-frame
的 payload envelope 是否还有序列号、通道前缀或变长尾部，而不是跑
40 分钟。

新增 iovec 分片实验：

- 默认 `--udp-packet-out-iov-mode concat` 仍把一个 native packet-out spec
  的 iovec payload 拼接成单个 UDP/RAP datagram。
- `--udp-packet-out-iov-mode split` 会把每个 iovec segment 单独发送成
  UDP/RAP datagram，并在 `native_udp_send` 里记录 `segmentIndex` /
  `segmentCount`。

当前 live 短测看到 native batch `iovCount=3`、每段约 1204 字节、拼接后约
3612 字节；如果 envelope 模式仍无响应，应短测 `len16 + split`，再比较
官方 0x81 data-frame 的实际 payload size。

`analyze-rap-zime` 现在会输出 `runnerInput.rapDataFrameSendTemplates`，保留
send 方向 0x81 data-frame 的非敏感头字段序列、payloadKind、payloadLength
和 envelope 标记。`zime-native-bridge` 已接入
`--udp-rap-template-mode auto|static|sequence|payload-kind`，默认 `auto` 在
存在 send 模板序列时按 payloadKind 轮换选择。后续 runner 不应只依赖单个
`rapDataFrameTemplate`。

## 26. 2026-07-03 task-forest 维护规则与 UDP probe 对齐任务

本轮仍然没有操作 GUI，没有触碰 CrossDesk，也没有读取/输出
`.tmp/state.json`、token、connectStr、accessToken、cpsid、手机号、密码、
JWT 或其它敏感连接材料。

用户新增明确要求：

- 每次有实质进展，不只更新本交接文档，也必须使用 `$task-forest` 分析当前
  对话和 workspace 状态。
- 长期目标、任务、进度、偏差、风险、决策和 follow-up 要写成
  task-forest proposal，避免后续 agent 重复造轮子。
- 本项目的 task-forest 数据位于：

```text
.agent-workbench/task-forest/
```

已执行：

```bash
python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py init \
  --workspace /home/demo/restore/cmcc-cloud-alive

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py proposal-save \
  --workspace /home/demo/restore/cmcc-cloud-alive \
  --proposal-file /home/demo/restore/cmcc-cloud-alive/notes/task-forest-proposal-20260703-cmcc-protocol.json \
  --overwrite
```

已保存但尚未应用的 proposal：

```text
TFP-20260703-cmcc-protocol
```

proposal 内容摘要：

- global task：实现 `家庭云电脑畅享版月包` 协议级保活 Python runner。
- done task：HTTP-only 和 CAG-only 保活路线已证伪。
- in-progress task：RAP/ZIME native bridge 与 UDP 短测工具。
- in-progress subtask：补齐 `rap-zime-udp-probe` 对 RAP 模板和 payload
  envelope 的支持。
- ready task：重新抓取当前 Win10 环境有效 official trace，生成 fresh
  runner input。
- ready task：用 fresh runner input 跑 session-owning live 短测直到
  `native_channel_created`。
- ready milestone：完成 `DISPLAY_INIT` 后真实 display path 和 40 分钟
  `verified-run`。
- decision：只处理 `家庭云电脑畅享版月包`，CrossDesk 不是 CMCC 客户端，
  不得操作 CrossDesk。
- risk：旧 runner input / UDP target / tunnel / 连接材料过期导致 UDP
  无响应。
- follow-up：每次实质进展都同步 `docs/delivery-handoff.md`。
- deviation：此前 native UDP/RAP 模板、77 个测试通过、
  `udp_response_pending` 等进展没有及时完整同步到本交接文档。

注意：按 task-forest skill 规则，proposal 已保存但没有直接 apply。正式
写入任务图需要用户确认后执行：

```bash
python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py proposal-apply \
  --workspace /home/demo/restore/cmcc-cloud-alive \
  TFP-20260703-cmcc-protocol --yes
```

本轮重新核对的本地验证状态：

```bash
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：通过，`78 tests OK`。这只证明本地研究工具和测试覆盖可运行，不证明
真实云电脑保活成功。

最新 live/短测结论仍保持：

- 动态模板短测使用 `auto + len16 + split + ztec prime`，确实用到了
  `runnerInput.rapDataFrameSendTemplates`。
- bridge report 仍停在：

```text
nativeMilestones.stage = udp_response_pending
```

- 单独外层 `rap-zime-udp-probe` 也没有收到 ZTEC ack。
- 因此旧历史 runner input 不能继续作为协议正确性的判断依据，下一轮需要
  fresh trace / fresh runner input。

本轮代码任务已完成：

- 目标：让 `rap-zime-udp-probe` 也能使用 runner input 中的
  `rapDataFrameTemplate`、`rapDataFrameSendTemplates` 和 RAP payload
  envelope。
- 原因：此前 `zime-native-bridge` 支持动态模板，但单独 UDP probe 仍偏向
  默认/静态发送，短测时容易把“全零 RAP 头不响应”和“目标/连接材料失效”
  混在一起。
- 已实现能力：
  - `--udp-rap-payload-envelope raw|len16|strip-reserve4-len16`
  - `--udp-rap-template-mode static|sequence|payload-kind|auto`
  - 默认保持兼容；传 runner input 时优先使用 trace-derived template。

改动文件：

```text
cmcc_cloud_alive/rap_zime.py
cmcc_cloud_alive/main.py
tests/test_python_modules.py
```

测试覆盖：

- `test_rap_zime_udp_probe_uses_runner_templates_and_len16_envelope`
  - 验证 `rap-zime-udp-probe` 能按 payload kind 命中
    `rapDataFrameSendTemplates`。
  - 验证 `len16` envelope 会进入 wire payload。
  - 验证发出的 RAP frame 使用 trace-derived `field06/word08/word12`。
- `test_rap_zime_udp_probe_cli_loads_native_report_payloads`
  - 验证 CLI 参数 `--udp-rap-payload-envelope` 和
    `--udp-rap-template-mode` 传入 `run_udp_probe`。

本轮验证：

```bash
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_udp_probe_uses_runner_templates_and_len16_envelope \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_udp_probe_cli_loads_native_report_payloads \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_udp_session_sends_ztec_and_rap_payload -v

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：通过，`78 tests OK`。

同步的 task-forest proposal：

```text
TFP-20260703-cmcc-protocol
```

proposal 已更新为：

- `udp-probe-template-envelope` 子任务 `status=done`、`progress=100`。
- 主任务进度从 40 调整为 42。
- native bridge/scaffold 进度从 70 调整为 72。
- 最新本地验证从 `77 tests OK` 更新为 `78 tests OK`。

注意：这只是让短测工具更接近官方 trace 的 RAP 0x81 data-frame 形态，仍然
不是保活成功。当前未完成项不变：

1. 还没有 fresh trace / fresh runner input。
2. 还没有真实 RAP/ZIME UDP response。
3. 还没有 `ZIME_ReceiveData ok`。
4. 还没有 `native_channel_created`。
5. 还没有 live stream、`DISPLAY_INIT` 和 display activity。
6. 还没有无官方 GUI 污染的 40 分钟 `verified-run`。

下一步 live 前建议先重新抓取当前有效 official trace，再生成 fresh
runner input。若仅做外层短测，可优先使用：

```bash
python3 bin/cmcc_cloud_alive.py rap-zime-udp-probe \
  --runner-input reports/<fresh-rap-zime-runner-input>.json \
  --native-report reports/<fresh-zime-native-bridge-packetout>.json \
  --udp-rap-payload-envelope len16 \
  --udp-rap-template-mode auto \
  --timeout 2 \
  --wait-response \
  --report-file reports/<fresh-rap-zime-udp-probe>.json
```

如果这一步仍无 ZTEC ack / RAP response，优先怀疑 fresh 材料、路由或更深层
动态字段；不要直接进入 40 分钟长测。

## 27. 2026-07-03 task-forest 已应用与 runner input 就绪度检查

本轮仍然没有操作 CrossDesk，没有读取/输出 `.tmp/state.json`、token、
connectStr、accessToken、cpsid、手机号、密码、JWT 或其它敏感材料。

用户已确认：

- 允许重新抓官方轨迹，但任何 GUI 点击前必须先截图确认。
- 允许 session-owning 实时短测，接受可能顶号，但不操作 CrossDesk。
- 先根据本轮澄清更新 proposal，再应用 `TFP-20260703-cmcc-protocol`。
- native bridge 与纯 Python 两条路线并行，但优先能最快验证 display path
  的路线。

已执行 task-forest：

```bash
python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py proposal-save \
  --workspace /home/demo/restore/cmcc-cloud-alive \
  --proposal-file /home/demo/restore/cmcc-cloud-alive/notes/task-forest-proposal-20260703-cmcc-protocol.json \
  --overwrite

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py proposal-apply \
  --workspace /home/demo/restore/cmcc-cloud-alive \
  TFP-20260703-cmcc-protocol --yes

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py validate \
  --workspace /home/demo/restore/cmcc-cloud-alive

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py export \
  --workspace /home/demo/restore/cmcc-cloud-alive
```

结果：

```text
validate: 通过
HTML: /home/demo/restore/cmcc-cloud-alive/.agent-workbench/task-forest/exports/task-forest.html
```

当前 task-forest 正式图：

- 节点数：14
- 边数：15
- open：7
- done：7
- blocked：0
- ready 重点任务：
  - `TF-0005`：重新抓取当前 Win10 环境有效 official trace，生成 fresh
    runner input。
  - `TF-0006`：用 fresh runner input 跑 session-owning live 短测直到
    `native_channel_created`，依赖 `TF-0005`。
  - `TF-0007`：完成 `DISPLAY_INIT` 后真实 display path 和 40 分钟
    `verified-run`，依赖 `TF-0006`。

新增本地代码能力：

```text
check-rap-zime-runner-input
```

用途：只做本地 runner input 结构就绪度检查，不连接云端，不证明材料仍 fresh，
并且不输出 UDP target、tunnel id、ZTEC target 等敏感/会话字段明文。

CLI：

```bash
python3 bin/cmcc_cloud_alive.py check-rap-zime-runner-input \
  reports/<runner-input>.json \
  --require-templates \
  --report-file reports/<runner-input>.readiness.json
```

检查项：

- `transport == rap-zime-udp`
- `primaryTunnelId` 存在
- `candidateUdpTargets` 非空
- `candidateZtecTargets` 非空
- `rapDataFrameTemplate` 存在
- `rapDataFrameSendTemplates` 非空
- `needsTraceWithSocketRemote == false`

新增/更新文件：

```text
cmcc_cloud_alive/rap_zime.py
cmcc_cloud_alive/main.py
tests/test_python_modules.py
reports/rap-zime-20260702-211530-wrap-template-dynamic.readiness.json
reports/rap-zime-runner-input-current.readiness.json
notes/task-forest-proposal-20260703-runner-input-readiness.json
```

本地检查结论：

- `reports/rap-zime-20260702-211530-wrap-template-dynamic.json`
  - 结构上具备短测字段：UDP target 数量 1、ZTEC target 数量 1、send
    templates 数量 8。
  - 但 freshness 仍未被结构检查证明；旧材料此前 live 短测已经无 ZTEC ack /
    无 UDP response。
- `reports/rap-zime-runner-input-current.json`
  - 不可用于短测。
  - 缺 UDP target、ZTEC target、RAP data-frame template、send templates，
    且 `needsTraceWithSocketRemote=true`。

测试：

```bash
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_runner_input_readiness_reports_redacted_gaps \
  tests.test_python_modules.PythonModuleTests.test_check_rap_zime_runner_input_cli_writes_readiness_report -v

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：通过，`80 tests OK`。

同步的 task-forest proposal：

```text
TFP-20260703-runner-input-readiness
```

该 proposal 已保存但尚未 apply。内容摘要：

- 新增 done 子任务：`新增 RAP/ZIME runner input 就绪度检查工具`。
- 更新主任务证据到 `80 tests OK`。
- 更新 `TF-0005` 进度：已有 readiness gate，但仍需要 fresh official trace。
- 更新交接文档 follow-up 进度。

下一步：

1. 获取 fresh official trace。若需要点击官方客户端，必须先截图给用户确认。
2. 用 `analyze-rap-zime` 生成 fresh runner input。
3. 用 `check-rap-zime-runner-input --require-templates` 做本地就绪度门禁。
4. 只有 fresh runner input ready 后，再做 session-owning live 短测，目标只看
   `nativeUdpReceived`、`ZIME_ReceiveData ok`、`native_channel_created`。
5. 未到 display path 前不跑 40 分钟 `verified-run`。

## 28. 2026-07-03 官方客户端连接瞬退 trace 结论

本轮没有操作 CrossDesk。官方 GUI 点击前已截图确认目标窗口为“移动云电脑”，
目标卡片为 `家庭云电脑畅享版月包`。由于用户刚调整过系统分辨率，窗口相对坐标
在 `xdotool` 下不可靠；后续 GUI 操作必须继续先截图，再确认鼠标落点所属窗口，
不要复用历史坐标。

本轮官方 trace 采样：

```text
reports/zime-transport-20260703-193100-fresh.jsonl
reports/zime-transport-20260703-193100-fresh.analysis.final.json
reports/rap-zime-20260703-193100-fresh-instant-exit.final.json
reports/rap-zime-20260703-193100-fresh-instant-exit.final.readiness.json
```

相关截图：

```text
reports/screenshots/official-client-current-20260703-194137.png
reports/screenshots/official-client-after-mousedown-click-20260703-194446.png
reports/screenshots/official-client-after-instant-exit-20260703-194610.png
```

观察结论：

- 官方客户端确实进入过“正在连接云电脑，请稍候...”。
- 用户观察到 `uSmartView_VDI_Client` / vdi client 进程只保留不到 5 秒。
- 进程检查也确认：连接后未看到云桌面 UI 进程常驻，只剩官方客户端和
  `bootCypc`，随后官方客户端回到列表页。
- 官方 SDK 日志的脱敏摘要显示：连接命令返回成功、`uSmartView_VDI_Client`
  启动，随后很快收到 disconnect 回调；中间出现 `resultCodeOne 7`。
- 已停止本次官方客户端采样，避免后台官方客户端继续污染后续判断。

最终 trace 分析：

```text
records=19442
DISPLAY_INIT: 未观察到
Surface/Draw/MARK: 未观察到
ACK/PONG maintenance: 未观察到
runner input readyForLiveShortTest=false
desktopKeepaliveProven=false
```

`check-rap-zime-runner-input` 缺失项：

```text
runner input transport is not rap-zime-udp
RAP primary tunnel id is missing
RAP UDP target is missing
RAP data-frame template is missing
ZTEC target is missing
send-side RAP templates are missing
```

判断：

- 这次 trace 是“官方连接瞬退失败证据”，不是 fresh runner input 成功证据。
- 不能用它启动 native bridge live 短测。
- 不能把官方客户端短暂显示连接页、HTTP infoReport/logConfig 成功、列表页仍显示
  `运行中` 当作保活成功。
- 当前距离 Codming 博客方案仍缺：稳定进入 desktop/display path、display 通道
  auth、`DISPLAY_INIT`、Surface/Draw/MARK、ACK/PONG 维护，以及无官方 GUI 污染的
  40 分钟 verified-run。

下一步优先级：

1. 先定位官方客户端为什么 `uSmartView_VDI_Client` 瞬退：重点看 CAG/VDI
   SDK 的断开原因、`resultCodeOne 7`、是否和当前分辨率、权限或注入环境有关。
2. 只有官方客户端能稳定进入桌面并产生 display path trace 后，再生成 fresh
   runner input。
3. fresh runner input 通过 readiness 后，才跑 session-owning native bridge
   短测；目标仍然只看真实 UDP response、`ZIME_ReceiveData ok`、
   `native_channel_created`。
4. 未进入 display path 前，不跑 40 分钟 verified-run。

## 29. 2026-07-03 修正：官方链路已到第一帧，崩在 SPICE_OUTBAND

本节修正第 28 节中“最终 trace 未观察到 display path”的判断边界。抓包分析报告
仍未提取到可用 `DISPLAY_INIT` / runner input，但官方 SDK 底层日志显示这次并非
完全没进 display path：官方 vdi client 已完成通道连接、surface、mark、多路
stream create，并收到 first frame，随后才崩溃退出。

脱敏后的关键时序：

```text
19:48:03.061  all channel 8/8 connect success
19:48:03.145  surface create success
19:48:03.194  mark deal success
19:48:03.200+ stream create success
19:48:03.416  first frame recv success
19:48:03.898  QUIC_create_data_stream
19:48:03.898  Set payload type to: SPICE_OUTBAND
19:48:03.898  catch signal[11]
19:48:03.898  reason: VDI client crash
```

当前修正结论：

- 第 28 节的 readiness 结论仍有效：本次 trace 不能作为 Python runner live
  短测输入，也不能启动 40 分钟 verified-run。
- 但“完全没进 display path”不准确；更准确的结论是：官方客户端已经进入
  display path 并收到第一帧，随后在 outband QUIC stream 创建阶段崩溃。
- 这提高了诊断价值：下一步不应优先继续猜 RAP runner input 字段，而应先区分
  `zime-probe.so` / `LD_PRELOAD` 是否诱发崩溃。

用户补充：IDA 不可用时可以安装。当前工具索引显示 `idapro` 本体仍不可用；
`r2`、Frida 可用。决策是：

- 短期不先安装 IDA，因为无 probe 官方短测更快地区分“probe 诱发”与“客户端自身
  或环境崩溃”。
- 如果无 probe 仍崩溃，再安装或配置 IDA/idalib-mcp，重点分析
  `uSmartView_VDI_Client` 和 `libspice-client-glib-zte-2.0.so.8.5.0` 在
  `QUIC_create_data_stream`、`QUIC_set_streams_pay_load_type`、
  `SPICE_OUTBAND` 相关路径的崩溃原因。

下一步顺序：

1. 做一次不带 `LD_PRELOAD` / `zime-probe.so` 的官方短测；任何 GUI 点击前仍先
   截图确认，不操作 CrossDesk。
2. 如果无 probe 不崩溃并稳定进桌面：说明 probe 侵入性过高，优先改 probe，
   缩小 hook 范围，只保留必要 socket/SSL/ZIME 采样。
3. 如果无 probe 仍崩溃：继续排查官方客户端运行环境、分辨率、outband QUIC 参数，
   并启用 IDA/r2/Frida 做静态与动态定位。
4. 只有稳定产生 fresh display path trace 后，才重新运行
   `analyze-rap-zime` 和 `check-rap-zime-runner-input --require-templates`。

## 30. 2026-07-03 无 probe 官方短测：客户端自身不再 5 秒瞬退

用户已将目标卡片置顶。本轮改用官方窗口截图和窗口内相对坐标，不再使用全屏固定坐标。

点击前窗口截图：

```text
reports/screenshots/official-client-window-only-before-connect-20260703-211029.png
```

截图确认目标卡片为：

```text
家庭云电脑畅享版月包
```

点击使用窗口内坐标：

```text
window id: 0x02200009
window geometry: 1022x574
connect button center: (174, 199)
```

点击后截图：

```text
reports/screenshots/official-client-window-no-probe-after-click-20260703-211107.png
reports/screenshots/official-client-window-no-probe-after-6s-20260703-211113.png
reports/screenshots/official-client-window-no-probe-after-12s-20260703-211120.png
```

结果：

- 6 秒和 12 秒截图均显示已进入 Win10 云桌面。
- `uSmartView_VDI_` 进程在本轮检查时已存活超过 7 分钟，不是 5 秒内瞬退。
- 官方 SDK 日志脱敏摘要显示：本轮无 probe 下同样经过 `surface create`、
  `mark deal`、多路 `stream create`、`first frame recv success`、
  `all channel connect success`，并出现 `QUIC_create_data_stream` /
  `SPICE_OUTBAND`。
- 本轮无 probe 摘要里没有观察到紧随 `SPICE_OUTBAND` 的 `catch signal[11]`
  或 `VDI client crash`。

修正后的判断：

- 官方客户端自身在当前环境可以稳定进入 Win10 桌面。
- 第 29 节记录的 `SPICE_OUTBAND` 后 `signal[11]`，高概率由
  `zime-probe.so` / `LD_PRELOAD` 或过宽 hook 范围诱发。
- 现在不应继续把问题归因到分辨率、官方客户端自身或云桌面不可用；下一步应先降低
  probe 侵入性，再重新抓 display path trace。

下一步：

1. 修改/新增低侵入 probe 模式：先不要 wrap callbacks，不 hook 复杂 QUIC/outband
   路径；优先只采必要 socket/SSL/ZIME send/receive 边界。
2. 重新抓官方 display path trace；GUI 操作仍必须先截图，点击使用窗口内坐标。
3. 重新运行 `analyze-rap-zime` 和
   `check-rap-zime-runner-input --require-templates`。
4. 只有 fresh runner input ready 后，才进入 session-owning native bridge live 短测。

## 31. 2026-07-03 低侵入 zime-probe 模式已实现并本地验证

本轮根据第 30 节结论收缩 probe 侵入性，目标是重新抓官方 display path trace 时尽量不
再诱发 `SPICE_OUTBAND` 后的 `signal[11]`。

代码变更：

- `research/zime-probe.c`
  - 默认 `ZIME_PROBE_CAPTURE_TRANSPORT=0`，不记录也不默认导出底层
    `socket/read/write/send/recv/SSL_*` interpose。
  - 默认 `ZIME_PROBE_WRAP_CALLBACKS=0`，callback table wrapping 仍为显式 opt-in。
  - 默认进程过滤收缩到 `uSmartView`，避免 Electron 主进程和 `bootCypc` 被 probe
    污染；如需扩大范围用 `ZIME_PROBE_PROCESS_FILTER`。
  - C++ callback 符号 interpose 改为编译期 opt-in，默认 `.so` 不再导出
    `DCCallbackImplC::*` 或 `TransportBatchImplC::OnSendData_Batch`。
- `scripts/build-zime-probe.sh`
  - 默认构建 `build/research/zime-probe.so`。
  - `ZIME_PROBE_TRANSPORT_INTERPOSE=1` 构建
    `build/research/zime-probe-transport.so`。
  - `ZIME_PROBE_CPP_INTERPOSE=1` 构建 `build/research/zime-probe-cpp.so`。
- `scripts/run-zime-probe.sh`
  - 新增 `ZIME_PROBE_MODE=low|transport|callback|full|cpp`。
  - 默认 `low` 只采 ZIME C API / struct 边界；`transport/full/cpp` 才启用底层
    transport interpose；`cpp` 才启用 C++ callback 符号 interpose。

本地验证：

```text
scripts/build-zime-probe.sh
ZIME_PROBE_TRANSPORT_INTERPOSE=1 scripts/build-zime-probe.sh
ZIME_PROBE_CPP_INTERPOSE=1 scripts/build-zime-probe.sh
nm -D build/research/zime-probe.so
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
bash -n scripts/build-zime-probe.sh
bash -n scripts/run-zime-probe.sh
```

验证结果：

- 三类 `.so` 均编译通过。
- 默认 `build/research/zime-probe.so` 只导出 ZIME API 符号；没有导出
  `send/recv/socket/connect/bind/read/write/SSL_*`，也没有导出
  `DCCallbackImplC::*` / `TransportBatchImplC::*`。
- `build/research/zime-probe-transport.so` 才导出底层 transport/SSL 符号。
- `build/research/zime-probe-cpp.so` 才额外导出 C++ callback 符号。
- `compileall` 通过；`python3 -m unittest discover -s tests -p 'test_python_*.py' -v`
  通过，80 tests OK。

下一步：

1. 先用 `ZIME_PROBE_MODE=low` 重新抓官方 trace。GUI 点击前仍必须先截图确认
   `家庭云电脑畅享版月包`，点击使用窗口内相对坐标，不操作 CrossDesk。
2. 如果 low trace 能稳定进入 Win10 且记录到足够 ZIME/display path 结构，再运行
   `analyze-zime-probe`、`analyze-rap-zime` 和
   `check-rap-zime-runner-input --require-templates`。
3. 如果 low trace 缺 UDP/RAP runner input 必要字段，再按证据逐级升级到
   `ZIME_PROBE_MODE=transport` 或 `callback`；`cpp` 只作为最后手段。
4. fresh runner input ready 后，再进入 session-owning native bridge live 短测。

## 32. 2026-07-03 low probe 官方验证失败：仍在 SPICE_OUTBAND 后崩溃退回客户端

用户观察到本轮并非没有点击成功，而是进入云桌面几秒后退回移动云电脑客户端本体。
这修正了最初“点击可能没生效”的判断。

本轮操作证据：

```text
ZIME_PROBE_MODE=low scripts/run-zime-probe.sh -- /home/demo/.local/bin/cmcc-jtydn-stable
reports/zime-low-20260703-223950.jsonl
reports/zime-client-low-20260703-223950.out
```

截图：

```text
reports/screenshots/official-client-low-probe-before-connect-20260703-224042.png
reports/screenshots/official-client-low-probe-after-click-20260703-224115.png
reports/screenshots/official-client-low-probe-after-6s-20260703-224120.png
reports/screenshots/official-client-low-probe-after-12s-20260703-224126.png
reports/screenshots/official-client-low-probe-returned-client-20260703-224444.png
```

过程约束执行情况：

- 点击前已截图确认第一张卡片为 `家庭云电脑畅享版月包`。
- 点击使用窗口内相对坐标，窗口 ID `0x03200009`，窗口几何 `1022x574`，
  坐标 `(174,199)`。
- 未操作 CrossDesk。

关键结果：

- 用户确认：云桌面确实进入了几秒，然后退回客户端本体。
- 退回后进程表只剩 `cmcc-jtydn` / `bootCypc`，未见 `uSmartView_VDI_` 常驻。
- `reports/zime-low-20260703-223950.jsonl` 为空，说明 low probe 没拿到可用 ZIME
  事件材料；不能作为 runner input。
- 官方 SDK 日志脱敏摘要显示本轮仍到达：

```text
22:41:18.418  surface create success
22:41:18.435  all channel 8/8 connect success
22:41:18.452  mark deal success
22:41:18.458  stream create success
22:41:18.530  first frame recv success
22:41:19.097  QUIC_create_data_stream
22:41:19.097  Set payload type to: SPICE_OUTBAND
22:41:19.097  catch signal[11]
22:41:19.097  reason: VDI client crash
```

修正后的判断：

- 只去掉 transport/C++ callback interpose 仍不够；`ZIME_PROBE_MODE=low` 这条
  LD_PRELOAD/ZIME API interpose 路线依旧会导致官方 vdi client 在
  `SPICE_OUTBAND` 路径崩溃，或至少无法排除其诱发作用。
- low trace 文件为空，所以它既不能生成 fresh runner input，也不能继续作为
  “最快验证 display path”的采样路线。
- 不能继续升级到 `transport` / `callback` / `cpp` 模式抓官方 GUI trace；这些模式
  侵入性更高，风险更大。

已清理：

- 已终止本次 low-probe 启动的 CMCC 进程组，避免后续无 LD_PRELOAD 抓取被污染。
- 清理过程中没有操作 CrossDesk。

下一步路线：

1. 切换到无 LD_PRELOAD 的外部采样：优先 `tcpdump` / `dumpcap` / `tshark` 抓取
   官方客户端稳定 no-probe 连接时的 UDP/TCP 外层流量。
2. 同步用 `ss` / `lsof` / `bpftrace` 或只读进程观察记录 `uSmartView_VDI_` 的 socket
   peer，不插入目标进程调用路径。
3. 对官方二进制和 SDK so 做静态分析：IDA 已可用；必要时分析
   `uSmartView_VDI_Client`、`libZIMEDataEngine.so`、
   `libspice-client-glib-zte-2.0.so.8.5.0` 的 `SPICE_OUTBAND` 和 RAP/ZIME
   packet-out 路径。
4. 用外部 pcap/静态字段重新生成或扩展 `analyze-rap-zime` 输入；在 runner input
   readiness 通过前，不跑 session-owning native bridge live 短测。

## 33. 2026-07-04 无 LD_PRELOAD pcap 元数据分析入口已落地

本轮先复核了用户补充的“进入云桌面几秒后退回客户端本体”。当前机器状态显示：

```text
uSmartView_VDI_Client 仍在运行
窗口 0x04a00006 可见，标题为“移动云电脑 ...”
客户端本体窗口 0x02e00009 仍在列表页
```

新截图：

```text
reports/screenshots/current-vdi-window-no-probe-still-alive-20260703-230821.png
reports/screenshots/current-client-window-no-probe-20260703-230821.png
```

第一张截图显示 Win10 桌面和任务栏时间 `23:09`，说明当前无 `LD_PRELOAD` /
无 `zime-probe.so` 的官方会话仍然稳定；“几秒后退回客户端本体”应继续归档到
第 32 节的 `ZIME_PROBE_MODE=low` 崩溃路径，而不是误判为当前 no-probe 路径。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `analyze_external_pcap()`。
  - 只通过 `tshark -T fields` 读取 frame time、endpoint、port、frame length、
    UDP/TCP length。
  - 不请求、不解析、不写入 `data`、`udp.payload`、`tcp.payload` 或任意 payload
    明文。
  - 可选读取同窗口 `ss -p` 快照，汇总 `uSmartView_VDI_` loopback peer 和
    `cmcc-jtydn` / `bootCypc` 外部 peer。
- `cmcc_cloud_alive/main.py`
  - 新增 CLI：`analyze-rap-zime-pcap`。
- `tests/test_python_modules.py`
  - 新增 pcap metadata-only 分析测试。
  - 新增 CLI 写报告测试。

验证命令：

```text
python3 -m compileall -q cmcc_cloud_alive tests/test_python_modules.py
python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_pcap_analysis_is_metadata_only tests.test_python_modules.PythonModuleTests.test_rap_zime_pcap_analysis_cli_writes_report -v
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

验证结果：

```text
新增相关测试通过
全量 82 tests OK
```

已用真实 no-probe pcap 生成脱敏报告：

```text
reports/official-no-ldpreload-active-20260703-225949.pcap-analysis.json
reports/official-no-ldpreload-active-20260703-225949.pcap-runner-readiness.json
```

pcap metadata 结论：

- pcap 时长约 59.7 秒，外部采样期间 VDI 会话稳定。
- 主要 UDP 会话均指向同一个外层候选 target：

```text
111.31.3.182:8899
```

- 两条主 UDP 流各约 9.2k 包，持续满采样窗口；另有多组低频 8899 控制流。
- `ss` 快照显示 `uSmartView_VDI_` 主要通过本机 loopback peer 通信，外层网络
  流量更多由客户端/SDK 侧进程承载。

runner input readiness 结论：

```text
readyForLiveShortTest=false
desktopKeepaliveProven=false
candidateUdpTargets=1
candidateZtecTargets=0
rapDataFrameSendTemplates=0
missing:
  - runner input transport is not rap-zime-udp
  - RAP primary tunnel id is missing
  - RAP data-frame template is missing
  - ZTEC target is missing
  - send-side RAP templates are missing
```

修正后的判断：

- 外部 pcap 路线已经证明 no-probe 稳定 display path 的外层 UDP target，但仅凭
  pcap 元数据不能生成可 live 短测的 runner input。
- 不能把该 pcap 报告直接喂给 `zime-native-bridge --runner-input`；当前门禁已经
  正确阻止。
- 下一步缺口不是“再点连接”或“再跑 low probe”，而是补齐：
  `primaryTunnelId`、`candidateZtecTargets`、`rapDataFrameTemplate`、
  `rapDataFrameSendTemplates`。

下一步建议：

1. 优先用静态分析或极低侵入外部观察补字段：
   `uSmartView_VDI_Client`、`libZIMEDataEngine.so`、
   `libspice-client-glib-zte-2.0.so.8.5.0`。
2. 如果必须再抓动态材料，避免 `LD_PRELOAD` 官方 GUI 路线；考虑只读系统级
   socket/uprobes/bpftrace 观察，且先验证不会插入目标进程调用路径。
3. 字段补齐后再运行：

```text
python3 -m cmcc_cloud_alive.main check-rap-zime-runner-input <fresh-report> --require-templates
```

4. 只有 readiness 通过后，才进入 session-owning native bridge live 短测。

## 34. 2026-07-04 IDA 静态分析修正 native bridge 短测路线

本轮优先使用 IDA Pro headless / idalib 分析官方 `libspice-client-glib-zte`。

先排除一个环境误判：

- `ida-pro-mcp --broker` 在线但没有 GUI IDB 绑定，因此 MCP resource 返回空内容。
- `IDADIR=/home/demo/tools/idapro-9.3` 后，pipx venv 内的 idapro 包可用。
- 直接打开 `/opt/.../libspice-client-glib-zte-2.0.so.8.5.0` 失败的真实原因是
  IDA 要在 `/opt` 只读目录旁写 `.i64`，报 `Permission denied`，不是 EULA 或
  batch-mode license 问题。
- 已将目标二进制复制到 `.tmp/ida-inputs/` 后成功自动分析。
- 不使用、不记录、不协助 license/keygen/EULA gate patch 路线。

新增只读 IDA 提取脚本：

```text
scripts/ida_extract_spice_zime.py
```

生成报告：

```text
reports/ida-libspice-zime-analysis-20260704.json
```

IDA 关键结论：

- `QUIC_create_data_channel` 里 `ZIME_CreateDataChannel()` 的 remote address 来自
  `kcp->dest_ip` / `kcp->dest_port`，也就是 ZIME channel context 的 UDP remote。
- `QUIC_create_data_stream` 构造 `T_ZIMEStreamParam_C`，调用
  `QUIC_set_streams_pay_load_type()` 后再调用 `ZIME_CreateDataStream()`。
- `QUIC_set_streams_pay_load_type()` 只根据 channel/link 类型写 payload type：
  `SPICE_PORT`、`SPICE_*` 或 `SPICE_OUTBAND`；其中 `sock_link_type == 2` 时写
  `SPICE_OUTBAND`。
- `QUIC_deal_quic_data_send()` 把 SPICE payload 交给
  `ZIME_SendData(engine, channel_id, stream_id, data, datalen)`。
- `QUIC_on_send_data_cb()` 是 ZIME packet-out 单包回调：先把输出 buffer 前 4 字节
  写成 `manage->kcp->conv`，然后直接 `sendto()` 到 `manage->kcp->dest_ip:dest_port`。
- `QUIC_on_send_data_batch_cb()` 走 `QUIC_send_packets_linux()`，同样是 Linux UDP
  batch send 路径。

修正后的判断：

- 对 native bridge 来说，`raw` UDP 模式不是兜底假模式，而是与 libspice 静态路径
  一致的候选路线：由 native ZIME 产出完整 UDP payload，Python 只负责外部 UDP
  send/receive。
- `rapDataFrameTemplate` / `rapDataFrameSendTemplates` 仍保留给旧 RAP wrapper 短测，
  但不应阻塞 `external-pcap-metadata-only` 输入的 native raw UDP 短测准备。
- 这不等于保活成功；它只是把下一步最快验证 display path 的路线从“补 RAP template”
  修正为“用 pcap metadata 的 UDP target 跑 native raw UDP session-owning 短测”。

代码变更：

- `cmcc_cloud_alive/main.py`
  - `zime-native-bridge --runner-input ... --udp-transport-mode auto` 在检测到
    `transport=external-pcap-metadata-only` 且没有 `primaryTunnelId` 时，自动选择
    `raw` UDP 模式。
  - 该分支只需要 `candidateUdpTargets`，不会要求 RAP tunnel id/template。
  - 普通 runner input 仍保持旧行为：auto 选择 `rap`。
- `tests/test_python_modules.py`
  - 新增测试覆盖 pcap metadata-only runner input 自动选择 raw native UDP。

验证命令：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_loads_runner_input_for_udp_transport tests.test_python_modules.PythonModuleTests.test_zime_native_bridge_cli_auto_uses_raw_for_pcap_metadata_only_input -v
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

验证结果：

```text
新增相关测试通过
全量 83 tests OK
```

下一步：

1. 使用
   `reports/official-no-ldpreload-active-20260703-225949.pcap-analysis.json`
   作为 runner input，执行一次明确标注 session-owning 的 native raw UDP 短测。
2. 短测目标只看：
   `native_udp_send`、真实 UDP response、`ZIME_ReceiveData ret=0`、
   `native_channel_created status=0 err=0`。
3. 如果 raw 模式仍无 response，再回到静态分析 `libZIMEDataEngine.so` 的 packet
   格式和 `T_ZIMEChannelContext_C` opaque 字段，而不是继续 GUI LD_PRELOAD probe。

## 35. 2026-07-04 native raw UDP session-owning 短测结果

按第 34 节的 IDA 结论，使用 no-probe pcap metadata-only runner input 跑了两次
`zime-native-bridge` raw UDP 短测。两次均不操作 GUI、不操作 CrossDesk；属于
session-owning/可能顶号短测，但未达到 display path。

命令要点：

```text
python3 -m cmcc_cloud_alive.main zime-native-bridge \
  --allow-native-run \
  --runner-input reports/official-no-ldpreload-active-20260703-225949.pcap-analysis.json \
  --display-init \
  --udp-read-timeout 0.5 \
  --udp-receive-limit 8 \
  --udp-process-ticks-after-receive 3 \
  --wait-channel-created-ticks 20
```

第二次额外加：

```text
--udp-ztec-prime --udp-ztec-timeout 0.8
```

报告：

```text
reports/zime-native-raw-pcap-metadata-20260704.json
reports/zime-native-raw-pcap-metadata-20260704.summary.json
reports/zime-native-raw-pcap-metadata-ztec-20260704.json
reports/zime-native-raw-pcap-metadata-ztec-20260704.summary.json
```

脱敏摘要结论：

- `ZIME_CreateDataEngine` / `ZIME_Init` /
  `ZIME_SetDataChannelCallback` / `ZIME_SetDataExternalTransport` 均成功。
- `ZIME_CreateDataChannel` 返回 0，`errorInfo=Operation successful.`。
- native packet-out 回调出现，并且 raw UDP 包已实际发到 `111.31.3.182:8899`。
- 第一轮 `sentPackets=6`，`receivedPackets=0`。
- 第二轮启用 ZTEC prime，`ztecSent=1`，但 `ztecAckReceived=0`，
  仍然 `receivedPackets=0`。
- 两轮均出现 `native_channel_created` 回调，但不是成功：
  `status=1`，`err=60`。
- 因为未观察到 UDP response，未调用成功的 `ZIME_ReceiveData`，也未创建 stream，
  没有发送 `DISPLAY_INIT`。

当前阶段：

```text
nativeMilestones.stage = udp_response_pending
desktopKeepaliveProven = false
displayPathObserved = false
verifiedRunPassed = false
```

修正后的判断：

- Python native bridge 已证明能让 native ZIME 产出 QUIC-like packet-out 并发往
  官方 no-probe pcap 识别出的 UDP target。
- 但仅有 pcap metadata 的 target 不足以让服务端响应；仍缺官方会话上下文字段。
- 缺口不再只是旧文档里的 `rapDataFrameTemplate`，而应优先静态恢复或外部观察：
  - `kcp->conv` / channel id 初始值与服务端关联方式；
  - `T_ZIMEChannelContext_C.socketParam.opaque` 字段真实内容；
  - `kcp->dest_ip/dest_port` 与 CAG/connect material 的映射；
  - 是否必须在官方已建立的 socket/session 生命周期内复用更多上下文。

下一步：

1. 继续用 IDA 分析 `libspice-client-glib-zte` 中 `kcp` 初始化和
   `QUIC_create_data_channel` 调用来源，定位 `dest_ip`、`dest_port`、`conv`、
   `pack_mtu`、`be_connected`、`user_data` 的赋值链。
2. 并行分析 `libZIMEDataEngine.so` 的 `ZIME_CreateDataChannel` /
   `ZIME_Init` 默认参数和 error 60 含义。
3. 如需重新 live 测试，先根据静态恢复更新参数；不要继续只用 metadata target
   重复 raw UDP 短测。

## 36. 2026-07-04 KCP sync/SYNACK 前置协商字段已恢复

继续沿第 35 节的失败点分析：`QUIC_create_data_channel` 的唯一直接调用者是
`deal_kcp_sync_ack_cmd`。这说明 native raw UDP 不能跳过 KCP 前置协商；官方客户端
只有在收到 `IKCP_CONV_SYNACK` 并完成 capability negotiation 后，才会调用
`ZIME_CreateDataChannel`。

IDA 新增分析函数：

```text
deal_kcp_sync_ack_cmd
ikcp_deal_svr_sync_ack
ikcp_deal_link_sync
ikcp_get_seg_info
ikcp_encode_seg
ikcp_output
udp_set_dest_addr_info
spice_init_udp_thread
split_spice_init_udp_info
```

关键静态结论：

- `ikcp_get_seg_info()` 从 UDP payload 起始处读取 21 字节 unaligned little-endian
  header：

```text
offset 0   u32 conv
offset 4   u8  cmd
offset 5   u16 wnd
offset 7   u32 ts
offset 11  u32 sn
offset 15  u32 una
offset 19  u16 len
offset 21  payload 或可选 FEC/stream 扩展
```

- `ikcp_encode_seg()` 写同样的 21 字节 header；若 `be_fec && !be_auth` 追加
  `total_pos` 两字节，若 `be_using_stream` 追加 `stream_id` 一字节。
- `ikcp_deal_svr_sync_ack()` 在收到服务端 SYNACK 后：
  - `kcp->conv = seg.una`
  - `kcp->use_quic = (seg.wnd & 0x20) != 0`
  - `kcp->be_using_stream = (seg.wnd & 0x02) != 0`
  - 根据 pack-check/FEC 调整 `pack_mtu` / `head_len`
  - 设置 `kcp->be_connected = 1`
  - 再通过 `ikcp_output()` 发 21 字节 ack
- `deal_kcp_sync_ack_cmd()` 只有在 `kcp->be_quic` 且 `kcp->use_quic` 为真时，
  才进入 `QUIC_create_data_channel()`。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `decode_kcp_segment()`。
  - 新增 `looks_like_kcp_segment()`，目前保守限制为 sync conv `0x80000002`，
    避免把 SPICE payload 误判为 KCP。
  - `classify_payload()` 新增 `kcp-sync-segment:*` 分类。
- `tests/test_python_modules.py`
  - 新增 KCP 21 字节 header 解码测试。
  - 修正后确认不会破坏现有 display-path SPICE 分类。

验证命令：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_trace_analysis_marks_family_native_trace_without_rap tests.test_python_modules.PythonModuleTests.test_rap_zime_decodes_kcp_sync_ack_segment_from_ida_layout -v
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

验证结果：

```text
全量 84 tests OK
```

下一步修正：

- 不要再重复只发 native raw ZIME packet-out；必须先实现或捕获 KCP sync/SYNACK
  前置握手。
- 继续恢复 `listen_udp_data_thread` / `ikcp_output` 的 UDP send 入口，确定首个
  client SYN 包如何构造、`conv/syn_id/cmd/wnd/len` 初值来自哪里。
- 之后把 KCP sync probe 接到 `zime-native-bridge` 前置步骤，再观察是否能拿到
  SYNACK、设置 `use_quic`，再进入 ZIME channel。

## 37. 2026-07-04 client SYN 构造与 KCP sync probe 已落地

按第 36 节的下一步继续 IDA headless 分析，扩展
`scripts/ida_extract_spice_zime.py` 的目标函数后重新生成：

```text
reports/ida-libspice-zime-analysis-20260704.json
```

新增重点函数：

```text
ikcp_send_link_sync
ikcp_create
ikcp_set_dest
assign_thread_new_kcp_conv
_udp_output
udt_output
listen_udp_data
split_ice_deal_spical_cmd_deal_syn
ice_deal_svr_sync_ack
ikcp_deal_clt_sync_ack
```

关键静态结论：

- `ikcp_send_link_sync()` 构造首个 client SYN：
  - `seg.conv = 0x80000001`
  - `seg.sn = kcp->syn_id`
  - `seg.ts = kcp->current`
  - `seg.len = kcp->mtu`
  - `seg.una = kcp->conv`
  - `seg.cmd` 包含 SSL、detect-mtu、client pack-check、client FEC、
    `0x40 support-data-ex`、multi-link 等 capability bit
  - `seg.wnd` 包含 GCC、stream、outband、QUIC capability bit
- 普通首包长度是 21 字节；reconnect 时才在 offset 21 追加 64 字节块。
- `ikcp_output()` 只有在 `be_pack_check && be_connected` 时追加 4 字节 check
  code；client SYN 发送时尚未 connected，因此首包仍是 21 字节。
- `assign_thread_new_kcp_conv()` 生成的新 conv 形态为
  `(thread->checkFlag | 0x80000000)`，但 client SYN wire header 的 sync conv
  仍固定为 `0x80000001`。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `KCP_CLIENT_SYN_CONV = 0x80000001` 和
    `KCP_SYNC_ACK_CONV = 0x80000002`。
  - `KCP_CMD_FLAGS` 新增 `support-data-ex`，`KCP_WND_FLAGS` 新增 `outband`。
  - 新增 `encode_kcp_segment()`。
  - 新增 `build_kcp_client_syn_segment()`。
  - 新增 `run_kcp_sync_probe()`，只发 client SYN 并等待 SYNACK；报告明确
    `desktopKeepaliveProven=false`、`displayPathObserved=false`、
    `verifiedRunPassed=false`。
  - `classify_payload()` 可区分 `kcp-client-syn:*` 与
    `kcp-sync-segment:*`。
- `cmcc_cloud_alive/main.py`
  - 新增 CLI：`rap-zime-kcp-sync-probe`。
- `tests/test_python_modules.py`
  - 新增 client SYN 编码测试。
  - 新增本地 UDP server sync probe 测试。

本地验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_encodes_kcp_client_syn_from_ida_layout tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack tests.test_python_modules.PythonModuleTests.test_rap_zime_decodes_kcp_sync_ack_segment_from_ida_layout -v
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
新增相关测试通过
全量 86 tests OK
```

live 短测：

```text
python3 -m cmcc_cloud_alive.main rap-zime-kcp-sync-probe \
  --runner-input reports/official-no-ldpreload-active-20260703-225949.pcap-analysis.json \
  --timeout 1 \
  --receive-limit 4 \
  --report-file reports/kcp-sync-probe-pcap-metadata-20260704.json
```

脱敏结论：

- 已向旧 no-probe pcap metadata target 发出 21 字节 client SYN。
- `clientSyn.conv = 0x80000001`。
- `clientSyn.cmdFlags = detect-mtu, client-pack-check, client-fec, support-data-ex`。
- `clientSyn.wndFlags = stream, quic`。
- `synackReceived=false`，`responses=[]`。
- 当前仍未进入 ZIME channel、未创建 stream、未发送 DISPLAY_INIT。

当前阶段：

```text
nativeMilestones.stage = synack_pending
desktopKeepaliveProven = false
displayPathObserved = false
verifiedRunPassed = false
```

修正后的判断：

- client SYN 编码和本地 sync probe 工具已具备，距离博客式协议保活又前进了一层。
- 但旧 pcap metadata 只有外层 UDP target，不足以让服务端返回 SYNACK。
- 下一步不能重复 raw ZIME packet-out，也不能重复只用旧 metadata target 发 SYN。
  必须优先做其中之一：
  1. 重新获取 fresh official trace/会话上下文；
  2. 继续静态恢复 `syn_id`、`conv`、`be_ssl`、outband、auth 或 socket/session
     绑定参数；
  3. 拿到 SYNACK 后再把 `conv/use_quic/be_using_stream/outband` 协商结果接入
     `zime-native-bridge`，再进入 `ZIME_CreateDataChannel`。

task-forest：

- 已保存并应用：

```text
TFP-20260704-kcp-sync-probe
```

- 新增完成节点：`实现 KCP client SYN 编码与 sync probe`。
- 新增后续节点：`获取 fresh KCP SYNACK 并接入 native bridge 前置握手`。

## 38. 2026-07-04 IDA 复核 ZIME channel/stream 创建门槛

本轮按用户再次要求使用 `$task-clarifier` 对齐任务后继续执行。对齐结论：

- 目标不变：参考 Codming 博客的 display-path 保活思路，在家庭云电脑畅享版月包上实现
  无官方 GUI 的 Python 协议 runner。
- 当前最短路线仍是 RAP/ZIME/SPICE display path；HTTP/CAG 只作为反例或获取连接材料入口。
- 可重新抓官方轨迹，但任何 GUI 点击前必须先截图确认，并使用窗口内坐标；不操作
  CrossDesk。
- 可做 session-owning 实时短测，接受可能顶号；但短测必须标注 researchOnly/
  sessionOwning，不能当作最终保活成功。
- 每次实质进展必须同步本交接文档和 task-forest。

本轮新增或更新的证据：

```text
scripts/ida_extract_spice_zime.py
reports/ida-libspice-zime-analysis-20260704-rerun.json
reports/ida-libZIMEDataEngine-analysis-20260704.json
reports/task-forest-proposals/TFP-20260704-ida-zime-channel-gates.json
```

脚本变更：

- `scripts/ida_extract_spice_zime.py` 的目标函数列表新增
  `ZIMEDataEngineImpl/Core` 内部符号：
  - `ZIME_SetDataExternalTransport`
  - `ZIME_CreateDataChannel`
  - `ZIMEDataEngineCore::CreateDataChannel`
  - `ZIME_PrepareForCreateDataChannel`
  - `ZIMEDataEngineCore::PrepareForCreateDataChannel`
  - `ZIME_CreateDataStream`
  - `ZIMEDataEngineCore::CreateDataStream`
  - `ZIMEQuicDataChannel::CreateStream`

IDA 关键结论：

- `QUIC_create_data_channel()` 不是从 CLI 参数直接裸建 channel；它从 KCP 对象读取：
  - `kcp->dest_ip`
  - `kcp->dest_port`
  - `kcp->pack_mtu`
  - `be_using_stream`
  - `use_quic`
  - outband/stream 等协商结果
- `deal_kcp_sync_ack_cmd()` 在收到 `IKCP_CONV_SYNACK` 后调用
  `ikcp_deal_svr_sync_ack()`，再判断 `kcp->use_quic`，然后才进入
  `QUIC_create_data_channel()`。
- `ikcp_deal_svr_sync_ack()` 会从 SYNACK 更新：
  - `kcp->conv = seg.una`
  - `be_pack_check`
  - `be_fec`
  - `be_support_data_ex`
  - `use_quic`
  - `be_using_stream`
  - `head_len`
- `libZIMEDataEngine.so` 内部 `CreateDataChannel` 的失败门槛包括：
  - engine 未 init 或已 destroy；
  - callback 未设置或 external transport 未设置；
  - engine 协议不是 QUIC/SCTP；
  - channelSize 超限。
- `ZIMEQuicDataChannel::CreateStream` 还会在 QUIC connection 未创建时失败。

因此，第 35 节里 native raw UDP 短测虽然能让 `ZIME_CreateDataChannel ret=0`，
但不能说明已经到 display path；没有 SYNACK/use_quic 和后续 QUIC connection gate，
继续裸建 stream 或重复发送 DISPLAY_INIT 都没有意义。

本地验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
compileall OK
Ran 86 tests in 1.663s
OK
```

当前距离 Codming 博客式协议保活的差距：

```text
已完成：
- SOHO 登录/列表/状态能力；
- HTTP-only 和 CAG-only 路线证伪；
- no-probe 官方客户端可稳定进入 Win10 的外层 UDP target 元数据；
- IDA 证明 native raw UDP packet-out 路线；
- KCP 21 字节 header、client SYN 编码和 sync probe；
- IDA 复核 ZIME channel/stream gate。

未完成：
- fresh SYNACK；
- 从 SYNACK 提取 conv/use_quic/be_using_stream/outband 等协商状态；
- 将 SYNACK 协商状态接入 native bridge；
- native_channel_created status=0 且 QUIC connection 建立；
- ZIME_CreateDataStream 成功；
- ZIME_SendData(DISPLAY_INIT) 后真实 display activity；
- 无官方 GUI 污染的 40 分钟 verified-run。
```

下一步优先级：

1. 不要再重复旧 metadata target 的 raw ZIME 或 KCP SYN 空测。
2. 优先重新抓 fresh official trace/会话上下文；如需 GUI，先截图确认畅享版客户端窗口和
   “连接”按钮位置，使用窗口内坐标，不碰 CrossDesk。
3. 或继续静态恢复 client SYN 所需的 `syn_id`、`conv`、auth、socket/session 绑定参数，
   直到 sync probe 能拿到 `synackReceived=true`。
4. 拿到 SYNACK 后，把 `conv/use_quic/be_using_stream/outband/pack_mtu/dest_ip/dest_port`
   接入 `zime-native-bridge`，再尝试 channel、stream 和 `DISPLAY_INIT`。
5. 只有观察到真实 display path 后，才进入 40 分钟 verified-run。

task-forest：

- 已保存并应用：

```text
TFP-20260704-ida-zime-channel-gates
```

- 新增完成节点：`用 IDA 确认 ZIME channel/stream 创建门槛`。
- 更新 `TF-0003`：native bridge 下一步收敛为 fresh SYNACK/use_quic gate。
- 更新 `TF-0029`：进度从 0% 到 15%，因为必要性已由 IDA 证据确认，但 SYNACK 仍未获得。
- 导出 HTML：

```text
/home/demo/restore/cmcc-cloud-alive/.agent-workbench/task-forest/exports/task-forest.html
```

## 39. 2026-07-04 修正 client SYN header len 字段

继续复核第 37、38 节后发现一个关键编码偏差：

- IDA `ikcp_send_link_sync()` 里明确执行 `seg.len = kcp->mtu`。
- 官方 fresh client SYN 实际只发送 21 字节 header：`data_len = 21`。
- 因此 wire payload 为空，但 KCP header 内的 `len` 字段应该是 MTU，不应该是 0。

之前 Python `build_kcp_client_syn_segment()` 复用了通用 `encode_kcp_segment()`，
导致 header `len = len(payload) = 0`。这可能是旧 metadata target 不回 SYNACK 的
原因之一，至少说明本地 SYN 编码还没有完全按 IDA 证据还原。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - `encode_kcp_segment()` 新增 `declared_len`，把 header 声明长度与实际发送
    payload 长度分离。
  - `build_kcp_client_syn_segment()` 对 fresh SYN 写入 `declared_len=mtu`，
    仍只发送 21 字节 header。
  - `looks_like_kcp_segment()` 允许 `KCP_CLIENT_SYN_CONV` 的 21 字节/85 字节
    SYN header 出现 `len > actual_payload`，以匹配官方实现。
- `tests/test_python_modules.py`
  - 更新 client SYN 测试，要求 `decoded["len"] == 1400`。
  - 要求 fresh SYN `payloadLengthMatches=false`，但仍能被 classifier 识别为
    `kcp-client-syn:*`。
  - 本地 UDP sync probe 测试也校验请求里的 `len=1400`。

修正后短测：

```text
python3 -m cmcc_cloud_alive.main rap-zime-kcp-sync-probe \
  --runner-input reports/official-no-ldpreload-active-20260703-225949.pcap-analysis.json \
  --timeout 1 \
  --receive-limit 4 \
  --report-file reports/kcp-sync-probe-pcap-metadata-lenmtu-20260704.json
```

脱敏结果：

```text
bytesSent = 21
clientSyn.len = 1400
clientSyn.payloadLengthMatches = false
clientSyn.cmdFlags = detect-mtu, client-pack-check, client-fec, support-data-ex
clientSyn.wndFlags = stream, quic
synackReceived = false
responses = []
```

判断：

- 这次短测不是重复旧空测，因为 wire encoding 已发生实质修正。
- 修正后旧 metadata target 仍不回 SYNACK，说明下一步仍不能继续旧 target 空跑。
- 下一步优先级不变：
  1. 获取 fresh official trace/会话上下文；
  2. 或继续恢复 `syn_id`、`conv`、auth、socket/session 绑定参数；
  3. 直到 `synackReceived=true` 后，再接入 native bridge。

本地验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_encodes_kcp_client_syn_from_ida_layout tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack tests.test_python_modules.PythonModuleTests.test_rap_zime_decodes_kcp_sync_ack_segment_from_ida_layout -v
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
KCP 相关 3 tests OK
Ran 86 tests in 1.763s
OK
```

task-forest：

- 需要记录本次修正为第 37 节 client SYN 实现的纠偏。
- `TF-0029` 仍处于 ready：`fresh SYNACK` 未获得。

## 40. 2026-07-04 修正 client SYN 默认 outband capability

继续沿第 39 节复核 SYNACK 缺口时，IDA 进一步确认：

```text
init_local_rw_sock_pair_udp()
  kcp->be_outband = 1
  if (proxy_sock->cag_client_key == 6) {
      kcp->be_multi = ...
      kcp->be_outband = 0
  }
  kcp->be_quic = use_quic
  ikcp_send_link_sync(...)
```

也就是说，在普通 SPICE_OUTBAND 路线里 client SYN 默认应带 outband capability；
只有 proxy type/key 为 6 的路径才清零。此前 `rap-zime-kcp-sync-probe` 默认
`be_outband=false`，需要手动传 `--outband` 才会置位。这与当前目标路线不一致。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - `build_kcp_client_syn_segment(..., be_outband=True)` 默认改为 true。
  - `run_kcp_sync_probe(..., be_outband=True)` 默认改为 true。
- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-sync-probe` 默认使用 outband。
  - 保留 `--outband` 作为显式置位选项。
  - 新增 `--no-outband`，用于非 outband/proxy type 6 路径。
- `tests/test_python_modules.py`
  - sync probe 测试要求默认 client SYN `wndFlags = stream, outband, quic`。

修正后短测：

```text
python3 -m cmcc_cloud_alive.main rap-zime-kcp-sync-probe \
  --runner-input reports/official-no-ldpreload-active-20260703-225949.pcap-analysis.json \
  --timeout 1 \
  --receive-limit 4 \
  --report-file reports/kcp-sync-probe-pcap-metadata-lenmtu-outband-20260704.json
```

脱敏结果：

```text
bytesSent = 21
clientSyn.len = 1400
clientSyn.wnd = 50
clientSyn.wndFlags = stream, outband, quic
synackReceived = false
responses = []
```

判断：

- 这是第二个 client SYN 编码纠偏：第 39 节修正 header `len=mtu`，本节修正
  outband capability 默认值。
- 修正后旧 metadata target 仍不回 SYNACK，说明继续用该 target 空跑意义不大。
- 下一步仍是 fresh official trace/会话上下文，或继续恢复 `syn_id`、`conv`、
  auth、socket/session 绑定参数。

本地验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_encodes_kcp_client_syn_from_ida_layout tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack -v
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
KCP/outband 相关 2 tests OK
Ran 86 tests in 1.639s
OK
```

## 41. 2026-07-04 确认旧 runner input 已过期，禁止继续重复 live 空测

按当前任务约束，后续不能再拿旧 trace-derived runner input 或 no-probe pcap
metadata-only 报告反复做 session-owning UDP/native 短测。为避免空转，本轮用
已有 `check-rap-zime-runner-input` freshness gate 重新生成两个脱敏 readiness
报告：

```text
reports/rap-zime-20260702-211530-wrap-template-dynamic.stale-readiness-20260704.json
reports/official-no-ldpreload-active-20260703-225949.pcap-stale-readiness-20260704.json
```

结论：

```text
2026-07-02 211530-wrap-template-dynamic:
  readyForLiveShortTest = false
  missing = runner input file is older than the configured max age
  ageSeconds ~= 47578
  maxAgeSeconds = 1800
  stale = true

2026-07-03 no-probe pcap metadata:
  readyForLiveShortTest = false
  missing =
    runner input transport is not rap-zime-udp
    RAP primary tunnel id is missing
    RAP data-frame template is missing
    ZTEC target is missing
    send-side RAP templates are missing
    runner input file is older than the configured max age
  ageSeconds ~= 17147
  maxAgeSeconds = 1800
  stale = true
```

判断：

- `rap-zime-20260702-211530-wrap-template-dynamic.json` 结构字段完整，但已经是旧
  会话材料；它可以继续作为离线模板/字段研究样本，不能继续作为 live 短测输入。
- `official-no-ldpreload-active-20260703-225949.pcap-analysis.json` 只提供外层
  UDP target 元数据，既缺 RAP tunnel/template/ZTEC，又已过 freshness gate。
- 因此下一次 session-owning live 短测必须先获得 fresh runner input，或者先用
  IDA/静态恢复补齐足以拿到 `synackReceived=true` 的会话/握手参数。
- 若必须重新抓官方轨迹，仍执行硬约束：GUI 点击前先截图确认 CMCC 客户端窗口和
  “连接”按钮，使用窗口内坐标，不操作 CrossDesk。

当前距离 Codming 博客式协议保活仍然卡在：

```text
fresh SYNACK / fresh runner input
  -> SYNACK 协商状态接入 native bridge
  -> native_channel_created status=0 err=0
  -> ZIME_CreateDataStream
  -> ZIME_SendData(DISPLAY_INIT)
  -> display activity
  -> 40 分钟 verified-run
```

本地验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
compileall OK
Ran 86 tests in 1.640s
OK
```

## 42. 2026-07-04 固化 KCP SYNACK 匹配和协商解释

继续推进 `fresh SYNACK` 缺口时，本轮把 IDA 中已经确认的 KCP 握手规则固化到
`rap-zime-kcp-sync-probe` 报告里，避免后续只看“有没有 UDP 回包”而忽略
SYNACK 匹配和协商字段。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - `run_kcp_sync_probe()` 报告新增 `localEndpoint`，记录本地 UDP socket
    实际绑定端点。
  - 报告新增 `idaHandshakeEvidence`，直接列出 IDA 证据：
    - client SYN 来自 `ikcp_send_link_sync`；
    - fresh SYN wire size 是 21 bytes，header `len = kcp->mtu`；
    - `get_thread_kcp` 对 SYN/SYNACK 类特殊命令的匹配规则是：
      incoming source port 必须匹配 `kcp->dest_port`，segment `sn` 必须匹配
      `kcp->syn_id`；
    - `ikcp_deal_svr_sync_ack` 从 SYNACK 中更新 `conv/use_quic/stream/FEC`
      等协商状态；
    - `deal_kcp_sync_ack_cmd` 只有在 SYNACK 后 `use_quic` 成立时才进入
      QUIC/ZIME channel 创建。
  - 新增 `kcp_synack_negotiation_summary()`，把收到的 SYNACK 转成 native
    bridge 可用的协商摘要：`newConvFromUna`、`packCheckNegotiated`、
    `fecNegotiated`、`useQuicNegotiated`、`streamNegotiated`、`headLen`。
- `tests/test_python_modules.py`
  - 扩展 `test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack`，校验新增
    IDA evidence 与 SYNACK negotiation summary。

生成的静态证据报告：

```text
reports/kcp-sync-ida-handshake-evidence-20260704.json
```

该报告是 `static_ida_handshake_evidence_only`，不触网、不证明保活、不证明
display path。它的用途是指导下一次 fresh SYNACK 捕获后的字段接入。

关键结论：

```text
client SYN:
  conv = 0x80000001
  wireSize = 21 bytes for fresh SYN
  declaredLen = kcp->mtu
  sn = kcp->syn_id
  una = kcp->conv

SYNACK match:
  function = get_thread_kcp
  appliesWhenCmd = 1,2,7,9
  rule = incoming source port must match kcp->dest_port and segment sn must match kcp->syn_id

SYNACK negotiation:
  kcp->conv = synack.una
  use_quic 取决于 synack.wnd 的 quic bit
  be_using_stream 取决于 synack.wnd 的 stream bit
  FEC/pack-check 取决于 synack.cmd 的 server-fec/server-pack-check bit
  headLen = 21 + FEC(2) + stream(1)
```

对当前卡点的影响：

- 旧 `synackReceived=false` 不能再简单解释为“21 字节 SYN 还不对”；
  client SYN 编码已按 IDA 修过两轮，现在更可能是会话上下文/目标/绑定条件不 fresh。
- 下一次 fresh trace/live 短测如果收到 SYNACK，必须先把上述协商字段接入 native
  bridge，再调用 `ZIME_CreateDataChannel`。
- 如果仍没有 SYNACK，应优先确认官方客户端当前会话的 `dest_ip/dest_port/syn_id`
  绑定条件，而不是继续重复旧 metadata target 空测。

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_decodes_kcp_sync_ack_segment_from_ida_layout \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_encodes_kcp_client_syn_from_ida_layout -v

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
KCP 相关 3 tests OK
compileall OK
Ran 86 tests in 1.658s
OK
```

## 43. 2026-07-04 固化 KCP auth preflight 缺口

继续按 IDA 证据推进 `fresh SYNACK` 卡点时，本轮发现直接发送 client SYN
仍少了一段前置状态机：`ikcp_set_auth_data_res()` 只有在 `deal_auth_res`
返回 200 后才调用 `ikcp_send_link_sync()`。也就是说，在 `kcp->be_auth`
成立的路径里，官方客户端是先完成 auth head / auth data，再进入 SYN/SYNACK。

IDA 关键证据来自 `reports/ida-libspice-zime-analysis-20260704-auth.json`：

- `ikcp_set_auth_data()`：
  - 首段 auth head 使用 `seg.conv = 0x80000006`；
  - 第二段 auth data 使用 `seg.conv = 0x80000008`；
  - 两段都写 `seg.sn = kcp->syn_id`、`seg.una = kcp->conv`；
  - payload 来自当前会话的 auth bytes，不能用旧 trace 或敏感状态随意拼。
- `deal_kcp_auth_cmd()`：
  - `cmd == 7` 处理 `IKCP_CONV_AUTH_HEAD_ACK`；
  - `cmd == 9` 处理 `IKCP_CONV_AUTH_ACK`。
- `ikcp_set_auth_data_res()`：
  - `deal_auth_res()` 返回 200 后才调用 `ikcp_send_link_sync()`。

本轮代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `KCP_AUTH_HEAD_CONV = 0x80000006`；
  - 新增 `KCP_AUTH_DATA_CONV = 0x80000008`；
  - 新增 `build_kcp_auth_segment()`，只编码 KCP auth envelope，不自动生成或发送
    auth payload；
  - `decode_kcp_segment()` / `_kcp_segment_summary()` / `classify_payload()`
    能识别 `kcp-auth-head` 和 `kcp-auth-data`；
  - `run_kcp_sync_probe()` 报告新增 `authPreflight`；
  - `kcp_sync_ida_evidence()` 新增 auth preflight 顺序说明。
- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_kcp_auth_preflight_codec`；
  - 扩展 `test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack`，校验
    auth preflight 报告字段。

生成的新证据报告：

```text
reports/kcp-auth-preflight-ida-evidence-20260704.json
```

该报告是 `kcp-auth-preflight-static-evidence`，不触网、不读取 `.tmp/state.json`、
不包含 auth payload，不证明保活、不证明 display path。

对当前卡点的影响：

- 旧 `synackReceived=false` 现在不能解释成单一的 SYN wire 问题；
  除了 fresh `dest_ip/dest_port/syn_id` 绑定，还必须恢复 auth head/data
  payload 来源，或者证明当前路径 `kcp->be_auth` 关闭。
- 下一次 live 前不应继续重复“只发 SYN 等 SYNACK”的旧 target 空测。
- 更准确的下一步是：
  1. 从 fresh official trace / IDA 静态 / 非敏感连接材料里恢复 auth head/data；
  2. 或证明当前 outband QUIC 路径不启用 auth；
  3. 再按 `AUTH_HEAD -> AUTH_HEAD_ACK -> AUTH_DATA -> AUTH_ACK -> SYN -> SYNACK`
     路线接入 probe；
  4. 收到 SYNACK 后再接 native bridge 的 `conv/use_quic/stream/FEC/headLen`。

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_preflight_codec \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack -v

python3 -m compileall cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
KCP auth/sync 相关 2 tests OK
compileall OK
Ran 87 tests in 1.637s
OK
```

## 44. 2026-07-04 analyze-rap-zime 增加 auth preflight 脱敏观察

第 43 节把 KCP auth preflight 从 IDA 静态证据固化进 codec。本轮继续推进：
让 `analyze-rap-zime` 能在官方 trace 中脱敏识别 auth head/data envelope。

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - `_sample_record()` 对 `kcp-auth-*` 样本隐藏 `hexPrefix`，避免报告中带出
    auth payload；
  - 新增 `_auth_preflight_record()` 和 `_record_kcp_auth_segment()`；
  - `analyze_trace()` 新增 `kcpAuthPreflight`：
    - `observed`
    - `counts`
    - `samples`
    - `payloadPolicy`
    - `nextStep`
  - `runnerInput` 新增 `kcpAuthPreflightObserved`。
- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_trace_analysis_reports_auth_preflight_redacted`，确认
    auth head/data 被识别且报告不包含 payload 明文或 payload hex。

用旧官方 trace 生成的新报告：

```text
reports/rap-zime-20260702-082921-auth-preflight-redacted-20260704.json
```

该报告来自：

```text
reports/zime-transport-20260702-082921.jsonl
```

脱敏观察结果：

```text
kcpAuthPreflight.observed = true
counts:
  auth-head      = 1
  auth-head:send = 1
  auth-data      = 1
  auth-data:send = 1
samples:
  payloadRedacted = true
  payload bytes not written
```

关键边界：

- 这证明旧官方 trace 中确实出现过 KCP auth head/data envelope，不再只是
  IDA 静态推断。
- 该报告仍不能用于 live replay；旧 trace 已 stale，auth payload 是会话材料，
  不能复用。
- 当前下一步仍是：
  1. 在 fresh trace 中脱敏确认 auth head/data；
  2. 恢复 fresh auth payload 的合法来源，或证明当前路径 auth disabled；
  3. 再把 auth preflight 接入 probe；
  4. 之后才期待 `SYNACK -> use_quic -> ZIME_CreateDataChannel`。

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_trace_analysis_reports_auth_preflight_redacted \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_trace_analysis_builds_runner_input -v

python3 -m compileall cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
auth trace 相关 2 tests OK
compileall OK
Ran 88 tests in 1.601s
OK
```

## 45. 2026-07-04 增加 KCP auth-ready live gate

本轮按 `$task-clarifier` 重新对齐目标后继续推进：当前离博客“协议保活已实现”
还差真实协议 runner 的 fresh auth/SYNACK、ZIME channel、DISPLAY_INIT/display
activity 和 40 分钟 verified-run。已完成的部分不要重复做：KCP SYN `len=mtu`、
默认 outband、SYNACK 协商解释、auth envelope codec、auth trace 脱敏识别都已落地。

本轮新增一个更硬的 live 前置检查，避免继续用旧 trace 或缺 auth 的输入空测：

- `cmcc_cloud_alive/rap_zime.py`
  - `runner_input_readiness()` 新增 `require_kcp_auth_ready`；
  - readiness 报告新增 `kcpAuth` 脱敏摘要：
    - `requiredForLiveSynack`
    - `preflightObservedInTrace`
    - `disabledProven`
    - `freshMaterialDeclared`
    - `materialSourceType`
    - `payloadStoredInReport=false`
    - `ready`
  - auth ready 只接受两类证据：
    1. fresh auth material 来源声明；
    2. 当前路径 auth disabled 证明。
  - 不输出、不保存、不复用 auth payload bytes。
- `cmcc_cloud_alive/main.py`
  - `check-rap-zime-runner-input` 新增：

```text
--require-kcp-auth-ready
```

用旧 20260702 auth trace 生成的新阻断报告：

```text
reports/rap-zime-20260702-082921-auth-gated-readiness-20260704.json
```

摘要：

```text
readyForLiveShortTest = false
missing:
  RAP UDP target is missing
  KCP auth is not ready: provide fresh auth material source or prove auth disabled
  trace lacks socket remote details required to drive the UDP runner
  runner input file is older than the configured max age
kcpAuth:
  requiredForLiveSynack = true
  preflightObservedInTrace = true
  disabledProven = false
  freshMaterialDeclared = false
  payloadStoredInReport = false
  ready = false
nextStep = Recover fresh KCP auth material or prove auth disabled before SYN/SYNACK live probing.
```

这条结论很重要：

- 旧 trace 已证明 auth preflight 结构存在，但不能作为 live replay 输入。
- 当前不是“继续重复发 21 字节 SYN 等 SYNACK”的阶段。
- 下一步必须二选一：
  1. 实现 fresh auth material builder 接口，来源只能是当前有效官方会话/可靠连接材料；
  2. 或用 IDA/trace 可靠证明当前 outband QUIC 路径 auth disabled。
- 只有 auth-ready 后才进入：

```text
AUTH_HEAD -> AUTH_HEAD_ACK -> AUTH_DATA -> AUTH_ACK -> SYN -> SYNACK
  -> use_quic/stream/FEC/headLen negotiation
  -> ZIME_CreateDataChannel
  -> ZIME_CreateDataStream
  -> DISPLAY_INIT/display path
```

本轮也纳入用户最新观察：官方连接实际能进入云桌面几秒，随后退回客户端本体，
`vdi_client` 进程只保留不到 5 秒。该现象不能当作 display path runner 成功；
它仍是需要定位的官方链路瞬退/first-frame 后退出问题。若后续必须 GUI 抓新轨迹，
必须先截图确认畅享版窗口和“连接”按钮，并使用窗口内坐标；不得操作 CrossDesk。

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_runner_input_readiness_reports_redacted_gaps \
  tests.test_python_modules.PythonModuleTests.test_check_rap_zime_runner_input_cli_writes_readiness_report -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
readiness/CLI 相关 2 tests OK
compileall OK
Ran 88 tests in 1.682s
OK
```

## 46. 2026-07-04 增加 fresh ZTEC auth buffer 分裂接口

第 45 节把 auth-ready gate 落地后，本轮继续把下一步拆细：先实现“已有 fresh
ZTEC auth buffer 后如何进入 KCP AUTH_HEAD/AUTH_DATA”的本地接口，而不是猜
`TnProxyData` 全结构或直接触发 GUI/live。

IDA 证据边界：

```text
ikcp_set_auth_data(kcp, pBuffer, head_len, data_len, random, use_ssl, detect_mtu=1, fec=1)
```

其中官方 CAG 普通认证路径 `deal_udt_using_cag()` 构造的 auth buffer：

```text
magic        = "ZTEC"
header len   = pBuffer[4:6]
buffer type  = 101
random_c     = pBuffer[10:14]
data len     = pBuffer[14:18]
AUTH_HEAD    = pBuffer[0 : header_len + 6]
AUTH_DATA    = pBuffer[header_len + 6 : header_len + 6 + data_len]
```

本轮代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `ZTEC_AUTH_HEADER_SIZE = 18`；
  - 新增 `parse_ztec_auth_buffer(auth_buffer)`：
    - 校验 `ZTEC` magic；
    - 解析 buffer type / random / head len / data len；
    - 返回 `authHead`、`authData` 和脱敏 `summary`；
    - `summary.payloadStoredInReport=false`；
  - 新增 `build_kcp_auth_preflight_from_buffer(auth_buffer, conv, syn_id, current)`：
    - 调用 `build_kcp_auth_segment(... auth_head=True)` 生成 AUTH_HEAD KCP envelope；
    - 调用 `build_kcp_auth_segment(... auth_head=False)` 生成 AUTH_DATA KCP envelope；
    - 返回 segment bytes 和脱敏 summary。
- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_builds_kcp_auth_preflight_from_ztec_buffer`；
  - 校验 AUTH_HEAD/AUTH_DATA 的 `conv/sn/una/len/payload`；
  - 校验 summary 不包含 auth payload 明文或 hex。

这一步的意义：

- 后续只要能从当前有效官方会话、内存 hook 或可靠 CAG builder 获得 fresh auth
  buffer，就能直接进入：

```text
parse_ztec_auth_buffer
  -> build_kcp_auth_preflight_from_buffer
  -> AUTH_HEAD/AUTH_DATA UDP send
  -> wait AUTH_HEAD_ACK/AUTH_ACK
  -> SYN/SYNACK
```

- 这还不是协议保活成功，也不是 display path 成功。
- 当前仍缺：
  1. fresh auth buffer 的可靠生成/获取来源；
  2. AUTH_HEAD_ACK/AUTH_ACK 的 live 处理；
  3. SYNACK 后 `conv/use_quic/stream/FEC/headLen` 接 native bridge；
  4. ZIME channel/stream/display path；
  5. 40 分钟 verified-run。

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_preflight_codec \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_kcp_auth_preflight_from_ztec_buffer \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_runner_input_readiness_reports_redacted_gaps -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
auth buffer split 相关 3 tests OK
compileall OK
Ran 89 tests in 1.645s
OK
```

## 47. 2026-07-04 增加 KCP auth + sync 前置状态机

第 46 节已经能从 fresh ZTEC auth buffer 分裂出 AUTH_HEAD/AUTH_DATA。本轮继续
补齐下一段本地状态机：在已有 fresh in-memory auth buffer 的前提下，按官方 IDA
顺序执行：

```text
AUTH_HEAD -> AUTH_HEAD_ACK(cmd=7)
AUTH_DATA -> AUTH_ACK(cmd=9)
SYN       -> SYNACK
```

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增常量：

```text
KCP_AUTH_HEAD_ACK_CMD = 7
KCP_AUTH_ACK_CMD      = 9
```

  - `decode_kcp_segment()` 新增：
    - `authHeadAckCmd`
    - `authAckCmd`
    - `authAckCmdAny`
  - `classify_payload()` 可识别：
    - `kcp-auth-head-ack`
    - `kcp-auth-ack`
  - `_kcp_segment_summary()` 输出 auth ACK 标记。
  - 新增 `_recv_kcp_until()`，用于读取 UDP responses 直到指定 KCP 条件满足。
  - 新增 `run_kcp_auth_sync_probe()`：
    - 输入必须是 fresh in-memory `auth_buffer`；
    - 调用 `build_kcp_auth_preflight_from_buffer()` 生成 AUTH_HEAD/AUTH_DATA；
    - 依次等待 AUTH_HEAD_ACK、AUTH_ACK；
    - ACK 都收到后才发送 client SYN；
    - 等待 SYNACK 并输出 `synackNegotiation`；
    - 报告只写 segment summary 和脱敏 auth summary，不写 auth payload。

测试变更：

- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn`；
  - 本地 UDP fake server 按顺序返回 `cmd=7`、`cmd=9`、SYNACK；
  - 验证发送顺序是 `auth_head -> auth_data -> client_syn`；
  - 验证报告不包含 auth payload 明文或 hex。

生成的新证据报告：

```text
reports/kcp-auth-sync-state-machine-20260704.json
```

该报告是静态/本地证据：

```text
type = kcp-auth-sync-state-machine-static-evidence
desktopKeepaliveProven = false
displayPathObserved = false
verifiedRunPassed = false
payloadStoredInReport = false
sequence = AUTH_HEAD, AUTH_HEAD_ACK, AUTH_DATA, AUTH_ACK, SYN, SYNACK
```

当前边界：

- 本轮没有触网、没有 GUI、没有操作 CrossDesk。
- 还没有 fresh auth buffer 的真实来源。
- 还没有真实 AUTH_HEAD_ACK/AUTH_ACK 或 SYNACK。
- 还没有把 SYNACK 协商字段接入 native bridge。
- 因此仍不能声称进入 display path 或完成协议保活。

下一步更具体了：

```text
fresh auth buffer source
  -> run_kcp_auth_sync_probe live
  -> AUTH_HEAD_ACK/AUTH_ACK/SYNACK
  -> conv/use_quic/stream/FEC/headLen
  -> native bridge ZIME_CreateDataChannel
```

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_sync_probe_sends_syn_and_records_synack \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_kcp_auth_preflight_from_ztec_buffer -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
auth+sync 相关 3 tests OK
compileall OK
Ran 90 tests in 1.664s
OK
```

## 48. 2026-07-04 增加 CAG type 101 fresh auth buffer builder

本轮按用户要求继续对齐博客目标：博客的协议保活成功点是 display 通道完成握手后
发送 `DISPLAY_INIT`，观察到 `SURFACE_CREATE` / `DRAW_COPY` / `MARK` 等显示活动。
当前家庭 Linux RAP/ZIME/SPICE 路线仍未到 display path，缺口仍在 KCP auth/SYNACK
进入 ZIME 前。本轮补的是 fresh auth material 来源侧的本地构造能力。

新增证据：

- 使用已有 IDA report `reports/ida-libspice-zime-auth-source-20260704.json`
  复核 `deal_udt_using_cag()`：
  - 普通 CAG auth path 使用 ZTEC buffer type `101`；
  - `data_len = 220`；
  - 无 opentelemetry 时 `buffer_len = 270`，`header_len_field = 44`，
    `AUTH_HEAD = 50` 字节；
  - opentelemetry 时 `buffer_len = 398`，`header_len_field = 172`，
    `AUTH_HEAD = 178` 字节；
  - auth_type `1/2` 会转到 `deal_udt_using_cag_uac()` type `102`，本轮不混入
    type 101 builder。
- 从目标 so 的 DWARF 中恢复 `TnProxyData_s` 字段偏移：

```text
dest_port   offset 0
flag_       offset 2
dest_ip     offset 4
client_uuid offset 20
username    offset 60
passwd      offset 124
flags       offset 188
extend      offset 192
sizeof      220
```

代码变更：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 CAG type 101 常量：
    - `ZTEC_CAG_TYPE101 = 101`
    - `ZTEC_CAG_TYPE101_DATA_LEN = 220`
    - `ZTEC_CAG_TYPE101_BUFFER_LEN = 270`
    - `ZTEC_CAG_TYPE101_OTEL_BUFFER_LEN = 398`
    - `ZTEC_CAG_TYPE101_PROXY_*_OFFSET`
  - 新增 `build_ztec_cag_type101_auth_buffer(...)`：
    - 写入 `ZTEC` magic、header len、type、random、data len、serial、link_type；
    - 按 DWARF offset 写入 `dest_port/dest_ip/client_uuid/username/passwd/flags`；
    - IPv4 写 4 字节地址，IPv6 写 16 字节地址并置 `flags=1`；
    - 返回 `authBuffer` 仅供 live 即时内存使用；
    - 返回脱敏 `summary`，不包含 username/password/vmid/dest_ip/dest_port 明文。
  - 新增 `build_ztec_cag_type101_auth_buffer_from_material(auth, connect_info, ...)`：
    - 从内存中的 CAG `auth` 和已解析 `connect_info` 提取
      `vmUserName/vmPassword/vmid/host/port`；
    - 调用 type101 builder 生成 `authBuffer`；
    - summary 只记录字段存在性，不记录敏感字段值。
- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_builds_fresh_cag_type101_auth_buffer_redacted`；
  - 新增 `test_rap_zime_builds_cag_type101_auth_buffer_from_material_redacted`；
  - 验证 builder 产物可被 `parse_ztec_auth_buffer()` 和
    `build_kcp_auth_preflight_from_buffer()` 消费；
  - 验证 buffer 内含敏感字段，但 summary 不含敏感明文。

生成的新证据报告：

```text
reports/kcp-auth-type101-builder-20260704.json
```

该报告只记录结构结论：

```text
desktopKeepaliveProven = false
displayPathObserved = false
verifiedRunPassed = false
payloadStoredInReport = false
builder = build_ztec_cag_type101_auth_buffer
```

当前边界：

- 本轮没有触网、没有 GUI、没有操作 CrossDesk。
- 这一步只把 “fresh CAG type 101 fields -> in-memory ZTEC auth buffer ->
  AUTH_HEAD/AUTH_DATA segments” 这段打通。
- 仍没有真实 live `AUTH_HEAD_ACK`、`AUTH_ACK` 或 `SYNACK`。
- 仍没有进入 `ZIME_CreateDataChannel` / `ZIME_CreateDataStream` /
  `DISPLAY_INIT`。
- 因此不能声称已经实现博客中的协议保活。

下一步：

```text
从当前有效 CAG/session 字段生成 type101 authBuffer
  -> run_kcp_auth_sync_probe live
  -> AUTH_HEAD_ACK / AUTH_ACK / SYNACK
  -> 提取 conv/use_quic/stream/FEC/headLen
  -> native bridge ZIME_CreateDataChannel
  -> ZIME_CreateDataStream
  -> DISPLAY_INIT / display path
```

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_fresh_cag_type101_auth_buffer_redacted \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_kcp_auth_preflight_from_ztec_buffer \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
type101 builder 相关 3 tests OK
compileall OK
Ran 91 tests in 1.735s
OK
```

追加 adapter 后复测：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_cag_type101_auth_buffer_from_material_redacted \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_fresh_cag_type101_auth_buffer_redacted -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
material adapter 相关 2 tests OK
compileall OK
Ran 92 tests in 1.639s
OK
```

Task-forest 同步：

- 已应用 `TFP-20260704-kcp-auth-type101-builder`，新增完成子任务 `TF-0040`。
- 已应用 `TFP-20260704-kcp-auth-live-synack-next`，新增未完成子任务 `TF-0041`，
  将 `TF-0029` 保持为 `in_progress`，避免派生视图把 fresh SYNACK 前置握手误判
  为 100% 完成。
- 已应用 `TFP-20260704-kcp-auth-material-adapter`，把 CAG auth/connect material
  adapter 进展补入 `TF-0040` 和 `TF-0041`。

## 49. 2026-07-04：CAG material -> KCP AUTH/SYNACK CLI 路径与报告脱敏闭合

本轮对齐后的真实目标仍是参考 Codming 博客实现无官方 GUI 的协议级保活：

```text
fresh CAG/session material
  -> type101 ZTEC authBuffer
  -> KCP AUTH_HEAD / AUTH_DATA
  -> AUTH_HEAD_ACK / AUTH_ACK
  -> client SYN / SYNACK
  -> RAP/ZIME/SPICE display path
  -> 40 分钟 verified-run
```

本轮完成的是上面前半段的本地代码和测试闭合，尚未执行真实 live 短测。

代码变更：

- `cmcc_cloud_alive/protocol_runner.py`
  - 新增 `_fetch_cag_auth_connect_str(...)`，保留 fresh CAG `auth` 与
    connectStr 原始解析路径；
  - `_fetch_cag_connect_str(...)` 继续返回兼容旧调用的 connectStr；
  - 新增 `fetch_cag_auth_connect_info(...)`，返回内存态
    `auth/connectInfo/publicConnectInfo`。注意：`auth/connectInfo` 可能含
    live session secrets，不得直接写报告。
- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `run_kcp_auth_sync_probe_from_cag_material(...)`：
    - 从 fresh CAG material 构造 type101 authBuffer；
    - 调用 `run_kcp_auth_sync_probe()` 执行
      `AUTH_HEAD -> AUTH_HEAD_ACK -> AUTH_DATA -> AUTH_ACK -> SYN -> SYNACK`；
    - 输出报告强制声明：
      `desktopKeepaliveProven=false`、`displayPathObserved=false`、
      `verifiedRunPassed=false`；
    - 不保存 authBuffer、username、password、vmid、accessToken、cpsid。
  - 新增 `_redact_cag_kcp_auth_sync_report(...)`：
    - 将 `stages[*].responses[*].remote` 替换为
      `<redacted:cag-udp-peer>`；
    - 保留 `bytesReceived/payloadKind/kcp` 摘要，便于审计 ACK/SYNACK 状态。
- `cmcc_cloud_alive/main.py`
  - 新增 CLI 子命令 `rap-zime-kcp-auth-from-cag`；
  - 入口会先通过 CAG 获取 fresh material，再调用脱敏的 KCP AUTH/SYNACK probe；
  - `--report-file` 写出的仍是 redacted report。
- `tests/test_python_modules.py`
  - 新增/补齐：
    - `test_protocol_runner_fetch_connect_info_uses_cag_material`
    - `test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report`
    - `test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path`
    - `test_rap_zime_builds_cag_type101_auth_buffer_from_material_redacted`

当前边界：

- 没有触发 GUI 点击，没有操作 CrossDesk。
- 没有读取或输出 `.tmp/state.json`、token、connectStr、accessToken、cpsid、密码、
  JWT 等敏感明文。
- 本轮没有执行真实 session-owning live 短测。
- 因此仍没有真实 `AUTH_HEAD_ACK/AUTH_ACK/SYNACK` 证据，更没有
  `ZIME_CreateDataChannel`、`DISPLAY_INIT` 或 display path 证据。
- 不能声称已经达到博客里的协议级保活。

本地验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_protocol_runner_fetch_connect_info_uses_cag_material \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_cag_type101_auth_buffer_from_material_redacted -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
focused CAG material/KCP auth tests: 4 tests OK
compileall OK
Ran 94 tests in 1.918s
OK
```

下一步：

```text
使用当前有效 CAG/session material 运行 rap-zime-kcp-auth-from-cag
  -> 目标只到 AUTH_HEAD_ACK / AUTH_ACK / SYNACK
  -> 成功后提取 conv/use_quic/stream/FEC/headLen
  -> 再接 native bridge 初始化 ZIME channel
```

注意：live 短测是 session-owning，可能顶掉官方客户端会话；真实 GUI 点击仍必须先截图并
使用窗口内坐标。

## 50. 2026-07-04：首次 CAG material live AUTH_HEAD 短测与 VM 目标字段修正

本轮执行了不碰 GUI 的 session-owning live 短测，范围严格限制在 KCP auth/SYNACK
前置阶段：

```text
rap-zime-kcp-auth-from-cag
  -> fresh CAG material
  -> type101 authBuffer
  -> AUTH_HEAD
  -> 等待 AUTH_HEAD_ACK
```

live 结果：

```text
reports/kcp-auth-from-cag-live-20260704.json
ok=false
authHeadAckReceived=false
authAckReceived=false
synackReceived=false
stage=auth_head
bytesSent=71
responses=0
desktopKeepaliveProven=false
displayPathObserved=false
verifiedRunPassed=false
```

第一次 live 后发现的实现问题：

- `connect_info.host/port` 表示 connectStr 网关 `h/p`，适合作为 UDP 发送目标；
- type101 `TnProxyData_s.dest_ip/dest_port` 更应优先来自 `vmip/vmport`；
- 原 adapter 把网关 `h/p` 同时写入 authBuffer 的 `dest_ip/dest_port`，这不是最可靠的
  映射。

修正：

- `cmcc_cloud_alive/protocol_runner.py`
  - `connect_info_from_connect_str()` 现在解析：
    - `vmid`
    - `vmHost` / `vmPort`
    - `vmHostV6` / `vmPortV6`
  - 对 `vmip/vmipv6` 做 URL decode，并从 `;` / `,` 多值列表里选首个非空值；
  - `public_connect_info()` 只暴露 `vmHostPresent/vmPortPresent` 等存在性，不暴露
    VM IP 值。
- `cmcc_cloud_alive/rap_zime.py`
  - `build_ztec_cag_type101_auth_buffer_from_material()` 现在写 authBuffer 时优先使用：
    `vmHost/vmPort` -> `vmHostV6/vmPortV6` -> fallback `host/port`；
  - summary 新增 `destFromVmArgs`，只表示是否使用 VM 目标字段，不记录具体值。
- `tests/test_python_modules.py`
  - 新增 `test_protocol_runner_connect_info_tracks_vm_dest_without_public_value`；
  - 新增 `test_rap_zime_cag_type101_material_prefers_vm_dest_over_gateway`；
  - 覆盖 URL-encoded `vmip` 多值形态和报告脱敏。

修正后再次 live：

```text
reports/kcp-auth-from-cag-live-vm-dest-normalized-20260704.json
ok=false
authHeadAckReceived=false
authAckReceived=false
synackReceived=false
stage=auth_head
bytesSent=71
responses=0
destFromVmArgs=true
desktopKeepaliveProven=false
displayPathObserved=false
verifiedRunPassed=false
```

结论：

- CAG material 获取成功；
- type101 authBuffer 构造成功；
- VM 目标字段已经进入 authBuffer；
- AUTH_HEAD 成功发出；
- 仍未收到 `AUTH_HEAD_ACK`。

当前缺口已经收敛到：

```text
UDP target / source-port / session 绑定
或 auth_type/type102/UAC 分支
或 link_type / opentelemetry 形态
或官方客户端还有额外 CAG/UDT 初始化动作
```

尚未进入：

```text
AUTH_DATA
client SYN/SYNACK
ZIME_CreateDataChannel
ZIME_CreateDataStream
DISPLAY_INIT
display path
40 分钟 verified-run
```

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_protocol_runner_connect_info_tracks_vm_dest_without_public_value \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_cag_type101_material_prefers_vm_dest_over_gateway \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
focused VM dest/CAG KCP tests: 4 tests OK
compileall OK
Ran 96 tests in 1.737s
OK
```

下一步优先级：

1. 用 IDA report/MCP 定位 `deal_udt_using_cag()` 中 type101 buffer 之后是否还有
   `ikcp_set_dest()`、`ikcp_set_auth_data()` 或 socket bind/source-port 约束；
2. 复核 `auth_type` 是否可能走 type102/UAC/token 分支；
3. 如果需要重新抓官方轨迹，必须截图确认 GUI，不操作 CrossDesk，且只在 CMCC 客户端
   窗口内定位。

## 51. 2026-07-04：按 IDA `split_spice_init_udp_info()` 修正 UDP 端口选择

继续排查第 50 节 `AUTH_HEAD` 无 ACK。IDA 报告中已有官方初始化逻辑：

```c
split_spice_init_udp_info(proxy_type, p, tmp_port, proxy_port, proxy_sport)

if (g_is_ice) {
  *tmp_port = p;
} else {
  if (proxy_port && *proxy_port)
    *tmp_port = proxy_port;
  if (proxy_sport && *proxy_sport)
    *tmp_port = proxy_sport;
}

spice_init_udp_thread(h, tmp_port[0], be_ssl)
```

并且：

```c
be_ssl = proxy_sport && *proxy_sport
```

因此 RAP 模式下 UDP target 端口不应总是 connectStr 的 `p`，而应优先：

```text
proxy-sport -> proxy-port -> p
```

本轮修正：

- `cmcc_cloud_alive/protocol_runner.py`
  - `connect_info_from_connect_str()` 新增官方端口选择逻辑；
  - `info["port"]` 现在表示实际 UDP target port；
  - `info["gatewayPort"]` 保留原始 `p`；
  - `info["udpPortSource"]` 记录 `p/proxy-port/proxy-sport`；
  - `info["udpSsl"]` 在 RAP 且 `proxy-sport` 存在时为 true。
- `cmcc_cloud_alive/rap_zime.py`
  - `run_kcp_auth_sync_probe_from_cag_material()` 默认把 `connect_info.udpSsl`
    合并到 KCP SYN capability；
  - 报告新增 `gatewayPortPresent/udpPortSource/udpSsl`，不记录敏感字段值。
- `cmcc_cloud_alive/main.py`
  - CLI `rap-zime-kcp-auth-from-cag` 的 `cagMaterial.connectInfo` 同步记录
    `udpPortSource/udpSsl`。
- `tests/test_python_modules.py`
  - `test_protocol_runner_connect_info_tracks_vm_dest_without_public_value` 覆盖
    `proxy-sport` 优先级；
  - `test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report` 覆盖
    `connectInfo.udpSsl=true` 时 client SYN 带 `ssl` capability；
  - 继续确认 report 不泄漏 CAG target、VM IP、账号密码或 token。

修正后 live：

```text
reports/kcp-auth-from-cag-live-proxy-sport-20260704.json
ok=false
stage=auth_head
bytesSent=71
responses=0
authHeadAckReceived=false
udpPortSource=proxy-sport
udpSsl=true
destFromVmArgs=true
desktopKeepaliveProven=false
displayPathObserved=false
verifiedRunPassed=false
```

结论：

- “UDP target 端口误用 `p`” 已按 IDA 逻辑修正；
- live 仍无 `AUTH_HEAD_ACK`；
- 当前 no-ACK 主因更可能在：
  - `auth_type` 不是普通 type101，而是 type102/UAC/token 分支；
  - type101 的 `link_type` 或 OTEL header 形态仍不匹配；
  - 官方还有额外 CAG/UDT 初始化或 session/source-port 绑定动作；
  - 需要 fresh 官方轨迹确认真实发出的 AUTH_HEAD/header 参数。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_protocol_runner_fetch_connect_info_uses_cag_material \
  tests.test_python_modules.PythonModuleTests.test_protocol_runner_connect_info_tracks_vm_dest_without_public_value \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_cag_type101_material_prefers_vm_dest_over_gateway \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
focused UDP target/proxy-sport tests: 5 tests OK
compileall OK
Ran 96 tests in 1.801s
OK
```

## 52. 2026-07-04：新增 ZTEC-prime AUTH 短测开关并确认 live 被登录态过期挡住

继续排查第 51 节 `AUTH_HEAD` 无 ACK。本轮用现有 IDA 报告复核了官方 UDP
生命周期，关键证据是：

```text
spice_init_udp_info()
  -> spice_init_udp_thread(h, selected_udp_port, be_ssl)
  -> udp_set_dest_addr_info(h, port, be_ssl)
  -> listen_udp_data_thread()
  -> listen_udp_data()
  -> ice_deal_sock()

init_local_rw_sock_pair_udp()
  -> create_udt_session(thread, dest_ip, dest_port, fd, ...)
  -> ikcp_set_dest(kcp, dest_ip, dest_port)
  -> kcp->syn_id = ZXRand()
  -> kcp->output = udt_output
  -> deal_udt_using_cag(kcp, kcp->be_ssl)
```

`get_thread_kcp()` 对 `cmd == 1/2/7/9` 的入站包使用：

```text
source_port == kcp->dest_port
syn_id == kcp->syn_id
```

来匹配 KCP 上下文。这个证据说明当前 Python 短测的 no-ACK 风险不只是
auth buffer 字段问题，也可能是缺少官方 UDP listen/session 生命周期或前置
ZTEC session 绑定。

本轮实现：

- `cmcc_cloud_alive/rap_zime.py`
  - `run_kcp_auth_sync_probe()` 新增 `ztec_prime/ztec_host/ztec_port/ztec_timeout`；
  - 开启时，在同一个 UDP socket 上先发一帧 ZTEC keepalive 并等待 ACK；
  - 然后继续原顺序：`AUTH_HEAD -> AUTH_HEAD_ACK -> AUTH_DATA -> AUTH_ACK -> SYN -> SYNACK`；
  - 默认行为不变，不传 `--ztec-prime` 时不会发送 ZTEC prime。
- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 新增：
    - `--ztec-prime`
    - `--ztec-host`
    - `--ztec-port`
    - `--ztec-timeout`
- CAG wrapper 的报告脱敏：
  - `target` 仍为 `<redacted:cag-udp-target>`；
  - `ztecPrime.target` 也替换为 `<redacted:cag-udp-target>`；
  - ZTEC request 只保留 `hostPresent/portPresent/sequencePresent`；
  - 不保存 CAG UDP target、VM IP、账号、密码、token、connectStr、authBuffer。

本地 fake UDP server 验证的顺序：

```text
ZTEC keepalive
AUTH_HEAD
AUTH_DATA
client SYN
```

并确认 CAG 报告脱敏闭合。

尝试执行 live：

```bash
python3 bin/cmcc_cloud_alive.py --state .tmp/state.json \
  rap-zime-kcp-auth-from-cag 2663816 \
  --timeout 1.0 \
  --receive-limit 4 \
  --ztec-prime \
  --report-file reports/kcp-auth-from-cag-live-ztec-prime-20260704.json
```

结果：没有进入 UDP 阶段，CAG 拉材料前置失败：

```text
listClouds failed: code=4015 msg=用户未登录，请先登录
```

因此本轮没有新的 live `AUTH_HEAD_ACK` 结论；`reports/kcp-auth-from-cag-live-ztec-prime-20260704.json`
未生成，只有 `.out` 记录了登录态过期错误。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_can_ztec_prime_first \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v

python3 -m compileall -q cmcc_cloud_alive tests
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
```

结果：

```text
focused ZTEC-prime/CAG KCP tests: 3 tests OK
compileall OK
Ran 97 tests in 1.794s
OK
```

当前下一步：

1. 重登后重新执行 `--ztec-prime` live 短测，只观察
   `ZTEC ACK / AUTH_HEAD_ACK / AUTH_ACK / SYNACK`，不创建 ZIME channel；
2. 如果仍无 ACK，优先用官方 fresh trace 或 IDA 继续恢复
   `ice_create_fd/send_udt_data/udp_get_local_port` 是否有本地端口绑定或额外
   session 初始化；
3. 继续复核当前畅享版 material 是否应该走 `auth_type=1/2` 的 type102/UAC/token
   分支；没有证据前不要把 type101 no-ACK 归因定死。

## 53. 2026-07-04：重登后 ZTEC-prime live 仍无 UDP 响应，失败阶段早于 AUTH_HEAD

承接第 52 节，先刷新登录态：

```text
reports/token-check-relogin-20260704.out
login ok
response.code=2000
```

随后做了两次 session-owning live 短测，均只观察到必要握手阶段，不创建 ZIME
channel、不发送 `DISPLAY_INIT`，也不运行 verified-run：

```text
reports/kcp-auth-from-cag-live-ztec-prime-after-relogin-20260704.json
reports/kcp-auth-from-cag-live-ztec-prime-vm-target-20260704.json
```

两份报告的共同结论：

```text
ok=false
target=<redacted:cag-udp-target>
cagMaterial.freshFetched=true
cagMaterial.connectInfo.udpPortSource=proxy-sport
cagMaterial.connectInfo.udpSsl=true
ztecPrime.enabled=true
ztecPrime.bytesSent=26
ztecPrime.ackReceived=false
ztecPrime.error=timeout waiting for ZTEC ack
desktopKeepaliveProven=false
displayPathObserved=false
verifiedRunPassed=false
```

重要边界：

- 这两次失败阶段早于 `AUTH_HEAD`，因为当前实现会先等待 ZTEC ACK；
- 因此不能把这两份报告描述成新的 `AUTH_HEAD` no-ACK 证据；
- 但它们排除了“重登后同 socket ZTEC-prime 立即得到 UDP 响应”这个假设；
- ZTEC-prime 的 CAG target 与 VM target 编码变体都没有收到任何 UDP response；
- 报告仍保持脱敏，不保存 CAG target、VM IP、账号、密码、token、connectStr 或
  authBuffer 明文。

当前缺口更新：

1. type101 普通 AUTH 路线仍停在 `AUTH_HEAD` 无 ACK；
2. ZTEC-prime 路线在前置 ZTEC ACK 阶段就超时；
3. 下一步不应继续盲测 link_type 或 target 组合，应优先恢复 type102/UAC/token
   auth buffer，或抓一条 fresh 官方 UDP 轨迹确认官方是否先做本地端口/session
   初始化；
4. 若必须 GUI 抓官方轨迹，仍遵守：先截图确认，使用窗口内定位，不操作 CrossDesk。

## 54. 2026-07-04：按 DWARF 恢复 type102/UAC builder，accessToken live 仍 AUTH_HEAD 无 ACK

本轮继续补齐第 53 节后的 type102/UAC/token 缺口。没有猜结构偏移，而是从目标
so 的 DWARF `TnProxyUacData_s` 恢复布局：

```text
TnProxyUacData_s byte_size=126
dest_port   @ 0
flag_       @ 2
dest_ip     @ 4
client_uuid @ 20
username    @ 60  (32 bytes)
flags       @ 92
reserve     @ 94
extend      @ 96
pwd_len     @ 124
passwd      @ 126 (dynamic padded token)
```

与 `deal_udt_using_cag_uac()` 伪代码一致：

```text
buffer type = 102
pd_len = align16(strlen(token) + 1)
buffer_len = pd_len + 176
otel buffer_len = pd_len + 304
header len field = buffer_len - pd_len - 132
data_len = pd_len + 126
auth head len = header len field + 6
auth data starts at pBuffer + 50, or +178 when OTEL
auth_type == "2" uses access_token, otherwise uses uactoken
```

本轮实现：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `ZTEC_CAG_TYPE102*` 布局常量；
  - 新增 `build_ztec_cag_type102_auth_buffer(...)`；
  - 新增 `build_ztec_cag_type102_auth_buffer_from_material(...)`；
  - `run_kcp_auth_sync_probe_from_cag_material()` 新增显式
    `auth_buffer_type/auth_type` 分支，默认仍保持 type101，不影响旧路径；
  - type102 summary 只记录结构、长度、token source、字段存在性，不记录 token、
    accessToken、uactoken、VM IP、账号或 authBuffer 明文。
- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 新增：
    - `--auth-buffer-type type101|type102`
    - `--cag-auth-type 1|2`
  - 不新增命令行 token 参数，避免敏感值进入 shell history。
- `tests/test_python_modules.py`
  - 新增 type102 DWARF layout/offset 测试；
  - 新增 type102 accessToken material adapter 脱敏测试；
  - 更新 CLI 测试，确认参数传到 probe。

验证：

```text
focused type102/type101/CLI tests: 5 tests OK
python3 -m compileall -q cmcc_cloud_alive tests: OK
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 99 tests in 1.777s
OK
```

随后执行一次 session-owning live 短测，不操作 GUI、不操作 CrossDesk，只到
AUTH/SYNACK 阶段：

```text
reports/kcp-auth-from-cag-live-type102-access-token-20260704.json
```

脱敏结论：

```text
proof=fresh_cag_type102_kcp_auth_sync_probe_only
authMaterialSource.sourceType=fresh-cag-material-type102-builder
authMaterialSource.bufferType=102
authMaterialSource.authType=2
authMaterialSource.tokenSource=access_token
authMaterialSource.authHeadLen=50
authMaterialSource.authDataLen=174
authMaterialSource.payloadStoredInReport=false
connectInfo.udpPortSource=proxy-sport
connectInfo.udpSsl=true
stage=auth_head
bytesSent=71
responses=0
auth_head.ackReceived=false
synackReceived=false
desktopKeepaliveProven=false
displayPathObserved=false
verifiedRunPassed=false
```

当前结论：

- type102/accessToken builder 已闭合到 KCP AUTH_HEAD live 发送；
- type102/accessToken 分支仍没有 `AUTH_HEAD_ACK`；
- 这说明“type101 错分支”不是唯一阻塞原因，仍可能存在官方 UDP
  listen/session 生命周期、本地端口/source-port 绑定、CAG 前置状态或 target/link
  选择差异；
- 下一步优先级应转向 fresh official UDP trace 或继续用 IDA/MCP 恢复
  `ice_create_fd/send_udt_data/udp_get_local_port/create_udt_session` 的本地 socket
  初始化要求，而不是继续盲测 auth buffer 字段。

## 55. 2026-07-04：恢复官方 UDP session/source-port 证据，并给 probe 增加本地 bind 实验开关

承接第 54 节，本轮按 task-clarifier 对齐后的路线推进：不继续盲测
type101/type102 auth buffer 字段，优先用现有 IDA 报告恢复官方 UDP
session/source-port 初始化语义。本轮没有操作 GUI，没有操作 CrossDesk，也没有执行新的
session-owning live 短测。

已生成脱敏证据报告：

```text
reports/ida-udp-session-source-port-20260704.json
```

核心 IDA 证据：

```text
spice_init_udp_thread()
  -> udp_set_dest_addr_info()
  -> g_thread_new("listen_udp_data_thread", listen_udp_data_thread, 0)
  -> wait until udp_get_tcp_link_info(nullptr) is non-null

listen_udp_data()
  -> g_sync_id = ZXRand()
  -> g_sock_listen_fd = ice_create_fd(0, 0)
  -> g_sock_udt_fd = ice_create_fd(0, 1)
  -> setsockopt(g_sock_udt_fd, SO_RCVBUF/SO_SNDBUF/IP_TOS/DF)
  -> udp_get_local_port(g_sock_listen_fd)
  -> ice_deal_sock()

init_local_rw_sock_pair_udp()
  -> get_proxy_kcp_dst_ip/port()
  -> create_fd_session(... TN_UDP_CLD_SOCK)
  -> create_udt_session(dest_ip, dest_port, udp_fd, ...)
  -> attach kcp to thread kcp_list
  -> kcp->user_data = udp_sock
  -> kcp->be_using_cag / be_algo_mode / be_outband / be_quic
  -> deal_udt_using_cag(kcp, kcp->be_ssl)

create_udt_session()
  -> ikcp_create()
  -> ikcp_set_dest(ip, port)
  -> kcp->output = udt_output
  -> kcp->syn_id = ZXRand()
  -> kcp->be_detech_mtu = 1

get_thread_kcp()
  -> for cmd 1/2/7/9: match port == kcp->dest_port and syn_id == kcp->syn_id
```

本轮判断：

- 当前 Python `run_kcp_auth_sync_probe()` 仍是单 UDP socket 路线，默认不显式
  `bind()`，不模拟 `listen_udp_data_thread`、`udp_get_tcp_link_info()`、`create_fd_session`
  或 thread `kcp_list`；
- 官方路径在 `deal_udt_using_cag()` 之前已经有 listen/thread/fd-session/KCP list
  生命周期，因此 `AUTH_HEAD` no-ACK 可能仍与本地 UDP/source-port/session 绑定有关；
- 这还不能证明 display path，也不能证明保活成功；仍未到
  `AUTH_HEAD_ACK/AUTH_ACK/SYNACK/DISPLAY_INIT/verified-run`。

本轮实现：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `kcp_udp_session_lifecycle_ida_evidence()`；
  - `run_kcp_auth_sync_probe()` 报告新增：
    - `idaUdpSessionEvidence`
    - `localSocketLifecycle`
  - 新增受控实验参数：
    - `local_bind_host`
    - `local_bind_port`
  - 默认仍不显式 bind；只有传入本地 bind 参数时才在发送前绑定本地 UDP
    source endpoint。
- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 新增：
    - `--local-bind-host`
    - `--local-bind-port`
- `tests/test_python_modules.py`
  - 新增本地 UDP bind/source-port 单测；
  - 更新 AUTH/SYNACK probe 报告字段断言；
  - 更新 CLI 参数透传测试。

验证：

```text
focused AUTH/SYNACK/source-port/CLI tests: 6 tests OK
python3 -m compileall -q cmcc_cloud_alive tests: OK
python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 100 tests in 2.095s
OK
```

当前下一步：

1. 只有在 fresh CAG/session material 有效时，才做一次 session-owning live 短测，
   优先使用 `--local-bind-host 0.0.0.0 --local-bind-port 0` 或可复核的本地端口策略，
   观察 `localSocketLifecycle`、`AUTH_HEAD_ACK/AUTH_ACK/SYNACK`；
2. 如果仍无 UDP response，不继续猜 auth 字段，转向 fresh official UDP trace，重点抓：
   `ice_create_fd` 真实 bind/listen 端口、`udp_get_local_port` 输出、sendto source port、
   AUTH_HEAD 前是否存在额外 UDP/TCP 初始化包；
3. 未拿到 SYNACK 前，不创建 ZIME channel，不发送 `DISPLAY_INIT`，不运行 40 分钟
   verified-run。

## 56. 2026-07-04：type102/accessToken + 显式 local bind live 仍 AUTH_HEAD 无 ACK

承接第 55 节，执行一次 session-owning live 短测；不操作 GUI，不操作 CrossDesk，
不创建 ZIME channel，不发送 `DISPLAY_INIT`，不运行 verified-run。

命令路线：

```text
rap-zime-kcp-auth-from-cag
  --auth-buffer-type type102
  --cag-auth-type 2
  --local-bind-host 0.0.0.0
  --local-bind-port 0
```

脱敏报告：

```text
reports/kcp-auth-from-cag-live-type102-access-token-localbind-20260704.json
```

结果摘要：

```text
proof=fresh_cag_type102_kcp_auth_sync_probe_only
ok=false
stage=auth_head
bytesSent=71
responses=0
auth_head.ackReceived=false
synackReceived=false
displayPathObserved=false
verifiedRunPassed=false

authMaterialSource.bufferType=102
authMaterialSource.authType=2
authMaterialSource.tokenSource=access_token
authMaterialSource.destFromVmArgs=true
authMaterialSource.payloadStoredInReport=false

connectInfo.udpPortSource=proxy-sport
connectInfo.udpSsl=true
connectInfo.payloadStoredInReport=false

localSocketLifecycle.explicitBindBeforeSend=true
localSocketLifecycle.requestedLocalBind=0.0.0.0:0
localSocketLifecycle.localEndpointAfterBind=0.0.0.0:<ephemeral>
localSocketLifecycle.officialListenThreadStarted=false
localSocketLifecycle.officialTcpLinkInfoWait=false
localSocketLifecycle.officialCreateFdSessionModeled=false
```

本轮结论：

- 显式本地 UDP bind/source-port 实验开关可用，报告能记录本地端口；
- 仅显式 bind 到 `0.0.0.0:0` 没有改变 live 结果，仍无任何 UDP response；
- 因此“Python 只是没有显式 bind 一个临时本地端口”不是充分解释；
- 更可能仍缺官方 `listen_udp_data_thread`、`udp_get_tcp_link_info()`、
  `create_fd_session(TN_UDP_CLD_SOCK)`、thread `kcp_list` 或 CAG 前置会话绑定；
- 不应继续重复同一 type102/accessToken + local bind 0 短测，下一步应抓 fresh
  official UDP trace 或继续用 IDA/MCP 补 `ice_create_fd/udp_get_local_port/send_udt_data`
  的具体语义。

仍未完成：

- 未收到 `AUTH_HEAD_ACK`；
- 未发送 `AUTH_DATA`；
- 未发送 client SYN；
- 未收到 SYNACK；
- 未到 ZIME channel、`DISPLAY_INIT`、display activity 或 40 分钟 verified-run。

## 57. 2026-07-04：补齐 ice_create_fd/udp_get_local_port/send_udt_data 证据，澄清 TCP listen port 与 UDP source port

承接第 56 节，本轮继续静态恢复缺失函数语义；未操作 GUI，未操作 CrossDesk，未执行
新的 live 短测。

新增 IDA 提取报告：

```text
reports/ida-libspice-zime-udp-fd-source-20260704.json
```

新增伪代码片段：

```text
reports/ida-snippet-ice_create_fd-20260704.txt
reports/ida-snippet-udp_get_local_port-20260704.txt
reports/ida-snippet-send_udt_data-20260704.txt
reports/ida-snippet-create_fd_session-20260704.txt
```

本轮确认的关键事实：

```text
ice_create_fd(port, be_udp)
  be_udp=0:
    socket(AF_INET, SOCK_STREAM, 0)
    setsockopt(SO_REUSEADDR)
    bind(127.0.0.1:port)
    fcntl(nonblocking)
    listen(fd, 5)

  be_udp=1:
    socket(AF_INET/AF_INET6, SOCK_DGRAM, 0)
    fcntl(nonblocking)
    return UDP fd

udp_get_local_port(fd)
  getsockname(fd)
  g_tcp_listen_port = ntohs(sockaddr_in.sin_port)

send_udt_data(buf, len, addr, addrlen, user, out_errno)
  sendto((int)user, buf, len, 0, addr, addrlen)

create_fd_session(pThread, fd, sock_flag, sock_type)
  stores fd in IceSocket
  stores sock_type
  links socket into thread socket ring
  initializes queue/mutex state
```

修正后的判断：

- `g_tcp_listen_port` 来自本地 `127.0.0.1` TCP listen socket，不是 outbound UDP
  source port；
- 官方 UDP AUTH 输出最终经 `send_udt_data()` 调 `sendto((int)user, ...)`，其中
  `user` 是挂到 KCP/IceSocket 上的 UDP fd；
- 第 56 节的 `--local-bind-host 0.0.0.0 --local-bind-port 0` live no-ACK
  只排除了“没有显式绑定临时 UDP source port”这个弱假设；
- 当前更应排查/复现的是官方本地 TCP listen readiness、
  `udp_get_tcp_link_info()` gate、`create_fd_session(TN_UDP_CLD_SOCK)` 和 thread
  `kcp_list` 生命周期，而不是继续换 UDP ephemeral bind。

本轮代码更新：

- `scripts/ida_extract_spice_zime.py`
  - 目标函数列表新增：
    - `create_fd_session`
    - `ice_create_fd`
    - `udp_get_local_port`
    - `send_udt_data`
- `cmcc_cloud_alive/rap_zime.py`
  - `kcp_udp_session_lifecycle_ida_evidence()` 补充上述函数的脱敏证据；
  - 明确区分 TCP listen readiness port 与 UDP source endpoint；
  - evidence report source 列入新的 IDA JSON 和 snippet 文件。
- `tests/test_python_modules.py`
  - 增加针对 TCP listen port / UDP sendto source endpoint 语义的断言。

验证：

```text
IDADIR=/home/demo/tools/idapro-9.3 \
  /home/demo/.local/share/pipx/venvs/ida-pro-mcp/bin/python \
  scripts/ida_extract_spice_zime.py \
  --binary .tmp/ida-inputs/libspice-client-glib-zte-2.0.so.8.5.0 \
  --output reports/ida-libspice-zime-udp-fd-source-20260704.json \
  --max-decompile-chars 20000

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn -v
OK
```

当前下一步：

1. 不再把 `g_tcp_listen_port` 当 UDP source port；
2. 优先设计一个本地 TCP listen readiness / IceSocket lifecycle parity 预检，或抓 fresh
   official trace 观察 AUTH_HEAD 前后的本地 TCP/UDP 初始化时序；
3. 若继续 live，只应验证“官方 readiness gate/session attach 是否影响 AUTH_HEAD_ACK”，
   而不是重复 type102 + local UDP bind 0。

## 58. 2026-07-04：AUTH probe 报告新增官方生命周期 parity gap，固化“不要重复 local bind 0”结论

承接第 57 节，本轮未操作 GUI，未操作 CrossDesk，未执行新的 live 短测；只把已经
确认的 IDA 事实和 live no-ACK 结论固化到 Python probe 的脱敏报告结构里。

本轮实现：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `_kcp_auth_probe_parity_assessment()`；
  - `run_kcp_auth_sync_probe()` 报告新增：
    - `officialParityAssessment.stageBlocked`
    - `officialParityAssessment.readinessPortInterpretation`
    - `officialParityAssessment.modeledByPython`
    - `officialParityAssessment.notModeledYet`
    - `officialParityAssessment.ruledOutByThisRun`
    - `officialParityAssessment.sourcePortHypothesisStatus`
    - `officialParityAssessment.doNotRepeatWithoutNewEvidence`
  - `localSocketLifecycle` 新增明确的官方生命周期 parity 标记：
    - `officialReadLoopStartedBeforeAuthHead=false`
    - `officialUdpFdAttachedToIceSocket=false`
    - `officialKcpAttachedToThreadList=false`
    - `officialTcpListenReadinessModeled=false`
  - `kcp_udp_session_lifecycle_ida_evidence().pythonRunnerDelta.nextImplementationChoices`
    已从“新增 local-bind 实验”修正为“没有新 official trace 证据前不要重复
    local-bind/source-port 探针”。

新增/更新测试：

- `test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn`
  - 验证 fake SYNACK 成功时 `officialParityAssessment.stageBlocked=null`；
  - 验证 report 保留 “`g_tcp_listen_port` 是本地 TCP listen readiness port，
    不是 outbound UDP source port” 的判断；
  - 验证官方 `create_fd_session(TN_UDP_CLD_SOCK)` 等缺口进入结构化
    `notModeledYet`。
- `test_rap_zime_kcp_auth_sync_probe_can_bind_local_udp_source`
  - 验证显式 `local_bind_host=127.0.0.1, local_bind_port=0` 后若仍停在
    `AUTH_HEAD`，报告会标记：
    - `sourcePortHypothesisStatus=explicit_ephemeral_bind_not_sufficient`
    - `ruledOutByThisRun` 包含 `lack_of_explicit_ephemeral_udp_bind`
    - `doNotRepeatWithoutNewEvidence` 包含
      `type102_accessToken_with_local_bind_0`

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_can_bind_local_udp_source -v

Ran 2 tests in 0.007s
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 100 tests in 1.725s
OK
```

当前判断：

- 距离博客里的“协议保活已实现”仍差关键 display path：
  `AUTH_HEAD_ACK -> AUTH_ACK -> SYNACK -> ZIME channel -> DISPLAY_INIT ->
  display activity -> 40 分钟 verified-run`；
- 本轮没有推进到 `AUTH_HEAD_ACK`，因此不能声明协议保活成功；
- 已把“显式临时 UDP bind/source-port 不足以解释 no-ACK”写入机器可读报告，
  后续不应再重复 type102/accessToken + local bind 0；
- 下一步优先恢复或抓取官方 `deal_udt_using_cag()` 前后是否存在 CAG/session
  绑定包、真实 AUTH_HEAD 前置包、真实 source endpoint 与 AUTH response 行为。

## 59. 2026-07-04：按 IDA 修正 CAG auth buffer 目标选择，默认 link_type=11 不再写 VM dest

承接第 58 节，继续用已有 IDA 伪代码复核 `deal_udt_using_cag()` 和
`deal_udt_using_cag_uac()`。本轮未操作 GUI，未操作 CrossDesk，未执行新的 live
短测。

新确认的关键 IDA 事实：

```text
deal_udt_using_cag()/deal_udt_using_cag_uac()
  default:
    link_type = 11
    dest_ip = s->host
    dest_port = s->proxy_sport when connect_type == 1 else s->proxy_port

  proxy_type == "ice":
    link_type = 139
    dest_ip = s->host
    dest_port = s->port

  udp_sock->data_buf[224] == 2:
    link_type = 140
    dest_ip = s->vm_ip unless proxy_type == "ice", then s->host
    dest_port = s->vm_proxy_port

  udp_sock->data_buf[224] == 1:
    enable_opentelemetry = s->has_connected == 0
```

本轮修正：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `_cag_material_destination()`；
  - type101/type102 CAG material builder 共用官方目标选择规则；
  - 默认 `link_type=11` 写网关 `host:port`；
  - 显式 `link_type=140` 时才写 VM 目标；
  - 显式 `link_type=139` 时按 ice `host/p` 选择；
  - summary 新增：
    - `destinationSource=proxy_gateway|vm_proxy|ice_host_port`
    - `linkTypeSelectionEvidence`
  - summary 仍只记录目标来源，不记录 IP/port 明文。

重要修正判断：

- 之前“VM dest 修正”把默认 type101/type102 的 auth buffer 目标改成 VM
  dest，但 IDA 证据表明默认 `link_type=11` 应写网关目标；
- VM dest 只应与 `link_type=140`/sock link flag 2 一起出现；
- 因此后续不能再把 `destFromVmArgs=true` 当作默认正确路径；
- 这解释了为什么“VM dest 修正后仍 no-ACK”不是强证据，后续 live 若重测，必须明确
  标注 `link_type=11 gateway` 或 `link_type=140 vm_proxy`，不能混用。

新增/更新测试：

- `test_rap_zime_cag_type101_material_uses_link_type_destination_rules`
  - 默认 `link_type=11` 断言写网关目标；
  - 显式 `link_type=140` 断言写 VM 目标；
  - 验证 summary 不泄露目标明文。
- `test_rap_zime_builds_cag_type102_material_from_access_token_branch_redacted`
  - 默认 type102/accessToken 也按 `link_type=11` 写网关目标；
  - 验证 `destinationSource=proxy_gateway`；
  - 验证 summary 不泄露网关或 VM 目标明文。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_cag_type101_auth_buffer_from_material_redacted \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_cag_type101_material_uses_link_type_destination_rules \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_cag_type102_material_from_access_token_branch_redacted \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_can_ztec_prime_first \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v

Ran 5 tests in 0.026s
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 100 tests in 1.767s
OK
```

当前下一步：

1. 跑全量 compileall + Python 单元测试；
2. 若继续 live，优先一组最小短测：
   - 默认 `link_type=11` + gateway target；
   - 只有在有 sock link flag 2 或显式实验目的时才测 `link_type=140` + VM target；
3. 未拿到 `AUTH_HEAD_ACK` 前，仍不创建 ZIME channel、不发送 `DISPLAY_INIT`、
   不运行 verified-run。

## 60. 2026-07-04：修正后 link_type=11/gateway live 仍 AUTH_HEAD 无 ACK

承接第 59 节，执行修正后的最小 session-owning live 短测；不操作 GUI，不操作
CrossDesk，不创建 ZIME channel，不发送 `DISPLAY_INIT`，不运行 verified-run。

前置状态：

- 首次运行 `rap-zime-kcp-auth-from-cag` 时 CAG 获取材料前失败：

```text
listClouds failed: code=4015 msg=用户未登录，请先登录
```

- 随后运行 `token-check`，使用缓存凭据重登成功：

```text
valid=true
response.code=2000
response.msg=re-login ok
```

短测 1：type101 + 默认 link_type=11/gateway

```text
python3 bin/cmcc_cloud_alive.py rap-zime-kcp-auth-from-cag 2663816 \
  --timeout 1 \
  --receive-limit 4 \
  --auth-buffer-type type101 \
  --link-type 11 \
  --report-file reports/kcp-auth-from-cag-live-linktype11-gateway-20260704.json
```

脱敏结果摘要：

```text
proof=fresh_cag_type101_kcp_auth_sync_probe_only
ok=false
stage=auth_head
responses=0
authHeadAckReceived=false
synackReceived=false

authMaterialSource.bufferType=101
authMaterialSource.linkType=11
authMaterialSource.destinationSource=proxy_gateway
authMaterialSource.materialFieldsPresent.destFromVmArgs=false
connectInfo.udpPortSource=proxy-sport
connectInfo.udpSsl=true
```

短测 2：type102/accessToken + 默认 link_type=11/gateway

```text
python3 bin/cmcc_cloud_alive.py rap-zime-kcp-auth-from-cag 2663816 \
  --timeout 1 \
  --receive-limit 4 \
  --auth-buffer-type type102 \
  --cag-auth-type 2 \
  --link-type 11 \
  --report-file reports/kcp-auth-from-cag-live-type102-access-token-linktype11-gateway-20260704.json
```

脱敏结果摘要：

```text
proof=fresh_cag_type102_kcp_auth_sync_probe_only
ok=false
stage=auth_head
responses=0
authHeadAckReceived=false
synackReceived=false

authMaterialSource.bufferType=102
authMaterialSource.authType=2
authMaterialSource.tokenSource=access_token
authMaterialSource.linkType=11
authMaterialSource.destinationSource=proxy_gateway
authMaterialSource.materialFieldsPresent.destFromVmArgs=false
connectInfo.udpPortSource=proxy-sport
connectInfo.udpSsl=true
```

本轮结论：

- 第 59 节修正后的 builder 已在 live report 中生效：
  - 默认 `link_type=11`；
  - `destinationSource=proxy_gateway`；
  - `destFromVmArgs=false`；
- 因此“之前默认 link_type=11 却误写 VM dest”不是唯一阻塞原因；
- type101 gateway 与 type102/accessToken gateway 均仍停在 `AUTH_HEAD`，没有任何
  UDP response；
- 这仍不是 display path 成功；仍未到
  `AUTH_HEAD_ACK/AUTH_ACK/SYNACK/ZIME channel/DISPLAY_INIT/verified-run`；
- 后续不应再盲目重复 auth buffer builder/local bind/ZTEC-prime 组合；更高价值
  的下一步是恢复 `proxy_sock->data_buf[224]` 的来源、官方
  `get_proxy_kcp_dst_ip()/get_proxy_kcp_dst_port()` 的 link_type 判定，或抓 fresh
  official UDP trace 看 AUTH_HEAD 前是否有 CAG/session 绑定包。

## 61. 2026-07-04：补充 IDA proxy/link_type 证据，区分 KCP 发送目标与 auth buffer 目标

承接第 60 节，本轮未操作 GUI，未操作 CrossDesk，未执行 live 短测；只使用本机
IDA 环境对本地 `.so` 做小范围静态证据提取，并保持报告脱敏。

新增 IDA 提取范围：

- `scripts/ida_extract_spice_zime.py`
  - `TARGET_NAMES` 新增：
    - `get_proxy_kcp_dst_ip`
    - `get_proxy_kcp_dst_port`
    - `get_proxy_type_by_link_type`
  - 新增 `link_flag_offset_uses` 静态线索扫描，用于收集 `0xe0/224`
    偏移附近的反汇编和小段伪代码，辅助排查 `proxy_sock->data_buf[224]`
    来源；该扫描会有结构体偏移误报，必须结合函数上下文使用。

生成的新证据产物：

```text
reports/ida-libspice-zime-proxy-linktype-source-20260704.json
reports/ida-libspice-zime-proxy-linktype-source-20260704-snippets.txt
```

本轮确认的静态事实：

```text
get_proxy_kcp_dst_ip(session, TN_MULTI_TCP_SOCK)
  enable_cag -> s->ag_ip
  non-CAG + proxy_type != "ice" -> s->vm_ip
  non-CAG + proxy_type == "ice" -> s->host

get_proxy_kcp_dst_port(session, TN_MULTI_TCP_SOCK)
  enable_cag -> s->ag_port
  non-CAG -> s->vm_proxy_port

get_proxy_type_by_link_type(session, link_type)
  returns 5 only when rap/no downward bw ctrl and link_type == 2
  otherwise returns 6

deal_udt_using_cag()/deal_udt_using_cag_uac()
  仍确认 auth buffer 内部目标规则：
  default link_type=11 -> host/proxy_sport 或 host/proxy_port
  proxy_type == "ice" -> link_type=139, host/port
  udp_sock->data_buf[224] == 2 -> link_type=140, vm_ip/vm_proxy_port
  udp_sock->data_buf[224] == 1 -> opentelemetry 条件
```

关键判断：

- `get_proxy_kcp_dst_ip()/port()` 选择的是 KCP socket/连接发送目标；
- `deal_udt_using_cag*()` 写入的是 CAG type101/type102 auth buffer 内部的
  proxy data 目标；
- 这两者不是同一层目标，后续不能把 `ag_ip/ag_port` 直接替换进 auth buffer
  的 `dest_ip/dest_port`；
- 第 59 节的 builder 规则没有被推翻：默认 `link_type=11` 仍写 gateway，
  只有 sock link flag 2 / `link_type=140` 才写 VM proxy；
- 本轮仍未定位到 `proxy_sock->data_buf[224]` 的可靠写入来源；
  `link_flag_offset_uses` 已给出候选偏移线索，但存在大量非 IceSocket 结构体
  `0xe0` 偏移误报，不能据此直接 live 盲测 `link_type=140`。

本轮代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `describe_official_kcp_destination_source()`；
  - 在 type101/type102 material builder 的 summary 中新增
    `officialKcpDestinationEvidence`；
  - 该字段只记录来源类别和 IDA evidence，不写 IP/port 明文；
  - 明确标记 `notAuthBufferDestination=true`，避免混淆 KCP 发送目标和 auth
    buffer 内部目标。
- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_describes_official_kcp_destination_source_redacted`；
  - 增强 `test_rap_zime_cag_type101_material_uses_link_type_destination_rules`，
    验证 builder summary 携带官方 KCP 目标来源 evidence 且不泄露目标明文。

验证：

```text
IDADIR=/home/demo/tools/idapro-9.3 \
  /home/demo/.local/share/pipx/venvs/ida-pro-mcp/bin/python \
  scripts/ida_extract_spice_zime.py \
  --binary .tmp/ida-inputs/libspice-client-glib-zte-2.0.so.8.5.0 \
  --output reports/ida-libspice-zime-proxy-linktype-source-20260704.json \
  --max-decompile-chars 24000
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_cag_type101_material_uses_link_type_destination_rules \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_describes_official_kcp_destination_source_redacted \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_cag_type102_material_from_access_token_branch_redacted -v

Ran 3 tests in 0.001s
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 101 tests in 1.624s
OK
```

当前状态：

- 仍未收到 `AUTH_HEAD_ACK`；
- 仍未发送 `AUTH_DATA`；
- 仍未收到 `AUTH_ACK`；
- 仍未发送 client SYN；
- 仍未收到 SYNACK；
- 仍未进入 ZIME channel；
- 仍未发送 `DISPLAY_INIT`；
- 仍未观察 display activity；
- 仍未通过 40 分钟 verified-run。

## 64. 2026-07-04：收窄 trace/analyze 到 `AUTH_HEAD_ACK(cmd=7)` 单一关口

承接第 63 节和本轮重新对齐：当前阶段不再扩展 HTTP/CAG/Docker/泛化 runner
工作，不再把 102/103 个本地测试通过误读为服务端承认 session。唯一近期
milestone 是解释并拿到真实 `AUTH_HEAD_ACK(cmd=7)`。本轮没有操作 GUI/CrossDesk，
没有执行 live 短测，没有构建本地 `.so` 二进制，没有生成新的持久 report，也没有读取
或输出敏感明文。

本轮确认的静态事实：

- `.tmp/ida-inputs/libspice-client-glib-zte-2.0.so.8.5.0` 是带
  `debug_info` 且未 strip 的 ELF；
- 本地符号表中可直接定位本轮关注函数：

```text
send_udt_data                         0x147c0a
check_spice_proxy_protocol_header      0x151c59
deal_udt_using_cag_uac                 0x152a2d
deal_udt_using_cag                     0x1530a9
init_local_rw_sock_pair_udp            0x153d04
deal_local_link_proxy_create           0x154c06
deal_unlinked_outband_head_data        0x154fe6
deal_unlinked_unknown_local_data       0x1552fe
deal_create_proxy_fd_session           0x162b03
```

代码更新：

- `research/zime-probe.c`
  - 新增 `ZIME_PROBE_AUTH_FOCUS`；
  - `payload_kind()` 增加 KCP AUTH 粗分类：
    - `kcp-auth-head`
    - `kcp-auth-data`
    - `kcp-auth-head-ack`
    - `kcp-auth-ack`
  - transport buffer 日志新增：
    - `authFocus`
    - `stack`
  - 启用 auth focus 时，为 KCP AUTH 相关包记录 `send_udt_data/udt_output/
    deal_udt_using_cag*` 等调用栈线索，供 fresh trace 对照。
- `scripts/run-zime-probe.sh`
  - 新增 `ZIME_PROBE_MODE=auth`；
  - 该模式只启用 transport interpose + auth focus，默认 `ZIME_PROBE_MAX_BYTES=256`，
    不 wrap callback，不修改返回值。
- `cmcc_cloud_alive/zime_probe.py`
  - 增加最小 KCP AUTH 分类；
  - `analyze-zime-probe` 输出新增 `authHeadAckFocus`：
    - 第一条 send-side `kcp-auth-head`；
    - 是否观察到 receive-side `kcp-auth-head-ack(cmd=7)`；
    - 同 fd 在 AUTH_HEAD 前的 socket/bind/connect/transport_buffer 前史；
    - `missingEvidence` 和下一步问题；
    - KCP auth payload 在 focus 摘要中保持脱敏。
- `tests/test_python_modules.py`
  - 新增 `test_zime_probe_auth_head_ack_focus_stops_at_first_auth_head`，
    验证无 `cmd=7` 时 stage 固定为 `auth_head_ack_missing`，并保留同 fd
    前史和调用栈线索。

验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

gcc -fsyntax-only -Wall -Wextra \
  -DZIME_PROBE_ENABLE_TRANSPORT_INTERPOSE=1 \
  -DZIME_PROBE_ENABLE_CPP_INTERPOSE=0 \
  research/zime-probe.c
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_display_init_seen_on_transport_receive \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_stops_at_first_auth_head -v
Ran 2 tests
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 103 tests
OK
```

当前判断：

- 这仍不是协议保活成功；
- 这也不是 `AUTH_HEAD_ACK` 成功；
- 但 trace/analyze 的阶段边界已经从“泛化 display/runner 证据”收窄到一个问题：
  官方客户端第一条 `AUTH_HEAD` 前，同 fd 和调用栈到底经历了哪些前置状态，为什么
  服务端随后返回或不返回 `cmd=7`。

下一步只做一件事：

1. 如需 fresh trace，先征得用户对 GUI/session-owning/持久日志路径的确认；
2. 使用 `ZIME_PROBE_MODE=auth` 低侵入抓官方链路；
3. 用 `analyze-zime-probe` 只看 `authHeadAckFocus`；
4. 若仍无 `cmd=7`，根据 `sameFdPreAuthEvents` 和 `stack` 反推缺失的
   local proxy/outband/session 最小前置复现。

下一步建议：

1. 继续定位 `proxy_sock->data_buf[224]` 的真实写入来源，优先从
   IceSocket/`create_udt_session`/official read loop 上下文反查，而不是使用
   全局 `0xe0` 偏移误报直接判断；
2. 若静态证据仍无法确定 sock link flag 2 的来源，优先抓 fresh official UDP
   trace，观察 AUTH_HEAD 前是否存在 CAG/session 绑定包或前置控制包；
3. 未拿到 `AUTH_HEAD_ACK` 前，仍不创建 ZIME channel、不发送 `DISPLAY_INIT`、
   不运行 verified-run。

## 62. 2026-07-04：定位 `data_buf[224]` 来源链路，确认来自前置 proxy fd session/link-type negotiation

承接第 61 节，继续排查 `proxy_sock->data_buf[224]` / sock link flag 来源。
本轮扩大分析面：除本地 `.so` 和已有报告外，同时核对 Python runner 的报告结构、
测试断言、符号表和 DWARF 名称；未操作 GUI，未操作 CrossDesk，未执行 live 短测，
未读取或输出敏感明文。

新增 IDA 提取：

- `scripts/ida_extract_spice_zime.py`
  - `TARGET_NAMES` 新增：
    - `init_outband_fd_session_bw_ctrl_link_type`
    - `reset_sock_bw_ctrl_link_type_by_bw_config`
    - `deal_bw_ctrl_sock_link_message`
    - `deal_udt_multi_link`
    - `deal_udt_multi_tcp_session_init`
    - `deal_udt_multi_tcp_socket_error`
    - `deal_create_proxy_fd_session`
    - `deal_unlinked_unknown_local_data`
    - `deal_unlinked_local_data_read`
    - `deal_unlinked_outband_head_data`
    - `deal_unlinked_outband_local_data`
    - `deal_local_link_proxy_create`
    - `set_sock_bw_ctrl_type`
    - `send_tunnel_link_message`
    - `send_tunnel_add_link`
    - `set_fd_session_flag`
    - `get_thread_proxy_fd_session`
  - 新增 `link_flag_decompile_uses`，扫描所有可反编译函数中出现的
    `data_buf[224]`、`sock_link_type`、`bw_ctrl_link_type`、
    `proxy_link_type`、`up_bw_ctrl_link_type`、`down_bw_ctrl_link_type`
    语义片段，减少第 61 节全局 `0xe0` 偏移扫描的误报。

生成的新证据产物：

```text
reports/ida-libspice-zime-link-flag-source-20260704.json
reports/ida-libspice-zime-link-flag-source-directed-20260704.json
reports/ida-libspice-zime-link-flag-source-directed-20260704-snippets.txt
```

本轮确认的关键链路：

```text
deal_unlinked_unknown_local_data(in_sock)
  after local proxy protocol header is collected:
    in_sock->data_buf[224] = 1
    if !check_spice_proxy_protocol_header(...):
      in_sock->data_buf[224] = 2
      BYTE4(in_sock->ssl) = 9
  if data_buf[224] != 2:
    deal_local_link_proxy_create(in_sock)
  else:
    deal_unlinked_outband_head_data(in_sock)
    ... may still call deal_local_link_proxy_create(in_sock)

deal_local_link_proxy_create(in_sock)
  v2 = port-channel override ? 1 : in_sock->data_buf[224]
  proxy_type_ex = get_proxy_type_by_link_type(session, v2)
  if proxy fd session is missing:
    deal_create_proxy_fd_session(thread, proxy_type_ex)

deal_create_proxy_fd_session(thread, fd_type_ex)
  link_type = 1
  if fd_type_ex == TN_MULTI_TCP_SOCK:
    link_type = 2
  proxy_sock->data_buf[224] = link_type
  proxy_sock->cag_client_key = fd_type_ex

init_local_rw_sock_pair_udp(...)
  udp_sock->data_buf[224] = proxy_sock->data_buf[224]
  create_udt_session(...)
  deal_udt_using_cag()/deal_udt_using_cag_uac() consumes udp_sock->data_buf[224]
```

判断更新：

- 第 61 节的“未定位到可靠写入来源”已被推进：
  `data_buf[224]` 不是 auth buffer builder 自己推导的值，而是来自前置
  local proxy protocol header/link-type negotiation 和 proxy fd session；
- `link_type=140` / VM proxy auth buffer 分支对应的是官方路径中
  `udp_sock->data_buf[224] == 2`，而该值由 proxy socket 继承；
- 因此 Python runner 仅构造 type101/type102 auth buffer 并直接发送 AUTH_HEAD，
  仍缺少官方前置链路：
  - local proxy protocol header 检测；
  - proxy fd session 创建；
  - `proxy_sock->data_buf[224]` 写入；
  - `proxy_sock->data_buf[224] -> udp_sock->data_buf[224]` 传播；
  - 再进入 `deal_udt_using_cag*()`；
- 这解释了为什么继续盲测 `--link-type 140` 仍不是可靠下一步：缺的是前置
  session/link-type negotiation 语义，而不只是 auth buffer 内部字段。

本轮代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `kcp_udp_session_lifecycle_ida_evidence()` 增加：
    - `deal_unlinked_unknown_local_data`
    - `deal_local_link_proxy_create`
    - `deal_create_proxy_fd_session`
    - `init_local_rw_sock_pair_udp` 的 link flag 传播说明；
  - `officialSequence` 明确写入 local proxy header -> proxy fd session ->
    UDP sock link flag 传播 -> KCP/session -> CAG AUTH 的顺序；
  - `_kcp_auth_probe_parity_assessment()` 新增缺失项：
    - `local_proxy_protocol_header_link_type_detection`
    - `deal_create_proxy_fd_session_link_type_assignment`
    - `proxy_sock_link_type_copied_to_udp_sock`
  - `actionableNextEvidence` 新增追踪 local proxy protocol header 的建议。
- `tests/test_python_modules.py`
  - 增强 `test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn`，
    验证 report 中包含 link-type negotiation 缺口、sourceReports 和函数证据。

验证：

```text
IDADIR=/home/demo/tools/idapro-9.3 \
  /home/demo/.local/share/pipx/venvs/ida-pro-mcp/bin/python \
  scripts/ida_extract_spice_zime.py \
  --binary .tmp/ida-inputs/libspice-client-glib-zte-2.0.so.8.5.0 \
  --output reports/ida-libspice-zime-link-flag-source-directed-20260704.json \
  --max-decompile-chars 32000
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_can_bind_local_udp_source -v

Ran 2 tests in 0.009s
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 101 tests in 1.740s
OK
```

当前状态：

- 仍未收到 `AUTH_HEAD_ACK`；
- 仍未发送 `AUTH_DATA`；
- 仍未收到 `AUTH_ACK`；
- 仍未发送 client SYN；
- 仍未收到 SYNACK；
- 仍未进入 ZIME channel；
- 仍未发送 `DISPLAY_INIT`；
- 仍未观察 display activity；
- 仍未通过 40 分钟 verified-run。

下一步建议：

1. 不再把 `link_type=140` 当作单纯 CLI/auth-buffer 组合盲测；若要测 140，
   必须先模拟或确认 local proxy protocol header/link-type negotiation；
2. 继续从 `deal_unlinked_unknown_local_data()` 需要的本地 proxy protocol header
   和 `deal_unlinked_outband_head_data()` 的 outband header 结构恢复最小前置包；
3. 或抓 fresh official UDP/local-loop trace，观察 AUTH_HEAD 前本地 proxy header、
   proxy fd session 创建和外层 UDP 发送顺序；
4. 未拿到 `AUTH_HEAD_ACK` 前，仍不创建 ZIME channel、不发送 `DISPLAY_INIT`、
   不运行 verified-run。

## 63. 2026-07-04：给 AUTH probe 增加 AUTH_HEAD 前预接收窗口实验模式

承接第 62 节。本轮没有操作 GUI/CrossDesk，没有执行 live 短测，没有生成新的本地
二进制或持久 report，也没有读取或输出敏感明文。目标是把“官方路径在 AUTH_HEAD 前
已有 read loop / fd-session 生命周期，而 Python probe 直接发送 AUTH_HEAD”的差异，
推进成 runner 可执行、可审计的显式实验开关。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `run_kcp_auth_sync_probe()` 新增显式参数：
    - `pre_auth_receive_timeout`
    - `pre_auth_receive_limit`
    - `pre_auth_bind_host`
  - 当且仅当 `pre_auth_receive_timeout > 0` 且 `pre_auth_receive_limit > 0` 时：
    - 若调用方未显式 `local_bind_*`，先把 UDP socket 绑定到
      `pre_auth_bind_host:0`；
    - 在发送 AUTH_HEAD 前短暂进入 `recvfrom()` 观察窗口；
    - 只记录 remote、长度、payloadKind、KCP summary，不保存 payload bytes；
    - `localSocketLifecycle` 标记：
      - `preAuthReceiveLoopStarted`
      - `implicitBindForPreAuthReceive`
      - `localEndpointAfterPreAuthBind`
    - report 新增 `preAuthReceive` 脱敏字段；
  - `_kcp_auth_probe_parity_assessment()` 新增判断：
    - 如果启用预接收窗口后仍停在 `auth_head`，记录
      `pre_auth_receive_window_alone`；
    - 如果同时使用隐式预绑定，记录
      `pre_auth_implicit_udp_bind_alone`；
    - `doNotRepeatWithoutNewEvidence` 增加
      `pre_auth_receive_window_without_proxy_header_or_official_trace`。
- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` CLI 新增：
    - `--pre-auth-receive-timeout`
    - `--pre-auth-receive-limit`
    - `--pre-auth-bind-host`
  - 默认值保持禁用，不改变既有 live probe 行为。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_sync_probe_can_start_pre_auth_receive_window`；
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path`，
    验证 CLI 参数传入 CAG material probe。

验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_can_start_pre_auth_receive_window \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v

Ran 2 tests in 0.032s
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 102 tests in 1.706s
OK
```

判断更新：

- 这不是协议保活成功，也不是 AUTH_HEAD_ACK 成功；
- 该改动只把“AUTH_HEAD 前 read loop/绑定窗口”从文档缺口变成可控 probe 模式；
- 如果后续 live 启用该模式仍无 ACK，应把
  `pre_auth_receive_window_without_proxy_header_or_official_trace` 视为已排除项，
  不要继续重复“只预接收、不恢复 proxy header/official trace”的盲测；
- 真正下一步仍是恢复 local proxy protocol header / outband header 的最小前置包，
  或抓 fresh official UDP/local-loop trace 证明 AUTH_HEAD 前实际包序。

当前状态未变：

- 仍未收到 `AUTH_HEAD_ACK`；
- 仍未发送 `AUTH_DATA`；
- 仍未收到 `AUTH_ACK`；
- 仍未发送 client SYN；
- 仍未收到 SYNACK；
- 仍未进入 ZIME channel；
- 仍未发送 `DISPLAY_INIT`；
- 仍未观察 display activity；
- 仍未通过 40 分钟 verified-run。

## 65. 2026-07-04：fresh official auth-focus trace 已证明云端承认官方 session，Python 缺口收窄到前置生命周期和首包形态

本轮按重新确认的阶段边界执行：只证明云端是否承认 session，未推进 Python
`SYNACK`、native bridge、`DISPLAY_INIT` 或 40 分钟 verified-run。用户手动点击
官方客户端第一张“家庭云电脑畅享版月包”的“连接”按钮；未由 agent 点击 GUI，未操作
CrossDesk。官方客户端曾进入桌面，约 3 秒内退回客户端。

执行与产物：

- 使用官方稳定包装器 `/home/demo/.local/bin/cmcc-jtydn-stable`，并在
  `ZIME_PROBE_MODE=auth` 下启动；
- 临时 probe 二进制：
  `build/research/zime-probe-transport.so`；
- 脱敏 JSONL：
  `reports/zime-auth-focus-fresh-20260704-203624.jsonl`；
- 未生成额外 report 文件；
- 未保存或输出 token、connectStr、accessToken、cpsid、密码、JWT 或 auth payload
  明文。

关键证据：

- JSONL 有效产生，约 9 MB，`uSmartView_VDI_` 进程写入 15997 条记录；
- `analyze-zime-probe` 的 `authHeadAckFocus.observed=true`，
  `stageBlocked=null`；
- 外网 UDP 目标仍是 CAG KCP 目标，记录中只保留端点、长度、方向和分类，不保留敏感
  payload；
- 首个外网 UDP fd 的关键序列：
  - 前置 loopback/local proxy 交互：
    `socket -> connect(127.0.0.1:*) -> send 160 -> recv 4`；
  - 随后创建外网 UDP fd；
  - 同 fd 连续发送 `kcp-auth-head`，长度 199；
  - 同 fd 收到 71 字节响应；
  - 随后发送 `kcp-auth-data`，长度 241；
- 后续 trace 中还可见多个 71 字节 `kcp-auth-head` 分支，因此不能简单断言
  “71 字节 AUTH_HEAD 一定错误”。更准确的结论是：Python 之前直接发送 71 字节
  AUTH_HEAD，缺少官方的 local proxy/session 生命周期和首个 199 字节 AUTH_HEAD
  形态。

本轮回答的问题：

- 云端会承认官方 session：是，fresh trace 已观察到 AUTH_HEAD 后继续 AUTH_DATA 的
  服务端承认路径；
- Python 之前为什么不被认：不是因为“协议太难”这种泛泛原因，而是 Python probe
  跳过了官方 session 生命周期，且首包形态与官方首个外网 fd 不一致；
- 现阶段不应继续盲测 type101/type102/link_type/local bind，下一步应把官方
  loopback/local proxy 前置交互和 199 字节 AUTH_HEAD 形态落实成最小 Python 复现。

注意：

- trace 中可见 `spice-display-init` 和 `spice-mark` 等官方客户端 display 证据，但
  这是官方 trace，不是 Python runner 成功；
- 用户观察到桌面约 3 秒后退回，说明本轮不能作为保活稳定性证据；
- 仍未完成 Python 协议级保活、未完成 40 分钟 verified-run。

下一步建议：

1. 只基于本次 fresh trace 做最小复现设计：先复现 local proxy/session 前置交互和
   199 字节首个 AUTH_HEAD，目标仍只到真实 `AUTH_HEAD_ACK(cmd=7)`；
2. 在 analyzer 中把“外网同 fd 71 字节响应后立即 AUTH_DATA”单独标成
   `auth_head_ack_like_response`，避免把 loopback/DBus 上的 `kcp-auth-head-ack`
   误当外网服务端 ACK；
3. Python 复现拿到 `cmd=7` 或等价 ACK-like 同 fd响应前，仍不推进 SYNACK/native
   bridge/DISPLAY_INIT/verified-run。

补充代码更新：

- `cmcc_cloud_alive/zime_probe.py`
  - `auth_head_ack_focus()` 新增 `authHeadAckLikeResponses`；
  - 新增 `authHeadAckConfirmed`；
  - 只有满足“同一 fd、同一 remote、AUTH_HEAD 之后、AUTH_DATA 之前收到响应，并且随后
    同 fd 发送 AUTH_DATA”的记录，才作为外网 ACK-like 证据；
  - 保留原 `authHeadAckReceiveIndexes`，但不再只依赖该字段判定阶段是否阻塞，避免
    loopback/family socket 分类误导。
  - 新增 `authGateReplayGap`：
    - `readyForPythonAuthGateReproduction=true`；
    - `firstExternalAuthHead.len=199`；
    - `sameFdAckLikeResponse.len=71`；
    - `expectedAuthDataAfterAckLikeLen=241`；
    - `officialPreAuthLocalProxyEvents` 收敛为：
      `connect(127.0.0.1:*) -> send len=160 -> recv len=4`；
    - `nextStep` 改为：
      `Reproduce the official local proxy/session bootstrap and first external AUTH_HEAD gate in Python; do not proceed to SYNACK/native bridge/DISPLAY_INIT yet.`
- `tests/test_python_modules.py`
  - 新增
    `test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response`，
    用 synthetic JSONL 覆盖本轮官方 trace 的关键形态；
  - 断言 `authGateReplayGap` 不含 auth payload 明文，并明确 `doNext` 必须停在
    `authHeadAckConfirmed`。

补充验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_stops_at_first_auth_head \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response -v

Ran 2 tests
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 104 tests
OK
```

更新后的阶段边界：

- 当前不是“继续猜下一包”；
- 当前也不是进入 SYNACK/native bridge；
- 当前唯一工程目标是让 Python 复现 `authGateReplayGap`：
  `local proxy/session bootstrap -> first external AUTH_HEAD len=199 -> same-fd
  ACK-like response len=71 -> AUTH_DATA len=241`；
- Python 复现没有达到 `authHeadAckConfirmed=true` 前，不应再发 SYN 或推进 display。

## 66. 2026-07-04：Python CAG AUTH CLI 默认改为官方首个 199 字节 otel AUTH_HEAD 形态

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。唯一
代码目标是把第 65 节 fresh official trace 里的首包形态固化到 Python 最小复现实验的
默认路径上，避免继续用旧 71 字节普通 AUTH_HEAD 作为默认主线。

新增信息：

- 第 65 节官方 trace 的首个外网 AUTH gate 是：
  `AUTH_HEAD len=199 -> same-fd ACK-like len=71 -> AUTH_DATA len=241`；
- Python 旧默认 `opentelemetry=False` 对应的首包不是该 199 字节形态；
- `rap-zime-kcp-auth-from-cag` 现在默认 `--opentelemetry` 开启，对齐 fresh trace 中
  命中的 otel AUTH_HEAD 形态；
- 仍保留 `--no-opentelemetry`，仅作为显式回退/对照，不作为当前主线。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 的 `--opentelemetry` 使用
    `argparse.BooleanOptionalAction`；
  - 默认值为 `True`；
  - help 文案明确该默认跟随 fresh official auth-focus trace。
- `tests/test_python_modules.py`
  - `test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path` 增加默认
    `opentelemetry=True` 断言；
  - 新增 `test_rap_zime_kcp_auth_from_cag_cli_can_disable_opentelemetry`，覆盖
    `--no-opentelemetry` 回退。

工具缺陷记录：

- `analyze-rap-zime` 对 fresh auth trace 仍可能被不完整 ZTEC 候选片段打断；
- 在该缺陷解决前，不应把 runner-input 缺失直接解释为协议证据缺失；
- 当前 AUTH gate 复现依据应以 `authGateReplayGap` 的脱敏字段为准：
  local proxy/session bootstrap、首个外网 `AUTH_HEAD len=199`、same-fd
  `ACK-like len=71`、随后 `AUTH_DATA len=241`。

task-forest 更新：

- 新增 `TF-0061`：复现官方 199 字节 AUTH gate 最小 Python 实验；
- 新增 `TF-0062`：记录 `analyze-rap-zime` 可能被不完整 ZTEC 候选片段打断的工具缺陷；
- 清理 `progress=100` 或 `derived_progress=100` 但仍在推进队列里的旧节点；
- 将 native bridge、SYNACK、DISPLAY_INIT、40 分钟 verified-run 等后续路线标记为
  blocked 或 deprecated，直到 `TF-0061` 跑出 same-fd 71 字节 ACK-like。

阶段边界未变：

- 没有真实 Python `AUTH_HEAD_ACK(cmd=7)` 或 same-fd ACK-like；
- 没有发送 Python `AUTH_DATA` 成功进入服务端承认路径；
- 不推进 SYNACK、native bridge、DISPLAY_INIT 或 40 分钟 verified-run；
- 不再盲测 type101/type102/link_type/local_bind/ztec_prime，除非新增变量能明确对应
  fresh trace 的字段差异。

补充验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_can_disable_opentelemetry -v

Ran 2 tests
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 105 tests
OK

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py validate --workspace /home/demo/restore/cmcc-cloud-alive
校验通过。

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py export --workspace /home/demo/restore/cmcc-cloud-alive
已导出：/home/demo/restore/cmcc-cloud-alive/.agent-workbench/task-forest/exports/task-forest.html
```

下一步只允许围绕：

```text
local proxy/session bootstrap
-> first external AUTH_HEAD len=199
-> same-fd ACK-like len=71
-> AUTH_DATA len=241
```

跑不出 71 字节 ACK-like，就停在 AUTH gate 问题，不讲 ZIME/display。

## 67. 2026-07-04：把 71 字节 ACK-like gate 接入 Python CAG AUTH 状态机

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。工作
只围绕第 65 节 fresh official trace 的最小 AUTH gate：

```text
first external AUTH_HEAD len=199
-> same-fd ACK-like len=71
-> AUTH_DATA len=241
```

新增信息：

- 旧 Python CAG AUTH 路线只在响应可解码为 `cmd=7` 时才发送 `AUTH_DATA`；
- 但第 65 节官方 trace 的可靠门槛是“同一外网 fd 在 AUTH_HEAD 后收到 71 字节响应，
  随后官方发送 AUTH_DATA”，不依赖 analyzer 必须把这 71 字节解成 `cmd=7`；
- 因此本轮把 `same-fd 71-byte ACK-like` 接入 Python runner 的 AUTH_HEAD gate：
  `cmd=7` 或同 remote 71 字节 ACK-like 都允许继续发 `AUTH_DATA`；
- CAG material 路线默认 `auth_gate_only=True`，到 `AUTH_DATA` 后停止，不再继续等
  `AUTH_ACK`、不发送 SYN、不触碰 native bridge/DISPLAY_INIT。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `OFFICIAL_AUTH_HEAD_ACK_LIKE_LEN = 71`；
  - 新增 `_recv_auth_head_gate_ack()`，只记录 remote、长度、payloadKind、KCP 摘要和
    `officialAuthHeadAckLike`，不保存 payload；
  - `run_kcp_auth_sync_probe()` 新增 `auth_gate_only`；
  - `authPreflight` 新增：
    - `authHeadAckLikeReceived`
    - `authHeadGateAccepted`
    - `officialAckLikeLength`
    - `authDataSentAfterAuthHeadGate`
  - report 新增：
    - `authGateOnly`
    - `authGateConfirmed`
  - `officialParityAssessment.pythonProbePath` 改成
    `AUTH_HEAD -> wait_cmd7_or_71_byte_ACK_like -> AUTH_DATA -> stop`
    （当 `auth_gate_only=True`）；
  - `run_kcp_auth_sync_probe_from_cag_material()` 默认：
    - `opentelemetry=True`
    - `auth_gate_only=True`
    - `nextStep` 明确 stop before `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT`。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_sync_from_cag_material_accepts_71_byte_ack_like_gate`；
  - 该测试用 fake UDP server 返回不可解码的 71 字节响应，断言 Python 发送：
    - `AUTH_HEAD` 199 字节；
    - `AUTH_DATA` 241 字节；
    - 不发送 SYN；
  - 更新 CAG material redaction / ztec-prime 测试，使其默认只验收到 AUTH gate，不再期待
    SYNACK。

阶段边界：

- 这不是 live 成功；
- 没有证明云端承认 Python session；
- 没有 `AUTH_ACK`、没有 SYNACK、没有 native channel、没有 `DISPLAY_INIT`；
- 但 Python 最小复现实验的状态机现在和第 65 节官方 gate 对齐：
  199 字节首包、71 字节同 fd ACK-like、241 字节 AUTH_DATA。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_runs_auth_then_syn \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_redacts_report \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_accepts_71_byte_ack_like_gate \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_can_ztec_prime_first -v

Ran 4 tests
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 106 tests
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 107 tests
OK

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py validate --workspace /home/demo/restore/cmcc-cloud-alive
校验通过。

python3 /home/demo/.codex/skills/task-forest/scripts/task_forest.py export --workspace /home/demo/restore/cmcc-cloud-alive
已导出：/home/demo/restore/cmcc-cloud-alive/.agent-workbench/task-forest/exports/task-forest.html
```

下一步仍只允许围绕 AUTH gate：

1. 如果要 live，只运行 CAG AUTH gate-only 路线，目标只看：
   `AUTH_HEAD len=199 -> same-fd ACK-like len=71 -> AUTH_DATA len=241`；
2. 每次实验必须说明新增信息对应第 65 节哪个字段；
3. 跑不出 71 字节 ACK-like，继续停在 local proxy/session bootstrap 和首包门槛，不讲
   SYNACK/ZIME/display。

## 68. 2026-07-04：gate-only live 发送 199 字节 AUTH_HEAD 仍无 71 字节 ACK-like

本轮执行一次最小 live AUTH gate-only 验证。没有操作 GUI/CrossDesk，没有生成本地二进制，
没有写 `--report-file`，只读取当前缓存登录态并打印脱敏 JSON 到 stdout。命令：

```text
python3 bin/cmcc_cloud_alive.py rap-zime-kcp-auth-from-cag --timeout 1.5 --receive-limit 4
```

新增信息：

- Python CAG material 路线已默认 `opentelemetry=true`、`auth_gate_only=true`；
- live 实际发出的 `AUTH_HEAD` 为 199 字节：
  - `authPreflight.headerLenField=172`
  - `authPreflight.authHeadLen=178`
  - stage `auth_head.bytesSent=199`
  - segment `len=178`
- 但同 fd 没收到任何响应：
  - `responses=[]`
  - `authHeadAckReceived=false`
  - `authHeadAckLikeReceived=false`
  - `authHeadGateAccepted=false`
  - `authDataSentAfterAuthHeadGate=false`
- 因为 `auth_gate_only=true` 且 gate 未通过，本轮没有发送 `AUTH_DATA`，没有发送 SYN，
  没有推进 native bridge / `DISPLAY_INIT` / verified-run。

这次实验对应第 65 节官方 trace 的字段：

```text
official firstExternalAuthHead.len=199
official sameFdAckLikeResponse.len=71
official expectedAuthDataAfterAckLikeLen=241
officialPreAuthLocalProxyEvents=connect(127.0.0.1:*) -> send len=160 -> recv len=4
```

判断：

- 已排除“只要 Python 发 199 字节 otel AUTH_HEAD，并接受 71 字节 ACK-like，就能被云端
  承认”的过简假设；
- 当前失败不再指向 type101/type102/link_type/local_bind/ztec_prime 组合；
- 当前失败也不指向 ACK parser，因为本轮连 71 字节原始响应都没有；
- 剩余关键差异仍是官方首包前的 local proxy/session bootstrap：
  `connect(127.0.0.1:*) -> send len=160 -> recv len=4`、proxy fd session、
  `data_buf[224]` link flag 传播、UDP fd/IceSocket/KCP thread-list 附着。

当前状态：

- Python 已能构造并发送官方首个外网 199 字节 AUTH_HEAD 形态；
- Python 已能在收到 71 字节 ACK-like 时发送 241 字节 AUTH_DATA；
- live 云端仍未承认 Python session；
- 未到 `AUTH_ACK/SYNACK/native_channel_created/DISPLAY_INIT/40 分钟 verified-run`。

下一步：

1. 不重复 `type101/type102/link_type/local_bind/ztec_prime` 盲测；
2. 不再验证 SYNACK/native bridge/DISPLAY_INIT；
3. 只围绕官方 AUTH_HEAD 前置生命周期做最小复现：
   - 从 fresh trace 中把 loopback/local proxy `send len=160` / `recv len=4`
     的结构字段恢复成脱敏 schema；
   - 判断这 160 字节是否只是本地 readiness/header，还是会导致 proxy fd session /
     `data_buf[224]` / KCP session 状态写入；
   - 在 Python 里复现最小 local proxy/session bootstrap 后，再跑同一个 gate-only live。

## 69. 2026-07-04：把官方三次 AUTH_HEAD pump 和重复 local bootstrap 固化到 gate-only runner

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。工作只
围绕第 65 节 fresh official trace 的 AUTH gate：

```text
local proxy bootstrap cycles
-> AUTH_HEAD len=199 repeated on same UDP fd
-> same-fd ACK-like len=71
-> AUTH_DATA len=241
```

用户新增观察：

- 上一轮 Python 单发 199 字节 `AUTH_HEAD` 后，官方客户端被顶下线；
- 这不能证明 Python session 已被云端承认，因为 Python 仍未收到 same-fd 71 字节
  ACK-like，也未发送 `AUTH_DATA`；
- 但它是有效的新信号：云端或官方会话管理大概率看到了 Python 的 session attempt，
  问题从“包完全没到”收窄为“缺少官方 gate 前置状态或 AUTH_HEAD pump 形态，导致
  ACK-like 没回到 Python fd”。

新增 trace 事实：

- 第 65 节 fresh trace 中，官方 ACK-like 前不是单发 `AUTH_HEAD`：
  - `idx=768`：same fd `AUTH_HEAD len=199`；
  - `idx=787`：same fd `AUTH_HEAD len=199`，距第一次约 77ms；
  - `idx=804`：same fd `AUTH_HEAD len=199`，距第二次约 82ms；
  - `idx=819`：same fd `ACK-like len=71`；
  - `idx=820`：随后 `AUTH_DATA len=241`。
- 第一次和第二次 `AUTH_HEAD` 周围各有一轮 local proxy bootstrap：
  - `idx=762/764/765`：`connect(127.0.0.1:*) -> send len=160 -> recv len=4`；
  - `idx=779/781/782`：同一 listen endpoint 上再次出现
    `connect -> send len=160 -> recv len=4`；
  - 两轮 160 字节本地帧的脱敏 header 都是 `u16Type=26`、`u16BodyLen=156`，
    server 侧 4 字节 header 与 client header 匹配。

代码更新：

- `cmcc_cloud_alive/zime_probe.py`
  - `authGateReplayGap.localProxyBootstrapSchema` 新增：
    - `cycleCountInAuthGateWindow`
    - `cyclesBeforeAckLike`
    - `cyclePositionCounts`
    - `repeatedBeforeAckLike`
    - `stateImplication`
  - 输出仍只保留 fd、方向、长度、header 字段和事件索引；不保存 160 字节 payload。
- `cmcc_cloud_alive/rap_zime.py`
  - `run_kcp_auth_sync_probe()` 新增：
    - `auth_head_attempts`
    - `auth_head_retry_interval`
  - 通用 raw probe 默认仍为单发，避免扩大旧测试语义；
  - CAG material 路线默认 `auth_head_attempts=3`、`auth_head_retry_interval=0.08`，
    对齐官方 `idx=768/787/804 -> 819`；
  - 仍然只有在收到 `cmd=7` 或 same-remote 71 字节 ACK-like 后才发送
    `AUTH_DATA`，否则不发 `AUTH_DATA`、不发 SYN。
- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 新增 CLI 参数：
    - `--auth-head-attempts`
    - `--auth-head-retry-interval`
- `tests/test_python_modules.py`
  - 新增/更新测试覆盖：
    - fresh trace 风格的重复 local bootstrap schema；
    - 三次 AUTH_HEAD pump；
    - CLI 参数传递；
    - 71 字节 ACK-like 后才发 241 字节 AUTH_DATA，且不发 SYN。

阶段判断：

- 当前新增变量不是 timeout 盲调，而是来自 fresh trace 的明确差异；
- “199 后顶下线”解释为 session collision / attempt 到达信号，不解释为成功；
- 如果下一次 gate-only live 在三次 pump 后仍无 71 字节 ACK-like，剩余差异将进一步集中
  到重复 local proxy bootstrap / proxy fd session / `data_buf[224]` / KCP thread-list
  附着，而不是继续换 authBuffer 组合。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_probe_can_pump_auth_head_from_official_trace \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_accepts_71_byte_ack_like_gate \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v

Ran 4 tests
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK
```

下一步仍只允许围绕 AUTH gate：

```text
三次 AUTH_HEAD pump
-> same-fd 71-byte ACK-like
-> AUTH_DATA
```

不拿到 71 字节 ACK-like，不进入 `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT`。

## 70. 2026-07-04：字段级脱敏 diff 发现官方 AUTH_HEAD 的 KCP len=0，Python preflight 已修正

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。只用
现有 fresh JSONL 的完整 hex 在内存里做字段级对照，输出仍保持脱敏。

新增信息：

- 第 65 节 fresh trace 的官方 `AUTH_HEAD` 虽然 wire 长度是 199 字节，但 KCP header
  的 declared `len` 字段是 `0`；
- 178 字节 ZTEC auth head 没有作为 KCP declared payload，而是紧跟 21 字节 KCP header
  追加在 tail/rest：
  - `wireLen=199`
  - `declaredLen=0`
  - `declaredPayloadBytes=0`
  - `tailBytesAfterDeclaredPayload=178`
  - `authBytesPlacement=tail_after_zero_declared_len`
- 官方 `AUTH_DATA` 同理：
  - `wireLen=241`
  - `declaredLen=0`
  - `tailBytesAfterDeclaredPayload=220`
- 这解释了为什么“199 字节长度对齐”不够：旧 Python preflight 把 auth bytes 写进
  KCP declared payload，`len=178/220`，wire 长度可以一样，但 KCP header 字段不同。

本轮脱敏 diff 还确认：

- 官方 `AUTH_HEAD` 的 ZTEC header 字段：
  - `headerLenField=172`
  - `authHeadLenFromHeader=178`
  - `bufferType=101`
  - `authDataLenField=220`
  - `opentelemetry=true`
- 官方 ACK-like 前的两轮 local proxy `send len=160` body 并非完全相同：
  - 比较 156 字节 body，`differingBytes=18`；
  - 差异集中在三个脱敏区间：
    - body offset `2..2`，对应 frame offset `6..6`，1 字节；
    - body offset `137..152`，对应 frame offset `141..156`，16 字节；
    - body offset `155..155`，对应 frame offset `159..159`，1 字节；
  - body offset `137..152` 两轮均分类为 `ascii-hex` 区域，但不输出具体字节；
  - 因此不能把 160 字节本地帧简单视为固定 readiness 包；
  - 仍需继续判断这些差异字段是否对应 `data_buf[224]`、proxy fd session 或 KCP
    thread-list 状态。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `build_kcp_auth_segment()` 新增 `declare_payload_len`；
  - `build_kcp_auth_preflight_from_buffer()` 默认按官方形态构造：
    `declared_len=0`，auth bytes 放在 KCP header 后的 tail/rest；
  - 新增 `redacted_kcp_auth_wire_summary()`；
  - 新增 `auth_gate_field_diff_from_trace()`：
    - 读取 fresh JSONL full hex 只用于内存解析；
    - 输出官方 160/4 local proxy、199 AUTH_HEAD、241 AUTH_DATA 的脱敏字段；
    - 可对比 Python 生成的 AUTH_HEAD/AUTH_DATA wire summary；
    - 不输出 token、auth payload、local proxy body 明文。
  - live/report 的 `authPreflight` 新增 `authHeadWire`、`authDataWire` 脱敏字段。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail`；
  - 更新 preflight 相关测试，从旧的 KCP declared payload 断言改为官方 tail/rest 形态；
  - 保留低层 `build_kcp_auth_segment()` 的 declared-payload codec 测试，用来证明旧形态会被
    diff 标出来。

阶段判断：

- 这是 fresh evidence 明确证伪旧 Python wire 形态，不是盲改 type/link/bind；
- Python 现在更接近官方 `199 -> 71 -> 241` gate：
  - 三次 AUTH_HEAD pump 已对齐；
  - KCP `declaredLen=0` + auth tail/rest placement 已对齐；
  - 仍未 live 证明同 fd 71 字节 ACK-like；
- 在拿到 71 字节 ACK-like 前，仍冻结 `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟
  verified-run`。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_kcp_auth_preflight_from_ztec_buffer \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_accepts_71_byte_ack_like_gate -v

Ran 3 tests
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 108 tests
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK
```

下一步仍是 TF-0063：

1. 继续用 fresh JSONL 对比两轮 160 字节 local proxy body 的脱敏结构差异；
2. 判断 18 个差异字节是否能映射到官方 local proxy protocol header / proxy fd session /
   `data_buf[224]` 写入；
3. 只有这个问题收敛后，才值得再跑一次 gate-only live。

## 71. 2026-07-05：160-byte local proxy diff 已映射到 IDA outband read 阶段，尾部 17 字节仍未恢复

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。继续
只使用现有 fresh JSONL 和既有 IDA/snippet，目标是回答第 70 节遗留问题：两轮
`send len=160` 的 18 个差异字节到底是否已经能映射到官方 local proxy / outband
header 生命周期。

新增信息：

- 第 70 节三段差异现在可按 IDA 已恢复 read 阶段脱敏映射：
  - body offset `2..2` / frame offset `6..6`：
    - 落在 `deal_unlinked_outband_head_data()` 的 68-byte USB IPC/outband header
      模型内；
    - 对应 `data_buf[106..106]`；
    - 证据链是 `deal_unlinked_unknown_local_data()` 先读 4 字节 proxy header 到
      `data_buf[216..219]`，随后 `deal_unlinked_outband_head_data()` 从
      `data_buf[proxy_incoming_header_pos + 100]` 继续读到 position `0x44`。
  - body offset `137..152` / frame offset `141..156`：
    - 仍是两轮均为 `ascii-hex` 的 16 字节区域；
    - 但它已经超出当前 IDA snippet 已确认的 116-byte outband local header 模型；
    - 不能再把它直接解释成已恢复的 `trace_id/span_id`，除非新增 xref 或 fresh trace
      hook 证明谁读取这段尾部。
  - body offset `155..155` / frame offset `159..159`：
    - 同样超出当前 116-byte outband local header 模型；
    - 只能暂标为未映射 tail/second-level local-proxy material。
- 继续查既有 IDA snippet 后，找到一个最近候选链路，但证据还没有闭合：
  - `send_tunnel_add_link()` 在调用 `send_tunnel_link_message()` 前会复制 OpenTelemetry
    trace 信息、调用 `set_sock_bw_ctrl_type()`；
  - `send_tunnel_link_message()` 会构造 command-26 本地 buffer，并通过
    `proxy_data_write()` 或 `spice_session_write_port_data()` 发送；
  - 但当前反编译显示的内部 buffer 是 158 字节，而 fresh trace 本地帧是 160 字节且
    `u16BodyLen=156`，存在 2 字节 envelope/header 差异；
  - 因此它只能作为 `body[137:152]` / `body[155]` 的候选生产者或后继链路，不能直接
    宣称尾部字段已恢复。
- 本轮进一步把这个候选链路降级为“非直接生产者”：
  - fresh trace 的公开 header shape 是：
    `commandByte=26`、`channelOrIdByte=0`、`lenAtOffset2=156`、`wireLen=160`；
  - `send_tunnel_link_message()` 的反编译 shape 是：
    `data[0]=26`、`data[1]=id`、`data[2:4]=154`、write `158` bytes；
  - 因此，除非 `proxy_data_write()` / `spice_session_write_port_data()` 另有 wrapper 或
    transform，否则 `send_tunnel_link_message()` 本体不是这两轮 bootstrap 160-byte
    frame 的直接生产者；
  - 现有 IDA JSON 没有 `proxy_data_write()`、`QUIC_proxy_data_write()` 或
    `spice_session_write_port_data()` 的完整反编译体，无法继续静态闭合 wrapper 行为。
- 这说明第 68 节 Python 失败剩余差异不是“160 字节 readiness 包没有发”，而是：
  官方 local proxy frame 至少有一个尚未恢复的尾部/二级结构参与 bootstrap cycle；
  Python 目前没有任何对应的 local proxy fd-session / outband header / tail material
  状态写入。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `_local_proxy_body_offset_evidence()`；
  - `auth_gate_field_diff_from_trace()` 的
    `official.localProxyFirstTwoBodyDiff` 新增 `differingOffsetEvidence`；
  - `localProxyCycles[].clientSend.frameHeader` 新增 `commandByte`、
    `channelOrIdByte`、`lenAtOffset2`、`commandByteSchemaMatches`、
    `sendTunnelLinkMessageDirectShapeExcluded`；
  - 输出只包含 offset、IDA read stage、`data_buf` 范围、候选语义和证据来源；
  - 对未映射尾部新增 `send_tunnel_add_link()` / `send_tunnel_link_message()` 候选证据，
    同时明确 `158` vs `160/u16BodyLen=156` 尚未闭合；
  - 不输出 local proxy body、auth payload、token、connectStr、accessToken、cpsid、
    密码或 JWT。
- `cmcc_cloud_alive/zime_probe.py`
  - `_redacted_frame_header()` 同步新增 command-byte schema 字段；
  - `authGateReplayGap.localProxyBootstrapSchema` 现在也能明确排除
    `send_tunnel_link_message()` 的直接 158-byte shape。
- `tests/test_python_modules.py`
  - 扩展 `test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail`；
  - 断言 body `2..2` 映射到 `deal_unlinked_outband_head_data` / `data_buf[106..106]`；
  - 断言 body `137..152` 和 `155..155` 仍为
    `beyond_recovered_116_byte_outband_local_header`，避免后续误判为已恢复字段。
  - 扩展 `test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response`；
  - 断言 fresh-style 160-byte local frame 的 `channelOrIdByte=0`、
    `lenAtOffset2=156`、`sendTunnelLinkMessageDirectShapeExcluded=true`。

阶段判断：

- 本轮新增变量来自 fresh trace 的 160-byte local proxy body diff 和 IDA read offset，
  不是 timeout、端口、type101/type102/link_type/local_bind 盲测；
- 当前不应跑下一次 gate-only live，因为还没有最小 Python bootstrap 可以表达未映射
  17 字节尾部结构；
- 下一步应继续找读取或生成 frame body `137..152` / `155` 的代码路径，优先目标是：
  `check_spice_proxy_protocol_header()`、`ZXMemcpy()` 源结构、
  `proxy_data_write()` / `QUIC_proxy_data_write()` / `spice_session_write_port_data()` 是否重包
  158-byte 内部消息、`ChannelLinkSocketEx` 填充，以及是否存在第二层 local proxy
  frame/tail reader。

验证：

```text
python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail -v

Ran 2 tests
OK

python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest discover -s tests -p 'test_python_*.py' -v
Ran 108 tests
OK
```

当前阶段边界仍不变：

- Python 仍未收到 same-fd 71-byte ACK-like；
- Python 仍未发送被云端承认后的 241-byte `AUTH_DATA`；
- 冻结 `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`；
- TF-0063 继续推进，直到 160-byte local proxy bootstrap 的未映射尾部有足够证据，才跑
  下一次 AUTH gate-only live。

## 72. 2026-07-05：writer 层静态闭合，未发现 158-byte 内部消息到 fresh 160-byte cmd26 frame 的重包路径

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。只基于
既有 IDA 输入和只读 headless 检查补齐第 71 节遗留的 writer 闭合问题：如果
`send_tunnel_link_message()` 生成的是 158-byte command-26 内部消息，后续 writer 是否会把
它重包或 transform 成 fresh trace 的 160-byte cmd26 local proxy bootstrap frame。

新增信息：

- fresh local proxy frame 的公开 shape 仍是：
  `commandByte=26`、`channelOrIdByte=0`、`lenAtOffset2=156`、`wireLen=160`。
- `send_tunnel_link_message()` 的直接 shape 仍是：
  `data[0]=26`、`data[1]=id`、`data[2:4]=154`、write `158` bytes。
- writer 层只读检查结论：
  - `proxy_data_write(data,len)` 透传到 QUIC/KCP/TCP writer 路径；
  - `QUIC_proxy_data_write(data,len)` 透传到 `QUIC_deal_quic_data_send`；
  - `udt_write_data(data,len)` 透传到 `SSL_write` / `ikcp_send`；
  - `send_tcp_data_with_cache(data,len)` 透传到 `send()`；
  - `spice_session_write_port_data()` 会写 `cmd=10` 的 port-channel proxy header，不是
    fresh `cmd=26` bootstrap 形态。
- 因此，当前证据把“writer 把 158-byte command-26 内部消息重包成 fresh 160-byte
  cmd26 frame”这条路径排除；`send_tunnel_link_message()` 仍可作为后继语义候选，但不是
  fresh 160-byte local proxy bootstrap frame 的直接生产者，也没有被已检查 writer 转换成
  该 shape。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `_local_proxy_writer_chain_evidence()`；
  - `auth_gate_field_diff_from_trace()` 的 `official` 输出新增
    `localProxyWriterChainEvidence`；
  - 该字段只记录 writer 名称、透传/重包判断、fresh shape 与下一步静态目标，不记录任何
    local proxy body 或 auth payload。
- `cmcc_cloud_alive/zime_probe.py`
  - `authGateReplayGap` 新增 `localProxyWriterChainEvidence`；
  - 主分析报告和字段 diff 报告现在能一致表达 writer 层排除结论。
- `tests/test_python_modules.py`
  - 扩展 auth focus 和 field diff 测试，断言五个 writer 均不把 command-26 内部消息重包成
    fresh 160-byte frame；
  - 断言 `spice_session_write_port_data()` 是 `cmd=10` port-channel 路径，不是 fresh
    `cmd=26` bootstrap 路径。

阶段判断：

- 本轮新增信息对应官方 trace 的 fresh 160-byte local proxy frame header shape，以及 IDA
  中 writer 层的 `data,len` 传递关系；
- 这不是针对 type101/type102/link_type/local_bind/ztec_prime 的盲测；
- 下一步应继续找 body `137..152` / `155` 的真实读取或生成路径，优先：
  `check_spice_proxy_protocol_header()`、`ZXMemcpy()` 源结构、`ChannelLinkSocketEx` 填充、
  第二层 local proxy frame/tail reader；
- 在最小 bootstrap 字段闭合前，不跑 AUTH gate-only live；在 Python 拿到 same-fd 71-byte
  ACK-like 前，继续冻结 `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 73. 2026-07-05：unlinked outband reader 只闭合到 116 字节，tail 40 字节仍需第二层路径

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。继续
只基于既有 IDA snippets/report 做静态收敛，目标是确认第 72 节之后的
`body[137:152]` / `body[155]` 是否能由已恢复的 unlinked outband reader 解释。

新增信息：

- `deal_unlinked_unknown_local_data()` 先读取 4-byte local proxy protocol header 到
  `data_buf[216..219]`，随后 outband 分支进入 `deal_unlinked_outband_head_data()`。
- `deal_unlinked_outband_head_data()` 只把流位置推进到 `0x44` / 68 字节。
- `deal_unlinked_local_data_read()` 在 `data_buf[224] == 2` 时继续调用
  `deal_unlinked_outband_local_data()`。
- `deal_unlinked_outband_local_data()` 的读取上限是 `proxy_incoming_header_pos < 0x74`，
  并读取到 `116 - proxy_incoming_header_pos`：
  - 覆盖 frame offsets `0..115`；
  - 对应 body offsets `0..111`；
  - 完成后构造 `ChannelLinkSocketEx link_info`、调用 `send_tunnel_add_link()`，然后把
    `data_buf[228]` 重置为 `0`。
- 因此 fresh trace 的 160-byte frame 中 `body[137:152]` / `body[155]` 不在已恢复
  unlinked 116-byte reader 覆盖范围内。
- `init_outband_fd_session_bw_ctrl_link_type()` 会把 `data_buf[118]` 和 `data_buf[151]`
  传给 OpenTelemetry helper，但它们映射到更早的 body 区域，不能解释
  `body[137:152]` / `body[155]`。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `localProxyWriterChainEvidence` 新增 `unlinkedOutbandReaderEvidence`；
  - tail offset evidence 明确记录 unlinked reader 只覆盖 body offsets `0..111`；
  - 明确禁止把 `body[137:152]` / `body[155]` 解释为已恢复 trace/span 或
    `send_tunnel_link_message()` material。
- `cmcc_cloud_alive/zime_probe.py`
  - `authGateReplayGap.localProxyWriterChainEvidence` 同步新增同一 reader-limit evidence。
- `tests/test_python_modules.py`
  - 聚焦测试断言 unlinked reader limit、tail offset 排除和 OpenTelemetry 参数映射边界。

验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail -v

Ran 2 tests
OK
```

阶段判断：

- 本轮新增信息对应官方 trace 的 fresh 160-byte local proxy frame tail offsets，以及 IDA
  unlinked outband reader 的实际读取上限；
- 这不是 type101/type102/link_type/local_bind/ztec_prime 盲测；
- 当前仍不能跑下一次 AUTH gate-only live，因为最小 Python bootstrap 还无法表达 tail
  40 字节的来源或后续消费；
- 下一步应继续找第二层 local proxy frame/tail reader 或更精确的 stream boundary：
  `deal_linked_outband_local_data_read()`、`local_data_tcp_read()` 调度、
  `fd_session_async_read_tcp_data()` 是否一次读入并缓存超出 116 字节的剩余数据，以及
  `ChannelLinkSocketEx` 中两个 `ZXMemcpy()` 的实参来源。

## 74. 2026-07-05：linked outband reader 成为 fresh tail 的候选消费路径，但 bootstrap schema 尚未闭合

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成本地二进制或额外报告。继续
只基于现有 IDA JSON/snippets、符号表和 objdump 做只读静态分析，目标是判断第 73 节
留下的 tail 40 字节是否可能被后续 reader 消费。

新增信息：

- 当前符号表确认两个不同函数：
  - `deal_linked_outband_local_data_read` at `0x155d0b`；
  - `deal_linked_local_data_read` at `0x156297`。
- `local_data_tcp_read()` 的调度规则是：
  - `get_proxy_channel_manage_by_fd()` 还没有 channel manage 时，调用
    `deal_unlinked_local_data_read()`；
  - channel manage 已存在时，调用 `deal_linked_local_data_read()`。
- `deal_linked_local_data_read()` 在 link type `2` 分支会调用
  `deal_linked_outband_local_data_read()`。
- `deal_linked_outband_local_data_read()` 会从 `in_sock + 0x9b0 + PROTOCOL_HEADER_SIZE`
  读取后续 outband payload，然后根据当前路径写入 `QUIC_stream_data_write()`、
  `udt_write_data_stream()`、`spice_session_write_port_data()` 或 `proxy_data_write()`。
- `PROTOCOL_HEADER_SIZE=4`、`SAFETY_MARGIN=24`、`MIN_READ_SIZE=50` 来自现有
  `.rodata` 常量。
- `fd_session_async_read_tcp_data()` 转到 non-SSL/SSL read；non-SSL 路径调用
  `recv()` 时长度是 caller-requested remaining length，并不会有意读超过 caller
  请求长度。

阶段判断：

- 第 73 节的 unlinked 116-byte reader 不会主动消费 160-byte frame 中的 tail 40 字节；
- 这些 tail bytes 可能留在 socket buffer 中，在 `send_tunnel_add_link()` 建立 channel
  manage 后，由 linked outband reader 作为后续 payload 消费；
- 这解释了“fresh local proxy frame wireLen=160，但 unlinked reader 只恢复到 116”的
  一个可行机制；
- 但它仍是 candidate，不是完整 schema：还需要确认同一官方 160-byte local proxy send
  的 tail 40 字节是否确实跨 reader 边界进入 `deal_linked_outband_local_data_read()`，
  以及 linked reader 转发后本地 4-byte ACK-like `recv len=4` 的语义。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `localProxyWriterChainEvidence` 新增 `linkedOutbandTailCandidate`；
  - 字段只记录调度关系、read limit 行为、常量和 candidate 结论，不记录 local proxy
    body 或 auth payload。
- `cmcc_cloud_alive/zime_probe.py`
  - `authGateReplayGap.localProxyWriterChainEvidence` 同步新增
    `linkedOutbandTailCandidate`。
- `tests/test_python_modules.py`
  - 聚焦测试断言 linked reader candidate 存在，但结论仍为 candidate，不是完整
    bootstrap schema。

验证：

```text
python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py
OK

python3 -m unittest \
  tests.test_python_modules.PythonModuleTests.test_zime_probe_auth_head_ack_focus_accepts_same_fd_ack_like_response \
  tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail -v

Ran 2 tests
OK
```

下一步：

- 继续恢复 `deal_linked_outband_local_data_read()` 的高层伪代码字段，尤其是它写出的
  `cmd=10`/proxy payload header 与官方 loopback `recv len=4` 的关系；
- 继续确认 `ChannelLinkSocketEx` 两个 `ZXMemcpy()` 的实参来源；
- 在 linked tail 路径和 4-byte local ACK 语义闭合前，不跑 AUTH gate-only live。

## 75. 2026-07-05：linked reader 转发 shape 与 loopback recv4 负证据补齐

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读原始 ELF `.tmp/ida-inputs/libspice-client-glib-zte-2.0.so.8.5.0` 的符号和
objdump 切片，目标是把第 74 节的 linked tail candidate 细化到转发 shape，并判断
官方 loopback `recv len=4` 是否能直接解释为 cmd26 bootstrap 的 ACK。

新增信息：

- `deal_linked_outband_local_data_read()` 的无带宽限制最大读取长度是
  `0xffff - PROTOCOL_HEADER_SIZE(4) - SAFETY_MARGIN(24) = 65507`，读取 buffer 仍是
  `in_sock + 0x9b0 + 4`。
- linked reader 读到 payload 后存在五类转发 shape：
  - QUIC port-channel：在 `in_sock+0x9b0` 前置 `cmd=10`、channel byte、u16 payload
    length，调用 `QUIC_stream_port_data_write()`，写出 `payloadLen + 4`；
  - QUIC data stream：无 4-byte local proxy header，调用 `QUIC_stream_data_write()`；
  - UDT data stream：无 4-byte local proxy header，调用 `udt_write_data_stream()`；
  - SPICE port-channel：前置同样的 `cmd=10` header，调用
    `spice_session_write_port_data()`；
  - proxy data：前置同样的 `cmd=10` header，调用 `proxy_data_write()`。
- `deal_linked_local_data_read()` 的 link type `1` 分支会先读 4-byte local proxy header
  到 `in_sock+0x184`；header 完整后进入 `deal_local_spice_proxy_head()`。
- `deal_local_spice_proxy_head(cmd=0x1a)` 调用 `deal_local_recved_cmd_link()`，后者在
  `send_tunnel_add_link()` 成功后只用 `send_tcp_data_with_cache(..., len=1)` 回写
  1-byte status。
- 因此官方 trace 的 loopback `send len=160 -> recv len=4` 不能直接解释为 cmd26
  bootstrap handler 的 ACK：已恢复的 cmd26 直接响应长度是 `1`，不是 `4`。
- `recv len=4` 更像 linked/port-channel 路径上的 `cmd=10` local proxy header 小帧，
  但仍需和官方 trace 的方向、fd 对端、读写时序对齐后才能闭合。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `linkedOutbandTailCandidate` 新增 `linkedMaxReadWithoutBwLimit=65507` 和五类
    `linkedForwardingShapes`；
  - 新增 `localRecv4SemanticsEvidence`，明确官方 `recv len=4` 不应当被当成 cmd26
    direct ACK。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步新增上述脱敏 evidence 字段，保证 auth gate replay 输出和 field diff 输出一致。
- `tests/test_python_modules.py`
  - 聚焦测试断言 linked reader 的转发 shape、cmd26 direct response len=1、以及
    official `recv len=4` 仍需方向/peer alignment。

阶段判断：

- 本轮新增信息对应官方 trace 的 loopback `send len=160` / `recv len=4` 字段，以及
  IDA/objdump 中 `deal_linked_outband_local_data_read()`、
  `deal_linked_local_data_read()`、`deal_local_spice_proxy_head()`、
  `deal_local_recved_cmd_link()` 的静态路径；
- 这不是 type101/type102/link_type/local_bind/ztec_prime 盲测；
- 现在可以排除“官方 `recv len=4` 是 cmd26 bootstrap 直接 ACK”这条解释；
- 下一步应继续确认 `ChannelLinkSocketEx` 两个 `ZXMemcpy()` 实参来源，并把官方
  loopback `recv len=4` 与 `cmd=10` header 路径做方向/peer alignment；
- 在 linked tail 路径和 4-byte local header 语义闭合前，仍不跑 AUTH gate-only live；
在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 81. 2026-07-05：fresh cmd26 stream-create gate 条件化

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读本地 ELF 符号表、objdump 小范围反汇编和既有 IDA 报告/snippets，目标是确认
`send_tunnel_add_link()` 中 `handle_quic_protocol_stream_create_processing()` 对 fresh cmd26
成功路径的真实作用。仍不读取或输出 local proxy frame body 明文、auth payload 或任何
token/connect material。

新增信息：

- `handle_quic_protocol_stream_create_processing(in_sock, link_id, channel_link_info)` 不生成
  或改写 `ChannelLinkSocketEx` body 字段；它消费的是 `in_sock`、proxy fd session、
  port-channel 和 QUIC/channel 状态；
- 对 fresh cmd26 来说，硬失败条件主要是：
  - 按 `get_proxy_type_by_link_type(in_sock->data_buf[224])` 找不到对应 proxy fd session；
  - 已进入 QUIC stream 创建分支，但 `QUIC_create_data_stream()` 返回失败；
- 但它在以下情况下也会返回成功，而不是要求无条件创建新 QUIC stream：
  - port-channel related socket check 已成立；
  - proxy fd session 存在但 `check_proxy_is_ready` 等价条件尚未 ready；
  - proxy fd session 没有 KCP/QUIC channel-manage ready state，因此未尝试 stream 创建；
- 只有当 proxy fd session 存在、ready、具备 KCP state，并且 QUIC/channel-ready byte
  置位时，才调用 `QUIC_create_data_stream()`。

对应官方 trace 字段：

- 本轮新增信息对应 fresh official trace 的 local bootstrap cycle：
  - accepted-side `recv len=156` 后进入 `send_tunnel_add_link()`；
  - cmd26 handler 只有在 `send_tunnel_add_link()` 返回非零后，才回写 1-byte status；
  - client-side `recv len=1` status 后，外网 fd 才继续 `AUTH_HEAD len=199`；
- 它解释的是“为什么 156-byte body 合成值之外还需要 proxy/session side effects”，不是
  local proxy body 明文或 auth payload；
- 它不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 在 `freshCmd26MinimalSynthesisSchema.valueSourceStaticEvidence` 中新增
    `streamCreateGateEvidence`；
  - 记录 `handle_quic_protocol_stream_create_processing()` 不合成 body 字段、硬失败条件、
    可无新 QUIC stream 返回成功的条件，以及 Python implication。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence。
- `tests/test_python_modules.py`
  - 断言 stream-create gate 不合成 `ChannelLinkSocketEx` 字段；
  - 断言缺 proxy fd session 和 QUIC stream 创建失败是硬失败；
  - 断言新建 QUIC stream 是条件路径，而不是 fresh cmd26 成功的无条件要求。

阶段判断：

- TF-0063 从“fresh cmd26 link route/KCP 目标来源已静态分离”推进为
  “fresh cmd26 side-effect gate 已条件化，body 值合成仍未闭合”；
- 这削弱了此前“必须创建 QUIC stream 才能继续”的过强表述：Python 最小 bootstrap
  仍必须复现或等价建模 proxy fd/session side effects，但新 QUIC stream creation 是否
  必须发生取决于 proxy/KCP/QUIC ready state；
- 仍未闭合：
  - fresh 160-byte body 中 `ChannelLinkSocketEx.info.dest_ip/dest_port/channel_type_id`
    的 Python 合成值；
  - `serial_num/vm_uuid/otlp_trace_id/otlp_parent_id` 的本地生成/官方脱敏推导边界；
  - Python-only 等价 state model 是否足以让外网 AUTH gate 返回 same-fd 71-byte ACK-like；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 90. 2026-07-05：尾部同步锚点

当前文件存在历史编号乱序：第 89 节已经记录本轮实质进展，但落在第 88 节之前的中部位置，
不是文件尾部。本节作为尾部锚点，不新增协议证据，只说明当前有效状态：

- 本轮新增代码和测试见第 89 节：
  `pre_auth_fresh_cmd26_bootstrap` 已接入本地 gate-only 合同；
- 验证结果仍是：
  `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过，`python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，111 个测试 OK；
- task-forest 已同步 TF-0063 和 TF-0061，并完成 `validate/export`；
- 未运行 live、未操作 GUI/CrossDesk、未读取或输出敏感明文；
- 仍未获得 Python live same-fd 71-byte ACK-like，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 92. 2026-07-05：pre-AUTH local proxy/session state contract 落地

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何
token/connect material，也没有生成额外报告或本地二进制。目标是在第 89 节已接入
fresh cmd26 frame 形态后，把仍未闭合的 native side effects 转成 Python 报告里的本地
readiness contract。当前文件仍存在历史编号乱序，本节作为真实尾部续接锚点。

新增信息：

- `run_kcp_auth_sync_probe()` 新增默认关闭的 `pre_auth_session_state_model`：
  - 它不发送新网络包，只把 AUTH_HEAD 前必须满足的 native side effects 映射成
    redacted required checks；
  - required checks 包括：
    `fresh_cmd26_status`、`type6_proxy_fd_session_slot`、`proxy_sock_udp_gate`、
    `init_local_rw_sock_pair_udp_kcp_attachment`、
    `quic_channel_manage_ready_or_bypassed`；
  - 只有这些 required checks 全部 modeled 时，`preAuthSessionState.readyForGateOnlyLive`
    才为 true；
  - 这仍只是本地 readiness contract，不是 cloud ACK-like proof，不允许推进
    SYNACK/native bridge/DISPLAY_INIT。
- `run_kcp_auth_sync_probe_from_cag_material()` 同步透传该参数，使 fresh CAG material
  gate-only 路线可以在本地 fake-server 测试中表达：
  `fresh cmd26 send160/status1 + session state contract closed -> AUTH_HEAD199 ->
  ACK-like71 -> AUTH_DATA241 -> stop`。
- `officialParityAssessment` 现在区分：
  - frame 形态 modeled；
  - 1-byte cmd26 status modeled；
  - local proxy/session state contract closed；
  - 仍需要后续 gate-only live 才能证明云端 same-fd 71-byte ACK-like 接受。

对应官方 trace 字段：

- `fresh_cmd26_status` 对应 client-side `recv len=1 cmd26 status`；
- `type6_proxy_fd_session_slot` 对应 loopback `send len=160 cmd26` 与 accepted-side
  `recv len=156 ChannelLinkSocketEx body` 触发的 local proxy/session 建立；
- `proxy_sock_udp_gate` 对应 `AUTH_HEAD len=199 follows local proxy/session setup`；
- `init_local_rw_sock_pair_udp_kcp_attachment` 对应同一外网 fd 在 ACK-like 前 pump
  `AUTH_HEAD len=199`；
- `quic_channel_manage_ready_or_bypassed` 对应 “AUTH_HEAD 在 cmd26 status 后发生，但
  stream creation 本身不是 AUTH gate 成功信号” 这一边界。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `_summarize_pre_auth_session_state_model()`；
  - `run_kcp_auth_sync_probe()` 增加 `preAuthSessionState` report 字段和
    `preAuthSessionStateContractClosed` lifecycle 字段；
  - `run_kcp_auth_sync_probe_from_cag_material()` 透传
    `pre_auth_session_state_model`；
  - `officialParityAssessment.nativeSideEffectBoundary` 在 state contract closed 时明确说明
    “仍需 gate-only live 证明 cloud ACK-like acceptance”。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_sync_from_cag_material_can_model_pre_auth_cmd26_bootstrap`；
  - 断言 state contract 全部 required checks 为 modeled、`readyForGateOnlyLive=true`；
  - 断言仍停在 `AUTH_HEAD -> AUTH_DATA`，不发送 SYN；
  - 断言 report 不存储 payload、auth material 或 CAG UDP target 明文。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_can_model_pre_auth_cmd26_bootstrap -v`
  通过。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run；
- TF-0063 可从“frame 形态已接入本地合同”推进为“pre-AUTH frame + session state
  readiness contract 已本地闭合，仍缺 gate-only live 云端确认”；
- 下一步应先跑全量单测和 task-forest 同步；之后如继续推进，只能考虑是否已经满足
  gate-only live 前置条件，验收仍只看 Python same-fd 71-byte ACK-like 后发送 241-byte
  AUTH_DATA。

## 89. 2026-07-05：pre-AUTH fresh cmd26 bootstrap 合同接入 gate-only 本地测试

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何
token/connect material，也没有生成额外报告或本地二进制。续接核对发现当前文档存在
编号乱序：第 88 节已落盘但位于文件中部，文件尾部仍停在第 87 节；本轮不重排历史
段落，只在尾部追加第 89 节作为新的同步锚点。

新增信息：

- `run_kcp_auth_sync_probe()` 新增默认关闭的
  `pre_auth_fresh_cmd26_bootstrap` 注入点：
  - 本地 TCP fake/local proxy 上先发送 fresh cmd26 frame；
  - frame 由 `build_fresh_cmd26_bootstrap_frame()` 生成，wireLen `160`，
    header 为 `cmd=0x1a`、`channel/id=0`、`u16BodyLen=156`；
  - 只等待并记录 1-byte status 长度，不记录 status byte；
  - report 只保存 redacted `frameSummary`/`builderSummary`，不保存 frame body、dest IP
    或 dest port。
- `run_kcp_auth_sync_probe_from_cag_material()` 透传该注入点，使 fresh CAG material
  路线可以在本地合同测试中覆盖完整 gate-only 顺序：
  `fresh cmd26 send160 -> status1 -> AUTH_HEAD len=199 -> same-fd 71-byte ACK-like
  -> AUTH_DATA len=241 -> stop`。
- `officialParityAssessment.modeledByPython` 现在能区分：
  - Python 已建模 pre-AUTH fresh cmd26 frame 形态；
  - 若本地 fake proxy 回 1-byte status，则记录 status gate；
  - 但 `nativeSideEffectBoundary` 明确声明这仍不等同于 native proxy fd/session、UDP
    gate、KCP attachment 或 QUIC/channel manage side effects。

对应官方 trace 字段：

- 本轮新增合同正向对应 fresh official trace 的：
  - loopback client `send len=160 cmd26`；
  - accepted-side `recv len=156 ChannelLinkSocketEx body`；
  - client-side `recv len=1 cmd26 status`；
  - 外网 fd 随后 `AUTH_HEAD len=199`，收到 same-fd 71-byte ACK-like 后发送
    `AUTH_DATA len=241`。
- 本轮只跑本地 fake TCP/UDP 合同测试，没有跑 live AUTH gate，因此没有新增云端
  same-fd 71-byte ACK-like 证据。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `_run_pre_auth_fresh_cmd26_bootstrap()`；
  - `run_kcp_auth_sync_probe()` 新增 `preAuthLocalBootstrap` report 字段和
    `freshCmd26LocalBootstrap*` lifecycle 字段；
  - `run_kcp_auth_sync_probe_from_cag_material()` 透传
    `pre_auth_fresh_cmd26_bootstrap`。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_sync_from_cag_material_can_model_pre_auth_cmd26_bootstrap`；
  - 本地 TCP fake proxy 断言收到 160-byte cmd26 header；
  - 本地 UDP fake server 断言随后收到 199-byte AUTH_HEAD 和 241-byte AUTH_DATA；
  - 断言 auth-gate-only 下仍不发送 SYN，不推进 SYNACK/native bridge/DISPLAY_INIT；
  - 断言 report 不包含 auth material 明文，也不包含 CAG UDP target 明文。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_can_model_pre_auth_cmd26_bootstrap -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，111 个测试 OK。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run；
- TF-0063 应从“Python builder 已落地”推进为“pre-AUTH fresh cmd26 frame 形态已接入
  gate-only 本地合同，native side effects 仍未闭合”；
- TF-0061 的下一步仍是 local proxy/session bootstrap state materialization：确认
  type6 proxy fd session slot、proxy_sock byte `0x2d` UDP gate、`init_local_rw_sock_pair_udp`
  KCP attachment 和 QUIC/channel manage 是否必须由 Python 显式建模；
- 只有 local bootstrap/state model 闭合后，才考虑下一次 AUTH gate-only live；验收仍只看
  Python 在 same fd 收到 71-byte ACK-like 后发送 241-byte AUTH_DATA。

## 88. 2026-07-05：fresh cmd26 Python bootstrap builder 落地

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何
token/connect material，也没有生成额外报告或本地二进制。目标是把第 87 节恢复出的
`add_link_to_proxy_by_socket()` producer-side source class 转成 Python 可测试 builder。
这仍是本地模型进展，不是云端 ACK 成功证据。

新增信息：

- Python 侧新增 `build_fresh_cmd26_bootstrap_frame()`：
  - 生成 wireLen `160` 的 fresh cmd26 frame；
  - header 固定为 `cmd=0x1a`、`channel/id=0`、`u16BodyLen=156`；
  - body 按 `ChannelLinkSocketEx` offset 写入；
  - IPv4 `dest_ip` 复用现有 `ipv4_to_little_endian()`，对应 native producer 的
    `inet_addr()` + `ntohl()` 写入方式；
  - IPv6 写入 `body[8:24]`，对应 `inet_pton(AF_INET6)` 分支；
  - `channel_type_id` 按 `(channel_type << 8) | channel_id` 合成，默认候选仍是
    `MAIN/0 = 0x0100`，但不称为 official-confirmed；
  - `trace_id`/`parent_id` 只作为 Python 生成的结构化候选写入，不读取 official local
    proxy body 明文。
- Python 侧新增 `summarize_fresh_cmd26_bootstrap_frame()`：
  - 只输出结构摘要和 channel type/id；
  - 不输出 body 明文、目标 IP 或端口；
  - `payloadStoredInReport=false`。

对应官方 trace 字段：

- 本轮 builder 对应 fresh official trace 的 local bootstrap 字段：
  - loopback client `send len=160 cmd26`；
  - accepted-side `recv len=156 ChannelLinkSocketEx body`；
  - client-side `recv len=1 cmd26 status`；
  - external `AUTH_HEAD len=199` 跟随 local proxy/session setup。
- 本轮没有跑 AUTH gate-only live，因此没有新增 same-fd 71-byte ACK-like 证据。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 fresh cmd26 offset/length 常量；
  - 新增 `build_fresh_cmd26_bootstrap_frame()`；
  - 新增 `summarize_fresh_cmd26_bootstrap_frame()`；
  - builder summary 脱敏，不保存 destination 明文。
- `tests/test_python_modules.py`
  - 新增 `test_rap_zime_builds_fresh_cmd26_bootstrap_frame_redacted`；
  - 新增 `test_rap_zime_builds_fresh_cmd26_bootstrap_frame_ipv6_and_bounds`；
  - 断言 header、body offset、IPv4 little-endian storage、IPv6 storage、OTLP NUL tail、
    `channel_type_id`、bounds error，以及 summary 不包含 IP/端口明文。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_fresh_cmd26_bootstrap_frame_redacted tests.test_python_modules.PythonModuleTests.test_rap_zime_builds_fresh_cmd26_bootstrap_frame_ipv6_and_bounds -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，110 个测试 OK。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT；
- TF-0063 从“producer-side body 合成路径已闭合”推进为“Python builder 已能生成
  fresh cmd26 bootstrap frame，仍缺 session side-effect integration”；
- 下一步应把 builder 接入 gate-only runner 的 pre-AUTH local bootstrap 设计或 fake
  server 合同中，继续确认 type6 proxy fd session slot、UDP gate、KCP attach 和
  QUIC/channel manage side effects 哪些必须显式建模；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 84. 2026-07-05：fresh body 字段值合成边界与后继 link message 分层

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读既有 ELF 反汇编和脱敏 IDA/snippet evidence，目标是把第 83 节后的
`ChannelLinkSocketEx` 字段值合成问题继续拆分成“fresh input 必须建模”和“后继
link message 派生输出不能反用”的两层。仍不读取或输出 local proxy frame body 明文、
auth payload 或任何 token/connect material。

新增信息：

- `send_tunnel_add_link()` 成功路径实际会把 fresh `ChannelLinkSocketEx` input 的这些
  字段复制或消费到 `ProxyChannelManage` / session state：
  - `body[0:2] dest_port`、`body[2] link_priority`、`body[3] link_type`；
  - `body[4:8] dest_ip` 或 `body[8:24] ipv6`；
  - `body[83] flag`、`body[84:88] channel_type`；
  - `body[104:136] otlp_trace_id`、`body[137:153] otlp_parent_id`；
  - `body[154:156] channel_type_id`，继续派生 channel type/id、stream metadata、
    bandwidth control 和 port-channel 决策。
- `send_tunnel_add_link()` 在调用 `send_tunnel_link_message()` 之前没有把 fresh input 的
  `body[24:40] serial_num` 或 `body[40:77] vm_uuid` 复制到
  `ProxyChannelManage`；这两个字段目前没有证据显示是 fresh cmd26 成功路径的关键输入。
- `send_tunnel_link_message()` 是后继派生输出而不是 fresh input 生产者：
  - 它构造后继内部 cmd26：`data[0]=26`、`data[1]=virtual_channel_id`、
    `data[2:4]=154`、`writeLen=158`；
  - 对 `sock_link_type=1`，它把后继输出 body 的 `serial_num` 由
    `spice_processtrack_get_serial_num()` 重新生成，而不是从 fresh input body 拷贝；
  - `deal_bw_ctrl_sock_link_message()` 可能根据 `in_sock->data_buf[238]`、session
    `bw_ctrl_cfg` 和 thread bandwidth state 派生 `bw_ctrl/tbw_ctrl/link_type`；
  - `vm_uuid` 只在后继 message 的 emergency branch 中看到来自 `s->vmid` 的复制。
- CAG auth buffer 侧也有独立生成点：
  - `deal_udt_using_cag()` 把 process-track serial 写入 auth buffer serial 区；
  - OpenTelemetry trace/span 来自全局 `g_otlp_trace_id/g_otlp_parent_id`；
  - 这说明 Python 可以生成结构合法的非敏感 trace/span 候选，但当前仍不能证明
    fresh cmd26 input 的 exact OTLP 值是否必须与官方全局值一致。

对应官方 trace 字段：

- 本轮新增信息对应 fresh official trace 的 local bootstrap 到外网 AUTH gate 顺序：
  - loopback client `send len=160`，cmd26；
  - accepted-side `recv len=156`，`ChannelLinkSocketEx` body；
  - client-side `recv len=1`，cmd26 status；
  - 外网 fd 随后发送 `AUTH_HEAD len=199`。
- 它解释的是 fresh input body 与后继 internal link message 的分层，不读取或输出
  local proxy frame body 明文/auth payload。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 在 `freshCmd26MinimalSynthesisSchema.valueSourceStaticEvidence` 中新增
    `freshBodyValueSynthesisBoundaries`；
  - 记录 `send_tunnel_add_link()` 实际复制/消费哪些 fresh input 字段、哪些字段未被复制，
    以及 `send_tunnel_link_message()` 后继派生规则不能反用为 fresh input 生成器。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence。
- `tests/test_python_modules.py`
  - 新增断言覆盖 fresh input 字段复制集合、`serial_num/vm_uuid` 未复制边界、
    后继 158-byte link message serial 生成来源、OTLP/auth 关系和 payload 不落盘约束。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT；
- TF-0063 从“type6 proxy fd/session 与 UDP/KCP pair side effects 已收窄”推进为
  “fresh body 字段值合成边界已分层，但 exact 值规则仍未闭合”；
- 下一步应继续确认 `dest_ip/dest_port/channel_type_id` 的最小可合成值，以及
  `serial_num/vm_uuid` 是否可在 fresh input body 中置零或本地生成；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 82. 2026-07-05：第 81 节后验证与任务同步锚点

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
遵守当前 gate：`local proxy/session bootstrap -> AUTH_HEAD len=199 -> same-fd
71-byte ACK-like -> AUTH_DATA len=241`。在 Python same-fd 71-byte ACK-like 前，继续冻结
`AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

工作区核对：

- `docs/delivery-handoff.md` 当前已有第 80 节和第 81 节，但它们位于第 75 节之后、
  第 76 节之前；本轮不重排历史内容，改用第 82 节在文件尾部补一个新的同步锚点；
- 第 80 节已记录 fresh cmd26 `link_type=1 -> proxy_type_ex=6`，并排除
  `proxy_type_ex=5` 作为 fresh cmd26 route；
- 第 81 节已记录 `handle_quic_protocol_stream_create_processing()` 不合成
  `ChannelLinkSocketEx` 字段，只按 proxy/session/QUIC/channel state 对 fresh cmd26
  成功路径做条件化 gate。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，108 个测试 OK。

阶段判断：

- 当前新增信息对应官方 trace 的 local bootstrap 字段：loopback client `send len=160`、
  accepted-side `recv len=156`、client-side `recv len=1` status，以及随后外网
  `AUTH_HEAD len=199`；
- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有读取或输出
  local proxy frame body 明文/auth payload；
- TF-0063 的下一步仍是恢复 fresh 160-byte body 的字段值合成规则和
  proxy fd/session、QUIC/channel manage 等价 state model；
- TF-0061 的下一步仍是 local proxy/session bootstrap 闭合，而不是 199-byte
  AUTH_HEAD 长度对齐。

## 83. 2026-07-05：type6 proxy fd session 与 UDP/KCP pair side effect 收窄

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读本地 ELF 反汇编和既有脱敏 evidence，目标是收窄第 81/82 节仍未闭合的
proxy fd/session 等价 state model。仍不读取或输出 local proxy frame body 明文、auth
payload 或任何 token/connect material。

新增信息：

- `deal_create_proxy_fd_session(fd_type_ex=6)` 对 fresh cmd26 的 type6 route 保持
  `link_type=1`，并把创建出的 proxy fd session 存到 thread/session 的 type6 slot；
- 该函数还把若干后续 gate 需要的 side effects 写入 proxy sock：
  - proxy sock byte `0x2d` 来自 `spice_session_get_network_protocol_type()!=0`，并决定是否
    创建 UDP proxy fd session；
  - proxy sock byte `0x68` 记录 `spice_session_get_client_type()==1`；
  - proxy sock word `0x18c` 写入 fresh route 的 `link_type=1`；
  - proxy sock dword `0x24` 写入 `fd_type_ex=6`。
- `init_local_rw_sock_pair()` 不直接合成 `ChannelLinkSocketEx` 字段；它会按
  `in_sock->data_buf[224]` 调 `get_proxy_type_by_link_type()` 查找 proxy fd session：
  - 缺 proxy fd session 时设置 fd-session error flag，并在 UDP/KCP pair 前停止；
  - 只有 proxy sock byte `0x2d` 为真时，才进入 `init_local_rw_sock_pair_udp()`；
  - 否则只把 `in_sock` 与 proxy fd session 直接配对。
- `init_local_rw_sock_pair_udp()` 的关键 side effects 是：
  - 创建 `TN_UDP_CLD_SOCK` fd session；
  - 把 proxy sock word `0x18c`、byte `0x2d`、byte `0x60` 复制到 udp sock；
  - 在创建 KCP 前建立 `in_sock` 与 udp sock 的 pair；
  - `create_udt_session()` 使用 `get_proxy_kcp_dst_ip/port()` 输出、proxy sock
    `fd_type_ex`、UDP fd 和 type6 boolean；
  - `deal_udt_using_cag()` 只在 KCP 已挂回 fd-session 状态后运行。

对应官方 trace 字段：

- 本轮新增信息对应 fresh official trace 的 local bootstrap 到外网 AUTH gate 顺序：
  - loopback client `send len=160`，cmd26；
  - accepted-side `recv len=156`，`ChannelLinkSocketEx` body；
  - client-side `recv len=1`，cmd26 status；
  - 外网 fd 随后发送 `AUTH_HEAD len=199`；
- 它解释的是 AUTH_HEAD 前官方本地 session side effects，而不是 local proxy frame body
  明文或 auth payload。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 在 `freshCmd26MinimalSynthesisSchema.valueSourceStaticEvidence` 中新增
    `localSessionBootstrapSideEffects`；
  - 记录 type6 proxy fd session slot、proxy sock byte `0x2d` UDP gate、
    `init_local_rw_sock_pair()` 查 session gate、以及 `init_local_rw_sock_pair_udp()` 的
    KCP attach 前置 side effects。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence。
- `tests/test_python_modules.py`
  - 新增断言覆盖 type6 route、proxy sock `0x2d`、UDP pair 字段拷贝、KCP attach 时序，
    以及不存储 payload 的约束。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT；
- TF-0063 从“stream-create gate 条件化”推进为“type6 proxy fd/session 与 UDP/KCP
  pair side effects 已收窄”，但仍未闭合 fresh body 字段值合成；
- 下一步应继续恢复 `ChannelLinkSocketEx.info.dest_ip/dest_port/channel_type_id` 的
  Python 合成值，以及 `serial_num/vm_uuid/otlp_trace_id/otlp_parent_id` 的生成边界；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 80. 2026-07-05：fresh cmd26 link route 与 KCP 目标来源静态分离

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读既有 IDA 静态报告和 snippets，目标是把第 79 节已闭合的
`ChannelLinkSocketEx` 布局继续推进到字段值来源/会话 side effect 分类。仍不读取或输出
local proxy frame body 明文、auth payload 或任何 token/connect material。

新增信息：

- fresh cmd26 被 `check_spice_proxy_protocol_header()` 接受后，官方路径先把
  `in_sock->data_buf[224]` 设为 link type `1`，再进入
  `deal_local_link_proxy_create()`；
- 对 fresh cmd26 来说，`get_proxy_type_by_link_type(session, 1)` 的静态结果是
  `proxy_type_ex=6`，因为该 helper 只有在 `link_type==2` 且 RAP/downward-bw-control
  条件允许时才返回 `5`；
- 因此 `proxy_type_ex=5` 是 outband/link type 2 条件，不是当前 fresh cmd26 的直接
  bootstrap route；
- `deal_create_proxy_fd_session(fd_type_ex=6)` 保持默认 `link_type=1`，并写入
  `proxy_sock->data_buf[224]=1`；这解释了 fresh cmd26 路径中 proxy sock link flag 的
  静态来源；
- `init_local_rw_sock_pair_udp()` 的 KCP 目标来自
  `get_proxy_kcp_dst_ip()/get_proxy_kcp_dst_port(session, proxy_sock->cag_client_key)`：
  - 非 `TN_MULTI_TCP_SOCK` 且 CAG 启用时，目标来源类别是 `ag_ip/ag_port`；
  - 非 `TN_MULTI_TCP_SOCK` 且非 CAG 时，目标来源类别是 `host/get_spice_proxy_dst_port`；
  - `TN_MULTI_TCP_SOCK` 且 CAG 启用时，目标来源类别是 `ag_ip/ag_port`；
  - `TN_MULTI_TCP_SOCK` 且非 CAG 时，目标来源类别是 `vm_ip/vm_proxy_port`，但 `ice`
    例外使用 `host/vm_proxy_port`；
- 这再次区分了三层目标：
  - `ChannelLinkSocketEx.info.dest_ip/dest_port`：复制到 `ProxyChannelManage.link_info`；
  - KCP socket 目标：由 `get_proxy_kcp_dst_ip()/port()` 选择；
  - CAG type101/type102 auth buffer 内部目标：由 `deal_udt_using_cag*()` 另行写入。

对应官方 trace 字段：

- 本轮新增信息对应 fresh official trace 的 local bootstrap 到外网 AUTH gate 顺序：
  - loopback client `send len=160`，cmd26；
  - accepted-side `recv len=156`，`ChannelLinkSocketEx` body；
  - external fd 随后发送 `AUTH_HEAD len=199`；
- 它没有使用或输出 local proxy body 字节，只解释 accepted cmd26 的 link route 与 KCP
  目标来源类别；
- 它不是 `type101/type102/link_type/local_bind/ztec_prime` live 盲测。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `freshCmd26MinimalSynthesisSchema` 新增 `valueSourceStaticEvidence`；
  - 记录 fresh cmd26 的 `link_type=1 -> proxy_type_ex=6 -> proxy_sock link_type=1`
    route；
  - 记录 KCP 目标来源类别，并明确 `ChannelLinkSocketEx.dest_*` 不是 KCP socket 目标、
    也不是 auth buffer 内部目标。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence，保持 analyzer 输出和 `rap_zime` 摘要一致。
- `tests/test_python_modules.py`
  - 断言 fresh cmd26 排除 `proxy_type_ex=5` outband 条件；
  - 断言 KCP 目标来源类别；
  - 断言 `ChannelLinkSocketEx.dest_*` 与 KCP/auth 目标分层。

阶段判断：

- TF-0063 从“wire body layout 已闭合，字段值合成规则未闭合”推进为
  “fresh cmd26 link route/KCP 目标来源已静态分离，body 字段值合成仍未闭合”；
- 已闭合：
  - fresh cmd26 不是 `link_type=2` outband route；
  - fresh cmd26 的 proxy route 是 `proxy_type_ex=6`；
  - KCP 目标来源类别不能直接等同于 `ChannelLinkSocketEx.info.dest_ip/dest_port`；
- 仍未闭合：
  - fresh 160-byte body 中 `ChannelLinkSocketEx.info.dest_ip/dest_port/channel_type_id`
    的 Python 合成值；
  - `serial_num/vm_uuid/otlp_trace_id/otlp_parent_id` 哪些可本地生成，哪些必须从官方
    trace 脱敏推导；
  - Python-only 等价 state model 是否足以获得外网 same-fd 71-byte ACK-like；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 76. 2026-07-05：fresh cmd26 header 方向闭合与 ChannelLinkSocketEx memcpy 来源补齐

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读既有 IDA/headless 证据和 fresh auth-focus trace 的脱敏事件字段，目标是补齐第 75
节仍悬空的方向语义，并确认两个 `ZXMemcpy()` 的 source offset 是否解释
`body[137:152]` / `body[155]`。

新增信息：

- `check_spice_proxy_protocol_header()` 接受 command byte `0x1a`、`0x0a`、`0x2a`。
  fresh 160-byte local proxy frame 的 command byte 是 `0x1a`，因此 header 被接受后仍走
  type/linkType `1`，首个 dispatcher 是 `deal_local_link_proxy_create()`，不是
  `deal_unlinked_outband_local_data()` 的 type `2` outband reader。
- fresh trace 的 loopback 方向现在可区分为：
  - client fd `send len=160`；
  - accepted-side fd 先 `recv len=4` 读取 local proxy header；
  - 同 accepted-side fd 再 `recv len=156` 读取 body；
  - cmd26 handler 通过 `send_tcp_data_with_cache(..., len=1)` 回写 1-byte status；
  - client fd 看到的 status recv 长度是 `1`。
- 因此官方 loopback `recv len=4` 不应再描述为“可能的 4-byte ACK-like”或“仍需方向对齐”：
  对 fresh cmd26 bootstrap 而言，它是 accepted-side header read；cmd26 直接 status 是
  1 字节。
- `deal_unlinked_outband_local_data()` 中两个 `ZXMemcpy()` 的 source mapping 已补齐：
  - `data_buf[118] -> ChannelLinkSocketEx + 0x68`，source frame offset `18`，对应
    body offset `14`；
  - `data_buf[151] -> ChannelLinkSocketEx + 0x89`，source frame offset `51`，对应
    body offset `47`。
- 这两个 source 都位于已恢复 reader 覆盖的早段，不是 fresh diff 中仍未闭合的
  `body[137:152]` / `body[155]`。

代码更新：

- `cmcc_cloud_alive/zime_probe.py`
  - 同步 `rap_zime.py` 里的 `freshCmd26HeaderPathEvidence`；
  - 扩展 `unlinkedOutbandReaderEvidence`，记录 `frameToDataBufMapping`、body offset
    映射和 `channelLinkSocketExMemcpyEvidence`；
  - 更新 `localRecv4SemanticsEvidence`，把 official `recv len=4` 定义为 accepted-side
    header read，并记录 `loopbackBodyRecvLen=156`、`loopbackCmd26StatusLen=1`。
- `tests/test_python_modules.py`
  - 增加断言：`data_buf[118]/[151]` 分别映射 body offset `14/47`；
  - 增加断言：fresh cmd26 被 `check_spice_proxy_protocol_header()` 接受后进入
    `deal_local_link_proxy_create()`；
  - 增加断言：official loopback `recv len=4` 是 accepted-side header read，cmd26
    status path 是 `len=1`。

阶段判断：

- 本轮新增信息对应 fresh official trace 的 loopback `send len=160`、accepted-side
  `recv len=4`、accepted-side `recv len=156`、client-side status `recv len=1` 字段；
- 它也对应静态路径 `check_spice_proxy_protocol_header()`、
  `deal_local_link_proxy_create()`、`deal_local_recved_cmd_link()` 和
  `ChannelLinkSocketEx` 两个 `ZXMemcpy()` source；
- 这进一步排除 type `2` unlinked outband reader 和 `data_buf[118]/[151]` 对
  `body[137:152]` / `body[155]` 的解释；
- 下一步继续找 fresh cmd26 body tail 的真实消费或生成路径，重点是
  `deal_local_link_proxy_create()` 后的 proxy fd session setup、第二层 local proxy frame、
  linked/tail reader 状态迁移；
- 仍不跑 AUTH gate-only live；在 Python same-fd 71-byte ACK-like 前继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 77. 2026-07-05：fresh cmd26 body tail 直接消费路径闭合

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读既有 ELF 反汇编和现有 IDA snippet，目标是闭合第 76 节仍未恢复的
`body[137:152]` / `body[155]` 真实消费路径。

新增信息：

- `deal_local_spice_proxy_head()` 在 dispatch command 前会按
  `ProxyProtolHeader.u16BodyLen` 调用 `fd_session_async_read_tcp_data()`，把 fresh cmd26
  的 `156` 字节 body 读入 `in_sock + 0x9b0`，读进度字段是 `in_sock + 0x194`。
- command byte `0x1a` 进入 `deal_local_recved_cmd_link()`；该函数把
  `in_sock + 0x9b0` 直接作为 `ChannelLinkSocketEx *` 传给
  `send_tunnel_add_link(in_sock, in_sock + 0x9b0)`。
- 因此 fresh cmd26 tail 不需要通过 linked outband reader 才能解释：它在同一次
  accepted-side `recv len=156` 后已经进入 `send_tunnel_add_link()`。
- `send_tunnel_add_link()` 对 fresh body 的关键 offset 消费如下：
  - `body[2]` 作为 `link_priority`，进入 `set_clt_fd_session_priority()`；
  - `body[104:135]` / `0x68..0x87` 作为第一段 OpenTelemetry 候选字符串源，
    通过 `ZXStrncopy(channel_manage+0x6a, channel_link_info+0x68, size=0x21,
    copyLen=0x20)` 复制；
  - `body[137:152]` / `0x89..0x98` 作为第二段 OpenTelemetry 候选字符串源，
    通过 `ZXStrncopy(channel_manage+0x8b, channel_link_info+0x89, size=0x11,
    copyLen=0x10)` 复制；
  - `body[154:155]` / `0x9a..0x9b` 是 `channel_type_id` word；
  - `body[155]` / `0x9b` 是 `channel_type_id` 高字节，进入
    `(word >> 8) & 0x7f` 的 channel type 派生，并影响 `set_sock_bw_ctrl_type()` 与
    port-channel 分支判断。
- 第 73-76 节关于 unlinked outband reader 的结论仍成立：`data_buf[118]` /
  `data_buf[151]` 是 type=2/unlinked reader 构造 `ChannelLinkSocketEx` 时的早段 source；
  fresh cmd26/type=1 路径则直接把 156-byte body 当作 `ChannelLinkSocketEx` 使用。
- 第 74-75 节的 linked outband reader 仍对后续 linked frame 有意义，但不再是 fresh
  cmd26 `body[137:152]` / `body[155]` 的主解释。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `freshCmd26BodyPathEvidence`，记录 full-body read、body buffer、dispatch、
    offset mapping 和 tail 结论；
  - 将 `linkedOutbandTailCandidate.candidateForFreshTail` 改为 `False`，保留为
    `candidateForLaterLinkedFrames=True`。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence，使 auth gate replay gap 和 field diff 输出一致。
- `tests/test_python_modules.py`
  - 断言 `body[137:152]` 映射到 `ChannelLinkSocketEx+0x89` 的 `ZXStrncopy()`；
  - 断言 `body[155]` 映射到 `channel_type_id` 高字节；
  - 断言 linked reader 不再作为 fresh cmd26 tail 的必要解释。

阶段判断：

- 本轮新增信息对应 fresh official trace 的 accepted-side `recv len=156` 字段，以及第
  70/71 节脱敏 diff 中的 `body[137:152]` / `body[155]` 字段；
- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测；
- 最小 bootstrap schema 从“tail 未知”推进为“tail 消费路径已知，但字段取值和 Python
  合成策略尚未闭合”；
- 下一步应恢复 `ChannelLinkSocketEx body[0:156]` 的字段级最小合成 schema，并确认
  `deal_create_proxy_fd_session()` / `init_local_rw_sock_pair()` 产生哪些必须的本地 session
  side effects；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 78. 2026-07-05：ChannelLinkSocketEx 最小合成 schema 消费侧分类

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读既有 IDA/headless 产物和脱敏 evidence，目标是把第 77 节已经确认的
`ChannelLinkSocketEx body[0:156]` 消费路径收敛成 Python 可实现前需要的最小 schema
分类。仍不读取或输出 local proxy body 明文。

新增信息：

- fresh cmd26 body 的消费侧已经能分为三类：
  - 必须合成且已定位 offset 的字段：
    - `body[2]` 是 `info.link_priority`，在 `send_tunnel_add_link()` 里先进入
      `set_clt_fd_session_priority()`；
    - `body[154:155]` 是 `channel_type_id`，派生 `channel_type/channel_id`，并继续影响
      `set_sock_bw_ctrl_type()`、port-channel 分支和 QUIC stream metadata。
  - 必须合成但精确 struct offset 尚未恢复的 `ChannelLinkSocketEx.info` 前缀字段：
    - `info.dest_ip`：决定 IPv4/IPv6 分支，并复制到 `ProxyChannelManage.link_info`；
    - `info.dest_port`：复制到 `ProxyChannelManage.link_info.dest_port`；
    - `info.link_type`：复制到 `ProxyChannelManage.link_info.link_type`；
    - `info.flag/info.channel_type`：只在 `in_sock->data_buf[224] == 1` 的 SPICE link-type
      分支中参与后续 channel type 写入。
  - 用于匹配官方 bootstrap shape、但不是 auth payload 明文的字段：
    - `body[104:135]` 和 `body[137:152]`，分别作为 OpenTelemetry trace/span 候选字符串源，
      在 stream 创建成功后复制到 channel manage。
- `QUIC_create_data_stream()` 补充了 cmd26 bootstrap 的状态约束：
  - 需要 session 上已有 `QUIC_engine` 且 `QUIC_inited == 1`；
  - 需要能通过 `get_proxy_type_by_link_type()` 找到对应 `QUICChannelManage`；
  - `QUIC_initialize_stream_manage()` 会把 `channel_type_id` 写入
    `StreamManage.ChannelType/ChannelId`；
  - `ZIME_CreateDataStream()` 失败会导致 `send_tunnel_add_link()` 失败。
- `QUIC_set_streams_pay_load_type()` 补充了 link flag 的语义：
  - `sock_link_type == 2` 映射为 `SPICE_OUTBAND`；
  - `sock_link_type == 1` 则按 SPICE channel type 名称映射；
  - 因此 Python 不能只发 199-byte `AUTH_HEAD`，也不能只合成 cmd26 body；还必须复现
    local proxy/session side effects，或者证明一个等价状态模型足以让外网 AUTH gate 返回
    same-fd 71-byte ACK-like。

对应官方 trace 字段：

- 本轮新增 schema 对应 fresh official trace 的 local bootstrap cycle：
  - loopback client `send len=160`；
  - accepted-side `recv len=4` local proxy header；
  - accepted-side `recv len=156` `ChannelLinkSocketEx` body；
  - client-side `recv len=1` status；
  - 随后才是外网 fd 的 `AUTH_HEAD len=199`。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `freshCmd26MinimalSynthesisSchema`，记录 body contract、消费字段分类、
    session side effects、Python implication 和未闭合项；
  - `nextStaticTargets` 从泛化的 body 字段级语义，收窄为 `ChannelLinkSocketEx.info`
    前缀精确 offset 和合成规则。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence，使 `analyze-zime-probe` 与 `rap_zime` 的脱敏输出一致。
- `tests/test_python_modules.py`
  - 断言新增 schema 的 body contract、官方 trace 字段、必需字段、link flag 到
    `SPICE_OUTBAND` 的映射，以及仍未闭合项。

阶段判断：

- 新增信息不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测；
- 它把 TF-0063 从“tail 消费路径已知”推进到“消费侧最小 schema 已分类”，但还没有完成
  Python 可合成字段值；
- 仍未闭合：
  - `info.dest_ip/info.dest_port/info.link_type/info.flag/info.channel_type` 的精确 struct
    offset；
  - 哪些字段值可由 Python 按本地 session 状态生成，哪些必须继续从官方 trace 脱敏推导；
  - Python-only 等价 state model 是否足以获得外网 same-fd 71-byte ACK-like；
- 因此下一步继续静态恢复 `ChannelLinkSocketEx.info` 前缀 offset 与
  `deal_create_proxy_fd_session()` / `init_local_rw_sock_pair()` side effects；在 Python
  same-fd 71-byte ACK-like 前继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 79. 2026-07-05：ChannelLinkSocketEx DWARF 布局恢复

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读本地 ELF 的 DWARF debug info，目标是闭合第 78 节仍未 typed 的
`ChannelLinkSocketEx.info` 前缀精确 offset。仍不读取或输出 local proxy body 明文。

新增信息：

- `.tmp/ida-inputs/libspice-client-glib-zte-2.0.so.8.5.0` 的 DWARF 明确给出
  `ChannelLinkSocketEx`：
  - byte size `156`；
  - `info` 位于 body offset `0`，类型是 `ChannelLinkInfoEx`；
  - `channel_type_id` 位于 body offset `154`，size `2`。
- `ChannelLinkInfoEx` byte size 是 `154`，字段布局如下：

```text
body[0:2]     dest_port       size=2
body[2]       link_priority   size=1
body[3]       link_type       size=1
body[4:8]     dest_ip         size=4
body[8:24]    ipv6            size=16
body[24:40]   serial_num      size=16
body[40:77]   vm_uuid         size=37
body[77]      protocol_type   size=1
body[78]      be_emergency    size=1
body[79:81]   bw_ctrl         size=2
body[81:83]   tbw_ctrl        size=2
body[83]      flag            size=1
body[84:88]   channel_type    size=4
body[88:104]  extend          size=16
body[104:137] otlp_trace_id   size=33
body[137:154] otlp_parent_id  size=17
body[154:156] channel_type_id size=2
```

- 这修正了第 78 节的阶段描述：
  - `info.dest_ip/info.dest_port/info.link_type/info.flag/info.channel_type` 的精确 struct
    offset 已恢复；
  - 仍未闭合的是字段值来源和 Python 合成规则，而不是布局。
- 第 77 节的 tail 解释得到加强：
  - `body[137:152]` 是 `otlp_parent_id` 的前 16 个有效字符位；
  - `body[153]` 是该 17-byte 数组的尾部 NUL/保留位；
  - `body[155]` 是 `channel_type_id` 高字节。

对应官方 trace 字段：

- 本轮新增布局对应 fresh official trace 的 accepted-side `recv len=156`
  `ChannelLinkSocketEx` body；
- 它解释第 70/71 节两轮 local proxy body 脱敏 diff 中的：
  - `body[2]`：`link_priority`；
  - `body[137:152]`：`otlp_parent_id` 有效字符区；
  - `body[155]`：`channel_type_id` 高字节。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `freshCmd26MinimalSynthesisSchema.schemaStatus` 更新为
    `static_layout_known_value_synthesis_not_closed`；
  - 新增 `dwarfStructEvidence`，记录 `ChannelLinkSocketEx` 和 `ChannelLinkInfoEx` 的
    offset/size；
  - `fieldConsumption` 从“精确 offset 未 typed”改为明确 body offset/range；
  - `notYetClosed` 收窄为字段值合成规则、OpenTelemetry/uuid 值来源、Python 等价 state
    model。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence。
- `tests/test_python_modules.py`
  - 断言 DWARF byte size、关键字段 offset、OTLP 数组长度和新的未闭合项。

阶段判断：

- 新增信息不是 blind live 实验，也不是 `type101/type102/link_type/local_bind/ztec_prime`
  盲测；
- TF-0063 从“消费侧最小 schema 已分类”推进为“wire body layout 已闭合，字段值合成规则
  未闭合”；
- 下一步应从官方静态路径恢复字段值来源：
  - `dest_port/dest_ip/link_type/channel_type_id` 如何从 session/proxy/channel 状态生成；
  - `serial_num/vm_uuid/otlp_trace_id/otlp_parent_id` 哪些可由 Python 本地生成，哪些必须从
    official trace 脱敏推导；
  - `deal_create_proxy_fd_session()` / `init_local_rw_sock_pair()` side effects 的 Python 等价
    state model；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 85. 2026-07-05：channel_type_id 合成语义静态收窄

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读现有 snippets、ELF 符号表和小范围 `objdump` 反汇编，目标是把第 84 节仍未闭合的
`ChannelLinkSocketEx.channel_type_id` 从“字段值未知”收窄为“结构和 side effect 已知、
官方首个候选值仍需推断”。仍不读取或输出 local proxy frame body 明文、auth payload
或任何 token/connect material。

新增信息：

- `channel_type_id` 的结构语义已闭合：
  - fresh cmd26 body `body[154:156]` 是 little-endian word；
  - `send_tunnel_add_link()` 使用 `(word >> 8) & 0x7f` 作为 SPICE `channel_type`，
    使用 `word & 0xff` 作为 `channel_id`；
  - `QUIC_initialize_stream_manage()` 同样只从 `ChannelLinkSocketEx+0x9a` 读取该 word，
    写入 `StreamManage+0x43 = channel_type` 和 `StreamManage+0x34 = channel_id`。
- `dest_ip/dest_port` 与 `channel_type_id` 的关系进一步分层：
  - `dest_ip/dest_port` 会复制或记录到 `ProxyChannelManage.link_info`；
  - 它们不参与 `StreamManage.ChannelType/ChannelId` 派生；
  - 它们仍不等同于 KCP socket 目标，也不等同于 CAG auth buffer 内部目标。
- `channel_type_id` 对 side effects 的影响已收窄：
  - `sock_link_type=1` 时，`QUIC_set_streams_pay_load_type()` 按
    `QUIC_spice_channel_type_to_string(channel_type)` 选择 `SPICE_*` payload type，未知
    channel type fallback 到 `SPICE_UNKNOWN`；
  - `sock_link_type=2` 时，payload type 独立映射为 `SPICE_OUTBAND`，但 fresh cmd26 已在
    第 80 节排除为 type6/link_type=1 路径；
  - `set_sock_bw_ctrl_type()` 中 `channel_type=2` 选择 bw ctrl type 2，
    `channel_type=10` 进入 port-channel 分支，其他 SPICE channel type 在
    `sock_link_type=1` 下选择 bw ctrl type 1。

对应官方 trace 字段：

- 本轮新增信息对应 fresh official trace 的 local bootstrap 字段：
  - loopback client `send len=160`，cmd26；
  - accepted-side `recv len=156`，`ChannelLinkSocketEx` body；
  - client-side `recv len=1`，cmd26 status；
  - external fd 随后发送 `AUTH_HEAD len=199`。
- 它解释的是 `body[154:156]` 的派生规则和 side effects，不读取或输出该 body 的具体
  字节值。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 在 `freshCmd26MinimalSynthesisSchema.valueSourceStaticEvidence` 中新增
    `channelTypeIdSynthesisRole`；
  - 将 `notYetClosed` 从泛化的 `dest_port/dest_ip/channel_type_id` 改为
    `dest_port/dest_ip` exact 值和“首个 channel_type_id 候选值”两个未闭合项。
- `cmcc_cloud_alive/zime_probe.py`
  - 同步上述 evidence。
- `tests/test_python_modules.py`
  - 新增断言覆盖 `channel_type_id` 的公式、QUIC stream manage 写入、payload type 映射、
    bandwidth 分支和 destination independence。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，108 个测试 OK。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT；
- TF-0063 从“fresh body 字段值合成边界已分层”推进为
  “`channel_type_id` 结构与 side effects 已闭合，官方首个候选值仍未闭合”；
- 下一步应继续确认 official first-channel candidate 是否是 MAIN/DISPLAY/PORT 中哪一个，
  以及 `dest_ip/dest_port` exact 合成值是否可以用本地 session/loopback 值替代；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 86. 2026-07-05：first-channel candidate 边界和 trace 负证据

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读 fresh auth trace 的脱敏结构字段、本地 ELF 符号表和小范围 `objdump`，目标是继续
收窄第 85 节的 official first-channel `channel_type_id` 候选。仍不读取或输出 local
proxy frame body 明文、auth payload、token/connect material。

新增信息：

- `QUIC_spice_channel_type_to_string()` 的 jump table 已闭合：
  - `0` 或越界映射到 `SPICE_UNKNOWN`；
  - `1..11` 分别映射为 `SPICE_MAIN`、`SPICE_DISPLAY`、`SPICE_INPUTS`、
    `SPICE_CURSOR`、`SPICE_PLAYBACK`、`SPICE_RECORD`、`SPICE_TUNNEL`、
    `SPICE_SMARTCARD`、`SPICE_USBREDIR`、`SPICE_PORT`、`SPICE_PROXY`。
- `spice_session_connect()` 的 channel 创建顺序提供候选优先级，但不是官方 body 值：
  - `spice_channel_new(session, 1, 0)` 无条件创建 MAIN channel；
  - `create_channel(cmain, 2, 0)` 只在 `is_create_main_displaychannel_in_advance()` 为真时
    预建 DISPLAY channel；
  - `SPICE_PORT/channel_type=10` 是 `send_tunnel_add_link()` 的 port-channel 特殊分支，
    不是 link-unify fresh cmd26 的无条件首个候选。
- `get_avaliable_virtual_channel_id()` 只分配 `ProxyChannelManage` 的 virtual link id：
  - 来源是 session 私有计数器和 `get_proxy_channel_manage_by_id()` 占用检查；
  - 它不决定 `channel_type_id` 低字节，不能反推 SPICE `channel_id`。
- fresh trace 中的 `zime_struct/ZIME_CreateDataStream param_before` 只提供安全的结构字段：
  - 已观察到 `u8Priority=9`、`u32MaxBandwidth=4294967295`；
  - 静态代码显示它们来自 `StreamParam.u8Priority = stream_manage->priority` 和
    `StreamParam.u32MaxBandwidth = -1`；
  - 该 trace 事件没有暴露 `StreamManage.ChannelType/ChannelId`，不能据此确认
    MAIN/DISPLAY/PORT 的官方首个值。

对应官方 trace 字段：

- 正向对应：
  - local bootstrap 的 loopback client `send len=160`；
  - accepted-side `recv len=156` `ChannelLinkSocketEx` body；
  - 随后的 external `AUTH_HEAD len=199`；
  - `zime_struct/ZIME_CreateDataStream param_before` 中的 `u8Priority` /
    `u32MaxBandwidth` 安全字段。
- 负证据：
  - 本轮没有读取或输出 `body[154:156]` 的具体字节；
  - `ZIME_CreateDataStream` 安全结构字段不能反推出官方 `channel_type_id`。

代码更新：

- `cmcc_cloud_alive/rap_zime.py` 和 `cmcc_cloud_alive/zime_probe.py`
  - 在 `channelTypeIdSynthesisRole` 中新增 `channelTypeNameTable`；
  - 新增 `firstChannelCandidateBoundary`，把 `0x0100` 记为 MAIN/0 的无条件静态候选，
    `0x0200` 记为 DISPLAY/0 的条件候选，`0x0a00` 记为非无条件首个候选；
  - 新增 `zimeCreateDataStreamTraceBoundary`，记录 trace 可见字段和不能推断的边界。
- `tests/test_python_modules.py`
  - 新增断言覆盖 channel type 名称表、MAIN/DISPLAY/PORT candidate 边界、virtual link id
    与 SPICE channel id 的分离，以及 ZIME trace 字段的负证据。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，108 个测试 OK。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT；
- TF-0063 从“官方首个候选值仍未闭合”推进为“候选优先级和 trace 负证据已闭合”；
- Python 侧下一步可以把 `0x0100` 作为静态优先候选进入本地 bootstrap 合成设计，但在
  未获得 same-fd 71-byte ACK-like 前，不能把它称为官方确认值；
- 继续恢复 `dest_ip/dest_port` exact 合成值和 type6 proxy fd/session、QUIC/channel
  manage 等价 state model；在 Python same-fd 71-byte ACK-like 前继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 87. 2026-07-05：fresh cmd26 producer-side 合成路径闭合

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有生成额外报告或本地二进制。继续
只读现有 `.i64`/ELF/DWARF 和小范围 `objdump`，目标是确认第 86 节后仍未闭合的
fresh 160-byte cmd26 body 是否存在官方 producer-side 合成函数。仍不读取或输出 local
proxy frame body 明文、auth payload、token/connect material。

新增信息：

- `add_link_to_proxy_by_socket()` 是 fresh 160-byte cmd26 frame 的 producer-side 闭合点：
  - 分配 `0xa0` 字节；
  - 写 header：`cmd=0x1a`、`channel/id byte=0`、`u16BodyLen=0x9c`；
  - 把栈上的 `ChannelLinkSocketEx` body 复制到 frame offset 4；
  - 通过 `spice_channel_flush_wire(..., len=0xa0)` 写出；
  - 随后 `spice_channel_read(..., len=1)` 读取 cmd26 status。
- 这条路径直接解释 fresh official trace 中的 local bootstrap 形态：
  - loopback client `send len=160`；
  - accepted-side `recv len=156` `ChannelLinkSocketEx` body；
  - client-side `recv len=1` cmd26 status；
  - 随后 external fd 继续 `AUTH_HEAD len=199`。
- `ChannelLinkSocketEx.info.dest_ip` 的 producer 规则已从“未知值来源”收窄为：
  - source 先取 `SpiceSessionPrivate.hostip`，该字段非空则使用它；
  - 否则回退 `SpiceSessionPrivate.host`；
  - IPv4 分支走 `inet_addr()` 后 `ntohl()` 写入 body offset `4..7`；
  - IPv6 分支走 `inet_pton(AF_INET6)` 写入 body offset `8..23`。
- `ChannelLinkSocketEx.info.dest_port` 的 producer 规则已从“未知值来源”收窄为：
  - 由 `get_channel_proxy_link_dest_port(channel)` 返回；
  - 静态分支通过 `ZXStrtoul()` 读取 session 私有字符串 offset `0x8`、`0x1240`
    或 `0x1238`；
  - exact runtime value 仍需 Python 安全 session state 提供，不能通过 replay local
    proxy body 明文获得。
- 其它 producer-side body 字段：
  - `body[2] link_priority` 来自 `get_channel_proxy_link_priority(channel)`；
  - `body[3] link_type` 在该 producer 中保持 zero-initialized；
  - `body[83] flag` 仅在特定 network-protocol/session 条件下写入 session offset
    `0x1f54` 的低字节，否则保持 0；
  - `body[104:136]` 来自 caller argument `+0x400`；
  - `body[137:153]` 来自 caller argument `+0x421`；
  - `body[154:156] channel_type_id` 来自目标函数直接读到的 channel private
    expression：`(field@0x974 << 8) | field@0x970`。这仍是静态 producer 证据，
    不是 official trace 字节确认值。

对应官方 trace 字段：

- 本轮新增信息正向对应 fresh official trace 的：
  - loopback client `send len=160 cmd26`；
  - accepted-side `recv len=156 ChannelLinkSocketEx body`；
  - client-side `recv len=1 cmd26 status`；
  - external `AUTH_HEAD len=199` 跟随 local proxy/session setup。
- 本轮没有读取或输出 `body[0:156]` 明文；所有结论只记录函数、offset、长度、分支和
  source class。

代码更新：

- `cmcc_cloud_alive/rap_zime.py` 和 `cmcc_cloud_alive/zime_probe.py`
  - 在 `freshCmd26MinimalSynthesisSchema.valueSourceStaticEvidence` 中新增
    `freshCmd26ProducerSideSynthesis`；
  - 将 `notYetClosed` 从“dest_ip/dest_port 合成规则未知”收窄为“Python 仍需安全地
    materialize session/channel state”。
- `tests/test_python_modules.py`
  - 新增断言覆盖 producer frame 形态、`hostip/host` source selection、dest_port 分支、
    OTLP source、`channel_type_id` source expression 和新的未闭合项措辞。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT；
- TF-0063 从“candidate 边界和 trace 负证据已闭合”推进为
  “fresh cmd26 producer-side body 合成路径已闭合，Python session/channel state
  materialization 未闭合”；
- 下一步应把 `add_link_to_proxy_by_socket()` 的 source class 转成 Python bootstrap
  builder：安全提供 `hostip/host`、selected port、channel type/id candidate、OTLP
  candidate，并确认是否必须显式建模 type6 proxy fd session slot、UDP gate 和 KCP
  attach；
- 在 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 91. 2026-07-05：真实文件尾部同步锚点

当前文件存在历史编号乱序：第 89 节记录了本轮实质进展，但落在文件中部；第 90 节也因
重复上下文落在中部。本节是当前真实文件尾部锚点，不新增协议证据，只声明续接状态：

- 本轮代码进展见第 89 节：`pre_auth_fresh_cmd26_bootstrap` 已接入本地 gate-only 合同；
- 验证结果：`compileall` 通过，`python3 -m unittest discover -s tests -p 'test_python_*.py' -v`
  通过，111 个测试 OK；
- task-forest 已同步 TF-0063/TF-0061，并完成 `validate/export`；
- 未运行 live、未操作 GUI/CrossDesk、未读取或输出敏感明文；
- 仍未获得 Python live same-fd 71-byte ACK-like，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 93. 2026-07-05：真实文件尾部同步锚点（二）

当前文件继续存在历史编号乱序：第 92 节记录了本轮实质进展，但因重复上下文落在文件中部。
本节是当前真实文件尾部锚点，不新增协议证据，只声明续接状态：

- 本轮代码进展见第 92 节：`pre_auth_session_state_model` 已接入 runner report，
  `preAuthSessionState.readyForGateOnlyLive` 只在本地 required checks 全部 modeled 时为 true；
- 验证结果：`compileall` 通过，聚焦 pre-auth cmd26 合同测试通过，全量
  `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，111 个测试 OK；
- task-forest 已同步 TF-0063/TF-0061，并完成 `validate/export`；
- 未运行 live、未操作 GUI/CrossDesk、未读取或输出敏感明文；
- 仍未获得 Python live same-fd 71-byte ACK-like；即使本地 readiness contract closed，
  也不允许推进 `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 94. 2026-07-05：pre-AUTH bootstrap/state contract CLI 入口落地

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何
token/connect material，也没有生成额外报告或本地二进制。目标是把第 89/92 节已经
通过本地 fake-server 合同验证的 pre-AUTH cmd26 + state readiness contract，从内部函数
参数推进到 `rap-zime-kcp-auth-from-cag` CLI 的显式、默认关闭入口。

新增信息：

- `rap-zime-kcp-auth-from-cag` 新增默认关闭参数：
  - `--pre-auth-cmd26-local-proxy host:port`：在 AUTH_HEAD 前向本地 loopback proxy
    发送 fresh cmd26 frame 并等待 1-byte status；
  - `--pre-auth-cmd26-channel-type` / `--pre-auth-cmd26-channel-id`：显式设置
    channel_type_id 候选，默认仍是 MAIN/0；
  - `--pre-auth-cmd26-trace-id` / `--pre-auth-cmd26-parent-id`：只接收非敏感结构化
    OTLP 候选；
  - `--pre-auth-state-contract`：显式声明本地 pre-AUTH session side-effect contract
    已 modeled，用于报告 `readyForGateOnlyLive`，不是 cloud ACK-like proof。
- CLI 从 fresh CAG `connectInfo.host/port` 构造 cmd26 `dest_ip/dest_port` 候选，但
  runner report 仍只保存 redacted summary，不保存目标明文、local proxy body、auth
  payload 或 status byte。
- 默认行为不变：不传新参数时，不发送 pre-AUTH cmd26，不声明 state contract closed。

对应官方 trace 字段：

- `--pre-auth-cmd26-local-proxy` 对应 loopback client `send len=160 cmd26` 和
  client-side `recv len=1 cmd26 status`；
- cmd26 frame body 的 accepted-side `recv len=156 ChannelLinkSocketEx body` 仍由
  redacted summary/shape 表达，不输出 body；
- `--pre-auth-state-contract` 对应 AUTH_HEAD 前 native side effects：type6 proxy fd
  session slot、proxy_sock UDP gate、`init_local_rw_sock_pair_udp` KCP attachment、
  QUIC/channel manage ready-or-bypassed；
- 这些本地入口只让下一次 AUTH gate-only live 的前置审计可执行；验收仍只看 Python
  same-fd 71-byte ACK-like 后发送 241-byte AUTH_DATA。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `cmd_rap_zime_kcp_auth_from_cag()` 构造并透传
    `pre_auth_fresh_cmd26_bootstrap` / `pre_auth_session_state_model`；
  - CLI parser 新增 pre-AUTH cmd26/state contract 参数。
- `tests/test_python_modules.py`
  - 扩展 `test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path`；
  - 断言 CLI 参数透传到 runner wrapper；
  - 断言报告仍不写入 CAG target、密码或 token 明文。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v`
  通过。

阶段判断：

- 这不是 `type101/type102/link_type/local_bind/ztec_prime` 盲测，也没有推进
  SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run；
- TF-0061 现在具备一个显式 CLI 入口来执行 pre-AUTH cmd26/local state contract +
  AUTH gate-only 的最小路径，但本轮没有跑 live；
- 下一步应跑全量单测并同步 task-forest；之后如继续推进，只能做 AUTH gate-only live
  前置审计或在明确允许的 session-owning 窗口运行 gate-only live。

## 95. 2026-07-05：TF-0063 schema 任务收口，主线转回 TF-0061

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取或输出敏感明文。第 94 节
已记录 `rap-zime-kcp-auth-from-cag` 的 pre-AUTH cmd26/state contract CLI 入口落地；
随后全量 Python 单测通过，并同步 task-forest。

状态更新：

- TF-0063 已从 `in_progress` 收口为 `done`：
  - fresh cmd26 producer/builder 已闭合；
  - send160/status1 本地 fake-server 合同已闭合；
  - pre-AUTH local proxy/session state readiness contract 已闭合；
  - CLI 入口已落地且默认关闭；
  - schema/report 均保持脱敏，不输出 local proxy body、auth payload、token/connect
    material 或 CAG UDP target 明文。
- TF-0061 仍是当前主线：
  - 下一步只能是 AUTH gate-only live 前置审计，或在明确 session-owning 窗口运行
    gate-only live；
  - 验收仍只看 Python same-fd 71-byte ACK-like 后发送 241-byte AUTH_DATA；
  - 未获得该证据前继续冻结
    `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，111 个测试 OK；
- `task_forest.py validate` 通过；
- `task_forest.py export` 已导出
  `.agent-workbench/task-forest/exports/task-forest.html`。

## 97. 2026-07-05：AUTH gate-only no-network preflight audit 落地

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取或输出敏感明文。目标是在
第 94-96 节 CLI 入口和 task-forest 纠偏之后，把“下一次能否跑 AUTH gate-only live”
变成一个可执行、脱敏、no-network 的前置审计，而不是直接触网。

新增信息：

- 新增 `build_auth_gate_live_preflight_audit_from_cag_material()`：
  - 使用内存中的 fresh CAG material 构造 AUTH_HEAD/AUTH_DATA KCP wire；
  - 只输出 `redacted_kcp_auth_wire_summary()`，不输出 auth payload；
  - 检查 AUTH_HEAD wireLen 是否为 199、AUTH_DATA wireLen 是否为 241；
  - 检查三次 AUTH_HEAD pump 配置是否 modeled；
  - 检查 pre-AUTH cmd26 local proxy 配置和 local proxy/session state contract 配置是否完整；
  - 输出 `readyForGateOnlyLiveAttempt`，但同时列出 runtime gates：
    live 时必须拿到 cmd26 1-byte status 和 same-fd 71-byte ACK-like。
- `rap-zime-kcp-auth-from-cag` 新增 `--auth-gate-preflight-only`：
  - 只执行上述审计；
  - 不连接 local proxy；
  - 不发送 UDP；
  - 不运行 live probe；
  - report 仍保持 CAG target、token、cpsid、密码和 auth payload 脱敏。

对应官方 trace 字段：

- AUTH_HEAD wireLen `199` 对应 fresh official trace 外网 fd 的三次 AUTH_HEAD；
- AUTH_DATA wireLen `241` 对应 same-fd ACK-like 后的 AUTH_DATA；
- `--pre-auth-cmd26-local-proxy` 配置对应 loopback `send len=160 cmd26`；
- runtime gate `fresh cmd26 local proxy must return 1-byte status` 对应 client-side
  `recv len=1 cmd26 status`；
- runtime gate `same external UDP fd must receive 71-byte ACK-like before AUTH_DATA`
  对应 official `recv len=71` 后发送 AUTH_DATA。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `OFFICIAL_AUTH_HEAD_WIRE_LEN=199` 和 `OFFICIAL_AUTH_DATA_WIRE_LEN=241`；
  - 新增 `build_auth_gate_live_preflight_audit_from_cag_material()`。
- `cmcc_cloud_alive/main.py`
  - `cmd_rap_zime_kcp_auth_from_cag()` 支持 `--auth-gate-preflight-only` 分支；
  - 分支只写/打印 no-network audit，不调用 live probe。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_preflight_only_does_not_probe_live`；
  - 断言 preflight-only 不调用 live probe；
  - 断言输出 wireLen 199/241、配置 ready、runtime gates 仍要求 status1 和 ACK-like71；
  - 断言 report 不包含密码、token、cpsid、CAG target 明文。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py`
  通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_does_not_probe_live -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，112 个测试 OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 下一步仍是：在明确 session-owning 窗口运行 AUTH gate-only live，并且只验收
  Python same-fd 71-byte ACK-like 后发送 241-byte AUTH_DATA；
- 在获得该证据前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 96. 2026-07-05：TF-0061 next_action 纠偏

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取或输出敏感明文。第 95 节后
复核 task-forest todo 发现 TF-0061 的 `next_action` 仍由旧 `execution_hints` 推导为
“继续 local proxy/session bootstrap”，与 TF-0063 已收口的当前状态冲突。

已修正：

- 使用 task-forest `fields-json` 覆盖 TF-0061 的 `execution_hints`；
- 当前 TF-0061 唯一有效 next action：
  - 只做 AUTH gate-only live 前置审计，或在明确 session-owning 窗口运行 gate-only live；
  - 验收只看 Python same-fd 71-byte ACK-like 后发送 241-byte AUTH_DATA；
  - 禁止 `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

验证结果：

- `task_forest.py validate` 通过；
- `task_forest.py export` 已导出
  `.agent-workbench/task-forest/exports/task-forest.html`。

## 98. 2026-07-05：续接复核和 AUTH gate-only preflight 执行边界

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json`，也没有读取或
输出 token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是
复核当前 workspace 与续接 prompt 的冲突，并确认第 97 节 no-network preflight 的实际执行
边界。

复核结论：

- 当前 workspace 与本轮用户粘贴的第一段 handoff 存在冲突：第 94-97 节已经落盘，TF-0063
  已收口为 `done`，主线已回到 TF-0061；
- `rap-zime-kcp-auth-from-cag --auth-gate-preflight-only` 的实现确实不会连接 local proxy、
  不发送 UDP、也不调用 live probe；
- 但该 CLI 分支仍会先调用 `protocol_runner.fetch_cag_auth_connect_info()` 获取 fresh CAG
  `auth/connectInfo` material；因此它是 no-network AUTH gate audit，不是 no-state audit；
- 在当前硬约束“不可读取 `.tmp/state.json` 或敏感明文”下，本轮不能自行用真实
  `user_service_id` 执行该 preflight。下一步需要明确 session-owning 授权窗口，或新增一个
  只接收调用方内存材料且不由 agent 读取本地 state 的受控入口。

对应官方 trace 字段：

- preflight 已能本地审计 AUTH_HEAD wireLen `199`、AUTH_DATA wireLen `241`、三次
  AUTH_HEAD pump、pre-AUTH cmd26 配置和 local state contract 配置；
- 这些只对应下一次 live gate 的配置就绪性，不对应官方 same-fd `recv len=71` 成功；
- 验收仍只看 Python 同一外网 fd 收到 71-byte ACK-like 后发送 241-byte AUTH_DATA。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，112 个测试 OK。

阶段判断：

- 本轮没有新增协议字段证据，也没有推进
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`；
- TF-0061 下一步保持为：在明确 session-owning 窗口运行 AUTH gate-only preflight/live，
  或先实现一个不由 agent 读取本地 state 的显式材料入口；
- 未拿到 Python same-fd 71-byte ACK-like 前，后续阶段继续冻结。

## 99. 2026-07-05：显式 CAG material preflight 入口落地

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是把
第 98 节发现的执行边界转成一个默认关闭入口：允许调用方自行提供 CAG `auth/connectInfo`
JSON，而不是由 agent/CLI 强制从本地 state fetch。

新增信息：

- `rap-zime-kcp-auth-from-cag` 新增 `--cag-material-file PATH`：
  - JSON 必须包含 `auth` 和 `connectInfo` object，可选 `publicConnectInfo`；
  - `PATH=-` 时从 stdin 读取，便于调用方在自己的 session-owning shell 中喂入内存材料；
  - 不传该参数时行为不变，仍走 `protocol_runner.fetch_cag_auth_connect_info()`；
  - 该入口默认关闭，不会让 agent 自动读取 `.tmp/state.json`。
- preflight-only 路径使用显式材料时：
  - 不调用 state-backed CAG fetch；
  - 不连接 local proxy；
  - 不发送 UDP；
  - 不调用 live probe；
  - report 只记录 `cagMaterial.source=explicit-cag-material-file`、presence/shape 和
    redacted AUTH wire summary，不保存 material 明文。

对应官方 trace 字段：

- 显式 material 入口不新增协议字段，只为第 97 节 no-network audit 提供受控输入来源；
- preflight 仍只审计 AUTH_HEAD wireLen `199`、AUTH_DATA wireLen `241`、三次 AUTH_HEAD
  pump、pre-AUTH cmd26 配置和 local state contract 配置；
- 真实验收仍必须来自 gate-only live：Python same-fd 71-byte ACK-like 后发送 241-byte
  AUTH_DATA，然后停止。

代码更新：

- `cmcc_cloud_alive/main.py`
  - 新增 `_load_explicit_cag_material()` 和 `_cag_material_report_summary()`；
  - `cmd_rap_zime_kcp_auth_from_cag()` 支持 `--cag-material-file`，并在 report 中只输出
    脱敏 material 来源摘要。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file`；
  - 断言显式 material preflight 不 fetch state、不跑 live、wireLen 199/241 就绪且报告脱敏。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_does_not_probe_live -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 现在可由用户在明确 session-owning shell 中自行提供 explicit material 先跑
  no-network preflight，随后才考虑 gate-only live；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 100. 2026-07-05：AUTH gate CLI 最终 report 写入收敛

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是复核
第 99 节显式 material 入口在 live 分支的报告写入边界，确保下一次 gate-only live 的
审计证据只由 CLI 写入最终脱敏 report。

新增信息：

- `rap-zime-kcp-auth-from-cag` 非 preflight-only 分支此前会把 `report_file` 下推给
  `run_kcp_auth_sync_probe_from_cag_material()`，随后 CLI 再追加 `cagMaterial` 摘要并二次写入；
- 已收敛为 runner 内部不写 report，CLI 在追加 `cagMaterial` 脱敏摘要后统一写入最终
  report；
- 这样下一次 AUTH gate-only live 的文件证据会包含与 stdout 一致的 `cagMaterial`
  source/presence 摘要，同时避免中间 report 缺少 CLI 级审计字段。

对应官方 trace 字段：

- 本轮不新增协议字段，也不改变 wire 行为；
- 该修正对应下一次 gate-only live 的证据保存边界：仍只关注
  `AUTH_HEAD len=199 -> same-fd recv len=71 -> AUTH_DATA len=241`；
- 未出现同 fd 71-byte ACK-like 前，不允许把 report 里的其他 ACK/PONG/MARK 字段当作成功。

代码更新：

- `cmcc_cloud_alive/main.py`
  - live 分支调用 `run_kcp_auth_sync_probe_from_cag_material(..., report_file=None)`；
  - CLI 追加 `cagMaterial` 后再调用 `_write_report()` 写最终脱敏 report。
- `tests/test_python_modules.py`
  - `test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path` 增加断言：
    runner wrapper 收到 `report_file is None`，最终 report 写入由 CLI 负责。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_uses_redacted_material_path tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 的下一步仍是用户侧 explicit material no-network preflight，ready 后才可在明确
  session-owning 窗口跑 gate-only live；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 101. 2026-07-05：explicit material CLI gate-only fake-server 合同补齐

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是验证
第 99-100 节新增的 explicit material CLI 入口不只支持 no-network preflight，也能在
本地 fake server 合同中完整执行下一次 gate-only live 所需的最小控制流。

新增信息：

- 新增 CLI 级 fake-server 合同测试：
  - `--cag-material-file` 使用测试临时 JSON，不调用 state-backed CAG fetch；
  - TCP fake local proxy 收到 fresh cmd26 frame `wireLen=160`，header 为
    `cmd=0x1a, channel/id=0, bodyLen=156`，随后返回 1-byte status；
  - UDP fake CAG 收到 AUTH_HEAD `wireLen=199` 后返回 71-byte ACK-like；
  - CLI 收到 ACK-like 后发送 AUTH_DATA `wireLen=241`，并以 gate-only 模式停止；
  - 最终 report 包含与 stdout 一致的 `cagMaterial` 脱敏摘要，且不保存 auth material、
    CAG target 或 local proxy frame body 明文。

对应官方 trace 字段：

- TCP fake local proxy 对应 official loopback `send len=160 cmd26` 和 client-side
  `recv len=1 cmd26 status`；
- UDP fake CAG 对应 official external fd 上的 `AUTH_HEAD len=199`、same-fd
  `recv len=71`、随后 `AUTH_DATA len=241`；
- 本轮只证明 CLI explicit material 路径的本地合同闭合，不证明云端接受 Python session。

代码更新：

- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`；
  - 断言 explicit material CLI 不 fetch state，完成
    `cmd26 send160/status1 -> AUTH_HEAD199 -> ACK-like71 -> AUTH_DATA241 -> stop`；
  - 断言最终 report 的 `cagMaterial` 与 stdout 一致且保持脱敏。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 现在具备下一次 AUTH gate-only live 的 CLI 级本地合同覆盖；
- 下一步仍是用户侧 explicit material no-network preflight，ready 后才可在明确
  session-owning 窗口跑 gate-only live；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 102. 2026-07-05：AUTH gate-only report 验收器落地

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是给
下一次用户侧 gate-only live 后的脱敏 report 增加一个机械验收器，避免把其他 ACK/PONG/MARK、
SYNACK、DISPLAY_INIT 或 40 分钟状态误判为当前 gate 成功。

新增信息：

- 新增 `assess_auth_gate_only_report(report)`：
  - 只接受当前唯一 gate：
    `local cmd26/status -> AUTH_HEAD199 -> same-remote 71-byte ACK-like -> AUTH_DATA241 -> stop`；
  - 要求 `preAuthLocalBootstrap.bytesSent=160`、`statusBytesReceived=1`；
  - 要求 `preAuthSessionState.readyForGateOnlyLive=true`；
  - 要求 `authHeadWire.wireLen=199`、`authDataWire.wireLen=241`；
  - 要求 auth_head responses 里存在 `officialAuthHeadAckLike=true` 且
    `bytesReceived=71`；
  - 要求 stages 精确停止在 `["auth_head", "auth_data"]`，auth_data stage 标记
    `stoppedAtAuthGate=true`；
  - 明确要求 `authAckReceived=false`、`synackReceived=false`、`displayPathObserved=false`、
    `verifiedRunPassed=false`。
- 新增 CLI：
  - `check-rap-zime-auth-gate-report <report> [--report-file ...]`；
  - 只读取已生成的脱敏 gate report，输出 `authGateOnlyAccepted`、逐项 checks、
    `missingEvidence` 和对应 official trace fields。
- 测试覆盖：
  - positive：第 101 节 explicit material CLI fake-server report 被验收为 accepted；
  - negative：移除 71-byte ACK-like evidence 后，验收器返回
    `missingEvidence=["same_remote_ack_like_71", ...]` 中的关键缺口。

对应官方 trace 字段：

- `pre_auth_cmd26_send160` 对应 loopback client `send len=160 cmd26`；
- `pre_auth_cmd26_status1` 对应 client-side `recv len=1 cmd26 status`；
- `auth_head_wire_len_199` 对应 external fd `AUTH_HEAD len=199`；
- `same_remote_ack_like_71` 对应 same external fd `recv len=71 ACK-like`；
- `auth_data_wire_len_241` 对应 external fd `AUTH_DATA len=241`；
- `stopped_at_auth_gate` 对应当前冻结边界：不推进 AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `assess_auth_gate_only_report()`；
- `cmcc_cloud_alive/main.py`
  - 新增 `check-rap-zime-auth-gate-report` CLI；
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 report 验收 CLI 和缺失 71-byte ACK-like 的 negative path。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 现在具备 gate-only live 后的机械验收入口；
- 下一步仍是用户侧 explicit material no-network preflight，ready 后才可在明确
  session-owning 窗口跑 gate-only live，并用该验收器复核 report；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 103. 2026-07-05：AUTH gate report 验收 CLI 支持非零退出

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是让
第 102 节的 report 验收器可直接作为 shell gate 使用：缺少当前唯一有效 gate 证据时返回
非 0，避免人工漏看 `authGateOnlyAccepted=false`。

新增信息：

- `check-rap-zime-auth-gate-report` 新增 `--require-accepted`：
  - 默认行为不变：读取脱敏 report，打印/写出 assessment；
  - 传入该参数时，如果 `authGateOnlyAccepted=false`，CLI 返回非 0；
  - 失败时仍先写出脱敏 assessment report，保留 `missingEvidence` 供复核。
- 测试覆盖：
  - positive：第 101 节 fake-server gate report 加 `--require-accepted` 返回 0；
  - negative：移除 71-byte ACK-like evidence 后，加 `--require-accepted` 返回 1，并写出
    `missingEvidence` 包含 `same_remote_ack_like_71`。

对应官方 trace 字段：

- `same_remote_ack_like_71` 仍对应 official same external fd `recv len=71 ACK-like`；
- 非零退出只表达当前 gate 证据缺失，不引入新的协议字段；
- 即使验收 CLI 返回 0，也只代表 AUTH gate-only 证据可复核，不代表
  AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run 成功。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `check-rap-zime-auth-gate-report` 新增 `--require-accepted`；
  - 缺少 gate evidence 时抛出 `CmccError`，由 CLI main 返回 1。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 `--require-accepted` 的 positive/negative 退出码和脱敏 assessment 写入。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 现在可以在下一次 gate-only live 后用 shell 退出码强制卡住后续阶段；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 104. 2026-07-05：AUTH gate preflight 支持 ready 非零退出

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是让
用户侧 explicit material no-network preflight 也能作为 shell gate 使用：配置不完整时直接
返回非 0，避免在 preflight 未 ready 时误跑 gate-only live。

新增信息：

- `rap-zime-kcp-auth-from-cag --auth-gate-preflight-only` 新增
  `--require-preflight-ready`：
  - 默认行为不变：输出/写入 no-network preflight audit；
  - 传入该参数时，如果 `readyForGateOnlyLiveAttempt=false`，CLI 返回非 0；
  - 失败时仍写出脱敏 preflight report，保留 `missingConfiguration` 供复核。
- 测试覆盖：
  - positive：explicit material + pre-AUTH cmd26 local proxy config +
    `--pre-auth-state-contract` 时返回 0；
  - negative：缺少 cmd26 local proxy 和 state contract 时返回 1，report 中
    `missingConfiguration` 包含 `pre_auth_cmd26_local_proxy` 和
    `type6_proxy_fd_session_slot`，且不保存敏感明文。

对应官方 trace 字段：

- 该 shell gate 只验证下一次 live attempt 的配置就绪性：
  - AUTH_HEAD wireLen `199`；
  - AUTH_DATA wireLen `241`；
  - loopback `send len=160 cmd26` 配置存在；
  - local proxy/session state contract 配置完整；
- 它不证明 same-fd `recv len=71 ACK-like`，也不允许推进
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 新增 `--require-preflight-ready`；
  - preflight-only 分支在 not ready 时抛出 `CmccError`，由 CLI main 返回 1。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file`，
    覆盖 positive/negative 退出码、`missingConfiguration` 和脱敏。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 现在可以先用 preflight shell gate 卡住配置缺口，再用 report shell gate 卡住
  live evidence 缺口；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 105. 2026-07-05：preflight ready gate 误用保护

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是修复
第 104 节新增 `--require-preflight-ready` 的一个误用边界：该参数只对
`--auth-gate-preflight-only` 有意义，不能在 live 分支被静默忽略。

新增信息：

- `rap-zime-kcp-auth-from-cag --require-preflight-ready` 如果未同时传
  `--auth-gate-preflight-only`，现在会立即返回非 0；
- 该校验发生在 `protocol_runner.fetch_cag_auth_connect_info()` 之前；
- 因此误用该参数不会触发 state-backed CAG material fetch，也不会发送 local proxy/UDP
  live 流量。

对应官方 trace 字段：

- 本轮不新增协议字段；
- 该修正对应执行边界：preflight shell gate 只验证下一次 live attempt 的配置就绪性，
  不能替代 official same external fd `recv len=71 ACK-like`。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `cmd_rap_zime_kcp_auth_from_cag()` 开头增加参数组合校验；
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_rejects_preflight_ready_gate_without_preflight`；
  - 断言误用时返回 1，并且不会调用 state-backed CAG fetch。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_rejects_preflight_ready_gate_without_preflight tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 的用户侧流程现在有两个明确 shell gate：
  preflight 配置 gate 和 live report evidence gate；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 106. 2026-07-05：AUTH gate report 验收收紧到同 fd/同 remote 脱敏证据

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是补强
第 102/103 节的 gate-only report 验收器：不能只凭 71 字节长度和 ACK-like 标记接受 report，
还必须有脱敏的同外网 fd/同 remote 证据。

新增信息：

- `_recv_auth_head_gate_ack()` 在每条 auth_head response summary 中新增：
  - `sameExternalFdAsAuthHead=true`：该响应由发送 AUTH_HEAD 的同一个 UDP socket/fd 收到；
  - `sameRemoteAsAuthTarget=true/false`：该响应 remote 是否等于本次 AUTH_HEAD 发送目标；
  - 仍不保存 payload，也不输出 remote 明文。
- `assess_auth_gate_only_report()` 的 `same_remote_ack_like_71` 检查现在要求：
  - `officialAuthHeadAckLike=true`；
  - `bytesReceived=71`；
  - `sameExternalFdAsAuthHead=true`；
  - `sameRemoteAsAuthTarget=true`；
  - `authPreflight.authHeadAckLikeReceived=true`。
- 测试覆盖：
  - positive：fake-server gate-only report 中 ACK-like response 带有同 fd/同 remote 布尔证据，
    report 仍被验收；
  - negative：把同 remote 布尔证据改为 false，即使 71 字节 ACK-like 标记仍在，也会缺失
    `same_remote_ack_like_71`，`--require-accepted` 不能放行。

对应官方 trace 字段：

- `sameExternalFdAsAuthHead=true` 对应 official external fd 上
  `AUTH_HEAD len=199 -> recv len=71 -> AUTH_DATA len=241` 的同 fd 顺序；
- `sameRemoteAsAuthTarget=true` 对应该 71 字节响应来自 AUTH_HEAD 的同一外网 peer；
- 本轮没有新增协议字段，只把验收器从“71 字节 ACK-like”收紧为
  “同 fd/同 remote 的 71 字节 ACK-like”。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - auth_head response summary 新增同 fd/同 remote 脱敏布尔字段；
  - `assess_auth_gate_only_report()` 收紧 `same_remote_ack_like_71`。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖同 fd/同 remote 正向和同 remote 缺失负向路径。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，115 tests OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 的 gate report 验收现在更贴近 official trace 的同 fd/同 remote 证据；
- 下一步仍是用户侧 explicit material no-network preflight，ready 后才可在明确
  session-owning 窗口跑 gate-only live，并用 `check-rap-zime-auth-gate-report
  --require-accepted` 复核 report；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 107. 2026-07-05：live 分支增加 gate readiness 前置失败开关

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是补强
用户侧执行边界：即使 live 分支默认 gate-only，如果调用者漏配 pre-AUTH cmd26 local proxy
或 state contract，之前仍可能直接进入 live probe。现在可以在 live 分支显式要求同参数
no-network readiness 审计先通过，否则在发任何 local proxy/UDP 流量前失败。

新增信息：

- `rap-zime-kcp-auth-from-cag` 新增 `--require-live-gate-ready`：
  - 仅在非 `--auth-gate-preflight-only` 的 live 分支作为前置 shell gate 使用；
  - live 前先用同一组 auth/connectInfo、cmd26/state、AUTH_HEAD pump 参数构造脱敏
    `auth-gate-live-preflight-audit`；
  - 如果 `readyForGateOnlyLiveAttempt=false`，CLI 返回非 0，输出/写入脱敏 preflight report，
    并且不调用 `run_kcp_auth_sync_probe_from_cag_material()`；
  - 如果 ready，再进入原 gate-only live 流程，并在最终 live report 中加入
    `liveGateReadinessPreflight` 脱敏摘要。
- 新增 `_build_auth_gate_preflight_report()`，让 preflight-only 分支和 live 前置 gate 共用同一
  readiness 逻辑，避免两套配置判断漂移。
- 测试覆盖：
  - negative：explicit material + `--require-live-gate-ready` 但未配置 cmd26/state contract，
    返回 1，写出脱敏 preflight report，且 fake live probe 不会被调用；
  - positive：fake-server gate-only live 加上 `--require-live-gate-ready` 后仍能完成
    `cmd26 send160/status1 -> AUTH_HEAD199 -> ACK-like71 -> AUTH_DATA241 -> stop`，最终 report
    带 ready 摘要。

对应官方 trace 字段：

- 该开关只卡住下一次 live attempt 的前置条件：
  - loopback `send len=160 cmd26` 配置存在；
  - local proxy/session state contract 配置完整；
  - AUTH_HEAD wireLen `199`、AUTH_DATA wireLen `241`；
  - AUTH_HEAD pump 至少三次；
- 它不证明 official same external fd `recv len=71 ACK-like`，也不允许推进
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

代码更新：

- `cmcc_cloud_alive/main.py`
  - 新增 `_build_auth_gate_preflight_report()`；
  - `rap-zime-kcp-auth-from-cag` 新增 `--require-live-gate-ready`；
  - live 分支在 readiness 不足时写脱敏 preflight report 后返回非 0，不触发 live probe。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_live_gate_ready_blocks_unready_live`；
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 ready gate 正向路径和最终 report 摘要。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_live_gate_ready_blocks_unready_live tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，116 tests OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- TF-0061 的 live 执行边界现在可以用 `--require-live-gate-ready` 防止 preflight 未 ready 时
  误发 live 流量；
- 下一步仍是用户侧 explicit material no-network preflight，ready 后在明确 session-owning
  窗口跑 gate-only live，并用 `check-rap-zime-auth-gate-report --require-accepted` 复核；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 108. 2026-07-05：live 分支可直接要求 AUTH gate report 被验收

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是补齐
下一次用户侧 gate-only live 的 shell 闭环：此前 live 完成后需要再手动运行
`check-rap-zime-auth-gate-report --require-accepted`，现在 live 命令自身也可以直接复用同一
验收器并据此返回非 0，减少漏验风险。

新增信息：

- `rap-zime-kcp-auth-from-cag` 新增 `--require-auth-gate-accepted`：
  - 只允许用于 live gate run，不能和 `--auth-gate-preflight-only` 混用；
  - live report 生成后立即调用 `assess_auth_gate_only_report()`；
  - 最终 report 内嵌 `authGateAcceptance` 脱敏 assessment；
  - 若 `authGateOnlyAccepted=false`，CLI 返回非 0，错误信息列出 `missingEvidence`；
  - 独立的 `check-rap-zime-auth-gate-report --require-accepted` 仍保留，便于事后复核已有 report。
- 测试覆盖：
  - misuse：`--auth-gate-preflight-only --require-auth-gate-accepted` 在 fetch CAG material 前返回 1；
  - negative：fake incomplete live report 缺少 same fd/remote 71-byte ACK-like 时返回 1，
    report 写入 `authGateAcceptance.missingEvidence`；
  - positive：fake-server gate-only live 加上
    `--require-live-gate-ready --require-auth-gate-accepted` 后仍完成
    `cmd26 send160/status1 -> AUTH_HEAD199 -> ACK-like71 -> AUTH_DATA241 -> stop`，
    report 中 `authGateAcceptance.authGateOnlyAccepted=true`。

对应官方 trace 字段：

- 自动验收仍只接受当前唯一 gate：
  - loopback `send len=160 cmd26` 与 `recv status len=1`；
  - external `AUTH_HEAD len=199`；
  - same external fd/remote `recv len=71 ACK-like`；
  - subsequent `AUTH_DATA len=241`；
  - gate-only stop，不推进 AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT。
- 本轮不新增协议字段，只把第 102/103/106 节验收器接入 live 命令退出码。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `rap-zime-kcp-auth-from-cag` 新增 `--require-auth-gate-accepted`；
  - live 分支嵌入 `authGateAcceptance` 并在不 accepted 时返回非 0；
  - preflight-only 混用该参数时在 fetch CAG material 前失败。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_rejects_acceptance_gate_with_preflight`；
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_require_auth_gate_accepted_fails_incomplete_live_report`；
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 live 自动验收 positive path。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_rejects_acceptance_gate_with_preflight tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_live_gate_ready_blocks_unready_live tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_require_auth_gate_accepted_fails_incomplete_live_report tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，118 tests OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- 下一次用户侧 gate-only live 可以用一个 shell 命令同时要求 readiness 和 accepted report：
  `--require-live-gate-ready --require-auth-gate-accepted`；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 109. 2026-07-05：AUTH gate accepted report 纳入脱敏不变量

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是收紧
第 102/103/106/108 节的机械验收：即使 gate evidence 齐全，accepted report 也必须证明
auth payload、local proxy frame body、ACK-like payload 和 CAG material 都没有写入 report。

新增信息：

- `run_kcp_auth_sync_probe()` 的 live report 现在在 `authPreflight` 中显式写入
  `payloadStoredInReport=false`。
- `assess_auth_gate_only_report()` 新增脱敏验收项：
  - `no_auth_payload_stored`：AUTH_HEAD/AUTH_DATA wire summary 和 auth preflight 均不存 payload；
  - `no_local_proxy_payload_stored`：pre-AUTH cmd26 local proxy frame body 不存 report；
  - `no_ack_like_payload_stored`：auth_head responses 不存 ACK-like/local response payload；
  - `no_auth_material_payload_stored`：CAG auth material source 只保留结构摘要；
  - `no_sensitive_payload_fields`：report 中不能出现携带非布尔/非空值的敏感 payload 字段名，
    presence 布尔如 `vmPassword=true` 仅表达字段存在，不视为明文。
- 测试覆盖：
  - positive：fake-server accepted report 包含上述脱敏 checks 且全部通过；
  - negative：把 `preAuthLocalBootstrap.payloadStoredInReport` 改为 true 时不 accepted；
  - negative：人为加入 `authBuffer` 字段时不 accepted。

对应官方 trace 字段：

- 本轮不新增协议字段；
- accepted report 仍只接受当前唯一 gate：
  `cmd26 send160/status1 -> AUTH_HEAD199 -> same fd/remote ACK-like71 -> AUTH_DATA241 -> stop`；
- 新增 checks 对应硬约束：不输出 auth payload、local proxy frame body、token/connectStr/
  accessToken/cpsid/密码/JWT 等敏感明文。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - live `authPreflight` report 增加 `payloadStoredInReport=false`；
  - `assess_auth_gate_only_report()` 新增脱敏不变量检查。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 accepted report 的脱敏 checks 和两条负向路径。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_require_auth_gate_accepted_fails_incomplete_live_report -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，118 tests OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- `--require-auth-gate-accepted` 现在同时要求 gate evidence 和 report 脱敏不变量；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 110. 2026-07-05：live readiness gate 误用保护

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是补齐
第 107 节新增 `--require-live-gate-ready` 的参数边界：它只对 live 分支有意义，不能在
`--auth-gate-preflight-only` 分支被静默忽略。

新增信息：

- `rap-zime-kcp-auth-from-cag --auth-gate-preflight-only --require-live-gate-ready`
  现在会立即返回非 0；
- 错误信息明确提示 live readiness gate 需要 live gate run，preflight-only 应使用
  `--require-preflight-ready`；
- 该校验发生在读取 explicit CAG material 或 state-backed CAG material fetch 之前；
- 因此误用该参数不会读取敏感 material，也不会发送 local proxy/UDP live 流量。

对应官方 trace 字段：

- 本轮不新增协议字段；
- 该修正只对应执行边界：
  - preflight-only gate 验证下一次 live attempt 的配置就绪性；
  - live readiness gate 则是 live 分支发流量前的同参数 readiness 审计；
  - 两者都不能替代 official same external fd `recv len=71 ACK-like`。

代码更新：

- `cmcc_cloud_alive/main.py`
  - `cmd_rap_zime_kcp_auth_from_cag()` 开头增加
    `--require-live-gate-ready` 与 `--auth-gate-preflight-only` 的混用校验。
- `tests/test_python_modules.py`
  - 新增
    `test_rap_zime_kcp_auth_from_cag_cli_rejects_live_ready_gate_with_preflight`；
  - 断言误用时返回 1，并且不会调用 state-backed CAG fetch。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_rejects_live_ready_gate_with_preflight tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_rejects_preflight_ready_gate_without_preflight tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_live_gate_ready_blocks_unready_live -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，119 tests OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- 下一次用户侧流程仍是 explicit material no-network preflight，ready 后用
  `--require-live-gate-ready --require-auth-gate-accepted` 跑 gate-only live；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 115. 2026-07-05：移动云电脑客户端 GUI 操作定位约束

用户补充 GUI/客户端操作约束：

- 如后续必须使用移动云电脑客户端，先将客户端窗口置顶/聚焦；
- 置顶/聚焦后必须先截图确认窗口状态；
- 后续 GUI 点击或键鼠操作必须基于客户端窗口内的相对坐标定位，不能用全屏绝对坐标盲点；
- 该约束叠加既有硬约束：不操作 CrossDesk；任何 GUI 点击前必须截图确认。

阶段判断：

- 这不是协议 live 成功证据；
- 该约束只用于未来必要的客户端辅助采样/确认，不能替代当前 AUTH gate accepted report；
- 当前协议主线仍是 TF-0061：解决 same-fd/same-remote 71-byte ACK-like 缺失。

## 114. 2026-07-05：zqoe loopback 最小 gate-only live 与头部抓包

本轮按用户明确授权使用 `.tmp/state.json` 做 fresh CAG fetch，并对候选 loopback proxy
做最小探测。没有操作 GUI/CrossDesk，没有推进 AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/
40 分钟 verified-run。报告和摘录只记录脱敏结构、长度和 gate 结果，不输出 token/
connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。

新增信息：

- 当前 `.tmp/state.json` 初始登录态已过期，第一次 CAG fetch 在发包前失败：
  `code=4015 用户未登录，请先登录`；
- 运行 `token-check` 后自动 relogin 成功，随后进入真实 gate-only live；
- 本机候选 loopback proxy 不是随机扫端口，而是由监听归属收窄：
  - `127.0.0.1:3240` 归属 `zqoe.service`；
  - 同一服务还有两个 loopback UDP socket；
  - 明显无关的 clash/redis/IDA/node/1panel 等监听端口未作为 cmd26 目标。
- 第一次真实 live report：
  - `reports/auth-gate-live-zqoe-20260705-094805.json`；
  - `cmd26 bytesSent=160`；
  - `cmd26 statusReceived=true`、`statusBytesReceived=1`；
  - `AUTH_HEAD wireLen=199`；
  - `AUTH_HEAD sendCount=3`、`totalBytesSent=597`；
  - 未收到 same fd/same remote 71-byte ACK-like；
  - 未发送 `AUTH_DATA`；
  - `authGateAcceptance.failureStage=auth_head_ack_like`。
- 第二次同参数 live + 头部抓包：
  - report：`reports/auth-gate-live-zqoe-capture-20260705-095127.json`；
  - head-only pcap：`.tmp/auth-gate-live-zqoe-capture-20260705-095127-ens3-udp-headers.pcapng`；
  - loopback head-only pcap：`.tmp/auth-gate-live-zqoe-capture-20260705-095127-lo-3240-headers.pcapng`；
  - `dumpcap` 只保存头部 snaplen，应用层 payload 未保存；
  - 外网 UDP metadata 显示三帧 `frame.len=241`、`udp.length=207`，对应
    `UDP payload len=199`；
  - loopback metadata 显示一次 TCP payload len 160 发往 `127.0.0.1:3240`。
- 第三次同参数 live + sudo `tcpdump -i any` 元数据：
  - report：`reports/auth-gate-live-zqoe-sudo-capture-20260705-095318.json`；
  - tcpdump metadata：`.tmp/auth-gate-live-zqoe-sudo-capture-20260705-095318.tcpdump.log`；
  - 三次外网 UDP `length 199` 已在 any/ens3 视角观察到；
  - 没有观察到 inbound 71-byte ACK-like。

抓包说明：

- 之前 `tcpdump -i any` 不可用的原因是 `/usr/bin/tcpdump` 没有
  `cap_net_raw/cap_net_admin`，普通用户打开 raw socket 被内核拒绝；
- `/usr/bin/dumpcap` 具备 `cap_net_admin,cap_net_raw=eip`，所以无需 sudo 即可抓；
- 用户提供 sudo 后，`tcpdump -i any` 也可以运行；
- 为避免保存敏感 payload，本轮抓包使用头部级 snaplen，只保留长度/方向/端口等元数据。

对应官方 trace 字段：

- 已对齐：
  - loopback `send len=160 cmd26`；
  - local proxy status read；
  - 外网三次 `AUTH_HEAD len=199`，间隔约 80ms；
- 未对齐：
  - official trace 中同 fd/同 remote `recv len=71 ACK-like`；
  - 因此 Python 未发送 `AUTH_DATA len=241`。

阶段判断：

- 这是有效 live 负证据，不是离线/fake-server 结果；
- 本轮失败点已经从“是否能发出 cmd26/AUTH_HEAD”收窄到
  `auth_head_ack_like`：云端没有承认当前 Python AUTH gate；
- 在 accepted report 出来前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`；
- 下一步不应回到盲扫 `type101/type102/link_type/local_bind/ztec_prime`，而应围绕
  official trace 中 AUTH_HEAD 前仍缺失的 session/proxy side effect 或 destination/session
  binding 找差异。

## 116. 2026-07-05：zqoe local response 16 字节 drain 实测

第 114 节头部抓包显示 zqoe 的 loopback 响应不是原先 runner 记录的 1 字节，而是 TCP
payload 16 字节。本轮只围绕该新增信息做最小代码修正和 live 复测；没有推进
AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run，也没有保存/输出 local proxy
response payload。

新增信息：

- `_run_pre_auth_fresh_cmd26_bootstrap()` 原来只 `recv(1)`；
- 直接改成 `recv(64)` 会使 zqoe 出现 `ConnectionResetError`，report 无法确认 status；
- 正确的最小兼容读法是：
  - 先读 1 字节 status，保留旧行为；
  - 再用短 timeout drain 当前可用的后续字节；
  - drain 阶段如遇 reset，只记录 `statusDrainError`，不把已收到 status 判失败；
  - 只记录长度和错误类名，不保存 payload。
- 代码已更新为上述 first-byte + drain 语义；
- fake-server 单测覆盖 16 字节 local response，确认报告只记录
  `statusBytesReceived=16` 和 `payloadStoredInReport=false`。

真实 live 结果：

- report：`reports/auth-gate-live-zqoe-drain-after-status-20260705-101013.json`；
- tcpdump metadata：
  `.tmp/auth-gate-live-zqoe-drain-after-status-20260705-101013.tcpdump.log`；
- `cmd26 bytesSent=160`；
- `statusReceived=true`；
- `statusBytesReceived=16`；
- `statusDrainError=ConnectionResetError`；
- `AUTH_HEAD wireLen=199`；
- `AUTH_HEAD sendCount=3`、`stageTotalBytesSent=597`；
- 仍未收到 same fd/same remote 71-byte ACK-like；
- `authGateAcceptance.failureStage=auth_head_ack_like`。

对应官方 trace 字段：

- 本轮新增信息对应 loopback bootstrap 的 client-side local proxy response；
- 它解释了第 114 节本地抓包中 zqoe 返回 16 字节，而旧 report 只显示 1 字节的问题；
- 该修正未改变外网 gate 结果：三次 `AUTH_HEAD len=199` 后仍无 official `recv len=71`。

阶段判断：

- local response drain 不是当前 ACK-like 缺失的主因；
- 当前阻塞点仍是 `auth_head_ack_like`，下一步应继续比较 official trace 的
  AUTH_HEAD 前 session/proxy side effect 或 destination/session binding；
- 继续禁止推进 AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run。

## 113. 2026-07-05：AUTH gate CLI 非零错误输出失败阶段

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是把第 112
节新增的验收失败分类接入 shell gate：下一次用户侧 gate-only live 或事后 report check
失败时，CLI 非零错误信息直接带出第一处阻塞阶段。

新增信息：

- `rap-zime-kcp-auth-from-cag --require-auth-gate-accepted` 在 live report 不 accepted 时，
  错误信息除 `missingEvidence` 外，还包含：
  - `stage=<failureStage>`；
  - `check=<failureCheck>`；
  - `officialTraceField=<failureOfficialTraceField>`。
- `check-rap-zime-auth-gate-report --require-accepted` 使用同一格式；
- 这只输出脱敏 assessment 元数据，不输出 auth payload、ACK-like payload、local proxy frame body
  或 CAG material 明文。

对应官方 trace 字段：

- 本轮不新增协议字段；
- CLI 错误定位仍只围绕当前唯一 gate：
  `cmd26 send160/status1 -> AUTH_HEAD199 -> same fd/remote ACK-like71 -> AUTH_DATA241 -> stop`；
- 例如缺失 same fd/same remote 71-byte ACK-like 时，错误信息会带出
  `stage=auth_head_ack_like` 和
  `officialTraceField=same external fd/remote recv len=71 ACK-like`。

代码更新：

- `cmcc_cloud_alive/main.py`
  - 新增 `_auth_gate_acceptance_error()`，统一格式化 live 分支和独立 check CLI 的非零错误；
  - `cmd_rap_zime_kcp_auth_from_cag()` 与 `cmd_check_rap_zime_auth_gate_report()` 复用该格式。
- `tests/test_python_modules.py`
  - 扩展 live 自动验收失败测试，断言错误输出包含 `stage=auth_head_ack_like` 和官方 trace 字段；
  - 扩展独立 check CLI negative path，断言错误输出包含 `stage`、`check` 和官方 trace 字段。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_require_auth_gate_accepted_fails_incomplete_live_report tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- 下一次用户侧流程仍是 explicit material no-network preflight，ready 后用
  `--require-live-gate-ready --require-auth-gate-accepted` 跑 gate-only live；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 111. 2026-07-05：accepted report 敏感字段名检测规范化

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是继续
收紧第 109 节的 accepted report 脱敏不变量：敏感字段名不能通过大小写、下划线或短横线变体
绕过 `no_sensitive_payload_fields`。

新增信息：

- `assess_auth_gate_only_report()` 的敏感字段检测现在会先把字段名规范化：
  - 转小写；
  - 移除非字母数字字符；
  - 再与敏感字段集合比较。
- 因此 `accessToken`、`access_token`、`ACCESS-TOKEN` 会落到同一个检测类别；
  `localProxyFrameBody`、`local_proxy_frame_body`、`LOCAL-PROXY-FRAME-BODY` 同理。
- presence 布尔仍被允许，例如 `vmPassword=true` 只表达字段存在，不输出明文，不视为违规；
  但同名字段携带非布尔/非空值会导致 report 不被 accepted。
- 测试覆盖：
  - accepted fake-server report 仍通过；
  - 人为加入 `authBuffer` 仍不 accepted；
  - 人为加入 `access_token` 不 accepted；
  - 人为加入 `LOCAL-PROXY-FRAME-BODY` 不 accepted。

对应官方 trace 字段：

- 本轮不新增协议字段；
- accepted report 仍只接受当前唯一 gate：
  `cmd26 send160/status1 -> AUTH_HEAD199 -> same fd/remote ACK-like71 -> AUTH_DATA241 -> stop`；
- 该增强只对应硬约束：不输出 token/connectStr/accessToken/cpsid/密码/JWT/auth payload/
  local proxy frame body 等敏感明文。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `contains_sensitive_report_key()` 改为规范化字段名后匹配敏感集合。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 snake_case 和 dashed uppercase 敏感字段名负向路径。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，119 tests OK。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- `--require-auth-gate-accepted` 的脱敏验收现在能覆盖常见字段名变体；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 112. 2026-07-05：AUTH gate report 验收失败阶段分类

本轮没有运行 live probe、没有操作 GUI/CrossDesk、没有读取 `.tmp/state.json` 或任何真实
token/connectStr/accessToken/cpsid/密码/JWT/auth payload/local proxy frame body。目标是让下一次
用户侧 gate-only live 之后，accepted report 如果失败，能直接指出第一处阻塞在官方 gate
时序的哪个阶段，而不是只给出 missingEvidence 列表。

新增信息：

- `assess_auth_gate_only_report()` 的返回值新增：
  - `failureStage`：第一处阻塞阶段；
  - `failureCheck`：第一条失败 check key；
  - `failureOfficialTraceField`：该失败对应的官方 trace 字段。
- accepted report 这三个字段均为 `null`；
- missing same fd/same remote 71-byte ACK-like 时，`failureStage=auth_head_ack_like`；
- report 脱敏不变量失败时，`failureStage=report_redaction`。

对应官方 trace 字段：

- 本轮不新增协议字段；
- 分类仍只围绕当前唯一 gate：
  `cmd26 send160/status1 -> AUTH_HEAD199 -> same fd/remote ACK-like71 -> AUTH_DATA241 -> stop`；
- 失败分类只是把既有 `checks[].officialTraceField` 提升为顶层定位信息，不能替代
  same-fd 71-byte ACK-like 证据。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - `assess_auth_gate_only_report()` 基于现有 checks 派生 `failureStage` /
    `failureCheck` / `failureOfficialTraceField`。
- `tests/test_python_modules.py`
  - 扩展
    `test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server`，
    覆盖 accepted 为 null、ACK-like 缺失定位到 `auth_head_ack_like`、脱敏失败定位到
    `report_redaction`。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_explicit_material_gate_only_fake_server -v`
  通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- 下一次用户侧流程仍是 explicit material no-network preflight，ready 后用
  `--require-live-gate-ready --require-auth-gate-accepted` 跑 gate-only live；
- 未拿到 Python same-fd 71-byte ACK-like 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 117. 2026-07-05：AUTH_HEAD 前 native side-effect contract 固化

本轮没有推进 SPICE、DISPLAY_INIT、ACK/PONG、SYNACK/native bridge 或 40 分钟
verified-run。没有读取或输出 token/connectStr/accessToken/cpsid/密码/JWT/auth
payload/local proxy frame body。用户明确要求回到第一包 `AUTH_HEAD` 前的官方 native
side effects；本轮据此把现有 IDA 证据固化成 runner 报告里的独立 contract，并明确区分
“静态已复原”和“Python runner 等价实现未完成”。

新增信息：

- `local_proxy_protocol_header_link_type_detection`
  - 官方路径：`deal_unlinked_unknown_local_data()` 读取 4-byte local proxy header；
    默认写 `in_sock->data_buf[224]=1`，`check_spice_proxy_protocol_header()` 不接受时切到
    `2/outband`；
  - fresh cmd26 对应 accepted-side `recv len=4` header，再 `recv len=156`
    `ChannelLinkSocketEx` body；
  - Python 当前只向 zqoe 发 cmd26，不拥有 accepted-side `IceSocket`，因此没有真实
    `data_buf[224]` 被后续 `init_local_rw_sock_pair_udp()` 消费。
- `deal_create_proxy_fd_session_link_type_assignment`
  - 官方路径：`deal_local_link_proxy_create()` 用 `data_buf[224]` 经
    `get_proxy_type_by_link_type()` 得到 type6 route，缺 session 时调用
    `deal_create_proxy_fd_session(fd_type_ex=6)`；
  - 该函数写入 `proxy_sock->data_buf[224]=1`、`proxy_sock->cag_client_key=6`，并设置
    UDT/network-protocol gate、SSL/client-type gate、enable_cag 等字段；
  - Python 的 `--pre-auth-state-contract` 只能记录配置假设，不能等价物化 proxy fd
    session slot。
- `create_fd_session_TN_UDP_CLD_SOCK`
  - 官方路径：`init_local_rw_sock_pair_udp()` 调
    `create_fd_session(thread, proxy_udp_fd, TN_SVR_SOCK, TN_UDP_CLD_SOCK)`；
  - 之后复制 `proxy_sock->data_buf[224]`、UDT gate、enable_cag 到 `udp_sock`，并先建立
    `in_sock <-> udp_sock` pair；
  - Python 当前只有 raw UDP socket 和 `getsockname()`，没有 `IceSocket` wrapper、pair
    pointer 或 fd-session flags。
- `thread_kcp_list_attachment_before_deal_udt_using_cag`
  - 官方路径：`create_udt_session()` 创建 KCP 后，`init_local_rw_sock_pair_udp()` 先插入
    thread `kcp_list`，再设置 `kcp->user_data=udp_sock`、`kcp->be_using_cag`，随后才运行
    `deal_udt_using_cag(kcp, kcp->be_ssl)`；
  - `get_thread_kcp()` 对 cmd `1/2/7/9` 的响应绑定规则是 source port 匹配
    `kcp->dest_port` 且 `syn_id` 匹配 `kcp->syn_id`；
  - Python 当前直接构造 KCP 字节，没有 thread `kcp_list` 对象或 native auth state
    transition。

对应官方 trace 字段：

- loopback accepted-side `recv len=4` local proxy header；
- loopback accepted-side `recv len=156` `ChannelLinkSocketEx` body；
- loopback client-side `recv len=1` cmd26 status/control；
- same external fd 随后发送 `AUTH_HEAD len=199`；
- same external fd/remote 必须收到 `len=71` ACK-like 后才发送 `AUTH_DATA len=241`。

代码更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增 `pre_auth_native_side_effect_contract()`；
  - `build_auth_gate_live_preflight_audit_from_cag_material()` 与
    `run_kcp_auth_sync_probe()` 报告新增 `preAuthNativeSideEffectContract`；
  - contract 明确 `status=static_contract_recovered_runner_equivalent_not_implemented`，
    四个 side effect 的 `runnerEquivalentImplemented=false`。
- `tests/test_python_modules.py`
  - 新增断言覆盖四个 contract key、官方 trace 字段、payload 不落盘、以及
    `AUTH_HEAD199 length parity is insufficient` 的 gate 边界；
  - preflight/live fake-server 报告均断言 contract 存在。
- `cmcc_cloud_alive/main.py`
  - 补齐本轮早先加入的 `--udp-target-source` parser 参数，避免 CLI 半更新破坏测试；
    该参数不是本轮主线验收，不改变当前 AUTH gate 判定。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_sync_from_cag_material_can_model_pre_auth_cmd26_bootstrap -v` 通过；
- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_does_not_probe_live tests.test_python_modules.PythonModuleTests.test_rap_zime_kcp_auth_from_cag_cli_preflight_only_accepts_explicit_material_file -v` 通过。

阶段判断：

- 这不是 live 成功，也不是 Python 云端 session 被承认；
- 当前新增 contract 的作用是阻止把 `cmd26 + AUTH_HEAD199` 长度对齐误判为 gate 通过；
- 下一步应实现或动态证明这四个 native side effects 的 Python 等价路径，验收仍只看
  Python same-fd/same-remote `71-byte ACK-like` 后发送 `AUTH_DATA241`；
- 未拿到该证据前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 118. 2026-07-05：四个 AUTH_HEAD 前 native side effects 的 Python 等价模型与 fresh live 复测

本轮按用户要求不再停留在离线文字结论：已使用 `.tmp/state.json` 做 fresh CAG fetch，
针对本机 `127.0.0.1:3240` local proxy 跑了 gate-only live，并用 tcpdump 只抓包头/长度，
不保存 payload。未操作 CrossDesk，未推进 SPICE、DISPLAY_INIT、ACK/PONG、SYNACK/native
bridge 或 40 分钟 verified-run。未输出 token/connectStr/accessToken/cpsid/密码/JWT/auth
payload/local proxy frame body。

新增信息：

- `cmcc_cloud_alive/rap_zime.py` 新增
  `build_pre_auth_native_equivalent_state_model()`，把第 117 节四个 native side effects
  落成脱敏的 Python gate-only state graph：
  - `local_proxy_protocol_header_link_type_detection`：
    建模 `in_sock.data_buf_224=1` 与 cmd26 local proxy header accepted；
  - `deal_create_proxy_fd_session_link_type_assignment`：
    建模 type6 `proxy_sock` session slot、`proxy_sock.data_buf_224=1`、
    `proxy_sock.fd_type_ex=6`、`proxy_sock.cag_client_key=6`；
  - `create_fd_session_TN_UDP_CLD_SOCK`：
    建模 `udp_sock.sock_type=TN_UDP_CLD_SOCK`、从 `proxy_sock` 复制 link flag、
    并建立 `in_sock <-> udp_sock` pair；
  - `thread_kcp_list_attachment_before_deal_udt_using_cag`：
    建模 `kcp.user_data=udp_sock`、`thread.kcp_list` 在
    `deal_udt_using_cag()` 前已含该 KCP、`kcp.be_using_cag` 从 `udp_sock` 继承。
- `pre_auth_native_side_effect_contract()` 现在支持传入 runner model：
  - 无 live/local status 时仍保持
    `static_contract_recovered_runner_equivalent_not_implemented`；
  - cmd26 local proxy status 已收到且四个 state checks 都满足时，报告为
    `static_contract_recovered_runner_equivalent_modeled_for_gate_only`；
  - 即使该模型闭合，`runnerConsequence` 仍明确要求 same-fd 71-byte ACK-like live
    acceptance，不能用长度对齐替代云端承认。
- `officialParityAssessment.notModeledYet` 在四个 side effects 已由 gate-only state model
  闭合后，不再继续列出这四项；当前剩余 native 差异收窄为：
  - `local_tcp_listen_readiness_fd`；
  - `udp_get_tcp_link_info_gate`；
  - `listen_udp_data_thread_ice_deal_sock_loop`。

本轮 live/抓包：

- preflight report：
  `reports/auth-gate-preflight-native-state-20260705-105048.json`
  - `readyForGateOnlyLiveAttempt=true`；
  - 没有发 UDP AUTH_HEAD；
  - 因 preflight 不连接 local proxy、不读取 runtime status，contract 仍未标记 runner
    equivalent modeled。
- live report：
  `reports/auth-gate-live-native-state-20260705-105124.json`
  - cmd26 local proxy `bytesSent=160`；
  - local proxy status/control `statusBytesReceived=16`；
  - contract 进入
    `static_contract_recovered_runner_equivalent_modeled_for_gate_only`；
  - `AUTH_HEAD` 发送 3 次，每次 wire len 199，总计 597；
  - 未收到 same-fd/same-remote 71-byte ACK-like；
  - `authGateAcceptance.failureStage=auth_head_ack_like`；
  - 未发送 `AUTH_DATA241`。
- 第一次 tcpdump 用 `udp port 8899` 过滤过窄，没有捕到当前 fresh CAG 目标；
  第二次改为只按 UDP 长度范围抓包头：
  `reports/auth-gate-live-native-state-anyudp-20260705-105221.json`
  - 仍失败在 `auth_head_ack_like`；
  - tcpdump header-only 日志确认外发 3 个 UDP payload len=199；
  - 未观察到 71-byte ACK-like 或 241-byte AUTH_DATA。

对应官方 trace 字段：

- 已复现并验证：loopback client send len=160 cmd26；
- 已复现并验证：client-side recv local proxy status/control response；
- 已复现并验证：same external send `AUTH_HEAD len=199` 三连；
- 仍未复现：same external fd/remote recv `len=71` ACK-like；
- 因此仍未发送：`AUTH_DATA len=241`。

代码/测试更新：

- `cmcc_cloud_alive/rap_zime.py`
  - 新增脱敏 native-equivalent state model；
  - `preAuthSessionState` 增加 `nativeEquivalentStateModel`；
  - preflight/live report 的 `preAuthNativeSideEffectContract` 改为按 runner model
    计算 `runnerEquivalentModeled`；
  - `officialParityAssessment` 按已建模 side effects 收窄剩余缺口。
- `tests/test_python_modules.py`
  - 扩展 fake-server gate-only 测试，断言四段 state graph 都建模、payload 不落盘、
    contract status 可进入 gate-only modeled；
  - 继续断言该状态不等于云端接受，验收仍要 71-byte ACK-like。

验证结果：

- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- targeted unittest 通过：
  `test_rap_zime_kcp_auth_sync_from_cag_material_can_model_pre_auth_cmd26_bootstrap`、
  `test_rap_zime_auth_gate_field_diff_uses_official_zero_declared_len_tail`；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，119 tests OK；
- fresh live 已跑，但 gate 未通过。

阶段判断：

- 本轮不是纸上验证：已经跑 fresh CAG live 与 header-only 抓包；
- 四个用户点名的 native side effects 已有 Python gate-only 等价模型，并在 live report
  中随 cmd26 status 闭合；
- 云端仍不回 71-byte ACK-like，说明剩余突破口不应再是这四个 side effects 的布尔建模，
  而应继续复原 `local_tcp_listen_readiness_fd`、`udp_get_tcp_link_info_gate`、
  `listen_udp_data_thread_ice_deal_sock_loop` 是否有未被 Python 模拟的 native readiness/
  source/session 绑定；
- 未拿到 Python same-fd/same-remote `71-byte ACK-like` 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。

## 119. 2026-07-05：TCP listen readiness live 复测与 plan 证据矩阵落地

本轮继续执行，不再只做离线结论。上一轮新增的 `--pre-auth-tcp-listen-readiness`
已经有 fresh live report；本轮补齐文档、任务图与 plan.md 证据化落地，并新增一个只做
控制面脱敏判定的 `product-route-check`。未操作 CrossDesk，未推进
SYNACK/native bridge/DISPLAY_INIT/ACK/PONG/40 分钟 verified-run，未输出或写入
`.tmp/state.json`、token、connectStr、accessToken、cpsid、密码、JWT、auth payload、
local proxy frame body。

最新 live 事实：

- report：`reports/auth-gate-live-tcp-readiness-20260705-110204.json`；
- `preAuthTcpListenReadiness.enabled=true`，`listenReady=true`；
- 该模型对应官方字段：
  - `listen_udp_data()` 创建 `g_sock_listen_fd`；
  - `udp_get_local_port(g_sock_listen_fd)` 设置本地 TCP listen readiness port；
  - `spice_init_udp_thread()` 等待 `udp_get_tcp_link_info(nullptr)` 非空；
- 同一 live 仍只发送三次 `AUTH_HEAD`，每次 `bytesSent=199`；
- 未收到 same-fd/same-remote `71-byte ACK-like`；
- 未发送 `AUTH_DATA241`；
- `authGateAcceptance.failureStage=auth_head_ack_like`；
- `officialParityAssessment.notModeledYet` 收窄为
  `listen_udp_data_thread_ice_deal_sock_loop`。

新增代码/测试：

- 新增 `cmcc_cloud_alive/product_router.py`：
  - `classify_firm_auth_route()` 按 `getFirmAuth` 控制面字段族分类为
    `zte-cag`、`scg`、`hybrid-zte-cag-and-scg` 或 `error-no-route-material`；
  - `route_check()` 先走 `cloud.selected_user_service_id()`，继续限制家庭云电脑畅享版月包；
  - report 只保存字段存在性和 `routeClass`，不保存 endpoint、账号、密码、token 或 auth
    material。
- `cmcc_cloud_alive/main.py` 新增 CLI：
  - `product-route-check [user_service_id] --report-file PATH`；
  - 这是控制面 route 证据，不建链、不跑 UDP、不触发 AUTH gate live。
- `tests/test_python_modules.py` 新增 3 个测试：
  - ZTE CAG 控制面分类与报告脱敏；
  - SCG/hybrid/error 分类；
  - CLI 写入脱敏 report。
- 新增 `docs/plan-zte-evidence-matrix.md`：
  - 记录 `/home/demo/桌面/plan_zte_alive/plan.md` 的本地行数与 SHA-256；
  - 把 plan 中的 route-check、fresh CAG material、cmd26 bootstrap、CAG TCP/TLS/mux/raw
    SPICE、DISPLAY_INIT/verified-run 分成已证明/可验证/冻结三类；
  - 不复制 plan 中敏感明文。
- fresh 控制面 route-check：
  - 命令：
    `python3 bin/cmcc_cloud_alive.py --state .tmp/state.json product-route-check --report-file reports/product-route-check-20260705-1432.json`；
  - report：`reports/product-route-check-20260705-1432.json`；
  - `routeClass=zte-cag`；
  - `SCG route candidate` 字段族为 absent；
  - `ZTE CAG route candidate` 字段族为 present；
  - `AUTH gate runner path` 状态仍为 `not_proven_by_control_plane_route_check`。

验证结果：

- `python3 -m unittest tests.test_python_modules.PythonModuleTests.test_product_route_check_redacts_and_classifies_firm_auth tests.test_python_modules.PythonModuleTests.test_product_route_classifies_scg_and_error_candidates tests.test_python_modules.PythonModuleTests.test_product_route_check_cli_writes_redacted_report -v`
  通过；
- `python3 -m compileall -q cmcc_cloud_alive tests scripts/ida_extract_spice_zime.py` 通过；
- `python3 -m unittest discover -s tests -p 'test_python_*.py' -v` 通过，119 tests OK。

阶段判断：

- `local_tcp_listen_readiness_fd` 与 `udp_get_tcp_link_info_gate` 已由 Python gate-only live
  readiness 建模，仍不能换来云端 71-byte ACK-like；
- `product-route-check` 只证明控制面 route 分类，不能替代 AUTH gate；
- 下一步应集中复原或动态证明
  `listen_udp_data_thread_ice_deal_sock_loop` 是否在 AUTH_HEAD 前产生 socket/session/source
  绑定副作用；
- 未拿到 Python same-fd/same-remote `71-byte ACK-like` 并随后发送 `AUTH_DATA241` 前，继续冻结
  `AUTH_ACK/SYNACK/native bridge/DISPLAY_INIT/40 分钟 verified-run`。
