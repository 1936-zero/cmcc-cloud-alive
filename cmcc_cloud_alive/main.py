"""Command line entry point for the Python protocol keepalive research tool."""

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

from . import account_keepalive, auth, cag_boot, cag_keepalive, cloud, core, desktop_keepalive, logout, mqtt_keepalive, power_monitor, probe, product_pin, product_router, protocol_runner, rap_zime, spice_protocol, strategy, token, trace_timeline, verified_run, zime_native_bridge, zime_probe


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _product_summary_line(report):
    # Label by protocol kind so interactive logs never look like a retired CLI path.
    # WebUI/simple path is hand-selected SCG/ZTE only; keep protocol logic untouched.
    kind = str(report.get("kind") or "-").strip().lower() or "-"
    if kind in ("scg", "zte"):
        tag = f"{kind}-keepalive"
    else:
        tag = "keepalive"
    ok = report.get("ok")
    stage = report.get("stage") or "-"
    duration = report.get("duration")
    err = report.get("error") or ""
    return (
        f"[{tag}] kind={kind} ok={ok} stage={stage} duration={duration}s"
        + (f" error={err}" if err else "")
    )


def _print_product_summary(report):
    """Print one-line product keepalive summary for interactive keepalive UI."""
    print(_product_summary_line(report), flush=True)


def _print_product_report(report):
    """Print product protocol report with a human summary plus redacted JSON."""
    _print_product_summary(report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def _emit_product_report(args, report):
    if getattr(args, "summary_only", False):
        _print_product_summary(report)
    else:
        _print_product_report(report)


def _write_report(obj, report_file):
    core.write_private_json_report(obj, report_file)


def _default_interactive_log_file(report_file, state_path):
    if report_file:
        return str(Path(report_file).with_suffix(".log"))
    if state_path:
        return str(Path(state_path).with_suffix(".interactive.log"))
    return None


def _append_log(log_file, line):
    if not log_file:
        return
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    path.chmod(0o600)


def _auth_gate_acceptance_error(prefix, assessment):
    missing = ", ".join(assessment.get("missingEvidence") or [])
    stage = assessment.get("failureStage")
    check = assessment.get("failureCheck")
    trace_field = assessment.get("failureOfficialTraceField")
    suffix = []
    if stage:
        suffix.append(f"stage={stage}")
    if check:
        suffix.append(f"check={check}")
    if trace_field:
        suffix.append(f"officialTraceField={trace_field}")
    detail = f": {missing}" if missing else ""
    if suffix:
        detail = f"{detail}; " + "; ".join(suffix)
    return f"{prefix}{detail}"


def _load_explicit_cag_material(path_text):
    if path_text == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_text).read_text(encoding="utf-8")
    try:
        material = json.loads(raw)
    except json.JSONDecodeError as err:
        raise core.CmccError(f"invalid --cag-material-file JSON: {err}") from err
    if not isinstance(material, dict):
        raise core.CmccError("--cag-material-file must contain a JSON object")
    auth_material = material.get("auth")
    connect_info = material.get("connectInfo")
    if not isinstance(auth_material, dict) or not isinstance(connect_info, dict):
        raise core.CmccError("--cag-material-file requires object fields: auth, connectInfo")
    public = material.get("publicConnectInfo")
    if not isinstance(public, dict):
        public = {
            "type": connect_info.get("type"),
            "host": connect_info.get("host"),
            "port": connect_info.get("port"),
            "gatewayPortPresent": bool(connect_info.get("gatewayPort")),
            "udpPortSource": connect_info.get("udpPortSource"),
            "udpSsl": bool(connect_info.get("udpSsl")),
            "accessTokenPresent": bool(connect_info.get("accessToken")),
            "cpsidPresent": bool(connect_info.get("cpsid")),
            "rawArgKeys": sorted((connect_info.get("rawArgs") or {}).keys()),
        }
    return {
        "auth": auth_material,
        "connectInfo": connect_info,
        "publicConnectInfo": public,
        "materialSource": "explicit-cag-material-file",
        "freshFetched": False,
    }


def _cag_material_report_summary(material):
    public = material.get("publicConnectInfo") or {}
    return {
        "freshFetched": bool(material.get("freshFetched")),
        "source": material.get("materialSource") or "fresh-cag-fetch",
        "connectInfo": {
            "type": public.get("type"),
            "hostPresent": bool(public.get("host")),
            "portPresent": bool(public.get("port")),
            "gatewayPortPresent": bool(public.get("gatewayPortPresent")),
            "udpPortSource": public.get("udpPortSource"),
            "udpSsl": bool(public.get("udpSsl")),
            "accessTokenPresent": bool(public.get("accessTokenPresent")),
            "cpsidPresent": bool(public.get("cpsidPresent")),
            "rawArgKeys": public.get("rawArgKeys") or [],
        },
        "payloadStoredInReport": False,
    }


def _build_auth_gate_preflight_report(args, material, pre_auth_cmd26, pre_auth_state):
    report = rap_zime.build_auth_gate_live_preflight_audit_from_cag_material(
        auth=material["auth"],
        connect_info=material["connectInfo"],
        syn_id=args.syn_id,
        conv=args.conv,
        current=args.current,
        pre_auth_fresh_cmd26_bootstrap=pre_auth_cmd26,
        pre_auth_session_state_model=pre_auth_state,
        auth_buffer_type=args.auth_buffer_type,
        auth_type=args.cag_auth_type or None,
        link_type=args.link_type,
        opentelemetry=args.opentelemetry,
        auth_head_attempts=args.auth_head_attempts,
        auth_head_retry_interval=args.auth_head_retry_interval,
        pre_auth_tcp_listen_readiness=args.pre_auth_tcp_listen_readiness,
    )
    report["cagMaterial"] = _cag_material_report_summary(material)
    return report


def _material_with_udp_target_source(material, source):
    """Return a shallow material copy with the requested UDP target source.

    The live material may contain session secrets.  This helper only changes the
    in-memory connect target and keeps reports on the existing redacted path.
    """
    normalized = str(source or "connect-info").strip().lower()
    if normalized in {"connect-info", "connect_info", "selected"}:
        return material
    if normalized not in {"firm-cag", "firm_cag"}:
        raise core.CmccError(f"unsupported --udp-target-source: {source}")
    auth_material = material.get("auth") or {}
    cag_host = auth_material.get("cagIp")
    cag_port = auth_material.get("cagPort")
    if not cag_host or not cag_port:
        raise core.CmccError("--udp-target-source firm-cag requires firm-auth cagIp/cagPort")
    copied = dict(material)
    connect_info = dict(material.get("connectInfo") or {})
    connect_info["host"] = cag_host
    connect_info["port"] = int(cag_port)
    connect_info["udpPortSource"] = "firm-auth-cagPort"
    connect_info["udpTargetSource"] = "firm-auth-cag"
    connect_info["udpSsl"] = True
    copied["connectInfo"] = connect_info
    public = dict(material.get("publicConnectInfo") or {})
    public.update({
        "host": cag_host,
        "port": int(cag_port),
        "udpPortSource": "firm-auth-cagPort",
        "udpTargetSource": "firm-auth-cag",
        "udpSsl": True,
    })
    copied["publicConnectInfo"] = public
    return copied


def _int_auto(value):
    return int(str(value), 0)


def _choose_username_with_cached(cached, prompt_func):
    """Return cached username or a newly entered username via an explicit menu."""
    if not cached:
        return prompt_func("账号")
    print(f"检测到已缓存账号：{cached}", flush=True)
    print("  1. 继续使用该账号", flush=True)
    print("  2. 重新输入账号", flush=True)
    choice = prompt_func("请选择账号来源", default="1").strip()
    if choice in ("", "1"):
        return cached
    if choice == "2":
        return prompt_func("账号")
    # 兼容老习惯：用户直接在选择处输入账号时，也视为切换账号。
    return choice


def cmd_login(args):
    state = core.load_state(args)
    cached = state.get("username") or ""
    username = args.username or ""
    if not username:
        username = _choose_username_with_cached(cached, _interactive_prompt)
    if not username:
        raise core.CmccError("username is required")
    password = args.password
    if not password:
        password = getpass.getpass("密码(输入不回显)：")
    if not password:
        raise core.CmccError("password is required")
    # 默认保存密码到 ~/.cmcc-cloud-alive/state.json，便于 token 失效时自动重登。
    _password_login_with_retry(username, password, args.state, save_password=(args.save_password or True))


def cmd_set_profile(args):
    core.set_profile(args)


def cmd_list(args):
    items = cloud.list_desktops(args.state)
    for index, item in enumerate(items):
        print(f"{index}：userServiceId={item.get('userServiceId')} vmName={item.get('vmName') or ''} spuCode={item.get('spuCode') or ''} sku={item.get('skuName') or ''} status={item.get('vmStatusShow') or item.get('vmStatus')}")


def cmd_select(args):
    _print(cloud.select_desktop(args.user_service_id, args.state))


def cmd_status(args):
    _print(cloud.status(args.user_service_id, args.state))


def cmd_power_monitor(args):
    _print(power_monitor.monitor(
        args.user_service_id,
        args.state,
        interval=args.interval,
        duration=args.duration,
        report_file=args.report_file,
        stop_on_off=args.stop_on_off,
        fail_on_off=args.fail_on_off,
        relogin=not args.no_relogin,
        stop_on_error=not args.no_stop_on_error,
    ))


def cmd_verified_run(args):
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    _print(verified_run.run(
        command,
        args.user_service_id,
        args.state,
        duration=args.duration,
        interval=args.interval,
        report_file=args.report_file,
        allow_command_exit=args.allow_command_exit,
        relogin=not args.no_relogin,
        stop_on_error=not args.no_stop_on_error,
        cwd=args.cwd or None,
    ))


def cmd_boot(args):
    _print(cag_boot.ensure_running(args.user_service_id, args.state, args.boot_wait, args.timeout))


def cmd_keepalive_once(args):
    result = desktop_keepalive.once(args.user_service_id, args.state, send_probe=args.probe, send_point=args.point, send_disconnect_time=args.disconnect_time, send_connect_events=args.connect_events, use_firm_auth=not args.no_firm_auth)
    if args.disconnect_time and not args.probe and not args.point and not args.connect_events:
        print(result.get("disconnectTime", ""), flush=True)
        return
    _print(result)


def cmd_mqtt_keepalive(args):
    _print(mqtt_keepalive.smoke(
        args=args,
        duration_seconds=args.duration,
        report_file=args.report_file,
    ))


def cmd_keepalive(args):
    desktop_keepalive.run_loop(
        args.user_service_id,
        args.state,
        interval=args.interval,
        run_seconds=args.run_seconds,
        account_relogin_hours=args.account_relogin_hours,
        send_probe=args.probe,
        send_point=args.point,
        send_disconnect_time=args.disconnect_time,
        send_connect_events=args.connect_events,
        use_firm_auth=not args.no_firm_auth,
    )


def _interactive_prompt(message, default=None):
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{message}{suffix}：").strip()
    except (EOFError, KeyboardInterrupt):
        raise core.CmccError("已取消输入")
    return raw or (default if default is not None else "")


def _password_login_with_retry(username, password, state_path, save_password=False):
    """Login with up to ``max_attempts`` retries on wrong password.

    Always exits via a clean :class:`core.CmccError` (caught by ``main``) so the
    user never sees a raw traceback when credentials are wrong or input is
    cancelled (EOF / Ctrl-C).
    """
    max_attempts = 3
    current = password
    for attempt in range(1, max_attempts + 1):
        try:
            auth.password_login(username, current, state_path,
                                save_password=save_password if attempt == 1 else False)
            return current
        except core.CmccError as err:
            if save_password:
                raise
            if attempt >= max_attempts:
                raise core.CmccError(
                    f"密码错误次数过多（已尝试 {max_attempts} 次），请确认账号密码后重试") from err
            print(f"登录失败：{err}")
            try:
                retry = getpass.getpass("请重新输入密码(输入不回显)：")
            except (EOFError, KeyboardInterrupt):
                raise core.CmccError("已取消密码输入") from err
            if not retry:
                raise core.CmccError("password is required") from err
            current = retry
    raise core.CmccError("password is required")


def _interactive_sleep(seconds, started, run_seconds):
    if not run_seconds:
        time.sleep(seconds)
        return
    remaining = run_seconds - (time.time() - started)
    if remaining > 0:
        time.sleep(min(seconds, remaining))


def _interactive_login(args):
    state = core.load_state(args)
    cached = state.get("username") or ""
    username = args.username or ""
    if not username:
        username = _choose_username_with_cached(cached, _interactive_prompt)
    if not username:
        raise core.CmccError("username is required")
    password = args.password
    if not password:
        if args.non_interactive:
            raise core.CmccError("password is required in --non-interactive mode")
        try:
            password = getpass.getpass("密码(输入不回显)：")
        except (EOFError, KeyboardInterrupt):
            raise core.CmccError("已取消密码输入")
    if not password:
        raise core.CmccError("password is required")
    password = _password_login_with_retry(username, password, args.state, save_password=True)
    return username, password


def _interactive_select(args):
    items = cloud.list_desktops(args.state)
    if not items:
        raise core.CmccError("no cloud PC found for this account")
    print(f"\n发现 {len(items)} 台云电脑（列表中任意云电脑都可选择）：")
    default_index = 0
    for index, item in enumerate(items):
        print(f"  {index}：userServiceId={item.get('userServiceId')} "
              f"vmId={item.get('vmId') or ''} spuCode={item.get('spuCode') or ''} "
              f"vmName={item.get('vmName') or ''} "
              f"sku={item.get('skuName') or item.get('productName') or item.get('goodsName') or ''} "
              f"status={item.get('vmStatusShow') or item.get('vmStatus')}")
    chosen = None
    if getattr(args, "user_service_id", None):
        chosen = args.user_service_id
    elif getattr(args, "non_interactive", False):
        chosen = str(items[0].get("userServiceId"))
        print(f"非交互模式自动选择第1台云电脑：{chosen}")
    else:
        raw = _interactive_prompt("选择云电脑序号", default=str(default_index)) or str(default_index)
        try:
            idx = int(raw)
        except ValueError:
            raise core.CmccError(f"invalid index：{raw}")
        if idx < 0 or idx >= len(items):
            raise core.CmccError(f"index out of range：{idx}")
        picked = items[idx]
        chosen = str(picked.get("userServiceId"))
    chosen_item = next((item for item in items if str(item.get("userServiceId")) == str(chosen)), None)
    if not chosen_item:
        raise core.CmccError(f"selected cloud PC not found：{chosen}")
    selected = cloud.select_desktop(chosen, args.state)
    print(f"已选择云电脑：userServiceId={chosen} vmId={selected.get('vmId') or ''} vmName={selected.get('vmName') or ''}")
    return chosen


