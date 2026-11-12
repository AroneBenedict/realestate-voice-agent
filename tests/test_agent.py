import pytest

from tests.conftest import talk
from voiceagent.agent import DEFER, AgentConfig, Call


def qualify(call, budget="under 1.1 crore"):
    return talk(call, "yes sure", "whitefield", "2 bhk", budget, "within 3 months")


def test_happy_path_books_a_visit_and_scores_the_lead(call, crm):
    turns = qualify(call)
    assert turns[-1].expecting == "confirm_summary" and "Whitefield" in turns[-1].text
    rec = call.respond("yes")
    assert rec.expecting == "visit_consent" and call.presented
    turns = talk(call, "yes let's book", "saturday", "my name is ravi")
    assert turns[-1].expecting == "phone"
    assert call.respond("9876543210").expecting == "confirm:phone"
    done = call.respond("yes")
    assert "booked for Saturday" in done.text and "3210" in done.text and "9876543210" not in done.text
    end = call.respond("no thanks")
    assert end.ended
    lead = crm.get(call.lead.id)
    assert lead.status == "visit_booked" and lead.score == "hot" and lead.visit["day"] == "saturday"
    assert lead.phone == "9876543210" and not call.violations


def test_chatty_caller_gives_everything_at_once(call):
    turn = call.respond("yes, I want a 3 bhk in hebbal around 1.8 crore, buying next year")
    assert turn.expecting == "confirm_summary"


def test_dialled_number_is_offered_instead_of_dictation(catalog, crm):
    call = Call(catalog, crm)
    call.caller_number = "9123456789"
    call.opening()
    qualify(call)
    talk(call, "yes", "yes book it", "sunday")
    assert call.respond("my name is priya").expecting == "phone_on_file"
    assert "booked" in call.respond("yes").text
    assert crm.get(call.lead.id).phone == "9123456789"


def test_phone_is_taken_in_two_halves_after_a_failed_dictation(call, crm):
    qualify(call)
    talk(call, "yes", "yes", "friday", "my name is arjun")
    turn = call.respond("nine eight seven six")          # dictation garbled
    assert turn.expecting == "phone_first5"
    assert call.respond("nine eight seven six five").expecting == "phone_last5"
    assert call.respond("four three two one zero").expecting == "confirm:phone"
    call.respond("yes")
    assert crm.get(call.lead.id).phone == "9876543210"


def test_uncertain_values_are_read_back_and_can_be_corrected(call):
    talk(call, "yes")
    turn = call.respond("white feel")
    assert turn.expecting == "confirm:locality" and "Whitefield" in turn.text
    assert call.respond("yes").expecting == "bhk"
    call.respond("2 bhk")
    turn = call.respond("one three crore")  # a dropped "point"
    assert turn.expecting == "confirm:budget"
    turn = call.respond("no, one point three crore")
    assert call.slots["budget"] == (None, 130.0) and turn.expecting == "timeline"


def test_summary_correction(call):
    qualify(call)
    turn = call.respond("no, 3 bhk")
    assert call.slots["bhk"] == 3 and turn.expecting == "confirm_summary"
    assert call.respond("no").expecting == "correction"


def test_brochure_questions_and_deferrals(call):
    qualify(call)
    call.respond("yes")
    project = call.presented[0].project
    answer = call.respond("when can i move in")
    assert answer.expecting == "visit_consent"
    assert ("ready to move in" in answer.text) or project.facts["possession"] in answer.text
    pets = call.respond("do you allow pets")
    assert (DEFER in pets.text) == ("pets" not in project.facts)
    assert call.respond("the metro close by").text.startswith(project.name)  # question word lost by ASR
    assert "repeat your question" in call.respond("what is the colour").text


def test_discount_legal_and_off_topic_are_handled_safely(call):
    qualify(call)
    call.respond("yes")
    assert "can't offer a discount" in call.respond("can you give me a discount").text
    assert "can't give legal" in call.respond("is the title clear").text
    assert "only able to help" in call.respond("who won the cricket").text
    assert not call.violations


@pytest.mark.parametrize("line,status", [
    ("please don't call me again", "do_not_call"),
    ("not interested, thanks", "not_interested"),
    ("let me talk to a real person", "handoff"),
    ("I'm driving, call me later", "callback"),
])
def test_exits(call, crm, line, status):
    turn = call.respond(line)
    assert turn.ended and crm.get(call.lead.id).status == status
    with pytest.raises(RuntimeError):
        call.respond("hello?")


def test_no_match_is_said_plainly(call, crm):
    qualify(call, budget="under 20 lakh")
    turn = call.respond("yes")
    assert turn.ended and "couldn't find" in turn.text and crm.get(call.lead.id).status == "no_match"


def test_silence_repeats_the_question(call):
    talk(call, "yes")
    turn = call.respond("")
    assert turn.text.startswith("Sorry, I didn't catch that.") and turn.expecting == "locality"


def test_turn_limit_hands_over(call, crm):
    for _ in range(24):
        call.respond("hmm")
    assert call.respond("hmm").ended and crm.get(call.lead.id).status == "handoff"


def test_trace_and_transcript_are_recorded_with_masked_numbers(call):
    qualify(call)
    talk(call, "yes", "yes", "monday", "my name is meera", "9876543210")
    tools = [e.tool for e in call.trace]
    assert tools[0] == "crm.create_lead" and "search_properties" in tools
    assert all("9876543210" not in text for _, text in call.transcript)


def test_baseline_config_cannot_read_spoken_budgets(catalog, crm):
    call = Call(catalog, crm, AgentConfig(spoken_numbers=False, contextual_numbers=False))
    call.opening()
    talk(call, "yes", "whitefield", "two bhk")
    assert call.expecting == "bhk"  # "two" was never understood
