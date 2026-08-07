"""The on-screen questions must stay measured.

The five chips are the first thing anyone clicks, so they are the whole first
impression. Before questions.json existed they were hand-authored in the packs
and never run: 27 of 55 scored below the 52% bar and one scored 22%. Nothing in
the build would have told you that.

These tests fail if the chips ever drift back to unmeasured or below bar.
Regenerate with:  node eval/build_bank.mjs .   (after build_site)
"""
import json
import pathlib

import pytest

DATA = pathlib.Path(__file__).resolve().parents[1] / "site" / "data"

# The bar the bank builder enforces. Kept in sync deliberately: if someone
# lowers the generator's floor, this test is where that shows up.
MIN_BANK_CONF = 56
MIN_TOP_CONF = 62
MIN_BANK_SIZE = 40
TOP_N = 5


def tenant_dirs():
    if not DATA.is_dir():
        return []
    return sorted(d for d in DATA.iterdir() if (d / "tenant.json").is_file())


def test_tenants_were_actually_discovered():
    """Guard against this whole module passing vacuously.

    Every other test here is parametrised over tenant_dirs(). If site/data is
    missing — wrong CI step order, a build that failed quietly — that list is
    empty, pytest collects nothing, and the suite reports success while checking
    nothing at all. This project has shipped that exact bug before: a page-weight
    test that had never once run because the step producing its input was not
    wired into CI. One unparametrised test makes the absence loud.
    """
    assert DATA.is_dir(), (
        f"{DATA} does not exist — run `python -m pipeline.build_site` before pytest"
    )
    found = tenant_dirs()
    assert len(found) >= 2, (
        f"only {len(found)} tenant(s) under {DATA}; the bank tests would be "
        "near-vacuous. Check build order: build_tenants -> build_site -> build_bank"
    )


def load_questions(d):
    p = d / "questions.json"
    if not p.is_file():
        pytest.fail(
            f"{d.name}/questions.json missing — the demo would silently fall back "
            "to unmeasured pack questions. Run: node eval/build_bank.mjs ."
        )
    return json.loads(p.read_text())


@pytest.mark.parametrize("d", tenant_dirs(), ids=lambda d: d.name)
def test_five_measured_questions_on_screen(d):
    q = load_questions(d)
    assert len(q["top"]) == TOP_N, f"{d.name}: expected {TOP_N} chips, got {len(q['top'])}"
    assert len(set(q["top"])) == TOP_N, f"{d.name}: duplicate chip questions"


@pytest.mark.parametrize("d", tenant_dirs(), ids=lambda d: d.name)
def test_on_screen_questions_clear_the_bar(d):
    q = load_questions(d)
    conf = q["meta"]["topConf"]
    assert min(conf) >= MIN_TOP_CONF, (
        f"{d.name}: a suggestion chip scores {min(conf)}%, below the {MIN_TOP_CONF}% "
        f"bar. All five: {conf}"
    )


@pytest.mark.parametrize("d", tenant_dirs(), ids=lambda d: d.name)
def test_chips_span_distinct_question_shapes(d):
    """Five phrasings of one shape demonstrates nothing about comprehension."""
    q = load_questions(d)
    fams = q["meta"]["topFamilies"]
    assert len(set(fams)) >= 3, f"{d.name}: chips collapse to {set(fams)}"


@pytest.mark.parametrize("d", tenant_dirs(), ids=lambda d: d.name)
def test_typeahead_bank_is_substantial_and_above_bar(d):
    q = load_questions(d)
    assert len(q["bank"]) >= MIN_BANK_SIZE, (
        f"{d.name}: bank has {len(q['bank'])} questions; the typeahead needs enough "
        "coverage that a reviewer typing freely lands on a tested question"
    )
    assert q["meta"]["minConf"] >= MIN_BANK_CONF, (
        f"{d.name}: bank floor is {q['meta']['minConf']}%, below {MIN_BANK_CONF}%"
    )


@pytest.mark.parametrize("d", tenant_dirs(), ids=lambda d: d.name)
def test_chips_are_drawn_from_the_bank(d):
    """The typeahead and the chips must agree; a chip absent from the bank means
    typing its own text would not suggest it."""
    q = load_questions(d)
    missing = [x for x in q["top"] if x not in q["bank"]]
    assert not missing, f"{d.name}: chips missing from bank: {missing}"


@pytest.mark.parametrize("d", tenant_dirs(), ids=lambda d: d.name)
def test_questions_read_as_questions(d):
    """Guards the generator's templating: a stray separator or an empty slot
    produces text that scores fine and reads as broken."""
    q = load_questions(d)
    for text in q["bank"]:
        assert text.endswith("?"), f"{d.name}: not a question — {text!r}"
        assert "  " not in text, f"{d.name}: doubled space — {text!r}"
        assert " ?" not in text, f"{d.name}: empty template slot — {text!r}"
        assert 12 < len(text) < 130, f"{d.name}: implausible length — {text!r}"
