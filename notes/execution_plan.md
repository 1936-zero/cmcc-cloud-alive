# EXECUTION PROTOCOL — cmcc-cloud-alive 落地 ZTE 保活全栈（对标 cloud-computer-keepalive 成品）

> 本协议由"逐段精读 B 成品 Go 源码 + 逐文件核验 C 现状 + 全读 GPT 版 gap-plan(1044行) 融合"产出。所有行号/偏移均来自实际读取，供执行 agent 直接照抄实现。
> 状态：**规划态，待用户确认后执行**。本文件不写任何 C 仓库代码，只给执行蓝本。

---

## §0 需求与硬约束

### §0.0 账号凭据（执行 agent 直接抄用这一节）

> ⚠️ 持久化策略：**仅在执行态内存中使用，绝不落盘日志/commit/明文文件**。写入 state.json 时若有此字段须脱敏或仅存 token 派生项；日志/异常打印一律 redact。

| 项 | 值 |
| --- | --- |
| 登录账号 | `gui1949` |
| 登录密码 | `Gui1936@@@` |
| 调用位置 | P1-007（route-check 实跑 firmAuth）、P5-009（first_desktop 严格匹配畅享版月包 vmId 之前需先登录） |

落地目标账号即此账号，全程只对它操作。

### §0.1 目标与路线

- **目标**：让 C 项目达到 B 成品等价的"云电脑桌面长期保活"——登录一次后，周期性维持云端桌面电源状态为"运行"，避免空闲自动关机。**只走 ZTE 路线落地**（SCG 仅占位不实现，理由见 §5）。
- **保活范围（最强硬约束）**：**仅保活"家庭云电脑畅享版月包"一个云桌面**，其他云桌面绝对不动。选桌面时用 `first_desktop(list, vmId)` 严格匹配，非目标 vmId 一律跳过，绝不开机/保活。
- **不执行声明**：本文件只是 plan，不含可执行代码落地动作；C 仓库代码改动须在用户确认本 plan 后、进入执行态才进行。

---

## §1 三项目绝对路径（给其他 agent 看懂）

| 符号 | 路径 | 语言 | 角色 |
| --- | --- | --- | --- |
| **B（成品蓝本）** | `/home/demo/cloud-computer-keepalive` | Go | 字节链完整实现，移植蓝本，**只读不写** |
| **C（待实现）** | `/home/demo/restore/cmcc-cloud-alive` | Python | 本次落地目标仓库 |
| **GPT gap-plan（参考）** | `/home/demo/.local/opt/GenericAgent-Desktop-Linux-Portable-v0.1.4/runtime/app/temp/desktop_uploads/sess-632b1ed1ca83/26d9f26de1c9__cmcc-cloud-alive-gap-plan.md` | md | 原子卡格式与治理章来源（1044 行，只读参考） |

B 原子卡字节蓝本关键文件绝对路径：
- `B/internal/spice/raw.go` — raw ZTE SPICE（729B REDQ:234-258 / 128零ticket:69-77 / RawState serial:360-397 / DISPLAY_INIT:456-459 / PING\SET_ACK\0x74 auto reply:407-437）
- `B/internal/zte/client.go:74-155` — ZTE HTTPS material 链（sysConfig→getToken→getDesktopList→startDesktop→async query）
- `B/cmd/keepalive.go` — 分流入口（56-63 scAuthCode=="" 走 ZTE：118/179/188/190/198/211/212/217/221/225/226）

---

## §2 探索发现（坐实证据汇总）

### 2.1 B 成品 ZTE 字节链全栈（精读行号偏移，移植蓝本）

