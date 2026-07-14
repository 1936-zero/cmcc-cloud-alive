"""Account login helpers."""

from . import core


def password_login(username, password, state_path=None, save_password=False):
    args = core.argparse.Namespace(
        username=username,
        password=password,
        verification_code="",
        random_code="",
        state=state_path,
    )
    core.password_login(args)
    if save_password:
        core.merge_state({"password": password, "passwordSavedAt": core.shanghai_now().isoformat()}, args)
    return core.load_state(args)


def sub_password_login(username, password, state_path=None, save_password=False):
    """Login with a sub-account name/password and optionally cache the password."""
    args = core.argparse.Namespace(
        username=username,
        password=password,
        verification_code="",
        random_code="",
        state=state_path,
    )
    core.sub_password_login(args)
    if save_password:
        core.merge_state({"password": password, "passwordSavedAt": core.shanghai_now().isoformat()}, args)
    return core.load_state(args)


def _state_is_sub_account(state):
    if not isinstance(state, dict):
        return False
    if state.get("isSubAccount") is True:
        return True
    return str(state.get("loginMode") or "").strip().lower() == "sub_password"


def login_from_cached_credentials(state_path=None):
    args = core.argparse.Namespace(state=state_path)
    state = core.load_state(args)
    username = state.get("username")
    password = state.get("password")
    if not username or not password:
        raise core.CmccError("cached username/password is required for automatic re-login")
    if _state_is_sub_account(state):
        return sub_password_login(username, password, state_path=state_path, save_password=True)
    return password_login(username, password, state_path=state_path, save_password=True)
