"""Unit tests for core/app_service/cancel_token.py — _CancelToken behaviour.

These tests cover the real token path independently of Qt and scan pipeline
machinery, so any regression in the token itself surfaces here before the
higher-level ScanWorker tests even run.
"""

from __future__ import annotations


class TestCancelToken:
    """_CancelToken — fresh state, request(), is_set(), __call__."""

    def test_fresh_token_is_clear(self):
        """A newly constructed token must not be set."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()
        assert not token.is_set(), (
            "is_set() must return False on a fresh _CancelToken"
        )

    def test_fresh_token_callable_is_false(self):
        """__call__ must return False on a fresh token (satisfies Callable[[], bool])."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()
        assert not token(), (
            "__call__() must return False on a fresh _CancelToken; "
            "walker.py passes cancel_check as a Callable[[], bool]"
        )

    def test_request_sets_is_set(self):
        """After request(), is_set() must return True."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()
        token.request()
        assert token.is_set(), (
            "is_set() must return True after request()"
        )

    def test_request_sets_callable(self):
        """After request(), __call__ must also return True."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()
        token.request()
        assert token(), (
            "__call__() must return True after request()"
        )

    def test_request_is_idempotent(self):
        """Calling request() more than once must not raise or flip back to False."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()
        token.request()
        token.request()
        assert token.is_set(), (
            "is_set() must remain True after a second request() call"
        )

    def test_callable_matches_is_set(self):
        """__call__ and is_set() must always agree."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()
        assert token() == token.is_set(), "before request: __call__ and is_set() must agree"
        token.request()
        assert token() == token.is_set(), "after request: __call__ and is_set() must agree"