```text
[HTTPS material 控制面]
getFirmAuth -> cs_sysConfig.action
  -> cs_getToken.action         (B: internal/zte/client.go:74-155)
  -> cs_getDesktopList.action   (first_desktop 严格匹配 vmId)
  -> cs_startDesktop.action -> async query 30x2s
  -> connectStr = DecodeConnectString(VDI AES-CBC/PKCS7)

[outer CAG transport: TCP/TLS 主路]
build_cag_auth_head_packet  len=199 payload"ZTEC"
  TCP first packet = packet[21:]  len=178          (B: cag_tcp.go)
  -> 读 50B local-key ACK, 校验 magic ZTEC
  -> conv = Uint32(headAck[14:18])                 (坐实:cag_tcp.go conv偏移14:18)
build_cag_auth_blob 220B offsets
  -> 发 TCP auth blob 220B -> 读 36B auth ACK
  -> 校验 authAck[4]==0x01 -> 同 socket TLS upgrade
  -> 返回 TLSStream + CAGSessionInfo(conv)

[CAG mux 多链: 4B frame, 主链 linkID=1 + 7 子链]
  cmd/data/add/close 常量, linkID dispatch, link queue, payload 分片
  100ms 轮询鉴权                              (B: cag_mux.go 266行)

[raw ZTE SPICE main 链]
729B main REDQ -> 抠 0x30 0x81 0x9f -> 162B RSA SPKI parse
  -> 128B zero ticket (不带 auth-type)
  -> 读 4B auth result (==0 才继续)
  -> MAIN_INIT 0x67 提取 session id
  -> ATTACH_CHANNELS / clientInfo / terminalInfo
  -> 见 0x68 或 0x73 判定 init OK

[subchannels + display loop]
links 2/3/4/5/6/7/8 顺序 add-link
  -> 每子链 REDQ -> 128零ticket -> result==0 authed
  -> link 5/7 发 DISPLAY_INIT bytes
  -> PING->PONG / SET_ACK->ACK_SYNC / 0x74->0x79 auto reply
  -> 25s heartbeat 标记                                (B: 25s SOHO heartbeat)
```

### 2.2 C 现状与缺口（坐实，三分件需从零补）

C 已实现近 80%：
- `C/cmcc_cloud_alive/protocol_runner.py` ProtocolSession：SPICE 单链完整（DISPLAY_INIT/SET_ACK/PING-PONG/SURFACE/DRAW/MARK-ACK + 24B 解帧）
- `fetch_cag_auth_connect_str` 已做 ZTE RSA
- zime/rap 路径返回 not_implemented

**C 缺口（本次要补的三件）：**

| 缺口 | B 蓝本 | C 现状 |
| --- | --- | --- |
| ① CAGMux 多链 | `B/internal/...cag_mux.go`(266行) 主链+7子链 100ms轮询鉴权 | 无对应实现 |
| ② RawMain REDQ+鉴权 | `B/internal/spice/raw.go:234-258` 729B→162B SPKI→128零ticket→4B result==0 | 无 |
| ③ 25s heartbeat 接通 loop | heartbeat 端点 | `cag_keepalive.run_verify` 有端点但未接 loop |

### 2.3 route 分流架构（GPT §0/§8 锐评要点，必须先建）

B `cmd/keepalive.go:56-63`：`scAuthCode==""` 走 ZTE route，否则走 SCG。
C `strategy.py:90-97` 当前直接抛 "not implemented yet"——**没有 route-check 闸门，盲目进协议**。
GPT §9.4 锐评：C 现有 CAG HTTPS material（`cs_connectDesktop`）≠ 成品 ZTE material（`cs_sysConfig/getToken/getDesktopList/startDesktop`），少了对"桌面选择/启动/connectStr 生成"的完整控制面。

---

## §3 风险与不确定点（执行前需消解，标 [?]）

- [?] `gui1949` 当前 firmAuth 实跑，`scAuthCode` 是否为空（决定走 ZTE 或 SCG）——执行态 P1 实跑一次 route-check 即知。
- [?] conv 偏移 `headAck[14:18]` 的字节序（little/big）——P7-007 按 Go 单测 fixture 确认。
- [?] 128B zero ticket 是否真的不带 auth-type——P9-007 按 raw.go:69-77 对齐，bytes fixture 验证。
- [?] 25s heartbeat 端点的 payload schema——P10-018 执行态抓一次实际包对齐。

---

## §4 原子任务卡（对齐 GPT 四列粒度：ID|文件|原子动作|验收）

> 全部标 `[ ]`，执行态每轮 `file_read` 找下一个 `[ ]`，做掉打 `[x]`。结束须 0 个 `[ ]` 残留。
> 标签 `[SOP]`=需查 SOP｜`[D]`=依赖前置｜`[P]`=探测确认｜`[?]`=不确定点

