"""Desktop-session HTTP keepalive aligned to the official connect callback."""

import json
import os
import time
import uuid
from pathlib import Path

from . import cloud, core, device_info, point, power_monitor, probe, token


LOCK_SCREEN_CODES = {4039, 4040, 4041, 4042}
OTHER_LOGIN_CODE = 4043
OFFICIAL_PROCESS_NAMES = {"cmcc-jtydn", "bootCypc", "uSmartView_VDI_Client"}


def _summary_response(response):
    return {
        "code": response.get("code"),
        "msg": response.get("msg"),
        "businessCode": response.get("businessCode") or response.get("returnCode") or "",
    }


def _disconnect_time_message(response):
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict) and data.get("message"):
            return data.get("message")
        if response.get("message"):
            return response.get("message")
    return str(response)


def _desktop_context(user_service_id, state_path=None, use_firm_auth=True):
    args = core.argparse.Namespace(state=state_path, user_service_id=str(user_service_id))
    item = cloud.status(user_service_id, state_path)
    auth = {}
    if use_firm_auth:
        auth = core.get_firm_auth(args)
    else:
        state = core.load_state(args)
        auth = {
            "vmId": state.get("lastVmId") or item.get("vmId") or item.get("vmUuid") or "",
            "spuCode": state.get("lastSpuCode") or item.get("spuCode") or "",
        }
    vm_id = auth.get("vmId") or auth.get("vmID") or auth.get("uuid") or item.get("vmId") or item.get("vmUuid")
    return item, auth, str(vm_id or "")


def connect_point_events(user_service_id, item, vm_id, trace_id, state_path=None):
    sku_name = item.get("skuName") or item.get("vmName") or ""
    sku_id = item.get("skuId") or ""
    spu_code = item.get("spuCode") or ""
    type_value = "2" if "sc-" in spu_code else "1"
    common = {
        "userServiceId": str(user_service_id),
        "type": type_value,
        "traceId": trace_id,
        "skuName": sku_name,
        "skuId": sku_id,
        "vmId": vm_id,
        "time": 0,
        "firstConnect": "0",
        "uuid": vm_id,
    }
    events = [
        ("Page_Term_SuccessLink", {
            "vmId": vm_id,
            "skuId": sku_id,
            "skuName": sku_name,
            "traceId": trace_id,
        }),
        ("Api_connect_success", common),
        ("Api_sdk_connect_success", common),
        ("Api_sdk_render_success", common),
    ]
    results = []
    for event_name, data in events:
        try:
            response = point.point_event(event_name, data, state_path)
            results.append({"eventName": event_name, "response": _summary_response(response)})
        except Exception as err:
            results.append({"eventName": event_name, "error": str(err)})
    return results


def heartbeat(user_service_id, state_path=None):
    args = core.argparse.Namespace(state=state_path)
    response = core.api_request("/cc/cloudPc/heartbeat/v2", {"userServiceId": str(user_service_id)}, args)
    code = int(response.get("code") or 0)
    if code == OTHER_LOGIN_CODE:
        raise core.CmccError("desktop heartbeat says cloud PC is logged in elsewhere/recycled", response=response)
    return response


def info_report(state_path=None):
    args = core.argparse.Namespace(state=state_path)
    return core.api_request("/cc/cloudPc/infoReport/v2", device_info.collect_device_info(), args)


def disconnect_time(user_service_id, state_path=None):
    args = core.argparse.Namespace(state=state_path)
    return core.api_request("/cc/cloudPc/getDisconnectTime/v1", {"userServiceId": str(user_service_id)}, args)


def log_report_config(state_path=None):
    args = core.argparse.Namespace(state=state_path)
    return core.api_request("/system/logReport/config/v2", None, args)