def cmd_interactive(args):
    """Productized interactive keepalive entry (user goal A).

    Flow: prompt account + hidden password -> login -> list target cloud PCs ->
    numbered selection -> write selectedUserServiceId -> keepalive loop with
    periodic status printing and exponential backoff retry on failure.
    """
    state_path = args.state
    args_ns = core.argparse.Namespace(state=state_path)
    args_ns.username = args.username
    args_ns.password = args.password
    args_ns.user_service_id = args.user_service_id
    args_ns.non_interactive = args.non_interactive

    started = time.time()
    report = {
        "task": "T1.2-A interactive keepalive",
        "state": state_path,
        "startedAt": core.shanghai_now().isoformat(),
        "username": "",
        "selectedUserServiceId": "",
        "rounds": 0,
        "acceptedRounds": 0,
        "failedRounds": 0,
        "lastError": "",
        "finishedAt": "",
        "elapsedSeconds": 0,
    }

    username, password = _interactive_login(args_ns)
    report["username"] = username
    target = _interactive_select(args_ns)
    report["selectedUserServiceId"] = target

    # 首次进入任务阶段：全局只执行这 1 次状态检测/开机逻辑。
    # 后续循环只做保活；状态打印仅展示，不联动开机。
    print("\n[首次开机检查] 正在检测云电脑状态……", flush=True)
    try:
        first_status = cloud.status(target, state_path)
        first_status_text = first_status.get("vmStatusShow") or first_status.get("vmStatus")
        print(f"[首次开机检查] 当前状态：{first_status_text} running={cloud.is_running(first_status)}", flush=True)
        report["initialPowerStatus"] = first_status
        if not cloud.is_running(first_status):
            print("[首次开机检查] 云电脑未运行，自动开机（只执行这一次，无需二次确认）……", flush=True)
            boot_result = cag_boot.ensure_running(target, state_path, args.boot_wait, args.boot_timeout)
            report["initialBoot"] = boot_result
            print("[首次开机检查] 开机流程完成，马上进入第一轮保活。", flush=True)
        else:
            print("[首次开机检查] 云电脑已运行，跳过开机，马上进入第一轮保活。", flush=True)
    except Exception as err:
        report["initialBootError"] = str(err)
        print(f"[首次开机检查] 首次状态检测/开机失败，任务终止，不进入保活：{err}", flush=True)
        _write_report(report, args.report_file)
        return

    heartbeat_interval = max(1, int(args.heartbeat_interval))
    status_interval = max(1, int(args.status_interval))
    run_seconds = int(args.duration or 0)
    if not args.non_interactive:
        default_minutes = max(1, int(round(heartbeat_interval / 60.0)))
        interval_minutes = max(1, int(_interactive_prompt("保活间隔分钟数", default=str(default_minutes))))
        heartbeat_interval = interval_minutes * 60
        run_seconds = int(_interactive_prompt("持续秒数(0=永久)", default=str(run_seconds)) or 0)
    report["heartbeatInterval"] = heartbeat_interval
    report["durationSeconds"] = run_seconds
    max_backoff = min(1800, max(60, heartbeat_interval * 10))
    log_file = _default_interactive_log_file(args.report_file, state_path)
    report["logFile"] = log_file or ""

    initial_disconnect = None
    try:
        initial_disconnect = desktop_keepalive.once(
            target, state_path,
            send_probe=False, send_point=False,
            send_disconnect_time=True, send_connect_events=False,
            use_firm_auth=not args.no_firm_auth,
        ).get("disconnectTime")
        report["initialDisconnectTime"] = initial_disconnect
        _print_disconnect_time(initial_disconnect)
    except Exception as err:
        report["initialDisconnectTimeError"] = str(err)
        print(f"[官方自动关机时长]获取失败：{err}", flush=True)

    print(f"\n进入保活循环：心跳间隔={heartbeat_interval}s 状态打印间隔={status_interval}s "
          f"运行时长={'永久' if not run_seconds else str(run_seconds) + 's'}")
    print("提示：当前 desktop HTTP keepalive 路由尚未被证明可独立保活，"
          "失败会退避重试，不会静默退出。Ctrl+C 可中断。\n")
    _append_log(log_file, f"[{core.short_time()}] 开始保活 target={target} interval={heartbeat_interval}s duration={run_seconds}s initialDisconnectTime={initial_disconnect}")

    count = 0
    backoff = heartbeat_interval
    last_status_print = 0.0
    try:
        while True:
            count += 1
            report["rounds"] = count
            try:
                token_ret = token.ensure_token(state_path, relogin=False)
                valid = token_ret[0] if isinstance(token_ret, (tuple, list)) else bool(token_ret)
                if not valid:
                    auth.password_login(username, password, state_path, save_password=False)
                print(f"[{core.short_time()}] 保活连接#{count} 开始", flush=True)
                _append_log(log_file, f"[{core.short_time()}] 保活连接#{count} 开始")
                result = desktop_keepalive.once(
                    target, state_path,
                    send_probe=args.probe, send_point=args.point,
                    send_disconnect_time=True, send_connect_events=args.connect_events,
                    use_firm_auth=not args.no_firm_auth,
                )
                accepted = bool(result.get("candidateAccepted"))
                report["lastResult"] = result
                if accepted:
                    report["acceptedRounds"] += 1
                    backoff = heartbeat_interval
                else:
                    report["failedRounds"] += 1
                elapsed = int(time.time() - started)
                hb = (result.get("heartbeat") or {}).get("code", "-")
                info = (result.get("infoReport") or {}).get("code", "-")
                disc = result.get("disconnectTime")
                disc_code = disc.get("code") if isinstance(disc, dict) else "-"
                disc_times = disc.get("disconnectTimes") if isinstance(disc, dict) else None
                status = "持续保活中" if accepted else "发送流量日志失败(可恢复)"
                line = (f"[{core.short_time()}] [{count}] {status}: "
                        f"发送流量日志 elapsed={core.format_duration(elapsed)} heartbeat={hb} "
                        f"disconnect={disc_code} disconnectTimes={disc_times} info={info}")
                print(line, flush=True)
                _append_log(log_file, line)
                if time.time() - last_status_print >= status_interval:
                    try:
                        snap = cloud.status(target, state_path)
                        print(f"  状态：{snap.get('vmStatusShow') or snap.get('vmStatus')} "
                              f"running={cloud.is_running(snap)}", flush=True)
                    except Exception as err:
                        print(f"  状态查询失败：{err}", flush=True)
                    last_status_print = time.time()
            except KeyboardInterrupt:
                raise
            except Exception as err:
                report["failedRounds"] += 1
                report["lastError"] = str(err)
                print(f"[{core.short_time()}] [{count}] 发送流量日志异常(可恢复)：{err} -> {backoff}s 后重试", flush=True)
                _append_log(log_file, f"[{core.short_time()}] [{count}] 发送流量日志异常(可恢复): {err} backoff={backoff}s")
                if run_seconds and time.time() - started >= run_seconds:
                    break
                _interactive_sleep(backoff, started, run_seconds)
                backoff = min(max_backoff, backoff * 2)
                continue
            if run_seconds and time.time() - started >= run_seconds:
                break
            _interactive_sleep(heartbeat_interval, started, run_seconds)
    except KeyboardInterrupt:
        print("\n收到中断信号，退出保活循环。", flush=True)
        report["lastError"] = "interrupted by user"
    finally:
        report["finishedAt"] = core.shanghai_now().isoformat()
        report["elapsedSeconds"] = int(time.time() - started)
        _append_log(log_file, f"[{core.short_time()}] 保活连接结束 rounds={report['rounds']} accepted={report['acceptedRounds']} failed={report['failedRounds']}")
        _append_log(log_file, f"[{core.short_time()}] 保活结束 rounds={report['rounds']} accepted={report['acceptedRounds']} failed={report['failedRounds']}")
        _write_report(report, args.report_file)
        if args.report_file:
            print(f"报告已写入：{args.report_file}", flush=True)
        if log_file:
            print(f"日志已写入：{log_file}", flush=True)
    _print(report)


def cmd_cag_keepalive_once(args):
    _print(cag_keepalive.once(
        args.user_service_id,
        args.state,
        boot_wait=args.boot_wait,
        timeout=args.timeout,
        observe_seconds=args.observe_seconds,
        post_http_prime=args.post_http_prime,
    ))


def cmd_product_route_check(args):
    _print(product_router.route_check(
        args.user_service_id,
        state_path=args.state,
        report_file=args.report_file or None,
    ))


def cmd_cag_keepalive(args):
    cag_keepalive.run_loop(
        args.user_service_id,
        args.state,
        interval=args.interval,
        run_seconds=args.run_seconds,
        account_relogin_hours=args.account_relogin_hours,
        boot_wait=args.boot_wait,
        timeout=args.timeout,
        post_http_prime=args.post_http_prime,
    )


def cmd_cag_verify(args):
    _print(cag_keepalive.run_verify(
        args.user_service_id,
        args.state,
        duration=args.duration,
        min_proof_seconds=args.min_proof_seconds,
        interval=args.interval,
        account_relogin_hours=args.account_relogin_hours,
        boot_wait=args.boot_wait,
        timeout=args.timeout,
        report_file=args.report_file,
        allow_official_client_present=args.allow_official_client_present,
        stop_on_off=not args.no_stop_on_off,
        post_http_prime=args.post_http_prime,
    ))


def cmd_token_check(args):
    valid, response = token.ensure_token(args.state, relogin=not args.no_relogin)
    _print({"valid": valid, "response": response})


def cmd_account_keepalive(args):
    refreshed, response = account_keepalive.check_or_refresh(args.state)
    _print({"refreshed": refreshed, "response": response})


def cmd_logout(args):
    result = {}
    if args.desktop:
        result["desktop"] = logout.desktop_logout(args.user_service_id, args.state)
    if args.account:
        result["account"] = logout.account_logout(args.state, clear_local=not args.keep_local)
    _print(result)


def cmd_probe_base(args):
    _print(probe.send_base(args.state))


def cmd_spice_offline_proof(args):
    proof = spice_protocol.create_offline_display_proof()
    _print({
        "ok": proof["success"],
        "route": "offline-spice-codec",
        "displayInitHex": proof["displayInit"].hex(),
        "responseTypes": [spice_protocol.decode_mini_message(item)["header"]["type"] for item in proof["responses"]],
        "progress": proof["progress"],
        "successSignal": "DISPLAY_INIT sent and surface/draw/mark signal observed",
    })


def cmd_protocol_run(args):
    _print(protocol_runner.run(
        args.user_service_id,
        args.state,
        connect_str=args.connect_str,
        run_seconds=args.run_seconds,
        boot_wait=args.boot_wait,
        timeout=args.timeout,
        success_only=args.success_only,
    ))


def cmd_analyze_zime_probe(args):
    _print(zime_probe.analyze(args.jsonl, report_file=args.report_file))


def cmd_extract_zime_sequence(args):
    _print(zime_probe.extract_sequence(
        args.jsonl,
        focus_kind=args.focus_kind,
        window=args.window,
        limit=args.limit,
        report_file=args.report_file,
    ))


def cmd_analyze_rap_zime(args):
    _print(rap_zime.analyze_trace(
        args.jsonl,
        report_file=args.report_file,
        sample_limit=args.sample_limit,
    ))


def cmd_analyze_rap_zime_pcap(args):
    try:
        report = rap_zime.analyze_external_pcap(
            args.pcap,
            ss_log=args.ss_log or None,
            report_file=args.report_file,
            sample_limit=args.sample_limit,
            focus_udp_port=args.focus_udp_port,
        )
    except ValueError as err:
        raise core.CmccError(str(err)) from err
    _print(report)


def cmd_check_rap_zime_runner_input(args):
    try:
        report = rap_zime.check_runner_input_file(
            args.runner_input,
            require_templates=args.require_templates,
            require_ztec=not args.no_require_ztec,
            require_kcp_auth_ready=args.require_kcp_auth_ready,
            max_age_seconds=args.max_age_seconds,
        )
    except ValueError as err:
        raise core.CmccError(str(err)) from err
    _write_report(report, args.report_file)
    _print(report)


def cmd_rap_zime_udp_probe(args):
    payloads = []
    for value in args.payload_hex or []:
        try:
            payloads.append(bytes.fromhex(value))
        except ValueError as err:
            raise core.CmccError(f"invalid --payload-hex: {value}") from err
    if args.native_report:
        report = json.loads(Path(args.native_report).read_text(encoding="utf-8"))
        native_payloads = zime_native_bridge.native_transport_payloads(report)
        if not native_payloads:
            raise core.CmccError("native report does not contain complete packet-out payloads")
        payloads.extend(native_payloads)
    try:
        report = rap_zime.run_udp_probe(
            runner_input_file=args.runner_input or None,
            target=args.target or None,
            tunnel_id=args.tunnel_id or None,
            payloads=payloads,
            ztec=not args.no_ztec,
            ztec_host=args.ztec_host or None,
            ztec_port=args.ztec_port,
            timeout=args.timeout,
            wait_response=args.wait_response,
            rap_payload_envelope=args.udp_rap_payload_envelope,
            rap_template_mode=args.udp_rap_template_mode,
        )
        _write_report(report, args.report_file)
        _print(report)
    except ValueError as err:
        raise core.CmccError(str(err)) from err


def cmd_rap_zime_kcp_sync_probe(args):
    try:
        report = rap_zime.run_kcp_sync_probe(
            runner_input_file=args.runner_input or None,
            target=args.target or None,
            timeout=args.timeout,
            receive_limit=args.receive_limit,
            syn_id=args.syn_id,
            conv=args.conv,
            current=args.current,
            mtu=args.mtu,
            be_ssl=args.ssl,
            detect_mtu=not args.no_detect_mtu,
            be_pack_check=not args.no_pack_check,
            be_fec=not args.no_fec,
            be_multi=args.multi,
            be_algo_mode=args.algo_mode,
            be_using_stream=not args.no_stream,
            be_quic=not args.no_quic,
            be_outband=True if args.outband is None else args.outband,
            report_file=args.report_file or None,
        )
        _print(report)
    except ValueError as err:
        raise core.CmccError(str(err)) from err


def cmd_rap_zime_kcp_auth_from_cag(args):
    try:
        if args.require_preflight_ready and not args.auth_gate_preflight_only:
            raise core.CmccError("--require-preflight-ready requires --auth-gate-preflight-only")
        if args.require_live_gate_ready and args.auth_gate_preflight_only:
            raise core.CmccError("--require-live-gate-ready requires a live gate run; use --require-preflight-ready with --auth-gate-preflight-only")
        if args.require_auth_gate_accepted and args.auth_gate_preflight_only:
            raise core.CmccError("--require-auth-gate-accepted requires a live gate run, not --auth-gate-preflight-only")
        if args.cag_material_file:
            material = _load_explicit_cag_material(args.cag_material_file)
        else:
            material = protocol_runner.fetch_cag_auth_connect_info(
                args.user_service_id,
                args.state,
                boot_wait=args.boot_wait,
                timeout=args.cag_timeout,
            )
            material["materialSource"] = "fresh-cag-fetch"
            material["freshFetched"] = True
        material = _material_with_udp_target_source(material, args.udp_target_source)
        pre_auth_cmd26 = None
        if args.pre_auth_cmd26_local_proxy:
            local_host, local_port = rap_zime.parse_udp_target(args.pre_auth_cmd26_local_proxy)
            connect_info = material["connectInfo"]
            if not connect_info.get("host") or not connect_info.get("port"):
                raise ValueError("CAG connectInfo host/port are required for pre-AUTH cmd26 bootstrap")
            pre_auth_cmd26 = {
                "local_host": local_host,
                "local_port": local_port,
                "dest_ip": connect_info.get("host"),
                "dest_port": connect_info.get("port"),
                "channel_type": args.pre_auth_cmd26_channel_type,
                "channel_id": args.pre_auth_cmd26_channel_id,
                "trace_id": args.pre_auth_cmd26_trace_id,
                "parent_id": args.pre_auth_cmd26_parent_id,
            }
        pre_auth_state = None
        if args.pre_auth_state_contract:
            pre_auth_state = {
                "type6_proxy_fd_session_slot": True,
                "proxy_sock_udp_gate": True,
                "init_local_rw_sock_pair_udp_kcp_attachment": True,
                "quic_channel_manage_ready_or_bypassed": True,
                "channel_type_id_candidate": f"0x{((args.pre_auth_cmd26_channel_type << 8) | args.pre_auth_cmd26_channel_id):04x}",
                "dest_ip_source": "CAG connectInfo host used as safe local candidate for hostip/host source class",
                "dest_port_source": "CAG connectInfo port used as safe local candidate for get_channel_proxy_link_dest_port source class",
                "opentelemetry_source": "CLI-supplied or empty structural candidate",
            }
        if args.auth_gate_preflight_only:
            report = _build_auth_gate_preflight_report(args, material, pre_auth_cmd26, pre_auth_state)
            if args.report_file:
                _write_report(report, args.report_file)
            _print(report)
            if args.require_preflight_ready and not report.get("readyForGateOnlyLiveAttempt"):
                missing = ", ".join(report.get("missingConfiguration") or [])
                raise core.CmccError(f"AUTH gate preflight not ready: {missing}")
            return
        if args.require_live_gate_ready:
            preflight_report = _build_auth_gate_preflight_report(args, material, pre_auth_cmd26, pre_auth_state)
            if not preflight_report.get("readyForGateOnlyLiveAttempt"):
                if args.report_file:
                    _write_report(preflight_report, args.report_file)
                _print(preflight_report)
                missing = ", ".join(preflight_report.get("missingConfiguration") or [])
                raise core.CmccError(f"AUTH gate live readiness not ready: {missing}")
        report = rap_zime.run_kcp_auth_sync_probe_from_cag_material(
            auth=material["auth"],
            connect_info=material["connectInfo"],
            timeout=args.timeout,
            receive_limit=args.receive_limit,
            syn_id=args.syn_id,
            conv=args.conv,
            current=args.current,
            mtu=args.mtu,
            be_ssl=args.ssl,
            detect_mtu=not args.no_detect_mtu,
            be_pack_check=not args.no_pack_check,
            be_fec=not args.no_fec,
            be_multi=args.multi,
            be_algo_mode=args.algo_mode,
            be_using_stream=not args.no_stream,
            be_quic=not args.no_quic,
            be_outband=True if args.outband is None else args.outband,
            ztec_prime=args.ztec_prime,
            ztec_host=args.ztec_host or None,
            ztec_port=args.ztec_port,
            ztec_timeout=args.ztec_timeout,
            local_bind_host=args.local_bind_host or None,
            local_bind_port=args.local_bind_port,
            pre_auth_receive_timeout=args.pre_auth_receive_timeout,
            pre_auth_receive_limit=args.pre_auth_receive_limit,
            pre_auth_bind_host=args.pre_auth_bind_host,
            pre_auth_fresh_cmd26_bootstrap=pre_auth_cmd26,
            pre_auth_session_state_model=pre_auth_state,
            pre_auth_tcp_listen_readiness=args.pre_auth_tcp_listen_readiness,
            auth_buffer_type=args.auth_buffer_type,
            auth_type=args.cag_auth_type or None,
            link_type=args.link_type,
            opentelemetry=args.opentelemetry,
            auth_head_attempts=args.auth_head_attempts,
            auth_head_retry_interval=args.auth_head_retry_interval,
            report_file=None,
        )
        report["cagMaterial"] = _cag_material_report_summary(material)
        if args.require_live_gate_ready:
            report["liveGateReadinessPreflight"] = {
                "readyForGateOnlyLiveAttempt": True,
                "configurationChecks": preflight_report.get("configurationChecks"),
                "missingConfiguration": [],
                "payloadStoredInReport": False,
            }
        if args.require_auth_gate_accepted:
            report["authGateAcceptance"] = rap_zime.assess_auth_gate_only_report(report)
        if args.report_file:
            _write_report(report, args.report_file)
        _print(report)
        if args.require_auth_gate_accepted and not report["authGateAcceptance"].get("authGateOnlyAccepted"):
            raise core.CmccError(_auth_gate_acceptance_error(
                "AUTH gate-only live report not accepted",
                report["authGateAcceptance"],
            ))
    except ValueError as err:
        raise core.CmccError(str(err)) from err


def cmd_check_rap_zime_auth_gate_report(args):
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        assessment = rap_zime.assess_auth_gate_only_report(report)
    except (OSError, json.JSONDecodeError, ValueError) as err:
        raise core.CmccError(str(err)) from err
    _write_report(assessment, args.report_file)
    _print(assessment)
    if args.require_accepted and not assessment.get("authGateOnlyAccepted"):
        raise core.CmccError(_auth_gate_acceptance_error("AUTH gate-only report not accepted", assessment))


