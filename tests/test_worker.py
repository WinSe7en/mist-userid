import pytest

from app.worker import classify_event


class TestClassifyEvent:
    def test_client_join_is_login(self):
        event = {"_topic": "client-join", "client_ip": "10.1.1.1"}
        assert classify_event(event) == "login"

    def test_roam_is_login(self):
        event = {
            "_topic": "client-sessions",
            "next_ap": "020000000a07",
            "termination_reason": 3,
        }
        assert classify_event(event) == "login"

    def test_disconnect_is_logout(self):
        event = {
            "_topic": "client-sessions",
            "next_ap": "000000000000",
            "termination_reason": 1,
        }
        assert classify_event(event) == "logout"

    def test_inactive_is_logout(self):
        event = {
            "_topic": "client-sessions",
            "next_ap": "000000000000",
            "termination_reason": 2,
        }
        assert classify_event(event) == "logout"

    def test_missing_next_ap_defaults_to_logout(self):
        event = {"_topic": "client-sessions", "termination_reason": 1}
        assert classify_event(event) == "logout"

    def test_missing_topic_with_real_next_ap_is_login(self):
        event = {"next_ap": "020000000a07"}
        assert classify_event(event) == "login"