def once(user_service_id=None, state_path=None, send_probe=False, send_point=False, send_disconnect_time=False, send_connect_events=False, use_firm_auth=True):
    target = cloud.selected_user_service_id(state_path, user_service_id)
    item, auth, vm_id = _desktop_context(target, state_path, use_firm_auth=use_firm_auth)
    trace_id = str(uuid.uuid4()).replace("-", "").upper()
    spu_code = item.get("spuCode") or auth.get("spuCode") or ""
    sku_name = item.get("skuName") or item.get("vmName") or ""

    connect_event_responses = None
    if send_connect_events:
        connect_event_responses = connect_point_events(target, item, vm_id, trace_id, state_path)

    point_response = None
    if send_point:
        try:
            point_response = point.point_event("Api_Term_HeartBeat", {
                "vmId": vm_id,
                "skuName": sku_name,
            }, state_path)
        except Exception as err:
            point_response = {"error": str(err)}

    heartbeat_response = heartbeat(target, state_path)
    disconnect_time_response = None
    if send_disconnect_time:
        try:
            disconnect_time_response = disconnect_time(target, state_path)
        except Exception as err:
            disconnect_time_response = {"error": str(err)}
    info_response = info_report(state_path)

    probe_response = None
    if send_probe and vm_id:
        try:
            probe_response = probe.send_performance(vm_id, trace_id, spu_code, state_path)
        except Exception as err:
            probe_response = {"error": str(err)}

    status_running = cloud.is_running(item)
    candidate_accepted = (
        status_running
        and int(heartbeat_response.get("code") or 0) in ({2000} | LOCK_SCREEN_CODES)
        and int(info_response.get("code") or 0) == 2000
    )
    result = {
        "accepted": False,
        "candidateAccepted": candidate_accepted,
        "desktopKeepaliveProven": False,
        "unsafeReason": (
            "Observed family-client HTTP heartbeat/infoReport/point routes are accepted by "
            "the server, but 2026-07-01 long-test evidence shows they do not keep the "
            "desktop powered and can kick an active official client session."
        ),
        "userServiceId": str(target),
        "vmId": vm_id,
        "spuCode": spu_code,
        "status": {
            "vmStatus": item.get("vmStatus"),
            "vmStatusShow": item.get("vmStatusShow"),
            "running": status_running,
        },
        "heartbeat": _summary_response(heartbeat_response),
        "disconnectTime": f"[官方自动关机时长]:{_disconnect_time_message(disconnect_time_response)}" if isinstance(disconnect_time_response, dict) and "error" not in disconnect_time_response else disconnect_time_response,
        "infoReport": _summary_response(info_response),
        "connectEvents": connect_event_responses,
        "point": _summary_response(point_response) if isinstance(point_response, dict) and "error" not in point_response else point_response,
        "probePerformance": probe_response,
        "at": core.shanghai_now().isoformat(),
    }
    args = core.argparse.Namespace(state=state_path)
    core.merge_state({
        "lastDesktopKeepaliveAt": result["at"],
        "lastDesktopKeepalive": result,
        "selectedUserServiceId": str(target),
    }, args)
    return result


def run_loop(user_service_id=None, state_path=None, interval=300, run_seconds=0, account_relogin_hours=24, send_probe=False, send_point=False, send_disconnect_time=False, send_connect_events=False, use_firm_auth=True):
    target = cloud.selected_user_service_id(state_path, user_service_id)
    started = time.time()
    last_account_refresh = time.time()
    count = 0
    while True:
        token.ensure_token(state_path, relogin=True)
        now = time.time()
        if account_relogin_hours and now - last_account_refresh >= account_relogin_hours * 3600:
            try:
                from . import auth
                auth.login_from_cached_credentials(state_path)
                last_account_refresh = now
            except Exception:
                last_account_refresh = now
        count += 1
        result = once(target, state_path, send_probe=send_probe, send_point=send_point, send_disconnect_time=send_disconnect_time, send_connect_events=send_connect_events, use_firm_auth=use_firm_auth)
        elapsed = int(time.time() - started)
        status = "候选接口已响应" if result["candidateAccepted"] else "候选接口失败"
        disconnect_code = (result.get("disconnectTime") or {}).get("code") if isinstance(result.get("disconnectTime"), dict) else "-"
        print(f"[{core.short_time()}] [{count}] {status}: {core.format_duration(elapsed)} heartbeat={result['heartbeat']['code']} disconnect={disconnect_code} info={result['infoReport']['code']}", flush=True)
        if not result["accepted"]:
            raise core.CmccError("desktop HTTP keepalive route is unproven and unsafe for loops", response=result)
        if run_seconds and time.time() - started >= run_seconds:
            return result
        time.sleep(max(1, int(interval)))


