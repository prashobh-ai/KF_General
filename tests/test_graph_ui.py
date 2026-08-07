"""End-to-end verification of the graph highlight and the typeahead.

These assert the behaviours that were MISSING, not merely that the page loads:

  * the camera reframes onto the activated subgraph  (fitTo() existed and was
    never called from anywhere — the single largest reason this graph did not
    feel like the Nova reference)
  * unrelated edges are dimmed rather than deleted   (needs an RGBA colour
    buffer; with itemSize 3 the only alpha control is material.opacity, one
    value for every edge, so deletion was the only approximation available)
  * three node tiers are populated on every answer
  * the typeahead honours the full ARIA combobox keyboard contract

None of that is observable from the DOM, so these reach into the live Galaxy
instance through the window.__kfGalaxy hook set in app.js.
"""
import contextlib
import functools
import http.server
import pathlib
import socket
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DEMO = "demo/q-airlines/"

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed"
)

pytestmark = pytest.mark.skipif(
    not (SITE / "data" / "q-airlines" / "questions.json").is_file(),
    reason="site not built — run build_site then eval/build_bank.mjs",
)

# Google Fonts is the only third-party request on the page. A sandboxed or
# offline runner cannot validate its cert chain, and that says nothing about
# this code, so first-party errors are what get asserted on.
THIRD_PARTY = ("fonts.googleapis.com", "fonts.gstatic.com",
               "ERR_CERT_", "ERR_INTERNET_DISCONNECTED", "ERR_NAME_NOT_RESOLVED")


@pytest.fixture(scope="module")
def server():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(SITE))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/{DEMO}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def page(server):
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(server, wait_until="networkidle")
        pg.wait_for_function("() => !!window.__kfGalaxy", timeout=15000)
        pg.wait_for_timeout(1500)
        pg.errs = errs
        yield pg
        browser.close()


@pytest.fixture()
def activated(page):
    """Ask the first suggestion and wait for the graph to react.

    The click is dispatched through JS rather than page.click(). ask() calls
    scrollIntoView({behavior:'smooth'}), so for roughly a second afterwards the
    chip is still travelling and Playwright's actionability check ("element is
    not stable") times out — which makes every activation test after the first
    one fail for a reason that has nothing to do with the graph. Real
    clickability is covered separately by test_suggestion_chip_is_clickable.
    """
    page.evaluate("() => window.__kfGalaxy.clearHighlight()")
    page.wait_for_timeout(150)
    page.eval_on_selector("#suggest .chip", "el => el.click()")
    # ask() defers 340ms before retrieval, and retrieval is not instant, so the
    # activation lands at a time that varies run to run. Wait on the condition
    # rather than a fixed duration — a fixed wait makes this flaky in exactly
    # the way that trains people to ignore a red build.
    page.wait_for_function(
        "() => window.__kfGalaxy.active && window.__kfGalaxy.active.size > 0",
        timeout=10000)
    page.wait_for_timeout(900)   # let the easing settle
    return page


# --- page health ------------------------------------------------------------

def test_no_first_party_console_errors(page):
    own = [e for e in page.errs if not any(t in e for t in THIRD_PARTY)]
    assert not own, f"first-party console errors: {own[:3]}"


# --- question bank on screen ------------------------------------------------

def test_five_suggestion_chips_render(page):
    chips = page.eval_on_selector_all("#suggest .chip", "els => els.length")
    assert chips == 5, f"expected 5 chips, got {chips}"


def test_typeahead_bank_is_loaded(page):
    assert page.evaluate("() => (window.__kfBank || []).length") >= 40


# --- typeahead --------------------------------------------------------------

def test_input_is_an_aria_combobox(page):
    assert page.get_attribute("#q", "role") == "combobox"
    assert page.get_attribute("#q", "aria-autocomplete") == "list"
    assert page.get_attribute("#q", "aria-controls")


def test_arrow_down_on_empty_box_offers_the_bank(page):
    page.fill("#q", "")
    page.click("#q")
    page.keyboard.press("ArrowDown")
    page.wait_for_selector(".ta-list .ta-item", timeout=3000)
    assert page.eval_on_selector_all(".ta-list .ta-item", "e => e.length") > 0
    assert page.get_attribute("#q", "aria-expanded") == "true"
    page.keyboard.press("Escape")


def test_typing_filters_and_marks_the_match(page):
    page.fill("#q", "")
    page.type("#q", "dispatch", delay=20)
    page.wait_for_selector(".ta-list .ta-item", timeout=3000)
    assert page.eval_on_selector_all(".ta-list .ta-item", "e => e.length") > 0
    assert page.eval_on_selector_all(".ta-list mark", "e => e.length") > 0
    page.keyboard.press("Escape")