def _resolve_zime_native_udp_args(args):
    udp_target = args.udp_transport_target or None
    udp_mode = args.udp_transport_mode
    udp_tunnel = args.udp_rap_tunnel_id or None
    udp_rap_flags = args.udp_rap_flags
    udp_rap_field06 = args.udp_rap_field06
    udp_rap_word08 = args.udp_rap_word08
    udp_rap_word12 = args.udp_rap_word12
    udp_rap_header16_prefix = args.udp_rap_header16_prefix_hex or None
    udp_rap_post_length = args.udp_rap_post_length_hex or None
    udp_rap_payload_envelope = args.udp_rap_payload_envelope
    udp_rap_send_templates = []
    udp_ztec_host = args.udp_ztec_host or None
    udp_ztec_port = args.udp_ztec_port
    remote_host = args.remote_host
    remote_port = args.remote_port
    runner_source = None
    if args.runner_input:
        try:
            runner_source = rap_zime.load_runner_input(args.runner_input)
            if udp_mode == "auto":
                udp_mode = (
                    "raw"
                    if runner_source.get("transport") == "external-pcap-metadata-only"
                    and not runner_source.get("primaryTunnelId")
                    else "rap"
                )
            if udp_mode == "rap":
                config = rap_zime.runner_config_from_input(
                    runner_source,
                    target=udp_target,
                    tunnel_id=udp_tunnel,
                    ztec_host=udp_ztec_host,
                    ztec_port=udp_ztec_port,
                )
            else:
                targets = list(runner_source.get("candidateUdpTargets") or [])
                selected_target = udp_target or (targets[0] if targets else None)
                if not selected_target:
                    raise core.CmccError("UDP target is required; pass --udp-transport-target or provide candidateUdpTargets")
                host, port = rap_zime.parse_udp_target(selected_target)
                ztec_targets = list(runner_source.get("candidateZtecTargets") or [])
                selected_ztec = ztec_targets[0] if ztec_targets else None
                if selected_ztec:
                    default_ztec_host, default_ztec_port = rap_zime.parse_udp_target(selected_ztec)
                else:
                    default_ztec_host, default_ztec_port = host, port
                config = {
                    "target": (host, port),
                    "targetText": f"{host}:{port}",
                    "tunnelIdHex": udp_tunnel,
                    "ztecHost": udp_ztec_host or default_ztec_host,
                    "ztecPort": int(udp_ztec_port or default_ztec_port),
                    "rapDataFrameTemplate": {},
                    "rapDataFrameSendTemplates": [],
                }
        except ValueError as err:
            raise core.CmccError(str(err)) from err
        udp_target = udp_target or config["targetText"]
        udp_tunnel = udp_tunnel or config["tunnelIdHex"]
        udp_ztec_host = udp_ztec_host or config["ztecHost"]
        udp_ztec_port = udp_ztec_port or config["ztecPort"]
        template = config.get("rapDataFrameTemplate") or {}
        udp_rap_flags = udp_rap_flags if udp_rap_flags is not None else template.get("flags")
        udp_rap_field06 = udp_rap_field06 if udp_rap_field06 is not None else template.get("field06")
        udp_rap_word08 = udp_rap_word08 if udp_rap_word08 is not None else template.get("word08")
        udp_rap_word12 = udp_rap_word12 if udp_rap_word12 is not None else template.get("word12")
        udp_rap_header16_prefix = udp_rap_header16_prefix or template.get("header16PrefixHex")
        udp_rap_post_length = udp_rap_post_length or template.get("postLengthHex")
        udp_rap_send_templates = config.get("rapDataFrameSendTemplates") or []
        if remote_host == "127.0.0.1":
            remote_host = config["target"][0]
        if remote_port == 0:
            remote_port = config["target"][1]
    elif udp_mode == "auto":
        udp_mode = "raw"
    return {
        "udp_transport_target": udp_target,
        "udp_transport_mode": udp_mode,
        "udp_rap_tunnel_id": udp_tunnel,
        "udp_rap_flags": 0 if udp_rap_flags is None else udp_rap_flags,
        "udp_rap_field06": 0 if udp_rap_field06 is None else udp_rap_field06,
        "udp_rap_word08": 0 if udp_rap_word08 is None else udp_rap_word08,
        "udp_rap_word12": 0 if udp_rap_word12 is None else udp_rap_word12,
        "udp_rap_header16_prefix": udp_rap_header16_prefix,
        "udp_rap_post_length": udp_rap_post_length,
        "udp_rap_payload_envelope": udp_rap_payload_envelope,
        "udp_rap_send_templates": udp_rap_send_templates,
        "udp_ztec_host": udp_ztec_host,
        "udp_ztec_port": udp_ztec_port,
        "remote_host": remote_host,
        "remote_port": remote_port,
        "runner_input_loaded": bool(runner_source),
    }


def cmd_zime_native_bridge(args):
    payloads = []
    if args.display_init:
        payloads.append(spice_protocol.encode_display_init())
    for value in args.payload_hex or []:
        try:
            payloads.append(bytes.fromhex(value))
        except ValueError as err:
            raise core.CmccError(f"invalid --payload-hex: {value}") from err
    try:
        opaque = bytes.fromhex(args.opaque_hex)
    except ValueError as err:
        raise core.CmccError(f"invalid --opaque-hex: {args.opaque_hex}") from err
    udp_args = _resolve_zime_native_udp_args(args)
    _print(zime_native_bridge.run_research_probe(
        lib_path=args.lib_path or None,
        payloads=payloads,
        allow_native_run=args.allow_native_run,
        inspect_only=args.inspect_only or not args.allow_native_run,
        remote_host=udp_args["remote_host"],
        remote_port=udp_args["remote_port"],
        local_host=args.local_host,
        local_port=args.local_port,
        opaque=opaque,
        protocol=args.protocol,
        mtu=args.mtu,
        business_type=args.business_type,
        stream_id=args.stream_id,
        process_ticks=args.process_ticks,
        read_iov_payload=args.read_iov_payload,
        udp_transport_target=udp_args["udp_transport_target"],
        udp_read_timeout=args.udp_read_timeout,
        udp_receive_limit=args.udp_receive_limit,
        udp_process_ticks_after_receive=args.udp_process_ticks_after_receive,
        udp_transport_mode=udp_args["udp_transport_mode"],
        udp_rap_tunnel_id=udp_args["udp_rap_tunnel_id"],
        udp_rap_flags=udp_args["udp_rap_flags"],
        udp_rap_field06=udp_args["udp_rap_field06"],
        udp_rap_word08=udp_args["udp_rap_word08"],
        udp_rap_word12=udp_args["udp_rap_word12"],
        udp_rap_header16_prefix=udp_args["udp_rap_header16_prefix"],
        udp_rap_post_length=udp_args["udp_rap_post_length"],
        udp_rap_payload_envelope=udp_args["udp_rap_payload_envelope"],
        udp_rap_send_templates=udp_args["udp_rap_send_templates"],
        udp_rap_template_mode=args.udp_rap_template_mode,
        udp_packet_out_iov_mode=args.udp_packet_out_iov_mode,
        wait_channel_created_ticks=args.wait_channel_created_ticks,
        udp_ztec_prime=args.udp_ztec_prime,
        udp_ztec_host=udp_args["udp_ztec_host"],
        udp_ztec_port=udp_args["udp_ztec_port"],
        udp_ztec_timeout=args.udp_ztec_timeout,
        report_file=args.report_file,
    ))


def cmd_trace_timeline(args):
    _print(trace_timeline.timeline(
        args.jsonl,
        limit=args.limit,
        include_unknown=args.include_unknown,
        report_file=args.report_file,
    ))


def cmd_http_session_replay(args):
    desktop_keepalive.run_official_http_loop(
        args.user_service_id,
        args.state,
        run_seconds=args.run_seconds,
        heartbeat_interval=args.heartbeat_interval,
        info_interval=args.info_interval,
        log_config_interval=args.log_config_interval,
        status_interval=args.status_interval,
        token_check_interval=args.token_check_interval,
        relogin_on_token_expired=args.relogin_on_token_expired,
    )


def cmd_http_session_verify(args):
    _print(desktop_keepalive.run_official_http_verify(
        args.user_service_id,
        args.state,
        duration=args.duration,
        heartbeat_interval=args.heartbeat_interval,
        info_interval=args.info_interval,
        log_config_interval=args.log_config_interval,
        status_interval=args.status_interval,
        min_proof_seconds=args.min_proof_seconds,
        report_file=args.report_file,
        allow_official_client_present=args.allow_official_client_present,
        stop_on_off=not args.no_stop_on_off,
    ))


def cmd_run(args):
    strategy.run(
        args.strategy,
        args.user_service_id,
        args.state,
        run_seconds=args.run_seconds,
        cycle_interval=args.cycle_interval,
        cycle_duration=args.cycle_duration,
        heartbeat_interval=args.heartbeat_interval,
        info_interval=args.info_interval,
        log_config_interval=args.log_config_interval,
        status_interval=args.status_interval,
        token_check_interval=args.token_check_interval,
        account_relogin_hours=args.account_relogin_hours,
        boot_if_off=not args.no_boot,
        boot_wait=args.boot_wait,
        boot_timeout=args.boot_timeout,
        cag_interval=args.cag_interval,
        allow_session_takeover=args.allow_session_takeover,
    )


def cmd_protocol_check(args):
    core.protocol_check(args)


def cmd_api_probe(args):
    core.api_probe(args)


def cmd_analyze_session_capture(args):
    core.analyze_session_capture(args)


def cmd_source_audit(args):
    core.source_audit(args)


def cmd_state(args):
    core.print_state(args)


# --- P11: product keepalive CLI entry --------------------------------------
#
# Mirrors B ``cmd/keepalive.go`` ``Keepalive()``: load firmAuth once, let
# ``product_router.classify_firm_auth_route`` decide SCG vs ZTE, then dispatch
# to the matching keepalive backend. Emits a redacted report with
# route/stage/ok/duration/error/nextStep (no-spin rule 4). No raw
# token/password/connectStr is ever printed.

def _product_keepalive_report(route_name="product-keepalive", stage="route-check"):
    return {
        "route": route_name,
        "stage": stage,
        "ok": False,
        "duration": 0,
        "error": "",
        "nextStep": "",
        "kind": "",
        "reason": "",
        "userServiceId": "",
        "vmId": "",
        "firmAuthSummary": {},
    }



def _report_product_pin_fields(report):
    """Extract product pin ids from report (top-level or nested product_pin).

    Keys accepted (first non-empty wins per dimension):
      usid: userServiceId / selectedUserServiceId / usid
      vmid: vmId / lastVmId / vmid
      spu:  lastSpuCode / spuCode / spu
    Nested dict under report["product_pin"] is also scanned.
    firmAuthSummary (product path) is also scanned so spuCode/lastSpuCode
    present only under the redacted firmAuth blob still pin-match (T50 residual).
    Returns (usid, vmid, spu) as strings or None when absent/empty.
    """
    nested = report.get("product_pin") if isinstance(report.get("product_pin"), dict) else {}
    firm = report.get("firmAuthSummary") if isinstance(report.get("firmAuthSummary"), dict) else {}

    def _pick(*keys):
        for src in (report, nested, firm):
            for k in keys:
                v = src.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    return s
        return None

    usid = _pick("userServiceId", "selectedUserServiceId", "usid")
    vmid = _pick("vmId", "lastVmId", "vmid")
    spu = _pick("lastSpuCode", "spuCode", "spu")
    return usid, vmid, spu


def _product_pin_matches(report):
    """T17/R7: product-id gate for business_ok (env-driven, default OFF).

    Public mode (CMCC_ENFORCE_PIN unset): always True — any selected product OK.
    Enforced mode: fail-closed against CMCC_PRODUCT_USID/VMID/SPU from env.
    Pure function on report dict (+ env); no I/O / no LIVE.
    """
    product_pin.refresh_pin_constants()
    if not product_pin.pin_enforced():
        return True
    expected_usid = product_pin.PRODUCT_USID
    expected_vmid = product_pin.PRODUCT_VMID
    expected_spu = product_pin.PRODUCT_SPU
    # Enforcement on but no expected USID configured → cannot prove product.
    if not expected_usid:
        return False
    usid, vmid, spu = _report_product_pin_fields(report)
    if usid is None and vmid is None and spu is None:
        return False
    if usid is not None and usid != expected_usid:
        return False
    if expected_vmid and vmid is not None and vmid != expected_vmid:
        return False
    if expected_spu and spu is not None and spu != expected_spu:
        return False
    # Require full triad when all expected fields are configured.
    if usid is None:
        return False
    if expected_vmid and vmid is None:
        return False
    if expected_spu and spu is None:
        return False
    return True


def compute_business_ok(report, forever=False):
    """D3/P07/T17: J* business-plane gate (fail-closed). Never true for tls_hold/forever.

    Mode plane (`ok`) may be True under tls_hold; business_ok stays False.
    VM plane requires multi-sample throughout (P06): vm_running is True AND
    vm_sample_count >= 2. Single-sample or missing samples => False.
    Accepts either vm_running or vm_running_throughout as the throughout flag.
    T17/R7: product pin (usid/vmId/spu) must match PRODUCT_* when present;
    missing pin triad fail-closes business_ok even if mode/VM plane is green.
    """
    if forever:
        return False
    mode = str(report.get("keepalive_mode") or "").lower()
    if mode != "spice":
        return False
    # Multi-sample floor (P06): wall/process ok alone is not business proof
    sample_count = int(report.get("vm_sample_count") or 0)
    if sample_count < 2:
        return False
    vm_flag = report.get("vm_running_throughout")
    if vm_flag is None:
        vm_flag = report.get("vm_running")
    if not (
        report.get("ok")
        and report.get("spice_ok")
        and not report.get("degraded")
        and vm_flag is True
    ):
        return False
    # T17 residual R7: product-id mismatch / missing pin fail-closes business
    return _product_pin_matches(report)


def _run_scg_keepalive(args, auth, route, vm_id, report, started):
    """Dispatch to the pure-Python SCG route (B keepalive.go SCG branch).

    Platform-maintenance soft-recover (ZTE parity):
      - Initial CEM getConnectInfo failure is tagged recoverable / platform_maintenance
        and does **not** kill an interactive forever outer loop (caller continues).
      - forever mode passes reconnect_fn so each recovery cycle re-fetches
        getConnectInfo (scgIp/port/scAuthCode may change after mass power-off).
    """
    import time
    from . import scg_route
    report["stage"] = "scg-cem-connect-info"
    sc_auth_code = product_router.extract_sc_auth_code(auth) or ""
    try:
        state = core.load_state(args)
        cfg = core.client_config(state)
        device_id = core.profile_device_id(state, cfg)

        def _reconnect_connect_info():
            """Refresh SCG endpoint after maintenance / VM mass-off (forever)."""
            # Prefer a possibly refreshed firmAuth from state (token soft-refresh
            # may have updated scAuthCode between rounds).
            try:
                fresh_auth = core.get_firm_auth(args)
                code = product_router.extract_sc_auth_code(fresh_auth) or sc_auth_code
            except Exception:
                code = sc_auth_code
            info = scg_route.get_connect_info(code, vm_id, device_id=device_id)
            return {
                "scgIp": info.get("scgIp") or info.get("scg_ip") or "",
                "scgPort": str(info.get("scgPort") or info.get("scg_port") or ""),
                "scAuthCode": info.get("scAuthCode") or code,
            }

        connect_info = scg_route.get_connect_info(sc_auth_code, vm_id, device_id=device_id)
        scg_ip = connect_info["scgIp"]
        scg_port = connect_info["scgPort"]
        sc_auth_code = connect_info.get("scAuthCode") or sc_auth_code
        report["stage"] = "scg-keepalive"
        scg_mode = str(getattr(args, "scg_mode", None) or "spice").strip().lower()
        report["scg_mode"] = scg_mode
        # Prefer route.userServiceId (set by cmd_product_keepalive from selected
        # desktop) over raw CLI args — product path often leaves args empty.
        usid = (
            str(getattr(route, "userServiceId", "") or "")
            or str(getattr(args, "user_service_id", "") or "")
            or str(getattr(report, "get", lambda *_: "")("userServiceId") or "")
        )
        if not usid and isinstance(report, dict):
            usid = str(report.get("userServiceId") or "")
        result = scg_route.run_scg_keepalive(
            scg_ip=scg_ip, scg_port=scg_port, sc_auth_code=sc_auth_code,
            vm_id=vm_id, duration=args.duration, forever=args.forever,
            user_service_id=usid,
            state_path=getattr(args, "state", None),
            mode=scg_mode,
            reconnect_fn=_reconnect_connect_info if args.forever else None,
        )
    except Exception as exc:  # noqa: BLE001 - surface CEM/TCP/TLS/protocol failure
        # Soft-tag: platform maintenance / CEM blip must not look like hard crash.
        tags = {}
        try:
            tags = scg_route.classify_scg_soft_failure(exc)
        except Exception:
            tags = {
                "error": "%s: %s" % (type(exc).__name__, exc),
                "recoverable": True,
                "platform_maintenance": False,
                "fail_reason": "scg_exception",
            }
        report["error"] = str(tags.get("error") or ("%s: %s" % (type(exc).__name__, exc)))
        report["ok"] = False
        report["recoverable"] = bool(tags.get("recoverable", True))
        report["platform_maintenance"] = bool(tags.get("platform_maintenance", False))
        report["fail_reason"] = str(tags.get("fail_reason") or "scg_exception")
        report["spice_ok"] = False
        if report.get("platform_maintenance"):
            report["nextStep"] = (
                "platform maintenance / VM mass-off suspected; "
                "retry getConnectInfo after backoff (forever outer loop continues)"
            )
        else:
            report["nextStep"] = "inspect CEM getConnectInfo / SCG TCP-TLS protocol"
        report["duration"] = round(time.monotonic() - started, 3)
        _emit_product_report(args, report)
        return report

    # Extract last-round SCG stats for product-level success criteria.
    # spice_ok=False means MAIN_INIT never arrived; returncode alone is false-positive.
    last = {}
    inner_stats = {}
    try:
        stats_obj = result.stats if isinstance(getattr(result, "stats", None), dict) else {}
        last = stats_obj.get("last") if isinstance(stats_obj.get("last"), dict) else {}
        inner_stats = last.get("stats") if isinstance(last.get("stats"), dict) else {}
    except Exception:
        last, inner_stats = {}, {}

    spice_ok = bool(last.get("spice_ok"))
    channels = list(last.get("connected_channels") or [])
    soho_code = None
    try:
        if inner_stats.get("soho_heartbeat_code") is not None:
            soho_code = int(inner_stats.get("soho_heartbeat_code") or 0)
    except Exception:
        soho_code = None
    lock_codes = {4039, 4040, 4041, 4042}
    desktop_lock_hint = bool(soho_code in lock_codes) if soho_code is not None else False

    tls_hold_ok = bool(last.get("tls_hold_ok"))
    keepalive_mode = str(last.get("keepalive_mode") or getattr(args, "scg_mode", None) or "spice")
    report["spice_ok"] = spice_ok
    report["tls_hold_ok"] = tls_hold_ok
    report["keepalive_mode"] = keepalive_mode
    report["connected_channels"] = channels
    report["soho_heartbeat_code"] = soho_code
    report["desktop_lock_hint"] = desktop_lock_hint
    report["scg_stats"] = inner_stats
    report["scg_progress"] = last.get("progress") or {}
    report["scg_rounds"] = (result.stats or {}).get("rounds") if isinstance(getattr(result, "stats", None), dict) else None
    report["degraded"] = bool(last.get("degraded")) if "degraded" in last else (not spice_ok)
    report["fail_reason"] = last.get("fail_reason") or ""

    # Fail-closed honesty on product report fields (I-PHASE-I-FLAGS).
    if keepalive_mode == "tls_hold":
        report["spice_ok"] = False
        report["degraded"] = True
        report["keepalive_mode"] = "tls_hold"
        if not report.get("fail_reason"):
            report["fail_reason"] = "tls_hold_mode_spice_skipped"
    elif report.get("spice_ok") and str(report.get("keepalive_mode") or "").lower() == "tls_hold":
        report["spice_ok"] = False
        report["degraded"] = True
    # Invariant: never (tls_hold and spice_ok)
    assert not (
        str(report.get("keepalive_mode") or "").lower() == "tls_hold" and report.get("spice_ok")
    ), "honesty invariant violated: tls_hold with spice_ok=True"

    # D3/P06/P07: surface KPI VM plane (fail-closed; not business proof alone)
    report["vm_running"] = last.get("vm_powered_throughout")  # may be None
    report["vm_running_throughout"] = last.get("vm_powered_throughout")
    if report["vm_running_throughout"] is None:
        report["vm_running_throughout"] = last.get("vm_running_throughout")
    report["vm_sample_count"] = last.get("vm_sample_count") or 0
    report["wall_hold_seconds"] = last.get("wall_hold_seconds")
    if report["vm_running"] is None and isinstance(inner_stats, dict):
        report["vm_running"] = inner_stats.get("vm_powered_throughout")
        if report["vm_running"] is None:
            report["vm_running"] = inner_stats.get("vm_running_throughout")
        if not report["vm_sample_count"]:
            report["vm_sample_count"] = inner_stats.get("vm_sample_count") or 0
        if report["wall_hold_seconds"] is None:
            report["wall_hold_seconds"] = inner_stats.get("wall_hold_seconds")
    if report.get("vm_running_throughout") is None:
        report["vm_running_throughout"] = report.get("vm_running")

    if args.forever:
        # Forever: never claim product PASS. Honesty flags still apply.
        # ok remains False until a finite run completes with mode-correct success.
        report["ok"] = False
        report["stage"] = "scg-keepalive-running"
        report["degraded"] = bool(report.get("degraded")) or (keepalive_mode == "tls_hold") or (not spice_ok)
        if keepalive_mode == "tls_hold":
            report["spice_ok"] = False
            report["degraded"] = True
            report["fail_reason"] = report.get("fail_reason") or "tls_hold_mode_spice_skipped"
        report["nextStep"] = (
            "SCG keepalive running (forever); report.ok stays False until finite exit; terminate to stop"
        )
    else:
        scg_mode = str(getattr(args, "scg_mode", None) or keepalive_mode or "spice").strip().lower()
        if scg_mode == "tls_hold":
            # Explicit degradation path: success = Auth+TLS held full duration (no SPICE claim).
            ok = (result.returncode == 0) and tls_hold_ok
        else:
            # Strict spice mode (default): require process success AND MAIN_INIT session.
            ok = (result.returncode == 0) and spice_ok
        report["ok"] = ok
        report["stage"] = "scg-keepalive-done" if ok else "scg-keepalive-failed"
        _stderr = result.stderr
        if isinstance(_stderr, bytes):
            _stderr = _stderr.decode("utf-8", "replace")
        if ok:
            report["error"] = ""
            if scg_mode == "tls_hold":
                report["nextStep"] = (
                    "tls_hold succeeded (Auth+TLS held); spice_ok remains False by design; "
                    "use --scg-mode spice when full SPICE session is required"
                )
            else:
                report["nextStep"] = ""
        else:
            if result.returncode != 0:
                report["error"] = (_stderr.strip() or "SCG Python route exited %d" % result.returncode)
                report["nextStep"] = "inspect SCG Python stderr / CEM GetConnectInfo"
            elif scg_mode == "tls_hold":
                report["error"] = (
                    "SCG tls_hold failed; returncode=%s tls_hold_ok=%s soho=%s"
                    % (result.returncode, tls_hold_ok, soho_code)
                )
                report["nextStep"] = "inspect TLS hold / soho heartbeat / network path to SCG"
            else:
                report["error"] = (
                    "SCG spice_ok=False (MAIN_INIT missing); returncode=%s channels=%s soho=%s lock_hint=%s"
                    % (result.returncode, channels, soho_code, desktop_lock_hint)
                )
                report["nextStep"] = (
                    "SPICE-over-trunk handshake failed; see golden TLS+local SPICE topology "
                    "or enable --scg-mode tls_hold if only TCP/TLS hold is required"
                )

    # D3: business_ok after mode ok (fail-closed; tls_hold never true)
    report["business_ok"] = compute_business_ok(report, forever=bool(getattr(args, "forever", False)))
    assert not (
        report.get("business_ok")
        and str(report.get("keepalive_mode") or "").lower() == "tls_hold"
    ), "D3 invariant: business_ok must never be True under tls_hold"

    report["duration"] = round(time.monotonic() - started, 3)
    _emit_product_report(args, report)
    return report