def official_http_once(user_service_id=None, state_path=None, include_status=False, do_heartbeat=True, do_info=True, do_log_config=True):
    """Replay only the visible HTTP timers from official connected-client HARs.

    This intentionally avoids login, getFirmAuth, CAG boot, connect events, and
    point analytics. It is an experiment harness, not a success claim.
    """
    target = cloud.selected_user_service_id(state_path, user_service_id)
    result = {
        "accepted": False,
        "desktopKeepaliveProven": False,
        "experimental": True,
        "userServiceId": str(target),
        "heartbeat": None,
        "infoReport": None,
        "logReportConfig": None,
        "status": None,
        "at": core.shanghai_now().isoformat(),
    }
    if do_heartbeat:
        result["heartbeat"] = _summary_response(heartbeat(target, state_path))
    if do_info:
        result["infoReport"] = _summary_response(info_report(state_path))
    if do_log_config:
        result["logReportConfig"] = _summary_response(log_report_config(state_path))
    if include_status:
        item = cloud.status(target, state_path)
        result["status"] = {
            "vmStatus": item.get("vmStatus"),
            "vmStatusShow": item.get("vmStatusShow"),
            "running": cloud.is_running(item),
        }
    args = core.argparse.Namespace(state=state_path)
    core.merge_state({
        "lastOfficialHttpReplayAt": result["at"],
        "lastOfficialHttpReplay": result,
        "selectedUserServiceId": str(target),
    }, args)
    return result


def _code(result, key):
    value = result.get(key)
    if isinstance(value, dict):
        return value.get("code")
    return "-"


def _status_text(result):
    status = result.get("status") if isinstance(result.get("status"), dict) else None
    if not status:
        return "-"
    return status.get("vmStatusShow") or status.get("vmStatus") or "-"


def _status_snapshot(user_service_id, state_path=None):
    return power_monitor.snapshot(user_service_id, state_path)


def official_client_processes():
    processes = []
    proc = Path("/proc")
    if not proc.exists():
        return processes
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        name = official_process_name(entry, cmdline)
        if name in OFFICIAL_PROCESS_NAMES:
            processes.append({"pid": int(entry.name), "cmdline": cmdline, "processName": name})
    return processes


def official_process_name(entry, cmdline):
    try:
        exe_name = (entry / "exe").resolve(strict=True).name
        if exe_name:
            return exe_name
    except OSError:
        pass
    argv0 = str(cmdline or "").split(" ", 1)[0]
    return Path(argv0).name


def _official_process_snapshot(stage, started):
    return {
        "stage": stage,
        "elapsedSeconds": int(time.time() - started),
        "at": core.shanghai_now().isoformat(),
        "processes": official_client_processes(),
    }


