import json
from pathlib import Path

from voiceagent.catalog import BrochureIndex, asked_amenity
from voiceagent.crm import InMemoryCRM
from voiceagent.guardrails import Guardrails, redact
from voiceagent.voice import NoisyASR, lakh_phrase, number_words, speakable


def test_catalogue_is_consistent(catalog):
    assert len(catalog.projects) == 22 and len(catalog.localities) == 11
    for project in catalog.projects:
        assert project.configs and project.facts["rera"].startswith("DEMO-")
        for c in project.configs:
            assert c.price_min_lakh < c.price_max_lakh and c.sqft_min < c.sqft_max
    raw = json.loads((Path(__file__).resolve().parents[1] / "data" / "projects.json").read_text())
    assert "Fictional" in raw["note"]


def test_search_respects_bhk_budget_and_locality(catalog):
    matches = catalog.search("Whitefield", 2, None, 110)
    assert matches and all(m.project.locality == "Whitefield" and m.config.bhk == 2 for m in matches)
    assert all(m.config.price_min_lakh <= 110 * 1.1 for m in matches)
    assert matches == sorted(matches, key=lambda m: (not m.within_budget, m.config.price_min_lakh))


def test_search_falls_back_to_nearby_and_then_to_nothing(catalog):
    exact = catalog.search("Kanakapura Road", 4, None, 10_000, include_nearby=False)
    nearby = catalog.search("Devanahalli", 4, None, 10_000)
    assert all(m.exact_locality for m in exact)
    assert all(m.project.locality in ("Devanahalli", "Yelahanka") for m in nearby)
    assert catalog.search("Whitefield", 2, None, 20) == []


def test_brochure_answers_only_what_it_knows(catalog):
    brochure = BrochureIndex()
    with_pets = next(p for p in catalog.projects if "pets" in p.facts)
    without = next(p for p in catalog.projects if "pets" not in p.facts)
    assert "pet" in brochure.answer(with_pets, "do you allow pets").text.lower()
    assert brochure.answer(without, "do you allow pets").text is None
    assert brochure.answer(with_pets, "what's your favourite colour").topic is None
    project = catalog.projects[0]
    pool = brochure.answer(project, "is there a swimming pool")
    assert pool.asked == "swimming pool" and pool.text.startswith("Yes" if pool.has else "No")
    assert asked_amenity("is there a gym") == "gym"


def test_speakable_and_prices():
    assert speakable("A 2 BHK, 1200 sq ft, 1.5 km & ₹90 lakh") == "A 2 B H K, 1200 square feet, 1.5 kilometres and 90 lakh"
    assert lakh_phrase(92.6) == "92.6 lakh" and lakh_phrase(191.4) == "1.91 crore" and lakh_phrase(100) == "1 crore"
    assert number_words(85) == "eighty five" and number_words(120) == "one hundred twenty"


def test_noisy_asr_spells_numbers_and_is_reproducible():
    clean = NoisyASR(0.0)("My budget is 85 lakh, number 9876543210.")
    assert clean == "my budget is eighty five lakh number nine eight seven six five four three two one zero"
    noisy_a, noisy_b = NoisyASR(0.3, seed=1), NoisyASR(0.3, seed=1)
    text = "I want a 2 BHK in Whitefield under 90 lakh"
    assert noisy_a(text) == noisy_b(text) != NoisyASR(0.0)(text)


def test_guardrails(catalog):
    g = Guardrails(catalog)
    price = lakh_phrase(catalog.projects[0].configs[0].price_min_lakh)
    assert g.check(f"It starts at {price}.") == []
    rules = {v.rule for v in g.check("I can offer you 10 percent off, it starts at 12.3 lakh, call 9876543210, "
                                     "and it will appreciate. " + "word " * 60)}
    assert rules == {"unauthorised_promise", "price_not_in_catalogue", "reads_out_full_phone_number",
                     "legal_or_financial_advice", "too_long_for_voice"}
    assert g.check("Up to 85 lakh?", {"85 lakh"}) == []
    assert redact("call 9876543210 now") == "call XXXXXX3210 now"


def test_crm_rules():
    crm = InMemoryCRM()
    lead = crm.create_lead()
    import pytest
    with pytest.raises(ValueError):
        crm.book_visit(lead.id, "P01", "saturday")  # no phone yet
    crm.update_lead(lead.id, phone="9876543210")
    assert crm.book_visit(lead.id, "P01", "saturday").status == "visit_booked"
    with pytest.raises(ValueError):
        crm.update_lead(lead.id, favourite_colour="blue")
    crm.update_lead(lead.id, status="do_not_call")
    with pytest.raises(ValueError):
        crm.book_visit(lead.id, "P01", "sunday")
    with pytest.raises(KeyError):
        crm.get("L99999")