### P0 — 冻结空转治理（先于一切，防止又在 research track 空转）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[x]` P0-001 | `C/docs/no-spin-rules.md` | 写明 `rap_zime.py` 属 research track，非 product default | 文档出现 `research track does not block product route` |
| `[x]` P0-002 | 同上 | 写明没有 route-check 前不跑 live CAG/SCG | 文档出现 `no live run before route-check` |
| `[x]` P0-003 | 同上 | 写明 ZTE route 先 TCP/TLS，UDP/KCP 只做 fallback | 文档列出 `TCP first, UDP fallback` |
| `[x]` P0-004 | 同上 | 写明每次 live run 必须有 stage 字段 | report schema 含 `route/stage/ok/error/nextStep` |
| `[x]` P0-005 | 同上 | 写明禁止无证据变体实验 | 文档列出 `no new variant without one new evidence field` |

### P1 — 产品分流合同（route-check 闸门，GPT §11 最短突破点）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[x]` P1-001 | `C/cmcc_cloud_alive/product_router.py` | 新建模块，只做 route 判断不建链 | import 通过 |
| `[x]` P1-002 | 同上 | 定义 `RouteKind = scg/zte/error` | 单测覆盖三枚举 |
| `[x]` P1-003 | 同上 | 定义 `ProductRoute` dataclass | 字段 `kind/reason/userServiceId/vmId` |
| `[x]` P1-004 | 同上 | 从 firmAuth 提取 `scAuthCode`，有值→kind=scg | fixture 通过 |
| `[x]` P1-005 | 同上 | 从 firmAuth 提取 `vmUserName/vmPassword`，缺失→ZTE 不可用 | fixture 通过 |
| `[x]` P1-006 | 同上 | 从 firmAuth 提取 `vmId/vmID/uuid` `vmcIp/vmcIP` `vmcPort/vmcPORT` `cagIp/cagPort` | multi-key fixture 覆盖 |
| `[x]` P1-007 | 同上 | 接入账号 `gui1949`/`Gui1936@@@`，实跑 firmAuth 一次 | live: kind=zte reason=scAuthCode empty and ZTE fields complete |
| `[x]` P1-008 | 同上 | scAuthCode 优先级高于 ZTE fields | 同时有两值→kind=scg |
| `[x]` P1-009 | 同上 | scAuthCode 空但 ZTE fields 全→kind=zte | fixture 通过 |
| `[x]` P1-010 | 同上 | redacted summary 不输出 token/password/connectStr | redaction test |
| `[x]` P1-011 | `C/main.py` | 新增 `product-route-check` CLI | 只输出 redacted route report |
| `[x]` P1-012 | `C/tests/` | route-check CLI fixture test | 不联网也能跑 |

> **闸门**：P1-007 实跑，若 kind≠zte 则停下问用户（本账号按约束应走 zte）。

### P5 — ZTE material 控制面（route==zte 后进入）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P5-001 | `C/cmcc_cloud_alive/zte_route.py` | 新建模块 | import 通过 |
| `[ ]` P5-002 | 同上 | 定义 `ZTEFirmAuth` dataclass | 字段等于 Go FirmAuth |
| `[ ]` P5-003 | 同上 | 建 CAG HTTPS client host=firm.CAGIP port=firm.CAGPort | import 通过 |
| `[ ]` P5-004 | 同上 | sysConfig query 参数对齐 Go version/requestFrom/name/RspSecurity | fake server 验证 |
| `[ ]` P5-005 | 同上 | 实现 `EncodeVDIPassword` AES block+base64 | 单测通过 |
| `[ ]` P5-006 | 同上 | getToken query/body 对齐 Go | fake server 验证 |
| `[ ]` P5-007 | 同上 | 解析 accessToken，缺失 stage=`zte_get_token` | fixture |
| `[ ]` P5-008 | 同上 | getDesktopList query 对齐 Go | fake server 验证 |
| `[ ]` P5-009 | 同上 | 实现 `first_desktop(list, vmId)`：**严格匹配畅享版月包 vmId** | 非目标 vmId 跳过且不开机 |
| `[ ]` P5-010 | 同上 | startDesktop body 对齐 Go | fixture byte/schema test |
| `[ ]` P5-011 | 同上 | 解析 connectStr，空则 async query 30x2s 可配置 | fake 第 N 次返回 |
| `[ ]` P5-012 | `C/cmcc_cloud_alive/zte_security.py` | 移植 `DecodeSecurityParams` AES-CBC/PKCS7 | 单测 |
| `[ ]` P5-013 | 同上 | 移植 `EncodeSecurityParams` 输出 uppercase hex | 单测 |
| `[ ]` P5-014 | 同上 | 移植 `DecodeConnectString` VDI AES decrypt | 单测 |
| `[ ]` P5-015 | `C/cmcc_cloud_alive/zte_connect_params.py` | 移植 command-line splitter quote/backslash | 单测 |
| `[ ]` P5-016 | 同上 | 解析 `-h/-p/-k/--vmid/--proxy-sport/--vmip` | fixture 通过 |
| `[ ]` P5-017 | `zte_route.py` | 输出 redacted material report | 不落 connectStr/key/password/token |

