from app.services.nomba_client import _naira_to_kobo, _kobo_to_naira


def test_naira_to_kobo_matches_official_sandbox_testing_example():
    # Confirmed real example from developer.nomba.com's sandbox-testing
    # doc: a checkout order created for 400000 kobo (₦4,000) is sent
    # with "amount": "400000.00" — a decimal STRING, not a bare
    # integer. This is the only confirmed example for THIS specific
    # endpoint's request body (see nomba_client.py's module docstring
    # point 5 for why the training quiz's integer convention doesn't
    # apply here — that example is for a webhook payload, a different
    # API surface, not this request).
    assert _naira_to_kobo(4000) == "400000.00"
    assert _naira_to_kobo(2500) == "250000.00"


def test_kobo_to_naira_round_trip():
    # _kobo_to_naira is for the OTHER direction — parsing amounts OUT
    # of webhook payloads, where the confirmed convention (training
    # quiz's signature-lab example) is a plain integer. Different API
    # surface, different format, kept separate on purpose.
    assert _kobo_to_naira(250000) == 2500
    assert _kobo_to_naira(1500000) == 15000


def test_kobo_to_naira_tolerates_string_and_float_input():
    # Webhook payloads and API responses aren't guaranteed to send
    # amount as the same JSON type every time.
    assert _kobo_to_naira("250000") == 2500
    assert _kobo_to_naira(250000.0) == 2500


def test_naira_kobo_round_trip_is_lossless_for_whole_naira_amounts():
    for amount in (0, 1, 100, 2500, 15000, 847000):
        kobo_string = _naira_to_kobo(amount)
        assert isinstance(kobo_string, str)
        assert _kobo_to_naira(kobo_string) == amount
