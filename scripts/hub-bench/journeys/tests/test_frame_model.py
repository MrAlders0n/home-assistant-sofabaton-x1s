from journeys.frame import allowed, check, diff, flatten
from journeys.model import ABSENT, IntentModel


def test_flatten_keeps_lists_as_leaves_and_marks_empty_dicts():
    flat = flatten({"a": {"b": [1, 2], "c": {}}})
    assert flat == {("a", "b"): [1, 2], ("a", "c"): {}}


def test_diff_sees_changed_added_and_removed_leaves():
    changes = diff({"x": {"a": 1, "b": 2}}, {"x": {"a": 1, "b": 3, "c": 4}})
    assert [c.text for c in changes] == ["x/b", "x/c"]


def test_patterns_cover_descendants_and_wildcards():
    path = ("cache", "activities", "101", "bindings", "176", "short")
    assert allowed(path, ["cache/activities/101/bindings"])
    assert allowed(path, ["cache/activities/*/bindings/176"])
    assert allowed(path, ["**/bindings/176"])
    assert not allowed(path, ["cache/activities/102"])
    assert not allowed(path, ["hub/activities/101"])


def test_the_306_shape_is_a_finding():
    """Saving the Soundbar's slot changed the Amp's slot: outside the step's
    allowance, so a finding."""

    before = {"ha": {"wifi": {"amp": {"slots": {"0": {"hard_button": "VOL+"}}},
                              "sb": {"slots": {"0": {"hard_button": ""}}}}}}
    after = {"ha": {"wifi": {"amp": {"slots": {"0": {"hard_button": ""}}},
                             "sb": {"slots": {"0": {"hard_button": "VOL+"}}}}}}
    result = check(before, after, ["ha/wifi/sb"])
    assert [c.text for c in result.allowed] == ["ha/wifi/sb/slots/0/hard_button"]
    assert [c.text for c in result.findings] == ["ha/wifi/amp/slots/0/hard_button"]


def test_check_limits_to_the_named_layers():
    result = check({"ha": {"a": 1}, "cache": {"a": 1}}, {"ha": {"a": 2}, "cache": {"a": 2}}, [], layers=("cache",))
    assert [c.text for c in result.findings] == ["cache/a"]


def test_intent_survives_until_replaced_and_absent_works():
    model = IntentModel()
    model.expect("activities/101/bindings/176/short", "4/13", step=3, why="bind OK in A")
    snap = {"cache": {"activities": {"101": {"bindings": {"176": {"short": "4/13"}}}}}}
    assert model.check(snap, ("cache", "hub")) == []

    later = {"cache": {"activities": {"101": {"bindings": {}}}}}
    misses = model.check(later, ("cache",))
    assert len(misses) == 1 and "asked at step 3" in misses[0].render()

    model.expect("activities/101/bindings/176", ABSENT, step=9, why="clear OK in A")
    model.forget("activities/101/bindings/176/short")
    assert model.check(later, ("cache",)) == []
    assert model.check(snap, ("cache",))  # still bound: the clear did not land