### P6 — ZTE outer/inner 严格分离（防把外层 CAG 与内层 SPICE 混了）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P6-001 | `zte_route.py` | 定义 `OuterCAGTarget` 只含 firm CAG host/port | 类型签名检查 |
| `[ ]` P6-002 | `zte_connect_params.py` | 定义 `InnerConnectParams` 来自 connectStr | 单测 |
| `[ ]` P6-003 | `zte_route.py` | dial 只接受 OuterCAGTarget | 类型签名检查 |
| `[ ]` P6-004 | `zte_cag.py` | auth blob 只接受 InnerConnectParams | 内外不同值单测 |
| `[ ]` P6-005 | `zte_cag_mux.py` | add-link 只接受 InnerConnectParams | 单测 |
| `[ ]` P6-006 | `product_router.py` | route report 显示 outer/inner present 不显示值 | redaction test |
| `[ ]` P6-007 | `C/tests/` | fixture outer=111.31.x inner=10.10.x 不互相覆盖 | fixture 通过 |

### P7 — ZTE CAG TCP/TLS 主路径

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P7-001 | `zte_cag.py` | 移植 `build_cag_auth_head_packet` len=199 payload ZTEC | bytes fixture |
| `[ ]` P7-002 | 同上 | TCP first packet = `packet[21:]` len=178 | bytes fixture |
| `[ ]` P7-003 | 同上 | TCP dial outer CAG | fake server accept |
| `[ ]` P7-004 | 同上 | 发送 178B local-key | fake 收 ZTEC |
| `[ ]` P7-005 | 同上 | 读取 50B local-key ACK | 长度不足 stage error |
| `[ ]` P7-006 | 同上 | 校验 ACK magic ZTEC | 非 ZTEC fail |
| `[ ]` P7-007 | 同上 | 解析 conv offset 14:18 `[?]` | fixture 通过 |
| `[ ]` P7-008 | 同上 | 移植 `build_cag_auth_blob` 220B offsets test | bytes fixture |
| `[ ]` P7-009 | 同上 | 支持 env auth template 241B strip 21B | fixture |
| `[ ]` P7-010 | 同上 | 支持 env auth template 220B patch vmId | fixture |
| `[ ]` P7-011 | 同上 | 发送 TCP auth blob 220B | fake 收 220B |
| `[ ]` P7-012 | 同上 | 读取 36B auth ACK | fake 返回 |
| `[ ]` P7-013 | 同上 | 校验 `authAck[4]==0x01` | false fail |
| `[ ]` P7-014 | 同上 | 同 socket TLS upgrade | fake TLS 通过 |
| `[ ]` P7-015 | 同上 | 返回 TLSStream + CAGSessionInfo(conv) | conv present |
| `[ ]` P7-016 | `C/tests/` | fake CAG TCP/TLS integration | L9-TCP 通过 |