def _zte_session_invalid(err_text):
    """True when ZTE material/startDesktop reports session-invalid 1000100."""
    text = str(err_text or "")
    low = text.lower()
    return (
        "1000100" in text
        or "会话失效" in text
        or "session invalid" in low
        or "session_invalid" in low
    )


def _run_zte_keepalive(args, auth, route, vm_id, report, started):
    """Dispatch to the ZTE material control-plane (B keepalive.go ZTE branch).

    ``zte_route`` is owned by w1 (P10); import defensively so a transient
    import error does not crash the CLI — it reports a redacted next-step.
    On ZTE 1000100 / session-invalid, refresh token + firmAuth once in-round
    then retry material exactly once (mirrors SCG token refresh behaviour).
    """
    import time
    report["stage"] = "zte-keepalive"
    try:
        from . import zte_route
    except Exception as exc:  # noqa: BLE001 - ZTE route may be mid-flight
        report["error"] = "zte_route unavailable: %s" % exc
        report["nextStep"] = "wait for ZTE route (P10) completion before retrying"
        report["duration"] = round(time.monotonic() - started, 3)
        _emit_product_report(args, report)
        return report

    firm = None
    material = None
    try:
        firm = zte_route.ZTEFirmAuth.from_auth_dict(auth)
        material = zte_route.run_material(firm, target_vm_id=vm_id)
        # Same-round one-shot retry: session invalid → ensure_token → firmAuth → material.
        if (not material.ok) and _zte_session_invalid(material.error):
            report["stage"] = "zte-session-refresh"
            print(
                "[%s] ZTE session invalid (1000100); ensure_token + firmAuth + retry once"
                % core.short_time(),
                flush=True,
            )
            try:
                from . import token as token_mod
                token_mod.ensure_token(getattr(args, "state", None), relogin=True)
            except Exception as tok_exc:  # noqa: BLE001 - still attempt firmAuth refresh
                print(
                    "[%s] ZTE ensure_token during 1000100 retry failed: %s"
                    % (core.short_time(), tok_exc),
                    flush=True,
                )
            try:
                selected = cloud.selected_user_service_id(
                    getattr(args, "state", None),
                    getattr(args, "user_service_id", None),
                )
                ns_args = core.argparse.Namespace(
                    state=getattr(args, "state", None),
                    user_service_id=selected,
                )
                auth = core.get_firm_auth(ns_args)
                firm = zte_route.ZTEFirmAuth.from_auth_dict(auth)
                material = zte_route.run_material(firm, target_vm_id=vm_id)
                report["sessionRefresh"] = True
            except Exception as retry_exc:  # noqa: BLE001 - surface refresh failure
                report["error"] = "1000100 retry failed: %s" % retry_exc
                report["nextStep"] = "re-login / re-select desktop, then retry ZTE"
                report["duration"] = round(time.monotonic() - started, 3)
                report["sessionRefresh"] = False
                _emit_product_report(args, report)
                return report
    except Exception as exc:  # noqa: BLE001 - surface any ZTE failure
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["nextStep"] = "inspect ZTE material stage %s" % report["stage"]
        report["duration"] = round(time.monotonic() - started, 3)
        _emit_product_report(args, report)
        return report
    md = material.to_dict()
    report["ok"] = md.get("ok", False)
    report["stage"] = md.get("stage") or "zte-keepalive"
    report["error"] = md.get("error") or ""
    report["nextStep"] = md.get("nextStep") or ""
    report["duration"] = round(time.monotonic() - started, 3)

    # --- P6–P9: full CAG → mux → raw-SPICE keepalive session ---
    if material.ok and material.connect_str:
        import os
        duration = float(getattr(args, "duration", 0) or 0)
        if duration <= 0:
            duration = float(os.environ.get("CCK_ZTE_KEEPALIVE_DURATION", "120"))
        try:
            # fix(b): derive the CAG auth template from the freshly obtained
            # material instead of requiring the CCK_ZTE_CAG_AUTH_TEMPLATE_HEX
            # env var.  build_cag_auth_blob(inner, None) builds a valid 220-byte
            # CAG auth blob from scratch (host/proxySport/vmId); its hex is a
            # valid 220-byte template that parse_auth_template accepts, so
            # run_zte_keepalive_session skips the env fallback.  (The type-101
            # auth buffer is 270 bytes and is rejected by parse_auth_template,
            # which only accepts 241/220-byte CAG templates.)
            from .zte_connect_params import decode_connect_params, inner_from_connect_params
            from .zte_cag import build_cag_auth_blob
            _cp = decode_connect_params(material.connect_str)
            _inner = inner_from_connect_params(_cp)
            auth_template_hex = build_cag_auth_blob(_inner, None).hex()
            counters = zte_route.run_zte_keepalive_session(
                firm, material.connect_str, duration=duration,
                auth_template_hex=auth_template_hex,
            )
            report["stage"] = "zte-keepalive-done"
            report["keepalive"] = counters
            report["nextStep"] = "session completed; inspect counters"
        except Exception as exc:  # noqa: BLE001 - surface CAG/mux/raw failure
            report["stage"] = "zte-keepalive-failed"
            report["error"] = "%s: %s" % (type(exc).__name__, exc)
            report["nextStep"] = ("inspect CAG/mux/raw stage; ensure "
                                  "CCK_ZTE_CAG_AUTH_TEMPLATE_HEX is set")
    elif material.ok and not material.connect_str:
        report["nextStep"] = "material ok but connect_str missing; cannot dial CAG"

    report["duration"] = round(time.monotonic() - started, 3)
    _emit_product_report(args, report)
    return report


def cmd_product_keepalive(args):
    """Product keepalive entry — route firmAuth to SCG or ZTE keepalive.

    Mirrors B ``cmd/keepalive.go`` ``Keepalive()``: load firmAuth, classify
    route, dispatch to the matching keepalive backend. Emits a redacted report
    (route/stage/ok/duration/error/nextStep); never prints raw credentials.
    """
    # I-E-P1-MODULE-GUARD: hard product pin before firmAuth/LIVE route
    product_pin.enforce_product_pin(
        getattr(args, "user_service_id", None),
        tag="PRODUCT-PIN",
    )

    import time
    started = time.monotonic()
    report = _product_keepalive_report()

    selected = cloud.selected_user_service_id(args.state, args.user_service_id)
    report["userServiceId"] = str(selected or "")
    ns_args = core.argparse.Namespace(state=args.state, user_service_id=selected)
    try:
        auth = core.get_firm_auth(ns_args)
    except Exception as exc:  # noqa: BLE001 - gate must report, not crash
        report["error"] = str(exc)
        report["kind"] = product_router.RouteKind.ERROR.value
        report["reason"] = "firmAuth failed: %s" % exc
        report["nextStep"] = "fix login/account/firmAuth; do not touch protocol"
        report["duration"] = round(time.monotonic() - started, 3)
        _emit_product_report(args, report)
        return report

    route = product_router.classify_firm_auth_route(auth)
    route.userServiceId = str(selected or "")
    if not route.vmId:
        route.vmId = str(auth.get("vmId") or auth.get("vmID") or auth.get("uuid") or "")
    report["kind"] = route.kind.value
    report["reason"] = route.reason
    report["vmId"] = route.vmId
    report["firmAuthSummary"] = product_router.redacted_firm_auth_summary(auth)

    vm_id = args.vm_id or route.vmId

    if route.kind == product_router.RouteKind.ERROR:
        report["error"] = route.reason
        report["nextStep"] = "stop; fix firmAuth fields before any protocol work"
        report["duration"] = round(time.monotonic() - started, 3)
        _emit_product_report(args, report)
        return report

    if route.kind == product_router.RouteKind.SCG:
        return _run_scg_keepalive(args, auth, route, vm_id, report, started)

    if route.kind == product_router.RouteKind.ZTE:
        return _run_zte_keepalive(args, auth, route, vm_id, report, started)

    # Defensive: unknown route kind.
    report["error"] = "unhandled route kind: %s" % route.kind
    report["nextStep"] = "extend product_router with the new route kind"
    report["duration"] = round(time.monotonic() - started, 3)
    _emit_product_report(args, report)
    return report


# ---------------------------------------------------------------------------
# P11-005/006/007: ZTE layered diagnostic sub-checks
# ---------------------------------------------------------------------------

def _zte_subcheck_preamble(args, route_name, stage):
    """Shared auth/route preamble for the ZTE diagnostic sub-checks.

    Loads firmAuth, classifies the route, validates it is ZTE, and builds a
    ``ZTEFirmAuth``.  Returns ``(report, started, firm, vm_id, zte_route)`` or
    ``None`` if a failure report has already been printed.
    """
    import time
    started = time.monotonic()
    report = _product_keepalive_report(route_name=route_name, stage=stage)

    selected = cloud.selected_user_service_id(args.state, args.user_service_id)
    report["userServiceId"] = str(selected or "")
    ns_args = core.argparse.Namespace(state=args.state, user_service_id=selected)
    try:
        auth = core.get_firm_auth(ns_args)
    except Exception as exc:  # noqa: BLE001 - gate must report, not crash
        report["error"] = str(exc)
        report["kind"] = product_router.RouteKind.ERROR.value
        report["reason"] = "firmAuth failed: %s" % exc
        report["nextStep"] = "fix login/account/firmAuth; do not touch protocol"
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return None

    route = product_router.classify_firm_auth_route(auth)
    route.userServiceId = str(selected or "")
    if not route.vmId:
        route.vmId = str(auth.get("vmId") or auth.get("vmID") or auth.get("uuid") or "")
    report["kind"] = route.kind.value
    report["reason"] = route.reason
    report["vmId"] = route.vmId
    report["firmAuthSummary"] = product_router.redacted_firm_auth_summary(auth)

    vm_id = args.vm_id or route.vmId

    if route.kind == product_router.RouteKind.ERROR:
        report["error"] = route.reason
        report["nextStep"] = "stop; fix firmAuth fields before any protocol work"
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return None

    if route.kind != product_router.RouteKind.ZTE:
        report["error"] = ("route is %s, not ZTE — ZTE sub-checks require a "
                           "ZTE route" % route.kind.value)
        report["nextStep"] = ("use product-keepalive, or fix firmAuth to "
                              "obtain a ZTE route")
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return None

    try:
        from . import zte_route
    except Exception as exc:  # noqa: BLE001 - ZTE route may be mid-flight
        report["error"] = "zte_route unavailable: %s" % exc
        report["nextStep"] = "wait for ZTE route (P10) completion before retrying"
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return None

    try:
        firm = zte_route.ZTEFirmAuth.from_auth_dict(auth)
    except Exception as exc:  # noqa: BLE001 - surface ZTE auth build failure
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["nextStep"] = "fix firmAuth ZTE fields (cagIp/cagPort/vmId)"
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return None

    return report, started, firm, vm_id, zte_route


def _zte_run_material(args, route_name, stage):
    """Run ``run_material`` and populate the report from its ``to_dict()``.

    Returns ``(report, started, material, firm, zte_route)`` or ``None`` if a
    failure report has already been printed.
    """
    import time
    pre = _zte_subcheck_preamble(args, route_name, stage)
    if pre is None:
        return None
    report, started, firm, vm_id, zte_route = pre
    try:
        material = zte_route.run_material(firm, target_vm_id=vm_id)
    except Exception as exc:  # noqa: BLE001 - surface any ZTE failure
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["nextStep"] = "inspect ZTE material stage %s" % stage
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return None
    md = material.to_dict()
    report["ok"] = md.get("ok", False)
    report["stage"] = md.get("stage") or stage
    report["error"] = md.get("error") or ""
    report["nextStep"] = md.get("nextStep") or ""
    return report, started, material, firm, zte_route


def cmd_product_zte_material_check(args):
    """P11-005: verify the ZTE material control-plane up to connectStr.

    Runs ``run_material`` (CAG HTTPS → token → desktop list → connectStr) and
    stops *before* connectStr parsing.  Emits the standard redacted report.
    """
    import time
    result = _zte_run_material(args, "product-zte-material-check",
                               "zte-material-check")
    if result is None:
        return
    report, started, material, firm, zte_route = result
    report["duration"] = round(time.monotonic() - started, 3)
    _print(report)


def cmd_product_zte_tcp_check(args):
    """P11-006: verify connectStr decode + outer/inner separation (pre-dial).

    Runs ``run_material`` then ``decode_connect_params`` /
    ``inner_from_connect_params`` / ``outer_from_firm`` and stops *before* the
    CAG TCP/TLS dial.  Emits the standard redacted report.
    """
    import time
    result = _zte_run_material(args, "product-zte-tcp-check", "zte-tcp-check")
    if result is None:
        return
    report, started, material, firm, zte_route = result
    if not material.ok or not material.connect_str:
        report["ok"] = False
        report["stage"] = "zte-tcp-check"
        if not report["error"]:
            report["error"] = ("material ok=%s but connect_str missing — "
                               "cannot decode" % material.ok)
        if not report["nextStep"]:
            report["nextStep"] = "fix material stage to obtain connectStr"
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return
    try:
        from .zte_connect_params import (decode_connect_params,
                                         inner_from_connect_params)
        cp = decode_connect_params(material.connect_str)
        inner_from_connect_params(cp)
        outer = zte_route.outer_from_firm(firm)
        _ = outer.address  # touch to validate outer separation
    except Exception as exc:  # noqa: BLE001 - surface decode failure
        report["ok"] = False
        report["stage"] = "zte-tcp-check"
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["nextStep"] = ("inspect connectStr decode / outer-inner "
                              "separation")
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return
    report["ok"] = True
    report["stage"] = "zte-tcp-check"
    report["error"] = ""
    report["nextStep"] = ("connectStr decoded; outer/inner separated; "
                          "ready to dial CAG TCP/TLS")
    report["duration"] = round(time.monotonic() - started, 3)
    _print(report)