def test_subsequence_matching_tolerates_dropped_vowels(page):
    """A reviewer on a phone will not reproduce our exact phrasing."""
    page.fill("#q", "")
    page.type("#q", "dsptch rls", delay=20)
    page.wait_for_selector(".ta-list .ta-item", timeout=3000)
    assert page.eval_on_selector_all(".ta-list .ta-item", "e => e.length") > 0
    page.keyboard.press("Escape")


def test_keyboard_selection_sets_activedescendant(page):
    page.fill("#q", "")
    page.type("#q", "what", delay=20)
    page.wait_for_selector(".ta-list .ta-item", timeout=3000)
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(150)
    assert page.eval_on_selector_all(".ta-list .ta-item.is-active", "e => e.length") == 1
    assert page.get_attribute("#q", "aria-activedescendant")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    assert page.get_attribute("#q", "aria-expanded") == "false"


# --- graph activation -------------------------------------------------------

def test_all_three_node_tiers_are_populated(activated):
    tiers = activated.evaluate("""() => {
      const c = {0: 0, 1: 0, 2: 0};
      window.__kfGalaxy.nodes.forEach(n => c[n.tier]++);
      return c;
    }""")
    assert tiers["2"] > 0, f"nothing activated: {tiers}"
    assert tiers["1"] > 0, f"no neighbours lit: {tiers}"
    assert tiers["0"] > 0, f"nothing dimmed: {tiers}"


def test_camera_reframes_onto_the_lit_subgraph(activated):
    """fitTo() was dead code — never called from anywhere. Regression guard."""
    cam = activated.evaluate("""() => {
      const g = window.__kfGalaxy;
      return {target: g.flyTarget, dist: g.flyDist};
    }""")
    assert cam["target"] is not None, "camera never framed the activation"
    assert cam["dist"] and cam["dist"] > 0


def test_suggestion_chip_is_clickable(page):
    """A genuine user click, with all of Playwright's actionability checks — so
    the JS-dispatched click used elsewhere cannot hide a chip that is covered,
    disabled or zero-size."""
    page.evaluate("() => { window.__kfGalaxy.clearHighlight(); window.scrollTo(0, 0); }")
    page.wait_for_timeout(700)          # let any in-flight smooth scroll finish
    page.click("#suggest .chip", timeout=8000)
    page.wait_for_function(
        "() => window.__kfGalaxy.active && window.__kfGalaxy.active.size > 0",
        timeout=10000)


def test_activation_flash_fires(page):
    page.evaluate("() => window.__kfGalaxy.clearHighlight()")
    page.wait_for_timeout(200)
    page.eval_on_selector("#suggest .chip", "el => el.click()")
    page.wait_for_selector(".stage.graph-activated", timeout=8000)


def test_edge_buffer_is_rgba(activated):
    """itemSize 3 makes per-edge alpha impossible — the whole reason unrelated
    edges used to be teleported off-screen instead of dimmed."""
    size = activated.evaluate(
        "() => window.__kfGalaxy.lines.geometry.attributes.color.itemSize")
    assert size == 4, f"colour attribute itemSize is {size}, expected 4"


def test_unrelated_edges_are_dimmed_not_deleted(activated):
    parked = activated.evaluate("""() => {
      const pos = window.__kfGalaxy.lines.geometry.attributes.position.array;
      let n = 0;
      for (let i = 0; i < pos.length; i += 6) if (pos[i] > 9999) n++;
      return n;
    }""")
    assert parked == 0, f"{parked} edges teleported off-canvas instead of dimmed"


def test_per_edge_alpha_spans_the_tiers(activated):
    a = activated.evaluate("""() => {
      const col = window.__kfGalaxy.lines.geometry.attributes.color;
      const out = [];
      for (let i = 0; i < col.count; i += 2) out.push(col.array[i * 4 + 3]);
      return {min: Math.min(...out), max: Math.max(...out)};
    }""")
    assert a["max"] - a["min"] > 0.5, (
        f"alpha spread {a} — active and unrelated edges are not visually separated"
    )
    assert a["min"] <= 0.10, f"unrelated edges not dim enough: {a}"


def test_clear_highlight_resets_every_node(activated):
    activated.evaluate("() => window.__kfGalaxy.clearHighlight()")
    activated.wait_for_timeout(300)
    assert activated.evaluate("""() => window.__kfGalaxy.nodes.every(
      n => n.tier === 0 && n.tSize === 1 && n.tAlpha === 1)""")