### P8 — ZTE CAG mux 多链（缺口①）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P8-001 | `zte_cag_mux.py` | 定义 mux cmd 常量 data/add/close 与 Go 对齐 | 常量对齐 |
| `[ ]` P8-002 | 同上 | 实现 4B frame pack cmd/linkID/u16len | bytes fixture |
| `[ ]` P8-003 | 同上 | 实现 frame parse | partial read test |
| `[ ]` P8-004 | 同上 | 实现 mux read loop linkID dispatch | fake test |
| `[ ]` P8-005 | 同上 | 实现 link queue 多 link 不串 | fixture |
| `[ ]` P8-006 | 同上 | 实现 link read buffer 支持 payload 分段 | fixture |
| `[ ]` P8-007 | 同上 | 实现 link write split payload>max 分片 | fixture |
| `[ ]` P8-008 | 同上 | 实现 close frame → EOF | fake close test |
| `[ ]` P8-009 | 同上 | 实现 deadline timeout 不永久阻塞 | test |
| `[ ]` P8-010 | `zte_cag_proxy.py` | 移植 add-link packet builder | byte fixture |
| `[ ]` P8-011 | 同上 | 生成 linkUUID/traceID/spanID | 长度正确 |
| `[ ]` P8-012 | 同上 | IP byte order 对齐 Go | fixture |
| `[ ]` P8-013 | `zte_cag_mux.py` | `open_link(1)` 发送 add-link | fake mux 收到 |
| `[ ]` P8-014 | `C/tests/` | fake mux multi-link integration | L10-mux 通过 |

### P9 — ZTE raw SPICE main（缺口②）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P9-001 | `zte_raw_spice.py` | 新建模块 | import 通过 |
| `[ ]` P9-002 | 同上 | 移植 main 729B REDQ builder B:raw.go:234-258 | len/offset/caps test |
| `[ ]` P9-003 | 同上 | 写 main REDQ 到 link1 | fake server 收 REDQ |
| `[ ]` P9-004 | 同上 | 读 16B REDQ header magic/size 校验 | fixture |
| `[ ]` P9-005 | 同上 | 读 REDQ body size<=4096 | test |
| `[ ]` P9-006 | 同上 | 提取 RSA key 162B SPKI parse | parse 成功 |
| `[ ]` P9-007 | 同上 | 发送 128B zero ticket 不发 auth-type `[?]` | bytes fixture |
| `[ ]` P9-008 | 同上 | 读 4B auth result ==0 才继续 | fixture |
| `[ ]` P9-009 | 同上 | 定义 `RawState` lastSerial/lastSuffix/nextSerial | test |
| `[ ]` P9-010 | 同上 | 实现 6B mini header read msgType/size | test |
| `[ ]` P9-011 | 同上 | 实现 ZTE serial prefix branch size==0 读 prefixTail | test |
| `[ ]` P9-012 | 同上 | 实现 suffix capture TakeReadBufferN(5) 等价 | test |
| `[ ]` P9-013 | 同上 | 提取 MAIN_INIT session id marker fallback | test |
| `[ ]` P9-014 | 同上 | MAIN_INIT 后 discard read buffer | fake link test |
| `[ ]` P9-015 | 同上 | 发送 ATTACH_CHANNELS serial 对齐 | test |
| `[ ]` P9-016 | 同上 | 发送 clientInfo hex 对齐 Go | test |
| `[ ]` P9-017 | 同上 | 发送 terminalInfo GUID 对齐 Go | test |
| `[ ]` P9-018 | 同上 | 见 0x68 或 0x73 判定 init OK | fake server |
| `[ ]` P9-019 | `C/tests/` | fake raw main handshake | L11-main 通过 |