def cmd_product_zte_display_check(args):
    """P11-007: verify CAG dial + mux + raw SPICE main handshake (pre-DisplayInit).

    Runs ``run_material`` → decode → CAG TCP/TLS dial → mux open → main link →
    raw SPICE main handshake, and stops *before* the DisplayInit subchannel
    setup.  Emits the standard redacted report.
    """
    import os
    import time
    result = _zte_run_material(args, "product-zte-display-check",
                               "zte-display-check")
    if result is None:
        return
    report, started, material, firm, zte_route = result
    if not material.ok or not material.connect_str:
        report["ok"] = False
        report["stage"] = "zte-display-check"
        if not report["error"]:
            report["error"] = ("material ok=%s but connect_str missing — "
                               "cannot dial" % material.ok)
        if not report["nextStep"]:
            report["nextStep"] = "fix material stage to obtain connectStr"
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return
    dial_timeout = float(getattr(args, "dial_timeout", 0) or 0) or 30.0
    tls_conn = None
    try:
        from .zte_connect_params import (decode_connect_params,
                                         inner_from_connect_params)
        from .zte_cag import CAGDialOptions, dial_cag_tcp_tls
        from .zte_cag_mux import CAGMux, open_cag_mux_link

        cp = decode_connect_params(material.connect_str)
        inner = inner_from_connect_params(cp)
        outer = zte_route.outer_from_firm(firm)

        auth_template_hex = os.environ.get("CCK_ZTE_CAG_AUTH_TEMPLATE_HEX", "")
        if not auth_template_hex:
            raise zte_route.ZTEError(
                "CCK_ZTE_CAG_AUTH_TEMPLATE_HEX env var not set — "
                "cannot dial CAG without auth template")

        opts = CAGDialOptions(
            address=outer.address,
            inner=inner,
            auth_template_hex=auth_template_hex,
            timeout=dial_timeout,
        )
        tls_conn, _session = dial_cag_tcp_tls(opts)
        mux = CAGMux.open(tls_conn)
        main_link = open_cag_mux_link(mux, cp)
        raw_result = zte_route.RawMainHandshake(
            main_link, cp.key, cp.vm_id,
            main_link.link_uuid, main_link.trace_id, main_link.redq_span_id,
        )
        if not raw_result.OK:
            raise zte_route.ZTEError(
                "raw SPICE main handshake failed: %s"
                % (getattr(raw_result, "error", None) or "unknown"))
    except Exception as exc:  # noqa: BLE001 - surface dial/mux/handshake failure
        report["ok"] = False
        report["stage"] = "zte-display-check"
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["nextStep"] = ("inspect CAG dial / mux / raw SPICE main "
                              "handshake stage")
        report["duration"] = round(time.monotonic() - started, 3)
        _print(report)
        return
    finally:
        if tls_conn is not None:
            try:
                close = getattr(tls_conn, "close", None)
                if callable(close):
                    close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
    report["ok"] = True
    report["stage"] = "zte-display-check"
    report["error"] = ""
    report["nextStep"] = ("CAG dialed + mux opened + raw SPICE main handshake "
                          "OK; ready for DisplayInit subchannels")
    report["duration"] = round(time.monotonic() - started, 3)
    _print(report)


