from datetime import datetime, timedelta, timezone

from app.appeals import check_can_appeal

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
USER = 1


def incident(**kw):
    return {"id": 7, "user_id": USER, "action": "ban", "expires_at": None, "revoked_at": None, **kw}


def test_valid_first_appeal():
    assert check_can_appeal(USER, incident(), [], NOW) is None


def test_active_timeout_can_be_appealed():
    assert check_can_appeal(USER, incident(expires_at=NOW + timedelta(minutes=5)), [], NOW) is None


def test_unknown_user_is_400():
    assert check_can_appeal(None, incident(), [], NOW) == 400


def test_missing_incident_is_400():
    assert check_can_appeal(USER, None, [], NOW) == 400


def test_foreign_incident_is_400():
    assert check_can_appeal(USER, incident(user_id=2), [], NOW) == 400


def test_revoked_incident_is_400():
    assert check_can_appeal(USER, incident(revoked_at=NOW - timedelta(minutes=1)), [], NOW) == 400


def test_expired_timeout_is_400():
    assert check_can_appeal(USER, incident(expires_at=NOW - timedelta(minutes=1)), [], NOW) == 400


def test_open_appeal_is_409():
    for status in ("new", "support_queue", "court_queue"):
        assert check_can_appeal(USER, incident(), [{"status": status}], NOW) == 409


def test_fourth_attempt_is_429():
    closed = [{"status": "rejected_auto"}, {"status": "rejected_auto"}, {"status": "resolved_upheld"}]
    assert check_can_appeal(USER, incident(), closed, NOW) == 429


def test_third_attempt_is_ok():
    closed = [{"status": "rejected_auto"}, {"status": "resolved_upheld"}]
    assert check_can_appeal(USER, incident(), closed, NOW) is None
