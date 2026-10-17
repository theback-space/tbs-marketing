from tbs_social import compliance
from tbs_social.ai import redact

from conftest import BRAND


def test_inbound_flags_personal_health_details():
    signals = compliance.scan_inbound("I was diagnosed with sciatica, my DOB is 4/12/1980")
    assert "discloses personal health history" in signals
    assert any("date" in s for s in signals)


def test_inbound_clean_praise_has_no_signals():
    assert compliance.scan_inbound("Love these desk stretch tips!") == []


def test_reply_confirming_patient_relationship_is_blocked():
    issues = compliance.check_outbound("Hope your treatment is going well!", BRAND, is_reply=True)
    assert any("HIPAA" in i for i in issues)


def test_reply_may_include_our_own_email():
    assert compliance.check_outbound("Email us at hello@clinic.test", BRAND, is_reply=True) == []


def test_reply_with_someone_elses_contact_is_blocked():
    issues = compliance.check_outbound("We'll email jen@example.com", BRAND, is_reply=True)
    assert issues


def test_banned_phrase_in_post():
    issues = compliance.check_outbound("Our method is a guaranteed cure!", BRAND, is_reply=False)
    assert len(issues) == 2


def test_emergency_detection():
    assert compliance.is_emergency("sudden loss of bladder control and back pain")
    assert not compliance.is_emergency("my back is a bit stiff")


def test_redaction_strips_identifiers():
    out = redact("Call 555-201-3344 or jen@example.com, DOB 4/12/1980")
    assert "555" not in out and "jen@" not in out and "1980" not in out


def test_patient_confirmation_with_intervening_words():
    for text in ("See you at your next appointment!", "Hope your recent MRI results were okay"):
        assert compliance.check_outbound(text, BRAND, is_reply=True), text
    assert compliance.scan_inbound("Loved my first session today")