def cmd_simple_keepalive(args):
    """Non-interactive entry used by WebUI LIVE child.

    Strictly aligns with simple menu path:
    _simple_run_keepalive -> _simple_forced_keepalive(hand-picked ZTE|SCG).
    Never routes through product-keepalive interactive / desktop HTTP keepalive.
    """
    state_path = args.state
    target = (
        getattr(args, "user_service_id", None)
        or getattr(args, "userServiceId", None)
        or ""
    )
    target = str(target or "").strip()
    if not target:
        try:
            st = core.load_state(state_path)
        except Exception:
            st = {}
        if isinstance(st, dict):
            target = str(
                st.get("userServiceId")
                or st.get("selectedUserServiceId")
                or st.get("user_service_id")
                or ""
            ).strip()
    if not target:
        raise core.CmccError(
            "simple-keepalive requires --user-service-id or state selectedUserServiceId"
        )
    protocol = str(getattr(args, "protocol", None) or "ZTE").upper()
    if protocol not in ("ZTE", "SCG"):
        protocol = "ZTE"
    interval_minutes = int(getattr(args, "interval_minutes", None) or 5)
    traffic_seconds = int(getattr(args, "traffic_seconds", None) or 60)
    mode = str(getattr(args, "mode", None) or "2")
    if mode not in ("1", "2"):
        ml = mode.lower()
        if ml in ("once", "single", "1", "one"):
            mode = "1"
        else:
            mode = "2"
    try:
        cloud.select_desktop(target, state_path, skip_target_assert=True)
    except TypeError:
        try:
            cloud.select_desktop(target, state_path)
        except Exception as err:
            print(f"[simple-keepalive] select_desktop warn: {err}", flush=True)
    except Exception as err:
        print(f"[simple-keepalive] select_desktop warn: {err}", flush=True)
    print(
        f"[simple-keepalive] start protocol={protocol} mode={mode} "
        f"interval_minutes={interval_minutes} traffic_seconds={traffic_seconds} "
        f"userServiceId={target}",
        flush=True,
    )
    _simple_run_keepalive(
        target,
        state_path,
        protocol,
        interval_minutes,
        traffic_seconds,
        mode,
    )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="移动云电脑保活工具：普通用户直接运行 python3 -m cmcc_cloud_alive，然后按中文提示操作。",
        epilog="常用：python3 -m cmcc_cloud_alive    高级：python3 -m cmcc_cloud_alive interactive --help",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="状态文件路径；默认 ~/.cmcc-cloud-alive/state.json（可用环境变量 CMCC_ALIVE_STATE 覆盖）",
    )
    sub = parser.add_subparsers(dest="cmd", required=False, metavar="命令")

    p = sub.add_parser("login", help="account login; omit password to use hidden prompt")
    p.add_argument("username", nargs="?", help="account; prompts when omitted")
    p.add_argument("password", nargs="?", help="optional; omit to avoid plaintext shell history")
    p.add_argument(
        "--save-password",
        action="store_true",
        help="兼容参数；当前默认保存到 ~/.cmcc-cloud-alive/state.json 以便 token 失效自动重登",
    )
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("set-profile")
    p.add_argument("profile", choices=["auto", "linux", "windows", "mac"])
    p.add_argument("--from-har", default="", help="import accepted X-SOHO fingerprint headers from HAR")
    p.add_argument("--preferred-host", default="soho.komect.com")
    p.set_defaults(func=cmd_set_profile)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("select")
    p.add_argument("user_service_id")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("status")
    p.add_argument("user_service_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("product-route-check")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--report-file", default="", help="write redacted control-plane route report")
    p.set_defaults(func=cmd_product_route_check)

    p = sub.add_parser("power-monitor")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--interval", type=int, default=60, help="seconds between independent power-state checks")
    p.add_argument("--duration", type=int, default=2400, help="monitor length; 0 means run forever")
    p.add_argument("--report-file", default="", help="write full JSON evidence report")
    p.add_argument("--stop-on-off", action="store_true", help="stop as soon as status is off or not running")
    p.add_argument("--fail-on-off", action="store_true", help="exit non-zero if status becomes off/not running or cannot be verified")
    p.add_argument("--no-relogin", action="store_true", help="do not maintain login state before status checks")
    p.add_argument("--no-stop-on-error", action="store_true", help="continue after a status check cannot be verified")
    p.set_defaults(func=cmd_power_monitor)

    p = sub.add_parser("verified-run")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--duration", type=int, default=2400, help="verification length; default is 40 minutes")
    p.add_argument("--interval", type=int, default=60, help="seconds between independent power-state checks")
    p.add_argument("--report-file", default="", help="write full JSON evidence report")
    p.add_argument("--allow-command-exit", action="store_true", help="allow the command to exit before the requested duration")
    p.add_argument("--no-relogin", action="store_true", help="do not maintain login state before status checks")
    p.add_argument("--no-stop-on-error", action="store_true", help="continue after a status check cannot be verified")
    p.add_argument("--cwd", default="", help="working directory for the command")
    p.add_argument("command", nargs=argparse.REMAINDER, help="command to run after --")
    p.set_defaults(func=cmd_verified_run)

    p = sub.add_parser("boot")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--boot-wait", type=int, default=180)
    p.add_argument("--timeout", type=int, default=15)
    p.set_defaults(func=cmd_boot)

    p = sub.add_parser("keepalive-once")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--probe", action="store_true", help="also send Windows terminalprobe-style performance report")
    p.add_argument("--point", action="store_true", help="also send point/custom analytics event")
    p.add_argument("--disconnect-time", action="store_true", help="also call /cc/cloudPc/getDisconnectTime/v1 as observed on macOS")
    p.add_argument("--connect-events", action="store_true", help="also send official connect-success point events")
    p.add_argument("--no-firm-auth", action="store_true", help="do not call /cc/getFirmAuth/v1 in this round")
    p.set_defaults(func=cmd_keepalive_once)

    p = sub.add_parser("keepalive")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--interval", type=int, default=300, help="desktop HTTP keepalive interval seconds")
    p.add_argument("--run-seconds", type=int, default=0, help="0 means run forever")
    p.add_argument("--account-relogin-hours", type=int, default=24)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--point", action="store_true")
    p.add_argument("--disconnect-time", action="store_true")
    p.add_argument("--connect-events", action="store_true")
    p.add_argument("--no-firm-auth", action="store_true")
    p.set_defaults(func=cmd_keepalive)

    p = sub.add_parser("mqtt-keepalive", help="open MQTT 3.1.1 over TLS to alive.soho.komect.com and smoke-test the link")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--duration", type=int, default=60, help="smoke-test length seconds; capped at 120 for safety")
    p.add_argument("--report-file", default="", help="write redacted JSON evidence report")
    p.set_defaults(func=cmd_mqtt_keepalive)

    p = sub.add_parser("interactive", help="productized interactive keepalive: login, select cloud PC, keepalive loop")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--username", default=None, help="account phone number; prompt if omitted")
    p.add_argument("--password", default=None, help="password; prompt hidden if omitted (never logged)")
    p.add_argument("--duration", type=int, default=0, help="run seconds; 0 means run forever")
    p.add_argument("--heartbeat-interval", type=int, default=300, help="keepalive round interval seconds")
    p.add_argument("--status-interval", type=int, default=60, help="状态展示间隔秒数；只打印，不触发开机")
    p.add_argument("--boot-wait", type=int, default=30, help="首次自动开机后的等待秒数")
    p.add_argument("--boot-timeout", type=int, default=300, help="首次自动开机最长等待秒数")
    p.add_argument("--report-file", default=None, help="write JSON report to this path")
    p.add_argument("--non-interactive", action="store_true", help="skip prompts; auto-select first target")
    p.add_argument("--probe", action="store_true")
    p.add_argument("--point", action="store_true")
    p.add_argument("--connect-events", action="store_true")
    p.add_argument("--no-firm-auth", action="store_true")
    p.set_defaults(func=cmd_interactive)

    p = sub.add_parser("cag-keepalive-once")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--boot-wait", type=int, default=180)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--observe-seconds", type=int, default=0, help="wait after CAG refresh and report whether the official desktop session process disappeared")
    p.add_argument("--post-http-prime", action="store_true", help="after CAG refresh, replay official visible HTTP timers once")
    p.set_defaults(func=cmd_cag_keepalive_once)

    p = sub.add_parser("cag-keepalive")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--interval", type=int, default=60, help="kept for compatibility; CAG keepalive loop is disabled")
    p.add_argument("--run-seconds", type=int, default=0, help="0 means run forever")
    p.add_argument("--account-relogin-hours", type=int, default=24)
    p.add_argument("--boot-wait", type=int, default=180)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--post-http-prime", action="store_true", help="after each CAG refresh, replay official visible HTTP timers once")
    p.set_defaults(func=cmd_cag_keepalive)

    p = sub.add_parser("cag-verify")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--duration", type=int, default=2400, help="verification run length; default is 40 minutes")
    p.add_argument("--min-proof-seconds", type=int, default=2400, help="minimum duration required before declaring CAG proven")
    p.add_argument("--interval", type=int, default=60, help="CAG HTTPS refresh interval seconds")
    p.add_argument("--account-relogin-hours", type=int, default=24)
    p.add_argument("--boot-wait", type=int, default=180)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--report-file", default="", help="write full JSON evidence report")
    p.add_argument("--allow-official-client-present", action="store_true", help="allow a contaminated takeover/control run")
    p.add_argument("--no-stop-on-off", action="store_true", help="continue collecting evidence after the first off/not-running status")
    p.add_argument("--post-http-prime", action="store_true", help="after each CAG refresh, replay official visible HTTP timers once")
    p.set_defaults(func=cmd_cag_verify)

    p = sub.add_parser("token-check")
    p.add_argument("--no-relogin", action="store_true")
    p.set_defaults(func=cmd_token_check)

    p = sub.add_parser("account-keepalive")
    p.set_defaults(func=cmd_account_keepalive)

    p = sub.add_parser("logout")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--desktop", action="store_true")
    p.add_argument("--account", action="store_true")
    p.add_argument("--keep-local", action="store_true")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("probe-base")
    p.set_defaults(func=cmd_probe_base)

    p = sub.add_parser("spice-offline-proof")
    p.set_defaults(func=cmd_spice_offline_proof)

    p = sub.add_parser("analyze-zime-probe")
    p.add_argument("jsonl", help="JSONL emitted by scripts/run-zime-probe.sh")
    p.add_argument("--report-file", default="", help="write full JSON analysis report")
    p.set_defaults(func=cmd_analyze_zime_probe)

    p = sub.add_parser("extract-zime-sequence")
    p.add_argument("jsonl", help="JSONL emitted by scripts/run-zime-probe.sh")
    p.add_argument("--focus-kind", default="spice-mini-unknown:0x082a", help="payload kind to center context windows around")
    p.add_argument("--window", type=int, default=6, help="records before/after each focus match")
    p.add_argument("--limit", type=int, default=160, help="maximum sequence records to print")
    p.add_argument("--report-file", default="", help="write runner-oriented sequence report")
    p.set_defaults(func=cmd_extract_zime_sequence)

    p = sub.add_parser("analyze-rap-zime")
    p.add_argument("jsonl", help="JSONL emitted by scripts/run-zime-probe.sh")
    p.add_argument("--sample-limit", type=int, default=40, help="maximum samples per section")
    p.add_argument("--report-file", default="", help="write RAP/ZIME runner-input report")
    p.set_defaults(func=cmd_analyze_rap_zime)

    p = sub.add_parser("analyze-rap-zime-pcap")
    p.add_argument("pcap", help="pcap/pcapng captured without LD_PRELOAD")
    p.add_argument("--ss-log", default="", help="optional ss -p snapshots captured during the same window")
    p.add_argument("--focus-udp-port", type=int, default=8899, help="UDP port to prioritize as RAP/ZIME outer flow candidate")
    p.add_argument("--sample-limit", type=int, default=20, help="maximum conversations per section")
    p.add_argument("--report-file", default="", help="write full JSON metadata report")
    p.set_defaults(func=cmd_analyze_rap_zime_pcap)

    p = sub.add_parser("check-rap-zime-runner-input")
    p.add_argument("runner_input", help="JSON report from analyze-rap-zime or a runnerInput object")
    p.add_argument("--require-templates", action="store_true", help="require send-side rapDataFrameSendTemplates for dynamic RAP 0x81 header selection")
    p.add_argument("--no-require-ztec", action="store_true", help="do not require candidateZtecTargets")
    p.add_argument("--require-kcp-auth-ready", action="store_true", help="require fresh KCP auth material source or proof that KCP auth is disabled before live SYN/SYNACK probing")
    p.add_argument("--max-age-seconds", type=float, default=None, help="mark the file not ready when its mtime is older than this many seconds; mtime is only a freshness hint")
    p.add_argument("--report-file", default="", help="write full JSON readiness report")
    p.set_defaults(func=cmd_check_rap_zime_runner_input)

    p = sub.add_parser("rap-zime-udp-probe")
    p.add_argument("--runner-input", default="", help="JSON report from analyze-rap-zime")
    p.add_argument("--target", default="", help="UDP target host:port; required when runner input has no candidate target")
    p.add_argument("--tunnel-id", default="", help="4-byte RAP tunnel id as hex; defaults to runner input primaryTunnelId")
    p.add_argument("--payload-hex", action="append", default=[], help="optional RAP payload to send, hex encoded")
    p.add_argument("--native-report", default="", help="append complete packet-out payloads from a zime-native-bridge report")
    p.add_argument("--no-ztec", action="store_true", help="skip the ZTEC keepalive probe")
    p.add_argument("--ztec-host", default="", help="IPv4 address encoded into the ZTEC request; defaults to target host")
    p.add_argument("--ztec-port", type=int, default=None, help="port encoded into the ZTEC request; defaults to target port")
    p.add_argument("--udp-rap-payload-envelope", choices=sorted(rap_zime.RAP_PAYLOAD_ENVELOPES), default=rap_zime.RAP_PAYLOAD_ENVELOPE_RAW, help="payload transform inside RAP data frames before sending probe payloads")
    p.add_argument("--udp-rap-template-mode", choices=sorted(rap_zime.RAP_TEMPLATE_MODES), default=rap_zime.RAP_TEMPLATE_MODE_STATIC, help="RAP 0x81 header template selection: static runner-input template, runner-input sequence, or payload-kind matched templates")
    p.add_argument("--timeout", type=float, default=5)
    p.add_argument("--wait-response", action="store_true", help="wait for one RAP response datagram after each payload")
    p.add_argument("--report-file", default="", help="write full JSON UDP probe report")
    p.set_defaults(func=cmd_rap_zime_udp_probe)

    p = sub.add_parser("rap-zime-kcp-sync-probe")
    p.add_argument("--runner-input", default="", help="JSON report from analyze-rap-zime-pcap or a runnerInput object")
    p.add_argument("--target", default="", help="UDP target host:port; required when runner input has no candidate target")
    p.add_argument("--timeout", type=float, default=1.0, help="seconds to wait for each UDP response")
    p.add_argument("--receive-limit", type=int, default=4, help="maximum UDP datagrams to inspect")
    p.add_argument("--syn-id", type=_int_auto, default=None, help="client SYN id; defaults to a generated 32-bit value")
    p.add_argument("--conv", type=_int_auto, default=0, help="client KCP conv copied into SYN una; defaults to 0")
    p.add_argument("--current", type=_int_auto, default=None, help="client current timestamp; defaults to monotonic milliseconds")
    p.add_argument("--mtu", type=int, default=1400, help="client-advertised MTU in SYN len")
    p.add_argument("--ssl", action="store_true", help="set client SYN SSL capability bit")
    p.add_argument("--no-detect-mtu", action="store_true", help="clear client SYN detect-MTU bit")
    p.add_argument("--no-pack-check", action="store_true", help="clear client pack-check capability bit")
    p.add_argument("--no-fec", action="store_true", help="clear client FEC capability bit")
    p.add_argument("--multi", action="store_true", help="set client multi-link capability bit")
    p.add_argument("--algo-mode", type=int, choices=[1, 2], default=1, help="1 clears GCC wnd bit; 2 sets it")
    p.add_argument("--no-stream", action="store_true", help="clear client stream capability bit")
    p.add_argument("--no-quic", action="store_true", help="clear client QUIC capability bit")
    p.add_argument("--outband", dest="outband", action="store_true", default=None, help="set client outband capability bit; default for family SPICE_OUTBAND route")
    p.add_argument("--no-outband", dest="outband", action="store_false", help="clear client outband capability bit for non-outband proxy type")
    p.add_argument("--report-file", default="", help="write full JSON KCP sync probe report")
    p.set_defaults(func=cmd_rap_zime_kcp_sync_probe)

    p = sub.add_parser("rap-zime-kcp-auth-from-cag")
    p.add_argument("user_service_id", nargs="?", help="target family cloud PC userServiceId; defaults to cached selected target")
    p.add_argument("--boot-wait", type=int, default=180, help="seconds to wait for CAG connectStr after boot/connect")
    p.add_argument("--cag-timeout", type=int, default=30, help="seconds for each CAG HTTPS request")
    p.add_argument("--timeout", type=float, default=1.0, help="seconds to wait for each UDP response")
    p.add_argument("--receive-limit", type=int, default=4, help="maximum UDP datagrams to inspect per stage")
    p.add_argument("--auth-head-attempts", type=int, default=3, help="AUTH_HEAD send attempts before declaring the gate missing; default follows fresh official trace")
    p.add_argument("--auth-head-retry-interval", type=float, default=0.08, help="seconds between AUTH_HEAD pump attempts; default follows fresh official trace")
    p.add_argument("--syn-id", type=_int_auto, default=None, help="client SYN id; defaults to a generated 32-bit value")
    p.add_argument("--conv", type=_int_auto, default=0, help="client KCP conv copied into auth/SYN una; defaults to 0")
    p.add_argument("--current", type=_int_auto, default=None, help="client current timestamp; defaults to monotonic milliseconds")
    p.add_argument("--mtu", type=int, default=1400, help="client-advertised MTU in SYN len")
    p.add_argument("--ssl", action="store_true", help="set client SYN SSL capability bit")
    p.add_argument("--no-detect-mtu", action="store_true", help="clear client SYN detect-MTU bit")
    p.add_argument("--no-pack-check", action="store_true", help="clear client pack-check capability bit")
    p.add_argument("--no-fec", action="store_true", help="clear client FEC capability bit")
    p.add_argument("--multi", action="store_true", help="set client multi-link capability bit")
    p.add_argument("--algo-mode", type=int, choices=[1, 2], default=1, help="1 clears GCC wnd bit; 2 sets it")
    p.add_argument("--no-stream", action="store_true", help="clear client stream capability bit")
    p.add_argument("--no-quic", action="store_true", help="clear client QUIC capability bit")
    p.add_argument("--outband", dest="outband", action="store_true", default=None, help="set client outband capability bit; default for family SPICE_OUTBAND route")
    p.add_argument("--no-outband", dest="outband", action="store_false", help="clear client outband capability bit for non-outband proxy type")
    p.add_argument("--auth-buffer-type", choices=["type101", "type102"], default="type101", help="fresh CAG auth buffer builder to use; default keeps the existing password-auth path")
    p.add_argument("--cag-auth-type", choices=["1", "2"], default="", help="type102 token branch hint: 1 uses uactoken, 2 uses accessToken")
    p.add_argument("--cag-material-file", default="", help="explicit JSON material with auth/connectInfo; use '-' for stdin and keep the file private")
    p.add_argument("--udp-target-source", choices=["connect-info", "firm-cag"], default="connect-info", help="select live UDP target source; default uses parsed connectInfo, firm-cag uses firm-auth cagIp/cagPort")
    p.add_argument("--link-type", type=int, default=rap_zime.ZTEC_CAG_TYPE101_LINK_TYPE_PROXY, help="CAG type101 link_type value; default is proxy path 11")
    p.add_argument(
        "--opentelemetry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="build the longer CAG auth head with opentelemetry trace/span placeholders; default follows fresh official auth-focus trace",
    )
    p.add_argument("--ztec-prime", action="store_true", help="send one ZTEC keepalive/ack probe on the same UDP socket before AUTH_HEAD")
    p.add_argument("--ztec-host", default="", help="IPv4 address encoded into the ZTEC prime request; defaults to CAG UDP target host")
    p.add_argument("--ztec-port", type=int, default=None, help="port encoded into the ZTEC prime request; defaults to CAG UDP target port")
    p.add_argument("--ztec-timeout", type=float, default=None, help="seconds to wait for ZTEC prime ack; defaults to --timeout")
    p.add_argument("--local-bind-host", default="", help="optional local UDP bind host for AUTH/SYNACK source-port experiments")
    p.add_argument("--local-bind-port", type=int, default=None, help="optional local UDP bind port for AUTH/SYNACK source-port experiments")
    p.add_argument("--pre-auth-receive-timeout", type=float, default=0.0, help="optional recvfrom window before AUTH_HEAD to model a pre-started UDP read loop")
    p.add_argument("--pre-auth-receive-limit", type=int, default=0, help="maximum datagrams to observe during the pre-AUTH receive window")
    p.add_argument("--pre-auth-bind-host", default="0.0.0.0", help="local host used for implicit UDP bind when pre-AUTH receive is enabled without --local-bind-host")
    p.add_argument("--pre-auth-tcp-listen-readiness", action="store_true", help="open a local 127.0.0.1 TCP listen fd before AUTH_HEAD to model ice_create_fd/udp_get_tcp_link_info readiness")
    p.add_argument("--pre-auth-cmd26-local-proxy", default="", help="optional local proxy host:port for fresh cmd26 send160/status1 bootstrap before AUTH_HEAD")
    p.add_argument("--pre-auth-cmd26-channel-type", type=int, default=1, help="fresh cmd26 channel_type candidate; default is SPICE_MAIN/1")
    p.add_argument("--pre-auth-cmd26-channel-id", type=int, default=0, help="fresh cmd26 channel_id candidate; default is 0")
    p.add_argument("--pre-auth-cmd26-trace-id", default="", help="optional non-secret OpenTelemetry trace id candidate for fresh cmd26")
    p.add_argument("--pre-auth-cmd26-parent-id", default="", help="optional non-secret OpenTelemetry parent/span id candidate for fresh cmd26")
    p.add_argument("--pre-auth-state-contract", action="store_true", help="mark the recovered pre-AUTH local proxy/session state contract as modeled for gate-only readiness reporting")
    p.add_argument("--auth-gate-preflight-only", action="store_true", help="build a redacted no-network audit for the AUTH gate-only live attempt, then stop")
    p.add_argument("--require-preflight-ready", action="store_true", help="with --auth-gate-preflight-only, return non-zero unless the no-network gate-only preflight is ready")
    p.add_argument("--require-live-gate-ready", action="store_true", help="before live gate-only traffic, run the same redacted readiness audit and fail non-zero if it is not ready")
    p.add_argument("--require-auth-gate-accepted", action="store_true", help="after live gate-only traffic, assess the redacted report and return non-zero unless ACK-like evidence is accepted")
    p.add_argument("--report-file", default="", help="write redacted JSON AUTH/SYNACK probe report")
    p.set_defaults(func=cmd_rap_zime_kcp_auth_from_cag)

    p = sub.add_parser("check-rap-zime-auth-gate-report")
    p.add_argument("report", help="redacted report from rap-zime-kcp-auth-from-cag gate-only run")
    p.add_argument("--require-accepted", action="store_true", help="return non-zero when the report does not prove the gate-only ACK-like path")
    p.add_argument("--report-file", default="", help="write redacted JSON gate acceptance assessment")
    p.set_defaults(func=cmd_check_rap_zime_auth_gate_report)

    p = sub.add_parser("zime-native-bridge")
    p.add_argument("--lib-path", default="", help="path to libZIMEDataEngine.so; defaults to installed Linux client path or CMCC_ZIME_LIB")
    p.add_argument("--payload-hex", action="append", default=[], help="SPICE/ZIME user payload to pass to native ZIME_SendData, hex encoded")
    p.add_argument("--display-init", action="store_true", help="append the local SPICE DISPLAY_INIT payload as one send probe")
    p.add_argument("--runner-input", default="", help="JSON report from analyze-rap-zime; auto-fills RAP UDP target and tunnel id")
    p.add_argument("--allow-native-run", action="store_true", help="actually call libZIMEDataEngine with fake external transport callbacks")
    p.add_argument("--inspect-only", action="store_true", help="inspect library exports and struct layout without native calls")
    p.add_argument("--remote-host", default="127.0.0.1", help="fake remote IPv4 address for the channel context")
    p.add_argument("--remote-port", type=int, default=0, help="fake remote UDP port for the channel context")
    p.add_argument("--local-host", default="0.0.0.0", help="fake local IPv4 address for the channel context")
    p.add_argument("--local-port", type=int, default=0, help="fake local UDP port for the channel context")
    p.add_argument("--opaque-hex", default="00000000", help="channel socket opaque bytes; observed traces commonly use 4 bytes")
    p.add_argument("--protocol", type=int, default=0, help="ZIME channel eDCProtocol candidate")
    p.add_argument("--mtu", type=int, default=zime_native_bridge.DEFAULT_BASE_MTU, help="ZIME channel base MTU candidate")
    p.add_argument("--business-type", type=int, default=1, help="ZIME channel business type candidate")
    p.add_argument("--stream-id", type=int, default=zime_native_bridge.DEFAULT_STREAM_ID, help="ZIME user stream id candidate; stream 0 is created internally during channel setup")
    p.add_argument("--process-ticks", type=int, default=zime_native_bridge.DEFAULT_PROCESS_TICKS, help="ZIME_DataChannelProcess2 calls after channel creation before stream creation")
    p.add_argument("--read-iov-payload", action="store_true", help="also dereference first iovec payload in native transport batch callbacks")
    p.add_argument("--udp-transport-target", default="", help="experimental UDP target host:port for native packet-out callbacks; disabled by default")
    p.add_argument("--udp-read-timeout", type=float, default=zime_native_bridge.DEFAULT_UDP_READ_TIMEOUT, help="seconds to wait for UDP responses after each native process tick")
    p.add_argument("--udp-receive-limit", type=int, default=zime_native_bridge.DEFAULT_UDP_RECEIVE_LIMIT, help="maximum UDP datagrams to read after each native process tick")
    p.add_argument("--udp-process-ticks-after-receive", type=int, default=zime_native_bridge.DEFAULT_UDP_PROCESS_TICKS_AFTER_RECEIVE, help="ZIME_DataChannelProcess2 calls after each ZIME_ReceiveData")
    p.add_argument("--udp-transport-mode", choices=["auto", "raw", "rap"], default="auto", help="send native packet-out as raw UDP payload or RAP data-frame payload; auto selects rap when --runner-input is used")
    p.add_argument("--udp-rap-tunnel-id", default="", help="4-byte RAP tunnel id hex when --udp-transport-mode=rap")
    p.add_argument("--udp-rap-flags", type=_int_auto, default=None, help="RAP data-frame flags; defaults to runner-input rapDataFrameTemplate or 0")
    p.add_argument("--udp-rap-field06", type=_int_auto, default=None, help="RAP data-frame field06 value; defaults to runner-input rapDataFrameTemplate or 0")
    p.add_argument("--udp-rap-word08", type=_int_auto, default=None, help="RAP data-frame word08 value; defaults to runner-input rapDataFrameTemplate or 0")
    p.add_argument("--udp-rap-word12", type=_int_auto, default=None, help="RAP data-frame word12 value; defaults to runner-input rapDataFrameTemplate or 0")
    p.add_argument("--udp-rap-header16-prefix-hex", default="", help="3-byte RAP header16 prefix hex; defaults to runner-input rapDataFrameTemplate or 000000")
    p.add_argument("--udp-rap-post-length-hex", default="", help="3-byte RAP post-length bytes hex; defaults to runner-input rapDataFrameTemplate or 000000")
    p.add_argument("--udp-rap-payload-envelope", choices=sorted(zime_native_bridge.RAP_PAYLOAD_ENVELOPES), default=zime_native_bridge.RAP_PAYLOAD_ENVELOPE_RAW, help="payload transform inside RAP data frames before/after native ZIME packets")
    p.add_argument("--udp-rap-template-mode", choices=sorted(zime_native_bridge.RAP_TEMPLATE_MODES), default=zime_native_bridge.RAP_TEMPLATE_MODE_AUTO, help="RAP 0x81 header template selection: static fields, runner-input sequence, or payload-kind matched runner-input templates")
    p.add_argument("--udp-packet-out-iov-mode", choices=sorted(zime_native_bridge.PACKET_OUT_IOV_MODES), default=zime_native_bridge.PACKET_OUT_IOV_MODE_CONCAT, help="send native packet-out iovecs as one concatenated datagram or separate UDP/RAP datagrams")
    p.add_argument("--udp-ztec-prime", action="store_true", help="send one ZTEC keepalive/ack probe on the native UDP socket before creating the ZIME channel")
    p.add_argument("--udp-ztec-host", default="", help="IPv4 address encoded into the ZTEC prime request; defaults to runner input ztec target or UDP target")
    p.add_argument("--udp-ztec-port", type=int, default=None, help="port encoded into the ZTEC prime request; defaults to runner input ztec target or UDP target")
    p.add_argument("--udp-ztec-timeout", type=float, default=None, help="seconds to wait for the ZTEC prime ack; defaults to --udp-read-timeout")
    p.add_argument("--wait-channel-created-ticks", type=int, default=zime_native_bridge.DEFAULT_WAIT_CHANNEL_CREATED_TICKS, help="extra ZIME_DataChannelProcess2/UDP drain ticks to wait for native_channel_created before stream creation; use 0 for legacy offline probing")
    p.add_argument("--report-file", default="", help="write full JSON bridge report")
    p.set_defaults(func=cmd_zime_native_bridge)

    p = sub.add_parser("trace-timeline")
    p.add_argument("jsonl", help="JSONL emitted by scripts/run-zime-probe.sh")
    p.add_argument("--limit", type=int, default=80, help="maximum key timeline entries to print")
    p.add_argument("--include-unknown", action="store_true", help="include unknown payloads in key timeline")
    p.add_argument("--report-file", default="", help="write full JSON timeline report")
    p.set_defaults(func=cmd_trace_timeline)

    p = sub.add_parser("http-session-replay")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--run-seconds", type=int, default=0, help="0 means run forever")
    p.add_argument("--heartbeat-interval", type=int, default=30, help="official connected HAR median: about 30s")
    p.add_argument("--info-interval", type=int, default=121, help="official connected HAR median: about 121s")
    p.add_argument("--log-config-interval", type=int, default=120, help="official connected HAR showed 120/180s")
    p.add_argument("--status-interval", type=int, default=60, help="poll cloud list/status this often; 0 disables")
    p.add_argument("--token-check-interval", type=int, default=0, help="0 disables active token-check during clean replay")
    p.add_argument("--relogin-on-token-expired", action="store_true", help="disabled by default to avoid polluting session tests")
    p.set_defaults(func=cmd_http_session_replay)

    p = sub.add_parser("http-session-verify")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--duration", type=int, default=2400, help="verification run length; default is 40 minutes")
    p.add_argument("--min-proof-seconds", type=int, default=2400, help="minimum duration required before declaring proven")
    p.add_argument("--heartbeat-interval", type=int, default=30, help="official connected HAR median: about 30s")
    p.add_argument("--info-interval", type=int, default=121, help="official connected HAR median: about 121s")
    p.add_argument("--log-config-interval", type=int, default=120, help="official connected HAR showed 120/180s")
    p.add_argument("--status-interval", type=int, default=60, help="poll cloud status this often; proof requires per-minute running snapshots")
    p.add_argument("--report-file", default="", help="write full JSON evidence report")
    p.add_argument("--allow-official-client-present", action="store_true", help="allow a contaminated control run; HTTP timer route remains rejected")
    p.add_argument("--no-stop-on-off", action="store_true", help="continue collecting evidence after the first off/not-running status")
    p.set_defaults(func=cmd_http_session_verify)

    p = sub.add_parser("run")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--strategy", choices=["auto", "http-timers", "cag-https", "spice"], default="auto", help="auto resolves to the SPICE/RAP/ZIME protocol target")
    p.add_argument("--allow-session-takeover", action="store_true", help="kept for old command compatibility; CAG keepalive is disabled")
    p.add_argument("--run-seconds", type=int, default=0, help="0 means run forever")
    p.add_argument("--cycle-interval", type=int, default=300, help="seconds between HTTP replay burst starts; 0 means continuous")
    p.add_argument("--cycle-duration", type=int, default=60, help="seconds to replay official HTTP timers per cycle")
    p.add_argument("--heartbeat-interval", type=int, default=30)
    p.add_argument("--info-interval", type=int, default=121)
    p.add_argument("--log-config-interval", type=int, default=120)
    p.add_argument("--status-interval", type=int, default=300)
    p.add_argument("--token-check-interval", type=int, default=300)
    p.add_argument("--account-relogin-hours", type=int, default=24)
    p.add_argument("--no-boot", action="store_true", help="do not use CAG HTTP boot when desktop is off")
    p.add_argument("--boot-wait", type=int, default=180)
    p.add_argument("--boot-timeout", type=int, default=15)
    p.add_argument("--cag-interval", type=int, default=60, help="CAG HTTPS fallback interval seconds")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("protocol-check")
    p.set_defaults(func=cmd_protocol_check)

    p = sub.add_parser("protocol-run")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--connect-str", default="", help="use an already obtained official connectStr instead of CAG fetch")
    p.add_argument("--run-seconds", type=int, default=2400, help="default is 40 minutes; 0 means run until interrupted")
    p.add_argument("--boot-wait", type=int, default=180)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--success-only", action="store_true", help="stop after the first display/surface proof")
    p.set_defaults(func=cmd_protocol_run)

    p = sub.add_parser("api-probe")
    p.add_argument("path", help="SOHO API path, for example /cc/cloudPc/heartbeat/v2 or /terminal/...")
    p.add_argument("--json", default=None, help="logical JSON body or @file; body is encrypted like the family client")
    p.add_argument("--timeout", type=int, default=30)
    p.set_defaults(func=cmd_api_probe)

    p = sub.add_parser("analyze-session-capture")
    p.add_argument("capture", nargs="+", help="Reqable HAR or plaintext JSONL captured after official desktop connection")
    p.add_argument("--baseline", action="append", default=[], help="optional pre-connect HAR/JSONL baseline for endpoint diff")
    p.add_argument("--source", action="append", default=[], help="optional unpacked source directory or app.asar for endpoint correlation")
    p.add_argument("--source-limit", type=int, default=12, help="max source hits per candidate")
    p.add_argument("--samples", action="store_true", help="include redacted request/response samples")
    p.add_argument("--include-all", action="store_true", help="include endpoints even when candidate score is not positive")
    p.add_argument("--report-file", default="", help="write full JSON analysis report")
    p.set_defaults(func=cmd_analyze_session_capture)

    p = sub.add_parser("source-audit")
    p.add_argument("--source", action="append", default=[], help="source directory or app.asar; defaults to installed family client app.asar")
    p.add_argument("--query", action="append", default=[], help="keyword to search")
    p.add_argument("--endpoint", action="append", default=[], help="endpoint/path to correlate")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--context", type=int, default=2)
    p.set_defaults(func=cmd_source_audit)

    p = sub.add_parser("product-keepalive")
    product_sub = p.add_subparsers(dest="product_mode")
    ip = product_sub.add_parser("interactive", help="interactive login, select cloud PC, and run keepalive loop")
    ip.add_argument("user_service_id", nargs="?")
    ip.add_argument("--username", default=None, help="account phone number; prompt if omitted")
    ip.add_argument("--password", default=None, help="password; prompt hidden if omitted (never logged)")
    ip.add_argument("--duration", type=int, default=0, help="run seconds; 0 means run forever")
    ip.add_argument("--heartbeat-interval", type=int, default=300, help="keepalive round interval seconds")
    ip.add_argument("--status-interval", type=int, default=60, help="status print interval seconds")
    ip.add_argument("--report-file", default=None, help="write JSON report to this path")
    ip.add_argument("--non-interactive", action="store_true", help="skip prompts; auto-select first target")
    ip.add_argument("--probe", action="store_true")
    ip.add_argument("--point", action="store_true")
    ip.add_argument("--connect-events", action="store_true")
    ip.add_argument("--no-firm-auth", action="store_true")
    ip.set_defaults(func=cmd_interactive)
    p.add_argument("--duration", type=int, default=None, help="hold each SCG Python keepalive round N seconds (default 60)")
    p.add_argument("--forever", action="store_true", help="repeat the SCG Python keepalive loop until interrupted")
    p.add_argument(
        "--scg-mode",
        choices=("spice", "tls_hold"),
        default="spice",
        help="SCG keepalive mode: spice (full trunk SPICE, default) or tls_hold (Auth+TLS hold + soho; no SPICE claim)",
    )
    p.add_argument("--user-service-id", default=None, help="override the selected user service id")
    p.add_argument("--vm-id", default=None, help="override the target desktop vmId")
    p.set_defaults(func=cmd_product_keepalive)

    p = sub.add_parser(
        "simple-keepalive",
        help="WebUI/non-interactive simple keepalive (ZTE/SCG forced)",
    )
    p.add_argument(
        "--user-service-id",
        default=None,
        help="target cloud PC userServiceId",
    )
    p.add_argument(
        "--protocol",
        default="ZTE",
        choices=["ZTE", "SCG", "zte", "scg"],
        help="hand-picked protocol",
    )
    p.add_argument(
        "--interval-minutes",
        type=int,
        default=5,
        help="keepalive interval minutes",
    )
    p.add_argument(
        "--traffic-seconds",
        type=int,
        default=60,
        help="single-round traffic seconds",
    )
    p.add_argument(
        "--mode",
        default="2",
        help="1=single round, 2=forever",
    )
    p.set_defaults(func=cmd_simple_keepalive)

    p = sub.add_parser("product-zte-material-check")
    p.add_argument("--state", default=None, help="override the state directory")
    p.add_argument("--user-service-id", default=None, help="override the selected user service id")
    p.add_argument("--vm-id", default=None, help="override the target desktop vmId")
    p.set_defaults(func=cmd_product_zte_material_check)

    p = sub.add_parser("product-zte-tcp-check")
    p.add_argument("--state", default=None, help="override the state directory")
    p.add_argument("--user-service-id", default=None, help="override the selected user service id")
    p.add_argument("--vm-id", default=None, help="override the target desktop vmId")
    p.set_defaults(func=cmd_product_zte_tcp_check)

    p = sub.add_parser("product-zte-display-check")
    p.add_argument("--state", default=None, help="override the state directory")
    p.add_argument("--user-service-id", default=None, help="override the selected user service id")
    p.add_argument("--vm-id", default=None, help="override the target desktop vmId")
    p.add_argument("--dial-timeout", type=float, default=30.0, help="CAG TCP/TLS dial timeout seconds")
    p.set_defaults(func=cmd_product_zte_display_check)

    p = sub.add_parser("state")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("legacy")
    p.add_argument("legacy_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=lambda args: raise_legacy(args))
    return parser



class SimpleInputCancelled(Exception):
    pass


def _format_disconnect_time_message(disconnect_time):
    if isinstance(disconnect_time, dict):
        data = disconnect_time.get("data")
        if isinstance(data, dict) and data.get("message"):
            return str(data.get("message"))
        if disconnect_time.get("message"):
            return str(disconnect_time.get("message"))
    return str(disconnect_time)


def _print_disconnect_time(disconnect_time):
    print(f"[官方自动关机时长]：{_format_disconnect_time_message(disconnect_time)}", flush=True)


def _simple_input(prompt, default=None, allow_cancel=True):
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        value = input(f"{prompt}{suffix}：").strip()
    except (EOFError, KeyboardInterrupt) as err:
        raise SimpleInputCancelled() from err
    if allow_cancel and value.lower() in ("exit", "quit", "q"):
        raise SimpleInputCancelled()
    if value == "" and default is not None:
        return str(default)
    return value


def _simple_choice(prompt, choices, default=None):
    """Prompt until the user enters one of choices; empty input uses default."""
    allowed = {str(choice) for choice in choices}
    while True:
        value = _simple_input(prompt, default=default)
        if value in allowed:
            return value
        print(f"输入无效，请输入：{'/'.join(sorted(allowed))}；输入 exit 返回主菜单。", flush=True)


def _simple_int(prompt, default=None, min_value=None, max_value=None):
    """Prompt until the user enters an integer in range; empty input uses default."""
    while True:
        value = _simple_input(prompt, default=default)
        try:
            number = int(value)
        except ValueError:
            print("输入格式错误，请输入数字。", flush=True)
            continue
        if min_value is not None and number < min_value:
            print(f"输入过小，请输入不小于 {min_value} 的数字。", flush=True)
            continue
        if max_value is not None and number > max_value:
            print(f"输入过大，请输入不大于 {max_value} 的数字。", flush=True)
            continue
        return number


def _safe_profile_name(value):
    text = str(value or "").strip()
    if not text:
        return ""
    safe = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_", "."):
            safe.append(ch)
        elif ch.isspace():
            safe.append("-")
    return "".join(safe).strip(".-_")[:60]


def _state_is_sub_account(state):
    """Whether a profile/state was last logged in as a sub-account."""
    if not isinstance(state, dict):
        return False
    if state.get("isSubAccount") is True:
        return True
    return str(state.get("loginMode") or "").strip().lower() == "sub_password"


def _state_label(path):
    try:
        state = core.load_state(str(path))
    except Exception:
        state = {}
    parts = []
    if state.get("username"):
        kind = "子账号" if _state_is_sub_account(state) else "主账号"
        parts.append(f"{state.get('username')}({kind})")
    desktop = state.get("desktopName") or state.get("selectedDesktopName") or state.get("cloudPcName")
    if desktop:
        parts.append(str(desktop))
    service_id = state.get("userServiceId") or state.get("selectedUserServiceId")
    if service_id:
        parts.append(str(service_id))
    return " / ".join(parts) if parts else Path(path).stem


def _profiles_dir():
    """User-local profiles dir (~/.cmcc-cloud-alive/profiles), never project tree."""
    root = Path(getattr(core, "DEFAULT_PROFILES_DIR", core.DEFAULT_DATA_DIR / "profiles"))
    return root


def _known_state_files(default_state=None):
    files = []
    seen = set()
    candidates = []
    if default_state:
        candidates.append(Path(default_state))
    candidates.append(core.state_path(None))
    # New default location
    candidates.extend(sorted(_profiles_dir().glob("*.json")))
    candidates.extend(sorted(core.DEFAULT_DATA_DIR.glob("*.json")))
    # Legacy project-local paths (read-only discovery; do not create)
    candidates.extend(sorted(Path(".runtime/profiles").glob("*.json")))
    candidates.extend(sorted(Path(".runtime").glob("*.json")))
    for path in candidates:
        path = Path(path)
        if not path.exists():
            continue
        # skip KPI / non-state json in data dir
        if path.name in ("scg_kpi.json",):
            continue
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        files.append(path)
    return files


def _next_profile_path():
    root = _profiles_dir()
    root.mkdir(parents=True, exist_ok=True)
    for i in range(1, 1000):
        path = root / f"desktop{i}.json"
        if not path.exists():
            return path
    raise core.CmccError(
        f"保活档案太多，请清理 {root} 后再试"
    )


def _choose_state_profile(args):
    """Friendly state/profile chooser for non-programmers.

    Users should not have to understand --state.  One keepalive task maps to
    one json profile automatically.
    """
    cli_state = getattr(args, "state", None)
    if cli_state:
        return cli_state, Path(cli_state).exists()
    files = _known_state_files()
    print("\n请选择保活档案：", flush=True)
    if files:
        for i, path in enumerate(files, 1):
            print(f"{i}. 继续使用：{_state_label(path)}  ({path})", flush=True)
        print(f"{len(files) + 1}. 新增一个账号/云桌面档案（自动创建独立json，可多开）", flush=True)
        idx = _simple_int("请选择", default="1", min_value=1, max_value=len(files) + 1)
        if 1 <= idx <= len(files):
            selected = str(files[idx - 1])
            print(f"当前使用档案：{selected}", flush=True)
            return selected, True
    else:
        print("1. 新增一个账号/云桌面档案（自动创建独立json，可多开）", flush=True)
        _simple_choice("按回车继续", choices=("1",), default="1")
    label = _simple_input("给这个保活档案起个名字（可直接回车）", default="")
    safe = _safe_profile_name(label)
    root = _profiles_dir()
    path = root / f"{safe}.json" if safe else _next_profile_path()
    if path.exists():
        base = _safe_profile_name(path.stem) or "desktop"
        for i in range(2, 1000):
            candidate = root / f"{base}-{i}.json"
            if not candidate.exists():
                path = candidate
                break
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"已创建/使用新档案：{path}", flush=True)
    print("提示：需要同时保活多台云电脑时，再开一个终端运行同一条命令，并选择/新增另一个档案即可。", flush=True)
    return str(path), False


