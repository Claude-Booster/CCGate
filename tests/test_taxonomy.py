from ccgate import taxonomy

def test_d1_codes_are_strings():
    assert taxonomy.D1_MODEL_SWITCH == "D1.model_switch"
    assert taxonomy.D1_EFFORT_CHANGE == "D1.effort_change"
    assert taxonomy.D1_TOOLS_CHANGED == "D1.tools_changed"
    assert taxonomy.D1_TTL_EXPIRED == "D1.ttl_expired"
    assert taxonomy.D1_UNCLASSIFIED == "D1.unclassified"

def test_d2_never_in_d1_all():
    assert taxonomy.D2_COMPACTION not in taxonomy.D1_ALL
    assert taxonomy.D2_TOOL_RESULT_CLEARING not in taxonomy.D1_ALL

def test_every_d1_has_a_fix_hint():
    for code in taxonomy.D1_ALL:
        assert code in taxonomy.FIX_HINTS, f"No fix hint for {code}"
