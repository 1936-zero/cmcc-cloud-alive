"""Cloud PC list, selection, and status helpers."""

from . import core


RUNNING_STATUS_VALUES = {1}
OFF_STATUS_VALUES = {16}
TARGET_SKU_KEYWORDS = ("任意云电脑",)
TARGET_ID_KEYS = ("vmId", "spuCode")
TARGET_PRODUCT_KEYS = ("skuName", "vmName", "productName", "goodsName")


def is_target_desktop(item):
    """Selection gate intentionally disabled: any listed cloud PC is selectable."""
    return True


def target_desktops(items):
    return [item for item in items if is_target_desktop(item)]


def _target_label():
    return TARGET_SKU_KEYWORDS[0]


def _first_target(items):
    targets = target_desktops(items)
    return targets[0] if targets else None


def _assert_target(item):
    """Compatibility hook: gate is disabled; any listed cloud PC is selectable."""
    if not is_target_desktop(item):
        raise core.CmccError("selected cloud PC is not selectable")


def list_desktops(state_path=None):
    args = core.argparse.Namespace(state=state_path)
    items = core.list_clouds(args)
    state = core.load_state(args)
    if items and not state.get("selectedUserServiceId"):
        target = _first_target(items)
        if target and target.get("userServiceId"):
            core.merge_state({
                "selectedUserServiceId": str(target.get("userServiceId")),
                "selectedDesktop": target,
                "selectedAt": core.shanghai_now().isoformat(),
            }, args)
    return items


def select_desktop(user_service_id, state_path=None, skip_target_assert=False):
    args = core.argparse.Namespace(state=state_path)
    items = core.list_clouds(args)
    target = str(user_service_id)
    for item in items:
        if str(item.get("userServiceId")) == target:
            if not skip_target_assert:
                _assert_target(item)
            core.merge_state({
                "selectedUserServiceId": target,
                "selectedDesktop": item,
                "selectedAt": core.shanghai_now().isoformat(),
            }, args)
            return item
    raise core.CmccError(f"userServiceId not found: {target}")


def selected_user_service_id(state_path=None, explicit=None):
    if explicit:
        args = core.argparse.Namespace(state=state_path)
        target = str(explicit)
        for item in core.list_clouds(args):
            if str(item.get("userServiceId")) == target:
                _assert_target(item)
                return target
        raise core.CmccError(f"userServiceId not found: {target}")
    args = core.argparse.Namespace(state=state_path)
    state = core.load_state(args)
    if state.get("selectedUserServiceId"):
        cached = state.get("selectedDesktop")
        if isinstance(cached, dict):
            _assert_target(cached)
            return str(state["selectedUserServiceId"])
        target = str(state["selectedUserServiceId"])
        for item in core.list_clouds(args):
            if str(item.get("userServiceId")) == target:
                _assert_target(item)
                return target
        raise core.CmccError(f"userServiceId not found: {target}")
    items = list_desktops(state_path)
    target = _first_target(items)
    if target and target.get("userServiceId"):
        return str(target["userServiceId"])
    if items:
        raise core.CmccError("no selectable cloud PC found")
    raise core.CmccError("no desktop found; run list first")


def status(user_service_id=None, state_path=None):
    args = core.argparse.Namespace(state=state_path, user_service_id=selected_user_service_id(state_path, user_service_id))
    return core.cloud_status(args)


def is_running(item):
    status_value = item.get("vmStatus")
    status_text = str(item.get("vmStatusShow") or "")
    return status_value in RUNNING_STATUS_VALUES or "运行" in status_text


def is_off(item):
    status_value = item.get("vmStatus")
    status_text = str(item.get("vmStatusShow") or "")
    return status_value in OFF_STATUS_VALUES or "关机" in status_text