def _desktop_display_name(item):
    for key in ("vmName", "name", "desktopName", "cloudPcName", "productName", "goodsName", "skuName"):
        value = item.get(key)
        if value:
            return str(value)
    return str(item.get("userServiceId") or item.get("vmId") or "未命名云桌面")


def _desktop_spu_code(item):
    for key in ("spuCode", "skuCode", "productCode", "skuId"):
        value = item.get(key)
        if value:
            return str(value)
    return "-"


def _print_desktop_list(items):
    print("\n可用云桌面列表：", flush=True)
    for idx, item in enumerate(items, 1):
        name = _desktop_display_name(item)
        ident = item.get("userServiceId") or item.get("vmId") or item.get("id") or "-"
        spu = _desktop_spu_code(item)
        print(f"[{idx}] {name} / {ident} | spuCode：{spu}", flush=True)


def _status_line(prefix, snap):
    return (f"{prefix} 状态：{snap.get('vmStatusShow') or snap.get('vmStatus')} "
            f"running={cloud.is_running(snap)}")


def _simple_ensure_token(state_path, context="接口调用"):
    """Ensure the selected profile has a valid token; auto re-login with saved password if expired.

    HTTP 5xx / network blips are *not* treated as token expiry: re-login against
    a dying gateway just cascades into a full process abort.  Return False so
    the long-run loop can skip/retry the round instead of exiting to cmcc>.
    """
    try:
        valid, response = token.check_token(state_path)
        if valid:
            return True
        if isinstance(response, dict) and response.get("transient"):
            msg = response.get("msg") or "gateway/network transient"
            print(
                f"[{core.short_time()}] {context}：接口瞬时失败（不判定token失效、不重登）：{msg}",
                flush=True,
            )
            return False
        code = response.get("code") if isinstance(response, dict) else None
        print(
            f"[{core.short_time()}] {context}：token已失效/不可用(code={code})，"
            f"使用档案内账号密码自动重新登录...",
            flush=True,
        )
        ok, refreshed = token.ensure_token(state_path, relogin=True)
        if not ok:
            # ensure_token itself may refuse re-login on a later transient blip
            msg = refreshed.get("msg") if isinstance(refreshed, dict) else refreshed
            print(f"[{core.short_time()}] {context}：token刷新未成功：{msg}", flush=True)
            return False
        print(f"[{core.short_time()}] {context}：token已自动刷新", flush=True)
        return True
    except Exception as err:
        # Never re-raise into the forever loop — log and let the caller decide.
        print(f"[{core.short_time()}] {context}：token检查/自动重登失败：{err}", flush=True)
        return False


def _simple_refresh_token_if_needed(state_path, context="账号token检测"):
    """Check token status and re-login only when the token is invalid."""
    try:
        valid, response = token.check_token(state_path)
        if valid:
            print(f"[{core.short_time()}] {context}：token有效", flush=True)
            return True
        if isinstance(response, dict) and response.get("transient"):
            msg = response.get("msg") or "gateway/network transient"
            print(
                f"[{core.short_time()}] {context}：接口瞬时失败（跳过重登）：{msg}",
                flush=True,
            )
            return False
        code = response.get("code") if isinstance(response, dict) else None
        print(
            f"[{core.short_time()}] {context}：token已失效/不可用(code={code})，"
            f"自动登录刷新token...",
            flush=True,
        )
        ok, refreshed = token.ensure_token(state_path, relogin=True)
        if not ok:
            msg = refreshed.get("msg") if isinstance(refreshed, dict) else refreshed
            print(f"[{core.short_time()}] {context}：token刷新未成功：{msg}", flush=True)
            return False
        print(f"[{core.short_time()}] {context}：token已自动刷新", flush=True)
        return True
    except Exception as err:
        print(f"[{core.short_time()}] {context}：token检测/自动刷新失败：{err}", flush=True)
        return False


