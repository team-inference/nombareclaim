from app.services.nomba_client import _naira_to_kobo, _kobo_to_naira


def test_naira_to_kobo_matches_training_material_example():
    # Confirmed example from Nomba's own training material:
    # a ₦2,500.00 charge is sent as amount: 250000.
    assert _naira_to_kobo(2500) == 250000


def test_kobo_to_naira_round_trip():
    assert _kobo_to_naira(250000) == 2500
    assert _kobo_to_naira(1500000) == 15000


def test_kobo_to_naira_tolerates_string_and_float_input():
    # Webhook payloads and API responses aren't guaranteed to send
    # amount as the same JSON type every time.
    assert _kobo_to_naira("250000") == 2500
    assert _kobo_to_naira(250000.0) == 2500


def test_naira_kobo_round_trip_is_lossless_for_whole_naira_amounts():
    for amount in (0, 1, 100, 2500, 15000, 847000):
        assert _kobo_to_naira(_naira_to_kobo(amount)) == amount
