import pytest

from app.utils import sanitize_username


class TestSanitizeUsername:
    def test_email_format(self):
        assert sanitize_username("jane.q.doe@example.edu") == "j***@example.edu"

    def test_simple_email(self):
        assert sanitize_username("jsmith@example.edu") == "j***@example.edu"

    def test_psk_name_no_at(self):
        assert sanitize_username("device_lobby_printer") == "d***"

    def test_simple_username_no_at(self):
        assert sanitize_username("admin") == "a***"

    def test_single_char_local(self):
        assert sanitize_username("j@example.edu") == "j***@example.edu"

    def test_empty_local_part(self):
        assert sanitize_username("@example.edu") == "***@example.edu"

    def test_empty_string(self):
        assert sanitize_username("") == ""

    def test_single_char(self):
        assert sanitize_username("x") == "x***"

    def test_domain_with_subdomain(self):
        assert sanitize_username("user@mail.example.edu") == "u***@mail.example.edu"

    def test_multiple_at_signs(self):
        # Only splits on first @
        assert sanitize_username("user@name@example.edu") == "u***@name@example.edu"