def _is_auth_expired_error(err):
    """True when control-plane returned INVALID_TOKEN_CODES (e.g. 4015)."""
    resp = getattr(err, "response", None)
    if isinstance(resp, dict):
        try:
            code = int(resp.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        if code in token.INVALID_TOKEN_CODES:
            return True
    text = str(err or "")
    if "code=4015" in text or "用户未登录" in text:
        return True
    for c in token.INVALID_TOKEN_CODES:
        if f"code={c}" in text:
            return True
    return False


def _simple_status_tick(target, state_path):
    try:
        snap = cloud.status(target, state_path)
        state_text = "开机运行中" if cloud.is_running(snap) else "已关机"
        print(f"[{core.short_time()}] 云桌面状态：{state_text}", flush=True)
    except Exception as err:
        if _is_auth_expired_error(err):
            print(
                f"[{core.short_time()}] 每分钟状态检测鉴权失效（{err}），强制重登后重试一次",
                flush=True,
            )
            try:
                token.ensure_token(state_path, relogin=True, force=True)
                snap = cloud.status(target, state_path)
                state_text = "开机运行中" if cloud.is_running(snap) else "已关机"
                print(f"[{core.short_time()}] 云桌面状态：{state_text}", flush=True)
                return
            except Exception as retry_err:  # noqa: BLE001
                print(
                    f"[{core.short_time()}] 每分钟状态检测失败（重登后仍失败）：{retry_err}",
                    flush=True,
                )
                return
        print(f"[{core.short_time()}] 每分钟状态检测失败：{err}", flush=True)


def _simple_keepalive_args(target, state_path, traffic_seconds):
    """Build an argparse-compatible Namespace for an interactive keepalive round.

    The interactive runner passes a userServiceId string, while some internal
    callers may pass a desktop dict.  Keep conversion in one place so protocol
    branches never accidentally assume dict shape again.
    """
    if isinstance(target, dict):
        user_service_id = (
            target.get("userServiceId")
            or target.get("userServiceID")
            or target.get("id")
        )
        vm_id = target.get("vmId") or target.get("vmID") or target.get("uuid")
    else:
        user_service_id = str(target or "")
        vm_id = None
    return argparse.Namespace(
        state=state_path,
        user_service_id=user_service_id,
        vm_id=vm_id,
        duration=max(1, int(traffic_seconds)),
        forever=False,
        summary_only=True,
    )


def _simple_zte_keepalive_args(target, state_path, traffic_seconds):
    """Backward-compatible alias for the proven ZTE long-test argument builder."""
    return _simple_keepalive_args(target, state_path, traffic_seconds)


def _simple_forced_keepalive(target, state_path, protocol, traffic_seconds):
    """Run the protocol explicitly selected in the interactive menu.

    This intentionally does *not* call ``cmd_product_keepalive`` because that
    command auto-classifies firmAuth (scAuthCode wins over ZTE fields).  In the
    simple customer UI, selecting ZTE must force the ZTE long-test path and
    selecting SCG must force the pure-Python SCG path; if the selected protocol
    fields are absent, the selected branch should fail clearly instead of
    silently switching to the other protocol.
    """
    import time
    started = time.monotonic()
    args = _simple_keepalive_args(target, state_path, traffic_seconds)
    selected = cloud.selected_user_service_id(args.state, args.user_service_id)
    args.user_service_id = selected
    report = _product_keepalive_report()
    report["userServiceId"] = str(selected or "")
    report["kind"] = str(protocol or "").lower()
    report["reason"] = "interactive forced protocol=%s" % protocol
    ns_args = core.argparse.Namespace(state=args.state, user_service_id=selected)
    try:
        auth = core.get_firm_auth(ns_args)
    except Exception as exc:  # noqa: BLE001 - gate must report, not crash
        # checkToken can claim valid while getFirmAuth returns 4015; force re-login once.
        if _is_auth_expired_error(exc):
            print(
                f"[{core.short_time()}] {protocol} firmAuth 鉴权失效（{exc}），"
                f"强制重登后重试 firmAuth 一次",
                flush=True,
            )
            try:
                token.ensure_token(state_path, relogin=True, force=True)
                auth = core.get_firm_auth(ns_args)
                report["sessionRefresh"] = True
            except Exception as retry_exc:  # noqa: BLE001
                report["error"] = str(retry_exc)
                report["stage"] = "%s-firmAuth-failed" % str(protocol or "").lower()
                report["nextStep"] = (
                    "re-login / check account password; firmAuth still 4015 after force re-login"
                )
                report["duration"] = round(time.monotonic() - started, 3)
                report["sessionRefresh"] = False
                _emit_product_report(args, report)
                return report
        else:
            report["error"] = str(exc)
            report["stage"] = "%s-firmAuth-failed" % str(protocol or "").lower()
            report["nextStep"] = (
                "fix login/account/firmAuth; selected protocol was not changed automatically"
            )
            report["duration"] = round(time.monotonic() - started, 3)
            _emit_product_report(args, report)
            return report

    route = product_router.classify_firm_auth_route(auth)
    route.userServiceId = str(selected or "")
    vm_id = args.vm_id or route.vmId or str(auth.get("vmId") or auth.get("vmID") or auth.get("uuid") or "")
    report["vmId"] = vm_id
    report["firmAuthSummary"] = product_router.redacted_firm_auth_summary(auth)

    if str(protocol).upper() == "SCG":
        return _run_scg_keepalive(args, auth, route, vm_id, report, started)
    return _run_zte_keepalive(args, auth, route, vm_id, report, started)


def _simple_cloud_status_with_force_retry(target, state_path, context="状态检测"):
    """cloud.status with one force re-login retry on 4015/auth expiry.

    checkToken/login-ok can still be followed by listClouds 4015 (gateway lag
    or concurrent same-account re-login).  Minute-status already retries with
    force=True; first-boot must do the same or child exits rc=1 before the loop.
    """
    try:
        return cloud.status(target, state_path)
    except Exception as err:
        if not _is_auth_expired_error(err):
            raise
        print(
            f"[{core.short_time()}] {context}：鉴权失效（{err}），强制重登后重试一次",
            flush=True,
        )
        token.ensure_token(state_path, relogin=True, force=True)
        return cloud.status(target, state_path)


def _simple_run_keepalive(target, state_path, protocol, interval_minutes, traffic_seconds, mode):
    interval_seconds = max(1, int(interval_minutes) * 60)
    traffic_seconds = max(1, int(traffic_seconds))
    _simple_ensure_token(state_path, "首次开机检查前")
    print("\n[首次开机检查] 正在检测云电脑状态……", flush=True)
    try:
        pre_snap = _simple_cloud_status_with_force_retry(
            target, state_path, context="首次开机检查"
        )
    except Exception as err:
        # Auth already force-retried once above; other errors still abort first-boot.
        print(f"[首次开机检查] 首次状态检测失败，任务终止，不进入保活：{err}", flush=True)
        return
    pre_snap_text = pre_snap.get("vmStatusShow") or pre_snap.get("vmStatus")
    print(f"[首次开机检查] 当前状态：{pre_snap_text} running={cloud.is_running(pre_snap)}", flush=True)
    if cloud.is_running(pre_snap):
        print("[首次开机检查] 云电脑已运行，跳过开机，马上进入第一轮保活。", flush=True)
    else:
        if str(protocol).upper() == "SCG":
            print("[首次开机检查] 云电脑未运行，当前选择SCG协议，将由getConnectInfo自动触发开机（无需二次确认）。", flush=True)
        else:
            print("[首次开机检查] 云电脑未运行，自动开机（只执行这一次，无需二次确认）……", flush=True)
            try:
                cag_boot.ensure_running(target, state_path, boot_wait=180, timeout=30, refresh_wait=5)
            except Exception as boot_err:
                if _is_auth_expired_error(boot_err):
                    print(
                        f"[首次开机检查] 开机鉴权失效（{boot_err}），强制重登后重试开机一次",
                        flush=True,
                    )
                    try:
                        token.ensure_token(state_path, relogin=True, force=True)
                        cag_boot.ensure_running(
                            target, state_path, boot_wait=180, timeout=30, refresh_wait=5
                        )
                    except Exception as boot_retry_err:  # noqa: BLE001
                        print(
                            f"[首次开机检查] 首次状态检测/开机失败，任务终止，不进入保活：{boot_retry_err}",
                            flush=True,
                        )
                        return
                else:
                    print(
                        f"[首次开机检查] 首次状态检测/开机失败，任务终止，不进入保活：{boot_err}",
                        flush=True,
                    )
                    return
            try:
                post_snap = _simple_cloud_status_with_force_retry(
                    target, state_path, context="首次开机后状态"
                )
            except Exception as post_err:
                print(
                    f"[首次开机检查] 首次状态检测/开机失败，任务终止，不进入保活：{post_err}",
                    flush=True,
                )
                return
            if cloud.is_running(post_snap):
                print("[首次开机检查] 开机流程完成，马上进入第一轮保活。", flush=True)
            else:
                print("[首次开机检查] 首次状态检测/开机失败，任务终止，不进入保活", flush=True)
                return
    try:
        disc = desktop_keepalive.disconnect_time(target, state_path)
        _print_disconnect_time(disc)
    except Exception as err:
        if _is_auth_expired_error(err):
            try:
                print(
                    f"[{core.short_time()}] 官方自动关机时长鉴权失效（{err}），强制重登后重试一次",
                    flush=True,
                )
                token.ensure_token(state_path, relogin=True, force=True)
                disc = desktop_keepalive.disconnect_time(target, state_path)
                _print_disconnect_time(disc)
            except Exception as retry_err:  # noqa: BLE001
                print(f"[官方自动关机时长]获取失败：{retry_err}", flush=True)
        else:
            print(f"[官方自动关机时长]获取失败：{err}", flush=True)

    print("\n开始保活：", flush=True)
    print("- 后续每轮保活前不再检测开机、不再触发开机", flush=True)
    print("- 每分钟状态检测只打印展示，不联动任何开机操作", flush=True)
    print("- 接口瞬时失败(5xx/网络)不退出，自动跳过本轮并重试", flush=True)
    print("- Ctrl+C 可退出\n", flush=True)
    round_no = 0
    last_status = 0.0
    # Backoff after gateway blips so we don't hammer a dying openresty.
    transient_backoff_seconds = 30
    try:
        while True:
            round_no += 1
            try:
                token_ok = _simple_ensure_token(state_path, f"第{round_no}轮保活前")
                if not token_ok:
                    # Transient 5xx or re-login failure: skip this round, wait, continue.
                    print(
                        f"[{core.short_time()}] 第{round_no}轮保活跳过："
                        f"token检查未通过，{transient_backoff_seconds}s后重试",
                        flush=True,
                    )
                    time.sleep(transient_backoff_seconds)
                    continue
                # Start round timing only after all pre-flight checks are done.
                # Token refresh/re-login time must not consume the user-configured
                # keepalive traffic duration or the configured round interval.
                started = time.time()
                print(
                    f"[{core.short_time()}] 第{round_no}轮保活开始 "
                    f"protocol={protocol} duration={traffic_seconds}s",
                    flush=True,
                )
                if protocol == "SCG":
                    print(
                        f"[{core.short_time()}] 第{round_no}轮SCG保活：手选SCG，调用纯Python SCG协议 "
                        f"duration={traffic_seconds}s userServiceId={target}",
                        flush=True,
                    )
                    product_report = _simple_forced_keepalive(
                        target, state_path, "SCG", traffic_seconds
                    ) or {}
                    print(
                        f"[{core.short_time()}] 第{round_no}轮SCG保活完成 "
                        f"kind={product_report.get('kind')} ok={product_report.get('ok')} "
                        f"stage={product_report.get('stage')} "
                        f"duration={product_report.get('duration')}s",
                        flush=True,
                    )
                else:
                    # ZTE must use the same keepalive path that passed the long-test:
                    # _run_zte_keepalive -> CAG/mux/raw-SPICE session.  Do NOT call
                    # cmd_product_keepalive here: that command auto-classifies firmAuth
                    # and would switch to SCG when scAuthCode is present, violating the
                    # customer's explicit menu choice.
                    print(
                        f"[{core.short_time()}] 第{round_no}轮ZTE保活：手选ZTE，调用长测同款CAG/mux/raw-SPICE "
                        f"duration={traffic_seconds}s userServiceId={target}",
                        flush=True,
                    )
                    product_report = _simple_forced_keepalive(
                        target, state_path, "ZTE", traffic_seconds
                    ) or {}
                    print(
                        f"[{core.short_time()}] 第{round_no}轮ZTE保活完成 "
                        f"kind={product_report.get('kind')} ok={product_report.get('ok')} "
                        f"stage={product_report.get('stage')} "
                        f"duration={product_report.get('duration')}s",
                        flush=True,
                    )
                _simple_status_tick(target, state_path)
                if str(mode) == "1":
                    print(f"[{core.short_time()}] 保活结束", flush=True)
                    print("单次保活任务已完成", flush=True)
                    return
                while time.time() - started < interval_seconds:
                    remain = interval_seconds - (time.time() - started)
                    time.sleep(min(60, max(1, int(remain))))
                    if time.time() - last_status >= 60:
                        _simple_status_tick(target, state_path)
                        last_status = time.time()
            except KeyboardInterrupt:
                raise
            except Exception as err:
                # Never let a single-round failure (firmAuth 502, mux error, etc.)
                # abort the forever long-test loop back to the cmcc> prompt.
                print(
                    f"[{core.short_time()}] 第{round_no}轮保活异常（将继续下一轮）：{err}",
                    flush=True,
                )
                time.sleep(transient_backoff_seconds)
    except KeyboardInterrupt:
        print("\n收到中断，已退出保活。", flush=True)
        print("如需释放桌面会话锁，请输入 logout", flush=True)


def _profile_label(state_path):
    """Short human label for a profile path (basename without .json)."""
    name = Path(str(state_path or "")).name
    if name.endswith(".json"):
        name = name[:-5]
    return name or str(state_path or "-")


def _simple_desktop_logout(state_path, user_service_id=None):
    """Release SOHO desktop session lock via /cc/cloudPc/logout/v2 for one profile."""
    profile = _profile_label(state_path)
    if not _simple_ensure_token(state_path, "桌面登出"):
        raise core.CmccError("token invalid; desktop logout cancelled")
    target = None
    if user_service_id:
        target = str(user_service_id)
    else:
        state = core.load_state(state_path)
        cached = state.get("selectedUserServiceId")
        if cached:
            target = str(cached)
        else:
            target = cloud.selected_user_service_id(state_path, None)
    if not target:
        raise core.CmccError("no selectedUserServiceId; run login and select a desktop first")
    print(f"[桌面登出] 档案={profile}  桌面={target}  请求中...", flush=True)
    response = logout.desktop_logout(target, state_path)
    code = None
    msg = ""
    if isinstance(response, dict):
        code = response.get("code")
        msg = str(response.get("msg") or response.get("message") or "").strip()
    ok = str(code) in ("0", "2000") or code in (0, 2000)
    status = "成功" if ok else "失败"
    detail = f"code={code}"
    if msg:
        detail = f"{detail}  msg={msg}"
    print(f"[桌面登出] 档案={profile}  桌面={target}  结果={status}（{detail}）", flush=True)
    if not ok and response is not None:
        # Keep raw body only on failure for diagnosis.
        _print(response)
    return response


def cmd_simple_repl(args):
    print("移动云电脑保活工具", flush=True)
    print(
        "请输入命令：login 登录并开始保活；logout 桌面登出；help 查看帮助；exit 退出。",
        flush=True,
    )
    last_state = getattr(args, "state", None)
    while True:
        try:
            cmd = input("cmcc> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。", flush=True)
            return
        if cmd == "":
            continue
        if cmd in ("exit", "quit", "q"):
            print("已退出。", flush=True)
            return
        if cmd in ("help", "?"):
            print(
                "可用命令：login / logout(desktop-logout) / help / exit",
                flush=True,
            )
            print(
                "说明：logout 调用 /cc/cloudPc/logout/v2 释放桌面会话锁；"
                "q/exit/quit 仅退出本地程序，不登出桌面。",
                flush=True,
            )
            continue
        if cmd in ("logout", "desktop-logout"):
            try:
                if last_state:
                    active_state = last_state
                else:
                    active_state, _existing = _choose_state_profile(args)
                    last_state = active_state
                _simple_desktop_logout(active_state)
            except SimpleInputCancelled:
                print("已返回主菜单。", flush=True)
            except core.CmccError as err:
                print(f"[桌面登出] 失败：{err}", flush=True)
                if getattr(err, "response", None) is not None:
                    _print(err.response)
            except ValueError as err:
                print(f"输入格式错误：{err}", flush=True)
            continue
        if cmd != "login":
            print(
                "未知命令。请输入 login、logout、help 或 exit。",
                flush=True,
            )
            continue
        try:
            active_state, existing_profile = _choose_state_profile(args)
            last_state = active_state
            state = core.load_state(active_state)
            cached_username = state.get("username") or ""
            # Existing profile already knows main/sub; only new profiles ask.
            if existing_profile and cached_username:
                is_sub_login = _state_is_sub_account(state)
                account_label = "子账号" if is_sub_login else "主账号"
                username = cached_username
                print(f"沿用档案{account_label}：{username}", flush=True)
            else:
                print("\n请选择登录方式：", flush=True)
                print("1. 主账号登录", flush=True)
                print("2. 子账号登录", flush=True)
                login_mode_pick = _simple_choice("登录方式", choices=("1", "2"), default="1")
                is_sub_login = login_mode_pick == "2"
                account_label = "子账号" if is_sub_login else "主账号"
                if cached_username and (_state_is_sub_account(state) == is_sub_login):
                    username = _choose_username_with_cached(cached_username, _simple_input)
                else:
                    username = _simple_input(account_label)
            if not username:
                print(f"{account_label}不能为空。", flush=True)
                continue
            same_login_mode = (
                bool(cached_username)
                and username == cached_username
                and (_state_is_sub_account(state) == is_sub_login)
            )
            cached_password = state.get("password") if same_login_mode else ""
            login_required = True
            password = ""
            if existing_profile and same_login_mode:
                valid_token, token_response = token.check_token(active_state)
                if valid_token:
                    login_required = False
                    print("档案内token仍有效，跳过重新登录。", flush=True)
                elif cached_password:
                    password = cached_password
                    code = token_response.get("code") if isinstance(token_response, dict) else ""
                    print(f"档案内token已失效/不可用(code={code})，使用缓存密码重新登录。", flush=True)
                else:
                    password = _simple_input("请输入密码")
            elif cached_password:
                print(f"检测到该{account_label}已缓存密码，回车可直接使用缓存密码。", flush=True)
                password = _simple_input("请输入密码", default="使用缓存密码")
                if password == "使用缓存密码":
                    password = cached_password
            else:
                password = _simple_input("请输入密码")
            if login_required:
                if not password:
                    print("密码不能为空。", flush=True)
                    continue
                print(f"正在以{account_label}登录...", flush=True)
                if is_sub_login:
                    auth.sub_password_login(username, password, active_state, save_password=True)
                else:
                    auth.password_login(username, password, active_state, save_password=True)
                print("登录成功。", flush=True)
            items = cloud.list_desktops(active_state)
            if not items:
                print("没有获取到云桌面。", flush=True)
                continue
            _print_desktop_list(items)
            index = _simple_int("请选择云桌面序号", default="1", min_value=1, max_value=len(items))
            selected = items[index - 1]
            target = str(selected.get("userServiceId") or "")
            if not target:
                print("所选云桌面缺少 userServiceId，无法继续。", flush=True)
                continue
            cloud.select_desktop(target, active_state, skip_target_assert=True)
            print(f"已选择：{_desktop_display_name(selected)} / {target} | spuCode：{_desktop_spu_code(selected)}", flush=True)
            print("\n请选择保活协议：", flush=True)
            print("1. ZTE", flush=True)
            print("2. SCG", flush=True)
            proto_pick = _simple_choice("协议", choices=("1", "2"), default="1")
            protocol = "SCG" if proto_pick == "2" else "ZTE"
            try:
                disc = desktop_keepalive.disconnect_time(target, active_state)
                _print_disconnect_time(disc)
            except Exception as err:
                print(f"[官方自动关机时长]获取失败：{err}", flush=True)
            interval_minutes = _simple_int("请输入保活间隔分钟", default="5", min_value=1)
            traffic_seconds = _simple_int("请输入单次流量持续秒", default="60", min_value=1)
            print("\n请选择模式：", flush=True)
            print("1. 单轮", flush=True)
            print("2. 永久", flush=True)
            mode = _simple_choice("模式", choices=("1", "2"), default="1")
            if mode not in ("1", "2"):
                mode = "1"
            _simple_run_keepalive(target, active_state, protocol, interval_minutes, traffic_seconds, mode)
        except SimpleInputCancelled:
            print("已返回主菜单。", flush=True)
        except core.CmccError as err:
            print(f"错误：{err}", flush=True)
            if err.response is not None:
                _print(err.response)
        except ValueError as err:
            print(f"输入格式错误：{err}", flush=True)

def raise_legacy(args):
    raise core.CmccError("use bin/cmcc_cloud_alive.py for legacy analyze/source-audit commands during migration")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        cmd_simple_repl(args)
        return 0
    if args.cmd == "logout" and not args.desktop and not args.account:
        args.desktop = True
        args.account = True
    try:
        args.func(args)
    except core.CmccError as err:
        print(f"Error：{err}")
        if err.response is not None:
            _print(err.response)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