### P10 — ZTE raw subchannels&display+keepalive loop（缺口③）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P10-001 | `zte_raw_spice.py` | 移植 raw channel REDQ builder type 2/5/6 caps | test |
| `[ ]` P10-002 | `zte_route.py` | 打开 links 2/3/4/5 add-link 顺序对齐 Go | fixture |
| `[ ]` P10-003 | 同上 | 发送前三个 sub REDQ link 3/2/4 顺序 | fixture |
| `[ ]` P10-004 | 同上 | 打开 links 6/7/8 trace/span 复用 main | fixture |
| `[ ]` P10-005 | 同上 | 发送剩余 sub REDQ link 6/7/8/5 顺序 | fixture |
| `[ ]` P10-006 | `zte_raw_spice.py` | subchannel 读 REDQ reply 含 REDQ 才送 ticket | fake link |
| `[ ]` P10-007 | 同上 | subchannel 发 128B zero ticket | fake link |
| `[ ]` P10-008 | 同上 | subchannel 读 4B auth result==0 authed | fixture |
| `[ ]` P10-009 | 同上 | 移植 raw DISPLAY_INIT bytes B:raw.go:456-459 | hex 对齐 Go |
| `[ ]` P10-010 | 同上 | 移植 raw input init bytes | hex 对齐 Go |
| `[ ]` P10-011 | `zte_route.py` | authed link 5/7 发 DISPLAY_INIT | 可配置默认对齐 Go |
| `[ ]` P10-012 | 同上 | authed link 6 发 input init 失败只 warn | test |
| `[ ]` P10-013 | `zte_raw_spice.py` | PING→PONG payload 回传 | test |
| `[ ]` P10-014 | 同上 | SET_ACK→ACK_SYNC generation 对齐 | test |
| `[ ]` P10-015 | 同上 | msg 0x74→reply 0x79 | fake test |
| `[ ]` P10-016 | `zte_route.py` | 每 authed link 启 keepalive reader timeout continue | test |
| `[ ]` P10-017 | 同上 | main raw loop 读消息 auto reply EOF 返回 stage error | test |
| `[ ]` P10-018 | 同上 | main raw loop SOHO heartbeat 25s interval `[?]` | 25s 对齐 |
| `[ ]` P10-019 | `C/tests/` | fake full ZTE to raw DISPLAY_INIT | L12 通过 |

### P11 — 产品 CLI 闭环（仅接 ZTE，SCG 占位）

| ID | 文件 | 原子动作 | 验收 |
| --- | --- | --- | --- |
| `[ ]` P11-001 | `C/main.py` | 新增 `product-keepalive` CLI `--duration/--forever` | CLI 可解析 |
| `[ ]` P11-002 | 同上 | product-keepalive 先 route-check log 输出 route | log |
| `[ ]` P11-003 | 同上 | route==zte 调 `zte_route.run()` | mock 验证 |
| `[ ]` P11-004 | 同上 | route==error 直接 fail 输 missing fields | test |
| `[ ]` P11-005 | 同上 | 新增 `product-zte-material-check` 到 connectStr parse 前可停 | test |
| `[ ]` P11-006 | 同上 | 新增 `product-zte-tcp-check` 到 CAG TCP/TLS 前可停 | test |
| `[ ]` P11-007 | 同上 | 新增 `product-zte-display-check` 到 raw DISPLAY_INIT 前可停 | test |
| `[ ]` P11-008 | 同上 | 所有 product CLI 输出同 schema route/stage/ok/duration/error/nextStep | test |
| `[ ]` P11-009 | 同上 | product CLI 默认不调 `rap_zime` | import test |
| `[ ]` P11-010 | 同上 | 旧 `run --strategy auto` 文案改指向 product CLI | 不误导 research |

> SCG 路线（P2/P3/P4）**本轮不实现，仅占位**：P11 route==scg 分支留 `NotImplementedError(stage=scg)`，不阻塞 ZTE。

---

## §5 为何只先落地 ZTE（route 路线抉择依据，不先验否定）

1. C 现有 ZTE 链已实现约 80%（protocol_runner ProtocolSession + fetch_cag_auth_connect_str 已做 RSA），SCG 仅 5%。落地 ZTE 风险最低、距离最近。
2. GPT gap-plan §9.4 明确：C 现有 CAG HTTPS material ≠ 成品 ZTE material，需补 token/list/startDesktop 控制面——即 P5。
3. GPT §11：最短突破点是 route-check（P1），先判定本账号 `gui1949` 走哪条，再按 P13 顺序 `route==zte → P5→P6→P7→P8→P9→P10`。
4. **不否定 SCG**：SCG 列为后续 Phase 2，本次只占位不实现，避免同时开两条线空转（GPT §0 纠偏要点）。

---

