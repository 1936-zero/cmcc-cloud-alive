"""SOHO token validity checks."""

from . import auth, core


INVALID_TOKEN_CODES = {4014, 4015, 4016, 4017, 4200, 4201}


def check_token(state_path=None):
    args = core.argparse.Namespace(state=state_path)
    try:
        response = core.api_request("/token/checkToken/v1", None, args)
    except Exception as exc:  # network/API errors are treated as invalid so caller may re-login
        response = {"code": 0, "msg": str(exc), "businessCode": ""}
    try:
        code = int(response.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    valid = code == 2000 and code not in INVALID_TOKEN_CODES
    core.merge_state({
        "lastTokenCheckAt": core.shanghai_now().isoformat(),
        "lastTokenCheckResponse": {
            "code": response.get("code"),
            "msg": response.get("msg"),
            "businessCode": response.get("businessCode") or "",
        },
    }, args)
    return valid, response


def ensure_token(state_path=None, relogin=True):
    valid, response = check_token(state_path)
    if valid:
        return True, response
    if not relogin:
        return False, response
    state = auth.login_from_cached_credentials(state_path)
    return True, {"code": 2000, "msg": "re-login ok", "userId": state.get("userId")}
