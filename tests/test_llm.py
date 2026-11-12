"""LLMCall is tested against a fake Messages API client: no network, no API key."""

from types import SimpleNamespace

from voiceagent.guardrails import SAFE_FALLBACK
from voiceagent.llm import TOOLS, LLMCall


def tool_use(call_id, tool_name, **arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=tool_name, input=arguments)


def text(value):
    return SimpleNamespace(type="text", text=value)


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return SimpleNamespace(content=self.turns.pop(0))


def test_tool_loop_searches_then_speaks(catalog, crm):
    client = FakeClient([
        [tool_use("t1", "search_properties", locality="Whitefield", bhk=2, budget_max_lakh=110)],
        [text("I found two options in Whitefield. Would you like to visit?")],
    ])
    call = LLMCall(catalog, crm, client=client, persona="test persona")
    reply = call.respond("2 BHK in Whitefield under 1.1 crore")
    assert reply.startswith("I found two options")
    assert client.requests[0]["tools"] == TOOLS and client.requests[0]["system"] == "test persona"
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1" and result["is_error"] is False and "projects" in result["content"]


def test_tool_errors_go_back_to_the_model(catalog, crm):
    client = FakeClient([
        [tool_use("t1", "book_site_visit", project_id="P01", day="saturday")],   # no phone yet
        [tool_use("t2", "get_project_fact", project_id="P999", question="parking")],
        [tool_use("t3", "delete_lead")],
        [text("Could I have your number first?")],
    ])
    call = LLMCall(catalog, crm, client=client, persona="p")
    assert call.respond("book it") == "Could I have your number first?"
    assert [t["ok"] for t in call.trace] == [False, False, False]
    errors = [r["messages"][-1]["content"][0] for r in client.requests[1:]]
    assert all(e["is_error"] for e in errors) and "unknown tool" in errors[2]["content"]


def test_guardrails_block_an_unsafe_reply(catalog, crm):
    client = FakeClient([[text("I can offer you 10 percent off if you book today!")]])
    call = LLMCall(catalog, crm, client=client, persona="p")
    assert call.respond("any discount?") == SAFE_FALLBACK
    assert call.violations[0].rule == "unauthorised_promise"


def test_booking_and_ending_update_the_crm(catalog, crm):
    client = FakeClient([
        [tool_use("a", "update_lead", name="Ravi", phone="9876543210", bhk=2)],
        [tool_use("b", "book_site_visit", project_id="P01", day="sunday")],
        [text("Booked for Sunday. Goodbye!"), tool_use("c", "end_call", outcome="visit_booked")],
    ])
    call = LLMCall(catalog, crm, client=client, persona="p")
    assert call.respond("I'm Ravi, 9876543210, book Sunday") == "Booked for Sunday. Goodbye!"
    lead = crm.get(call.lead.id)
    assert call.ended and lead.status == "visit_booked" and lead.visit == {"project_id": "P01", "day": "sunday"}


def test_runaway_tool_use_is_capped(catalog, crm):
    client = FakeClient([[tool_use(f"t{i}", "search_properties", locality="Hebbal", bhk=3, budget_max_lakh=200)]
                         for i in range(5)])
    call = LLMCall(catalog, crm, client=client, persona="p")
    assert "sales expert" in call.respond("show me everything")
    assert len(client.requests) == 5


def test_persona_file_exists_and_sets_the_rules(catalog, crm):
    call = LLMCall(catalog, crm, client=FakeClient([]))
    assert "Never promise a discount" in call.system and "under 40 words" in call.system
