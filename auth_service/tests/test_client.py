import pytest

from examples.studio_auth_client import LoginError, StudioAuthClient


def client():
    return StudioAuthClient("https://auth.example.test", "device-test-123456")


def tokens():
    return {"access_token": "access", "refresh_token": "refresh", "user": {"username": "alice"}}


def test_sdk_network_error_never_grants_permission():
    auth = client()
    auth.tokens = tokens()
    def fail(*args):
        raise LoginError("network_error", "offline")
    auth._post = fail
    with pytest.raises(LoginError):
        auth.verify()


def test_sdk_refresh_timeout_discards_token_and_does_not_retry():
    auth = client()
    auth.tokens = tokens()
    calls = []
    def fail(action, *args):
        calls.append(action)
        raise LoginError("network_error", "offline")
    auth._post = fail
    with pytest.raises(LoginError):
        auth.refresh()
    assert auth.tokens is None
    assert calls == ["refresh"]


def test_sdk_only_refreshes_once():
    auth = client()
    auth.tokens = tokens()
    calls = []
    def fake(action, *args):
        calls.append(action)
        if action == "refresh":
            return tokens()
        raise LoginError("access_expired", "expired")
    auth._post = fake
    with pytest.raises(LoginError):
        auth.verify()
    assert calls == ["verify", "refresh", "verify"]
    assert auth.tokens is None


def test_sdk_success_and_logout_network_failure_clears_local_state():
    auth = client()
    auth.tokens = tokens()
    auth._post = lambda *args: {"valid": True, "user": {"username": "alice"}}
    assert auth.verify()["username"] == "alice"
    def fail(*args):
        raise LoginError("network_error", "offline")
    auth._post = fail
    with pytest.raises(LoginError):
        auth.logout()
    assert auth.tokens is None


@pytest.mark.parametrize("url", ["http://auth.test", "https://user:pass@auth.test", "https://auth.test/api",
                                 "https://auth.test?token=abc", "https://auth.test/#token"])
def test_sdk_requires_dedicated_https_origin(url):
    with pytest.raises(ValueError):
        StudioAuthClient(url, "device-test-123456")


def test_sdk_rejects_non_boolean_permission():
    auth = client()
    auth.tokens = tokens()
    auth._post = lambda *args: {"valid": "false", "user": {}}
    with pytest.raises(LoginError):
        auth.verify()