def run_official_http_loop(
    user_service_id=None,
    state_path=None,
    run_seconds=0,
    heartbeat_interval=30,
    info_interval=121,
    log_config_interval=120,
    status_interval=300,
    token_check_interval=0,
    relogin_on_token_expired=False,
):
    target = cloud.selected_user_service_id(state_path, user_service_id)
    started = time.time()
    next_heartbeat = started
    next_info = started
    next_log = started
    next_status = started if status_interval else float("inf")
    next_token = started if token_check_interval else float("inf")
    count = 0
    last = {
        "accepted": False,
        "desktopKeepaliveProven": False,
        "experimental": True,
        "userServiceId": str(target),
    }
    while True:
        now = time.time()
        if run_seconds and now - started >= run_seconds:
            return last

        do_token = now >= next_token
        if do_token:
            valid, response = token.ensure_token(state_path, relogin=relogin_on_token_expired)
            next_token = now + max(1, int(token_check_interval))
            if not valid and not relogin_on_token_expired:
                raise core.CmccError("token expired during HTTP replay", response=response)

        do_heartbeat = now >= next_heartbeat
        do_info = now >= next_info
        do_log = now >= next_log
        do_status = now >= next_status
        if do_heartbeat or do_info or do_log or do_status:
            count += 1
            last = official_http_once(
                target,
                state_path=state_path,
                include_status=do_status,
                do_heartbeat=do_heartbeat,
                do_info=do_info,
                do_log_config=do_log,
            )
            if do_heartbeat:
                next_heartbeat = now + max(1, int(heartbeat_interval))
            if do_info:
                next_info = now + max(1, int(info_interval))
            if do_log:
                next_log = now + max(1, int(log_config_interval))
            if do_status:
                next_status = now + max(1, int(status_interval))
            elapsed = int(time.time() - started)
            print(
                f"[{core.short_time()}] [{count}] HTTP候选计时器: {core.format_duration(elapsed)} "
                f"heartbeat={_code(last, 'heartbeat')} info={_code(last, 'infoReport')} "
                f"logConfig={_code(last, 'logReportConfig')} status={_status_text(last)}",
                flush=True,
            )

        sleep_until = min(next_heartbeat, next_info, next_log, next_status, next_token)
        time.sleep(max(1, min(5, int(sleep_until - time.time()) if sleep_until > time.time() else 1)))