## §6 验收门槛（P12 停止点：每级失败停在哪，GPT P12 融合）

| ID | 等级 | 原子成功条件 | 不通过时停在哪 |
| --- | --- | --- | --- |
| `[ ]` P12-001 | L0 | route-check 能判断 scg/zte/error | 停在 firmAuth |
| `[ ]` P12-002 | L8 | ZTE material 拿 token/list/connectStr | 停在 ZTE HTTP |
| `[ ]` P12-003 | L9 | ZTE CAG TCP/TLS 成功 | 停在 CAG TCP |
| `[ ]` P12-004 | L10 | ZTE CAG mux open link1 | 停在 mux/add-link |
| `[ ]` P12-005 | L11 | ZTE raw main MAIN_INIT | 停在 raw main |
| `[ ]` P12-006 | L12 | ZTE raw display DISPLAY_INIT | 停在 raw display |
| `[ ]` P12-007 | L13 | ZTE 路 120s short keepalive 不断 | 停在 session loop |
| `[ ]` P12-008 | L14 | `product-keepalive --forever` verified-run 40min running | 才算追平成品 |

**仅保活畅享版月包硬验收**：全程确认被保活的桌面 vmId 始终是畅享版月包那一个，任何阶段发现误触发其他桌面开机/保活→立刻停，回退排查。

---

## §7 依赖与回退边

- **强依赖链**：P0 → P1（route-check 闸门）→ P5 material → P6 inner/outer 分离 → P7 TCP/TLS → P8 mux → P9 raw main → P10 display+loop → P11 CLI 闭环 → P12 验收。
- **P6 必须在 P7 前**：outer/inner 分离是协议正确性前置，混淆会全链错。
- **P9 依赖 P8**：raw main 写 link1，mux 不通则 main 无法发。
- **回退边**：
  - P1 route-check 若 kind≠zte → 停下问用户，不强行进协议。
  - P7 conv 偏移онс偏序失败超 3 次 → 停该链，file_read `B` 对比字节，仍未解请求用户干预，不推进 P8。
  - 最弱前置：ZTE 全栈实在卡死，**绝不动其他云桌面**；可降级到"仅 HTTP power_monitor 维持电源"（独立性已由 cloud.py/power_monitor 证明）但须告知用户。

---

## §8 执行顺序总览（GPT P13 融合）

```text
先做三个原子任务（不空跑）：
1. P1-001~P1-012：product-route-check
2. P11-001~P11-004：product-keepalive shell 只接 mock route
3. P1-007：用账号 gui1949 实跑 firmAuth 一次，只输出 redacted route report

完成后按 route 分流：
if route == zte:  做 P5 -> P6 -> P7 -> P8 -> P9 -> P10
elif route == scg: 本轮占位 NotImplementedError（Phase 2）
else: 停，修登录/账号/firmAuth，不碰协议
```

---

## §9 执行结果回填区（执行 agent 完成后追加，勿覆盖上文）

<!-- 每阶段 [x] 勾选、验证命令实际输出、仅保活畅享版月包对照记录、route-check 实跑结果、P12 各级通过/停点 -->

### P1 — 产品分流合同（route-check 闸门，GPT §11 最短突破点）
- P1-001~006,008~012 全部 `[x]`：product_router.py 重写为 RouteKind(scg/zte/error)+ProductRoute dataclass，对齐 B keepalive.go 路由判定（scAuthCode 非空→SCG，否则 ZTE fields 全→ZTE，否则 error）。
- 127 项 unittest 全绿（含 P1-004/005/006/008/009/010/012 fixture）。
- P1-007（实跑 firmAuth）`[x]`：live route-check 输出 kind=zte, reason="scAuthCode empty and ZTE fields complete (B keepalive.go:127)", userServiceId=2663816, vmId=163c68a9-5e1e-4cba-b9bb-68ad599a8abf。本账号走 ZTE 路由（符合约束）。

- P0-001~005 全部 `[x]`，文件 `docs/no-spin-rules.md` 已创建。
- 验收：5 个关键串均 grep 命中（research track does not block product route / no live run before route-check / TCP first, UDP fallback / route / stage / ok / error / nextStep / no new variant without one new evidence field）。

