import pytest

from app.worker import classify_event, validate_username, MAX_USERNAME_LENGTH


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


class TestValidateUsername:
    def test_valid_username(self):
        is_valid, reason = validate_username("jsmith@example.edu")
        assert is_valid is True
        assert reason == ""

    def test_valid_username_with_special_chars(self):
        is_valid, reason = validate_username("user.name+tag@example.edu")
        assert is_valid is True
        assert reason == ""

    def test_valid_username_max_length(self):
        username = "a" * MAX_USERNAME_LENGTH
        is_valid, reason = validate_username(username)
        assert is_valid is True
        assert reason == ""

    def test_too_long_username(self):
        username = "a" * (MAX_USERNAME_LENGTH + 1)
        is_valid, reason = validate_username(username)
        assert is_valid is False
        assert reason == "too_long"

    def test_null_byte_rejected(self):
        is_valid, reason = validate_username("user\x00name@example.edu")
        assert is_valid is False
        assert reason == "control_chars"  # null is < 32, caught by control char check

    def test_control_char_newline_rejected(self):
        is_valid, reason = validate_username("user\nname@example.edu")
        assert is_valid is False
        assert reason == "control_chars"

    def test_control_char_tab_rejected(self):
        is_valid, reason = validate_username("user\tname@example.edu")
        assert is_valid is False
        assert reason == "control_chars"

    def test_control_char_carriage_return_rejected(self):
        is_valid, reason = validate_username("user\rname@example.edu")
        assert is_valid is False
        assert reason == "control_chars"

    def test_bell_character_rejected(self):
        is_valid, reason = validate_username("user\x07name@example.edu")
        assert is_valid is False
        assert reason == "control_chars"

    def test_empty_username(self):
        # Empty is technically valid (no control chars, not too long)
        # but the worker already checks for missing username before validate
        is_valid, reason = validate_username("")
        assert is_valid is True
        assert reason == ""

    def test_integer_username_rejected(self):
        is_valid, reason = validate_username(12345)
        assert is_valid is False
        assert reason == "not_a_string"

    def test_none_username_rejected(self):
        is_valid, reason = validate_username(None)
        assert is_valid is False
        assert reason == "not_a_string"

    def test_list_username_rejected(self):
        is_valid, reason = validate_username(["user@example.edu"])
        assert is_valid is False
        assert reason == "not_a_string"


class TestWatchdogHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_pings_and_cancels(self, monkeypatch):
        import asyncio
        from app import worker

        pings = []
        monkeypatch.setattr(worker.notifier, "notify", pings.append)

        task = asyncio.create_task(worker.watchdog_heartbeat())
        await asyncio.sleep(0.01)  # let the task run its first iteration
        task.cancel()

        assert "WATCHDOG=1" in pings
