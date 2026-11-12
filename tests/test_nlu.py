import pytest

from voiceagent.nlu import garbled_amount, normalise, parse, parse_budget, parse_locality, words_to_digits


@pytest.mark.parametrize("spoken,digits", [
    ("eighty five lakhs", "85 lakhs"),
    ("one point two crore", "1.2 crore"),
    ("two bhk", "2 bhk"),
    ("nine eight seven six five four three two one zero", "9876543210"),
    ("half a crore", "0.5 crore"),
    ("one hundred lakh", "100 lakh"),
])
def test_spoken_numbers(spoken, digits):
    assert words_to_digits(spoken) == digits


@pytest.mark.parametrize("text,budget", [
    ("under 85 lakh", (None, 85.0)),
    ("1.2 crore", (None, 120.0)),
    ("70 to 90 lakh", (70.0, 90.0)),
    ("85 lakh to 1.1 crore", (85.0, 110.0)),
    ("1.2 to 1.5 crore", (120.0, 150.0)),
    ("around 1 crore", (90.0, 110.0)),
    ("i have 3 kids", None),
    ("5 lakh", None),  # not a plausible flat budget
])
def test_budget(text, budget):
    assert parse_budget(normalise(text)) == budget


@pytest.mark.parametrize("text,locality,uncertain", [
    ("looking in whitefield", "Whitefield", False),
    ("something in hsr layout", "HSR Layout", False),
    ("white feel area", "Whitefield", True),
    ("near hebal", "Hebbal", True),
    ("electronics city", "Electronic City", True),
    ("yelahanka new town", "Yelahanka", False),
    ("mumbai", None, False),
])
def test_locality(text, locality, uncertain):
    assert parse_locality(text) == (locality, uncertain)


def test_fuzzy_locality_can_be_switched_off():
    assert parse_locality("white feel area", fuzzy=False) == (None, False)


def test_full_parse_of_a_noisy_transcript():
    p = parse("uh yes i'm looking for a two bhk in white field budget under ninety lakh")
    assert p.intent == "affirm"
    assert p.slots["locality"] == "Whitefield" and p.slots["bhk"] == 2 and p.slots["budget"] == (None, 90.0)
    assert "uh" not in p.slots["_text"]


def test_without_spoken_numbers_the_digits_are_lost():
    assert "budget" not in parse("ninety lakh", spoken_numbers=False).slots


@pytest.mark.parametrize("text,intent", [
    ("please don't call me again", "do_not_call"),
    ("i'm not interested", "not_interested"),
    ("can i speak to a real person", "human"),
    ("i'm driving call me later", "callback"),
    ("can you give me a discount", "discount"),
    ("is the title clear any legal issues", "legal"),
    ("is it rera registered", "question"),
    ("yes sure", "affirm"),
    ("no not right now", "deny"),
])
def test_intents(text, intent):
    assert parse(text).intent == intent


def test_garbled_amounts_are_flagged_uncertain():
    assert garbled_amount("under point 3 crore") and garbled_amount("1 3 crore") and garbled_amount("1 point crore")
    assert not garbled_amount("1.3 crore")
    p = parse("my budget is one three crore")
    assert "budget" in p.uncertain


def test_names_need_an_explicit_introduction():
    assert parse("my name is priya").slots["name"] == "Priya"
    assert "name" not in parse("i am in a meeting").slots
    assert "name" not in parse("this is for my parents").slots


def test_timeline_purpose_phone_and_day():
    p = parse("within 3 months for my family, number 9876543210, saturday works")
    assert p.slots["timeline"] == "0-3 months" and p.slots["purpose"] == "self-use"
    assert p.slots["phone"] == "9876543210" and p.slots["visit_day"] == "saturday"
    assert parse("just exploring").slots["timeline"] == "exploring"
