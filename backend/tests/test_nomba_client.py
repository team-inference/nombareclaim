from app.services.nomba_client import _naira_to_kobo, _kobo_to_naira


def test_naira_to_kobo_matches_official_worked_example():
    # CORRECTED: Nomba's sandbox-testing guide has a real worked
    # example — a checkout order created with "amount": "400000.00"
    # comes back with order.amount / transaction.transactionAmount of
    # 4000.00, exactly divided by 100. So the create-order amount is
    # in KOBO, but sent as a DECIMAL STRING, not a bare integer.
    # ₦2,500.00 must become the string "250000.00", not the int 250000.
    assert _naira_to_kobo(2500) == "250000.00"


def test_kobo_to_naira_round_trip():
    assert _kobo_to_naira(250000) == 2500
    assert _kobo_to_naira(1500000) == 15000


def test_kobo_to_naira_tolerates_string_and_float_input():
    # Webhook payloads and API responses aren't guaranteed to send
    # amount as the same JSON type every time.
    assert _kobo_to_naira("250000") == 2500
    assert _kobo_to_naira(250000.0) == 2500


def test_naira_kobo_round_trip_is_lossless_for_whole_naira_amounts():
    # _naira_to_kobo now returns a decimal string (e.g. "250000.00"),
    # and _kobo_to_naira already tolerates string input, so the round
    # trip still holds.
    for amount in (0, 1, 100, 2500, 15000, 847000):
        assert _kobo_to_naira(_naira_to_kobo(amount)) == amount