def run_official_http_verify(
    user_service_id=None,
    state_path=None,
    duration=2400,
    heartbeat_interval=30,
    info_interval=121,
    log_config_interval=120,
    status_interval=60,
    min_proof_seconds=2400,
    report_file=None,
    allow_official_client_present=False,
    stop_on_off=True,
):
    target = cloud.selected_user_service_id(state_path, user_service_id)
    started = time.time()
    before_processes = official_client_processes()
    report = {
        "ok": False,
        "accepted": False,
        "desktopKeepaliveProven": False,
        "routeRejected": True,
        "rejectedReason": (
            "Pure SOHO HTTP visible timers are rejected as desktop keepalive. "
            "Long tests showed accepted responses while the VM still powered off."
        ),
        "experimental": True,
        "aborted": False,
        "abortReason": "",
        "userServiceId": str(target),
        "requestedDurationSeconds": int(duration),
        "minProofSeconds": int(min_proof_seconds),
        "processProofPolicy": {
            "allowOfficialClientPresent": bool(allow_official_client_present),
            "note": (
                "Official client/SDK processes may keep the desktop alive through native transport. "
                "A run with such processes present cannot prove pure Python HTTP desktop keepalive."
            ),
        },
        "statusVerification": {
            "intervalSeconds": int(status_interval),
            "stopOnOff": bool(stop_on_off),
            "note": (
                "Power state is verified through the cloud status API. Accepted HTTP timer "
                "responses alone are never treated as desktop keepalive success."
            ),
        },
        "intervals": {
            "heartbeat": int(heartbeat_interval),
            "infoReport": int(info_interval),
            "logReportConfig": int(log_config_interval),
            "status": int(status_interval),
        },
        "startedAt": core.shanghai_now().isoformat(),
        "endedAt": None,
        "durationSeconds": 0,
        "events": [],
        "statusBefore": None,
        "statusAfter": None,
        "statusSnapshots": [],
        "stoppedEarly": False,
        "stopReason": "",
        "firstOffAt": None,
        "firstOffElapsedSeconds": None,
        "firstNonRunningAt": None,
        "firstNonRunningElapsedSeconds": None,
        "officialClientProcessSnapshots": [
            {
                "stage": "before",
                "elapsedSeconds": 0,
                "at": core.shanghai_now().isoformat(),
                "processes": before_processes,
            }
        ],
        "otherLoginDetected": False,
        "errors": [],
        "officialClientProcessesBefore": before_processes,
        "officialClientProcessesAfter": [],
        "successCriteria": {
            "noOtherLogin": False,
            "poweredThroughout": False,
            "ranAtLeastMinProofSeconds": False,
            "noOfficialClientProcess": False,
        },
    }

    def finish_report():
        args = core.argparse.Namespace(state=state_path)
        core.merge_state({
            "lastHttpSessionVerifyAt": report["endedAt"],
            "lastHttpSessionVerify": {
                "ok": report["ok"],
                "accepted": report["accepted"],
                "desktopKeepaliveProven": report["desktopKeepaliveProven"],
                "aborted": report["aborted"],
                "abortReason": report["abortReason"],
                "stoppedEarly": report["stoppedEarly"],
                "stopReason": report["stopReason"],
                "durationSeconds": report["durationSeconds"],
                "firstOffAt": report["firstOffAt"],
                "firstOffElapsedSeconds": report["firstOffElapsedSeconds"],
                "firstNonRunningAt": report["firstNonRunningAt"],
                "firstNonRunningElapsedSeconds": report["firstNonRunningElapsedSeconds"],
                "successCriteria": report["successCriteria"],
            },
        }, args)

        core.write_private_json_report(report, report_file)
        return report

    if before_processes and not allow_official_client_present:
        report["aborted"] = True
        report["abortReason"] = "official_client_process_present_before_verify"
        report["endedAt"] = core.shanghai_now().isoformat()
        report["durationSeconds"] = int(time.time() - started)
        report["officialClientProcessesAfter"] = official_client_processes()
        report["officialClientProcessSnapshots"].append({
            "stage": "after_abort",
            "elapsedSeconds": report["durationSeconds"],
            "at": report["endedAt"],
            "processes": report["officialClientProcessesAfter"],
        })
        report["errors"].append({
            "stage": "preflight",
            "error": (
                "official client/SDK process is already running; HTTP timer verification "
                "is rejected as keepalive and this can only be a contaminated control run"
            ),
            "at": report["endedAt"],
        })
        report["successCriteria"] = {
            "noOtherLogin": False,
            "poweredThroughout": False,
            "ranAtLeastMinProofSeconds": False,
            "noOfficialClientProcess": False,
        }
        return finish_report()

    try:
        report["statusBefore"] = _status_snapshot(target, state_path)
        report["statusBefore"]["elapsedSeconds"] = 0
        report["statusSnapshots"].append(report["statusBefore"])
        if stop_on_off and (report["statusBefore"].get("off") or not report["statusBefore"].get("running")):
            report["stoppedEarly"] = True
            report["stopReason"] = "initial_power_state_not_running"
    except Exception as err:
        report["errors"].append({"stage": "statusBefore", "error": str(err), "at": core.shanghai_now().isoformat()})

    next_heartbeat = started
    next_info = started
    next_log = started
    next_status = started + max(1, int(status_interval)) if status_interval else float("inf")
    count = 0
    while not report["stoppedEarly"] and time.time() - started < int(duration):
        now = time.time()
        do_heartbeat = now >= next_heartbeat
        do_info = now >= next_info
        do_log = now >= next_log
        do_status = now >= next_status
        if do_heartbeat or do_info or do_log:
            count += 1
            event = {
                "index": count,
                "elapsedSeconds": int(now - started),
                "at": core.shanghai_now().isoformat(),
                "heartbeat": None,
                "infoReport": None,
                "logReportConfig": None,
                "error": None,
            }
            try:
                result = official_http_once(
                    target,
                    state_path=state_path,
                    include_status=False,
                    do_heartbeat=do_heartbeat,
                    do_info=do_info,
                    do_log_config=do_log,
                )
                event["heartbeat"] = result.get("heartbeat")
                event["infoReport"] = result.get("infoReport")
                event["logReportConfig"] = result.get("logReportConfig")
            except core.CmccError as err:
                event["error"] = str(err)
                if err.response is not None:
                    event["response"] = err.response
                    if int((err.response or {}).get("code") or 0) == OTHER_LOGIN_CODE:
                        report["otherLoginDetected"] = True
                report["errors"].append({"stage": "http", "error": str(err), "at": core.shanghai_now().isoformat()})
            report["events"].append(event)
            heartbeat_code = ((event.get("heartbeat") or event.get("response") or {}).get("code"))
            if int(heartbeat_code or 0) == OTHER_LOGIN_CODE:
                report["otherLoginDetected"] = True
            if do_heartbeat:
                next_heartbeat = now + max(1, int(heartbeat_interval))
            if do_info:
                next_info = now + max(1, int(info_interval))
            if do_log:
                next_log = now + max(1, int(log_config_interval))
            print(
                f"[{core.short_time()}] [{count}] HTTP验证: {core.format_duration(int(now - started))} "
                f"heartbeat={_code(event, 'heartbeat')} info={_code(event, 'infoReport')} "
                f"logConfig={_code(event, 'logReportConfig')}",
                flush=True,
            )

        if do_status:
            try:
                snapshot = _status_snapshot(target, state_path)
                snapshot["elapsedSeconds"] = int(now - started)
                report["statusSnapshots"].append(snapshot)
                report["officialClientProcessSnapshots"].append(_official_process_snapshot("status", started))
                next_status = now + max(1, int(status_interval))
                print(
                    f"[{core.short_time()}] 状态快照: {snapshot.get('vmStatusShow')} running={snapshot.get('running')}",
                    flush=True,
                )
                if stop_on_off and (snapshot.get("off") or not snapshot.get("running")):
                    report["stoppedEarly"] = True
                    report["stopReason"] = "power_state_not_running"
                    break
            except Exception as err:
                report["errors"].append({"stage": "status", "error": str(err), "at": core.shanghai_now().isoformat()})
                next_status = now + max(1, int(status_interval))

        sleep_until = min(next_heartbeat, next_info, next_log, next_status)
        time.sleep(max(1, min(5, int(sleep_until - time.time()) if sleep_until > time.time() else 1)))

    try:
        report["statusAfter"] = _status_snapshot(target, state_path)
        report["statusAfter"]["elapsedSeconds"] = int(time.time() - started)
        report["statusSnapshots"].append(report["statusAfter"])
    except Exception as err:
        report["errors"].append({"stage": "statusAfter", "error": str(err), "at": core.shanghai_now().isoformat()})
    report["endedAt"] = core.shanghai_now().isoformat()
    report["durationSeconds"] = int(time.time() - started)
    report["officialClientProcessesAfter"] = official_client_processes()
    report["officialClientProcessSnapshots"].append({
        "stage": "after",
        "elapsedSeconds": report["durationSeconds"],
        "at": report["endedAt"],
        "processes": report["officialClientProcessesAfter"],
    })
    snapshots = report["statusSnapshots"]
    summary = power_monitor.summarize(
        snapshots,
        errors=[item for item in report["errors"] if str(item.get("stage", "")).startswith("status")],
        requested_duration=int(min_proof_seconds),
        duration_seconds=report["durationSeconds"],
    )
    powered = summary["poweredThroughout"]
    report["firstOffAt"] = summary["firstOffAt"]
    report["firstOffElapsedSeconds"] = summary["firstOffElapsedSeconds"]
    report["firstNonRunningAt"] = summary["firstNonRunningAt"]
    report["firstNonRunningElapsedSeconds"] = summary["firstNonRunningElapsedSeconds"]
    no_other_login = not report["otherLoginDetected"]
    ran_enough = report["durationSeconds"] >= int(min_proof_seconds)
    no_official_client = not any(snapshot.get("processes") for snapshot in report["officialClientProcessSnapshots"])
    report["successCriteria"] = {
        "noOtherLogin": no_other_login,
        "poweredThroughout": powered,
        "ranAtLeastMinProofSeconds": ran_enough,
        "noOfficialClientProcess": no_official_client,
    }
    report["candidateAccepted"] = no_other_login and powered and ran_enough and no_official_client
    report["accepted"] = False
    report["desktopKeepaliveProven"] = False
    report["ok"] = False

    return finish_report()
