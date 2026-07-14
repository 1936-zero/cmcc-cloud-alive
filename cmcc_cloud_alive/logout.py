"""Desktop and account logout helpers."""

from . import cloud, core


def desktop_logout(user_service_id=None, state_path=None):
    """Release SOHO desktop session lock via /cc/cloudPc/logout/v2.

    When *user_service_id* is provided, call the API directly and skip
    listClouds validation.  That validation is useful for resolving a
    missing id, but it turns token-expired / stale-usid cases into a
    hard failure before the logout endpoint is even reached (WebUI 502).
    """
    args = core.argparse.Namespace(state=state_path)
    if user_service_id is not None and str(user_service_id).strip():
        target = str(user_service_id).strip()
    else:
        target = cloud.selected_user_service_id(state_path, None)
    response = core.api_request(
        "/cc/cloudPc/logout/v2", {"userServiceId": str(target)}, args
    )
    core.merge_state(
        {
            "lastDesktopLogoutAt": core.shanghai_now().isoformat(),
            "lastDesktopLogoutUserServiceId": str(target),
            "lastDesktopLogoutResponse": response,
        },
        args,
    )
    return response


def account_logout(state_path=None, clear_local=True):
    args = core.argparse.Namespace(state=state_path)
    response = core.api_request("/login/logout/v1", None, args)
    if clear_local:
        state = core.load_state(args)
        for key in ["sohoToken", "userId", "nickname", "phone", "isLogined"]:
            state.pop(key, None)
        state["lastAccountLogoutAt"] = core.shanghai_now().isoformat()
        core.save_state(state, args)
    return response
