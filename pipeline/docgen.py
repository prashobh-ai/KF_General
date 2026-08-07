"""Turn a domain pack into a corpus of controlled documents.

Design note
-----------
The generator writes Markdown with a YAML-ish control block at the top. That
choice is deliberate: the downstream index needs stable document → section →
paragraph addressing so a citation can point at "document X, section Y,
paragraph 3", and Markdown headings give that for free without a parser.

Each document is assembled as:

    control header block      (real document-management apparatus)
    body sections             (canonical order from the pack's DocType)
    definitions and acronyms  (drawn from the pack lexicon)
    references                (cross-links to sibling documents — graph edges)
    revision history          (a plausible amendment trail)
    approval                  (role titles from the pack)

The cross-reference section is what makes the corpus a *fabric* rather than a
pile: it is the source of most graph edges, and it is what lets the knowledge
health scorer detect orphan documents that nothing else cites.
"""

from __future__ import annotations

import datetime as dt
import random
import re
import textwrap

from .distributions import DateSpread, lognormal_int, weighted_choice, zipf_weights
from .identifiers import IdFactory
from .packs import CLASSIFICATIONS, RETENTION_RULES, Pack
from .world import build_world
from .packs.base import DocType

# Person-name components. Deliberately drawn from a small invented set so no
# generated name collides with a recognisable public figure.
FORENAMES = (
    "Adaeze", "Bertil", "Carys", "Dmitri", "Elowen", "Faruq", "Greta",
    "Hollis", "Imogen", "Janek", "Kirra", "Lorcan", "Mireille", "Nikolai",
    "Oksana", "Perrin", "Quilla", "Rasmus", "Saoirse", "Tobias", "Ulla",
    "Verity", "Wren", "Xiomara", "Yannick", "Zofia", "Anouk", "Bramwell",
    "Cassian", "Delphine", "Eamon", "Fenella", "Gideon", "Hana", "Isolde",
)
SURNAMES = (
    "Abernathy", "Blackwood", "Castellan", "Dunmore", "Ellingham",
    "Fairweather", "Grimsby", "Hawthorne", "Ingersoll", "Jerrold",
    "Kingsley", "Lammermoor", "Marchetti", "Northcote", "Ostrander",
    "Pemberton", "Quennell", "Ravensworth", "Sundqvist", "Thackeray",
    "Underhill", "Vasquez", "Whitlock", "Yarrow", "Zeller", "Ashcombe",
    "Braithwaite", "Corvino", "Drummond", "Eastbrook", "Fitzgerald",
)


# Share of non-stub documents that carry extraction debris, by domain.
# Table-heavy engineering and transactional corpora sit high; narrative
# professional-services corpora sit low.
LAYOUT_DENSITY = {
    "q-aerotech": 0.62,        # task cards, capability lists, parts tables
    "q-assure-claims": 0.58,   # EDI segment maps, code tables
    "q-retail": 0.54,          # routing guides, EDI specs, chargeback schedules
    "q-airlines": 0.46,        # MEL items, load sheets
    "q-devicelab": 0.42,       # V&V protocols, traceability matrices
    "q-quality": 0.40,         # RTMs, defect matrices
    "q-pharma": 0.36,          # batch records, but heavy narrative investigation
    "q-cruise": 0.30,          # procedures with occasional logs
    "q-health": 0.26,          # policies and pathways, largely narrative
    "q-bank": 0.22,            # credit memoranda, policy prose
    "q-assurance": 0.16,       # planning memoranda and workpaper narrative
}


def _person(rng: random.Random) -> tuple[str, str]:
    return rng.choice(FORENAMES), rng.choice(SURNAMES)


def _slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", s)[:70]


class DocumentBuilder:
    """Builds one tenant's corpus.

    Seeded from the tenant slug so the corpus is byte-reproducible: rebuilding
    must not reshuffle every identifier, or diffs become useless and any cache
    keyed on content is invalidated for no reason.
    """

    def __init__(self, pack: Pack, seed: int, *, peak_month: int = 6):
        self.pack = pack
        self.rng = random.Random(seed)
        self.ids = IdFactory(seed)
        # A document estate accumulates over years, not one refresh cycle.
        # A three-year window put every median inside the fresh threshold,
        # so Currency reported 100 regardless of the corpus.
        self.dates = DateSpread(self.rng, years=7, peak_month=peak_month)
        self.people = [
            (f, s, role)
            for role in pack.roles
            for f, s in [_person(self.rng)]
        ]
        self._registry: list[dict] = []
        self._used: set[str] = set()

        # The domain world: concrete instances documents will reference by
        # identifier. Because many documents cite the same instances, the graph
        # gains cross-document structure that metadata alone cannot produce.
        # Layout density varies by industry, and so should extraction debris.
        # A maintenance task card or an EDI companion guide is mostly tables;
        # an audit planning memorandum is mostly prose. Applying one debris
        # rate everywhere made Readability read the same on every tenant.
        self.layout_density = LAYOUT_DENSITY.get(pack.slug, 0.34)

        self.world, self.world_rels = build_world(pack, seed)
        self.world_kinds = list(self.world.keys())

    # -- helpers -----------------------------------------------------------

    def _owner(self) -> tuple[str, str, str]:
        return self.rng.choice(self.people)

    def _classification(self) -> str:
        labels = [c for c, _ in CLASSIFICATIONS]
        weights = [w for _, w in CLASSIFICATIONS]
        return self.rng.choices(labels, weights=weights, k=1)[0]

    def _codes(self, key: str, n: int) -> list[tuple[str, str]]:
        try:
            cs = self.pack.code_system(key)
        except KeyError:
            return []
        if not cs.codes:
            return []
        weights = zipf_weights(len(cs.codes))
        picked, seen = [], set()
        for _ in range(n * 3):
            c = self.rng.choices(cs.codes, weights=weights, k=1)[0]
            if c[0] not in seen:
                seen.add(c[0])
                picked.append(c)
            if len(picked) == n:
                break
        return picked

    # -- prose -------------------------------------------------------------

    # Section names across eleven domains vary enormously, but they cluster
    # into a small number of rhetorical modes. Matching on keywords lets one
    # sentence bank serve every pack while still giving each section its own
    # voice — a "Scope" section that reads like a "Reporting" section is the
    # fastest way to make generated prose feel hollow.
    _MODES = (
        ("purpose", ("purpose", "scope", "statement", "objective", "background",
                     "intent", "context", "policy statement")),
        ("applicability", ("applicab", "effectivity", "eligib", "inclusion",
                           "exclusion", "identification", "identity", "profile")),
        ("criteria", ("criteria", "requirement", "threshold", "limit",
                      "acceptance", "standard", "specification", "control")),
        ("procedure", ("procedure", "step", "instruction", "method",
                       "execution", "process", "operation", "administration")),
        ("assessment", ("assessment", "analysis", "evaluation", "review",
                        "investigation", "finding", "risk", "impact",
                        "determination", "rationale")),
        ("evidence", ("record", "documentation", "evidence", "traceab",
                      "sign-off", "log", "retention")),
        ("reporting", ("report", "escalat", "notification", "communication",
                       "disposition", "resolution", "closure")),
        ("governance", ("responsib", "authority", "role", "approval",
                        "delegation", "monitoring", "compliance", "verification",
                        "conclusion")),
    )

    def _mode(self, section: str) -> str:
        low = section.lower()
        for mode, keys in self._MODES:
            if any(k in low for k in keys):
                return mode
        return "procedure"

    def _paragraph(self, dtype: DocType, section: str, subject: str,
                   ctx: dict) -> str:
        """Generate one section paragraph in the section's rhetorical mode.

        Every sentence carries at least one concrete anchor — a role, a system,
        a code, an interval, a threshold. Generic assurance prose ("this is
        important and must be followed") is what makes synthetic documents
        read as filler, and it also gives the retrieval index nothing to
        discriminate on.
        """
        p = self.pack
        rng = self.rng
        unit, site = ctx["unit"], ctx["site"]
        mode = self._mode(section)
        lex = rng.sample(list(p.lexicon), k=min(4, len(p.lexicon)))
        role = rng.choice(p.roles)
        role2 = rng.choice(p.roles)
        wf = rng.choice(p.workflows) if p.workflows else None
        state = rng.choice(wf.states) if wf else "Approved"
        nxt = rng.choice(wf.terminal or wf.states) if wf else "Closed"
        days = rng.choice((2, 3, 5, 7, 10, 14, 20, 30, 45, 60, 90))
        hours = rng.choice((4, 8, 12, 24, 48, 72))
        pct = rng.choice((80, 85, 90, 95, 97, 98, 99))
        n = rng.choice((2, 3, 5, 8, 10, 12))

        code_txt = ""
        if p.code_systems:
            cs = rng.choice(p.code_systems)
            if cs.codes:
                c, m = rng.choice(cs.codes)
                code_txt = f"{cs.name} `{c}` ({m.lower()})"

        banks = {
            "purpose": [
                f"This document establishes how {unit} governs {subject} across "
                f"{p.tenant} operations, and supersedes any local practice that "
                f"conflicts with it.",
                f"The scope covers {subject} from initiation through to "
                f"'{nxt}', including work performed at {site} and by delegated "
                f"third parties acting on behalf of {unit}.",
                f"Requirements derive from {dtype.authority}; where that "
                f"authority is silent, the more restrictive of local practice "
                f"and this document applies.",
                f"Out of scope: activities managed under a separate authority, "
                f"and any {subject} performed outside {p.tenant}'s certificate "
                f"or contractual remit.",
                f"The intent is to make {subject} reconstructable after the "
                f"fact. A reviewer who was not present must be able to reach "
                f"the same conclusion from the record alone.",
            ],
            "applicability": [
                f"This applies to all personnel in {unit} and to any contracted "
                f"resource performing {subject} at {site}.",
                f"Applicability is determined by configuration and effectivity, "
                f"not by convenience of scheduling; where effectivity is "
                f"ambiguous, {role} makes the determination and records the "
                f"basis.",
                f"Items entering the process in state '{state}' are in scope. "
                f"Items already past '{nxt}' are handled under the change "
                f"process instead.",
                f"Where {code_txt or 'the governing code'} applies, the more "
                f"specific classification takes precedence over the general "
                f"category.",
                f"Exclusions must be documented at the point of decision. An "
                f"undocumented exclusion is treated as an unassessed item.",
            ],
            "criteria": [
                f"Acceptance requires all mandatory checks to pass with no "
                f"open findings above the agreed threshold, verified within "
                f"{hours} hours of completion.",
                f"The tolerance band is fixed at issue and may not be widened "
                f"during execution; a widening requires a new revision approved "
                f"by {role}.",
                f"Where measurement is subjective, at least {n} independent "
                f"observations are required before a conclusion is recorded.",
                f"Performance is considered acceptable at {pct}% or above "
                f"against the stated measure, assessed on a rolling "
                f"{days}-day window rather than a single observation.",
                f"Criteria referencing {code_txt or 'controlled codes'} are "
                f"restated in full here so the reader does not have to hold two "
                f"documents open to apply them.",
                f"A borderline result is not a pass. If the outcome sits within "
                f"measurement uncertainty of the limit, it is escalated to "
                f"{role2} for adjudication.",
            ],
            "procedure": [
                f"Confirm the item is in state '{state}' and that prerequisites "
                f"are complete before starting; starting out of sequence "
                f"invalidates the downstream verification.",
                f"Record each step in {dtype.system} as it is completed rather "
                f"than retrospectively at the end of the task.",
                f"Where the procedure calls for {lex[0]}, use the current "
                f"revision only. Working from a cached or printed copy is not "
                f"permitted once a newer revision is effective.",
                f"On completion, hand the item to {role} for independent check. "
                f"The person who performed the work may not also verify it.",
                f"If the condition found differs from the condition expected, "
                f"stop and raise the deviation before proceeding — do not "
                f"adapt the procedure in place.",
                f"Interruptions longer than {hours} hours require the "
                f"prerequisite checks to be repeated before work resumes.",
            ],
            "assessment": [
                f"Assessment considers likelihood and consequence together; a "
                f"low-likelihood outcome with severe consequence is not "
                f"downgraded on frequency alone.",
                f"The analysis must identify what would have to be true for the "
                f"conclusion to be wrong, and state whether that condition was "
                f"tested or assumed.",
                f"Contributing factors are recorded separately from the "
                f"immediate cause. Recording only the immediate cause produces "
                f"corrective actions that do not prevent recurrence.",
                f"Where {subject} interacts with adjacent processes, the "
                f"assessment extends to those interfaces rather than stopping "
                f"at the {unit} boundary.",
                f"Quantitative inputs are traced to their source system. An "
                f"input that cannot be traced is treated as an assumption and "
                f"declared as such.",
                f"{role} reviews the assessment for proportionality: over-"
                f"assessment consumes capacity that a genuinely higher risk "
                f"elsewhere needs.",
            ],
            "evidence": [
                f"Records are created contemporaneously, attributable to a "
                f"named individual, legible, original and accurate.",
                f"Each entry carries the identifier of the item, the date, the "
                f"performing individual and the outcome. Entries missing any of "
                f"these are incomplete regardless of the work performed.",
                f"Corrections are made by single strike-through with initials "
                f"and date; the original entry must remain readable. "
                f"Obliteration is a data integrity finding.",
                f"Electronic records in {dtype.system} carry an audit trail "
                f"that cannot be disabled by the record's author.",
                f"Retention runs from the date the record becomes inactive, "
                f"not from the date it was created.",
                f"The evidence set must be sufficient for {role2} to reach the "
                f"same conclusion without interviewing the originator.",
            ],
            "reporting": [
                f"Report within {hours} hours of discovery. The reporting clock "
                f"starts at discovery, not at confirmation.",
                f"Escalation to {role} is mandatory where the condition affects "
                f"more than one item, one site, or one period.",
                f"Interim reports are issued at {days}-day intervals until the "
                f"item reaches '{nxt}'.",
                f"Communication to affected parties states what is known, what "
                f"is not yet known, and when the next update will arrive.",
                f"Under-reporting is treated more seriously than over-"
                f"reporting: a report later found to be unnecessary carries no "
                f"sanction.",
                f"Closure requires evidence that the action worked, not "
                f"evidence that the action was taken.",
            ],
            "governance": [
                f"{role} is accountable for this document and for the "
                f"competence of personnel applying it within {unit}.",
                f"Approval authority may not be delegated below the level "
                f"stated here. Delegation for a defined absence must be "
                f"recorded in advance.",
                f"Compliance is monitored through periodic sampling rather than "
                f"full census; the sample is drawn at least every "
                f"{days} days and is not selected by the assessed party.",
                f"Findings are tracked to closure with a named owner and a "
                f"target date. A finding without both is not a finding, it is "
                f"an observation.",
                f"This document is reviewed on change of {dtype.authority}, on "
                f"organisational change affecting {unit}, and on schedule — "
                f"whichever falls first.",
                f"Independence is preserved: verification is performed by "
                f"someone outside the reporting line that produced the work.",
            ],
        }

        # Deduplicate across the whole document. Without this, the same
        # sentence surfaces in adjacent paragraphs and the reader immediately
        # sees the template behind the prose.
        # Topical sentences. The mode banks above give a section its rhetorical
        # shape but are deliberately domain-agnostic, which left the corpus
        # lexically flat: section headings like "Barcode Verification" and
        # "High-Alert Medications" never reached the prose at all, so no
        # retriever could find them and LSA had no real co-occurrence structure
        # to learn. These sentences put the section's own subject matter into
        # the text, which is also how a real procedure reads.
        topic = section.rstrip('.').strip()
        topic_l = topic[0].lower() + topic[1:] if topic else topic
        topical = [
            f"{topic} for {subject} is performed by {unit} and evidenced in "
            f"{dtype.system}.",
            f"This section sets the {topic_l} requirements that apply to "
            f"{subject} at {site}.",
            f"{role} confirms that {topic_l} has been completed before the item "
            f"advances beyond '{state}'.",
            f"Where {topic_l} cannot be completed as specified, the shortfall is "
            f"recorded against {subject} and escalated within {hours} hours.",
            f"{topic} is assessed against {dtype.authority} and against the "
            f"{lex[0]} requirements held by {unit}.",
        ]
        if code_txt:
            topical += [
                f"{topic} references {code_txt}, which governs how {subject} is "
                f"classified in {dtype.system}.",
                f"Records of {topic_l} carry {code_txt} so that {subject} can be "
                f"reconciled across {unit} reporting.",
            ]

        pool = [s for s in banks[mode] if s not in self._used]
        if len(pool) < 2:
            pool = banks[mode]
        k = rng.randint(1, min(3, len(pool)))
        picked = rng.sample(pool, k=k)

        # Lead with a topical sentence most of the time. A section that opens by
        # naming its own subject is both more realistic and far more retrievable.
        tpool = [t for t in topical if t not in self._used]
        if tpool:
            lead = rng.sample(tpool, k=min(rng.randint(1, 2), len(tpool)))
            picked = lead + picked

        self._used.update(picked)
        return " ".join(picked)

    def _debris(self, dtype: DocType, subject: str, eff) -> str:
        """A block of the text PDF extraction actually produces."""
        rng = self.rng
        kind = rng.choice(("row", "header", "equation", "caption"))
        if kind == "row":
            return " ".join(
                f"{rng.choice(('A','B','C','D'))}{rng.randint(1,99):02d}   "
                f"{rng.randint(1,9999)}   {rng.randint(0,99)}.{rng.randint(0,9)}   "
                f"{rng.choice(('PASS','FAIL','N/A','OPEN'))}"
                for _ in range(rng.randint(3, 5)))
        if kind == "header":
            return (f"{dtype.abbrev} Rev {self.ids.version()} "
                    f"Effective {eff.isoformat()} Page {rng.randint(2, 40)} of "
                    f"{rng.randint(40, 90)} Uncontrolled when printed")
        if kind == "equation":
            return (f"C = {rng.randint(2, 40)} x {rng.randint(2, 12)} / "
                    f"{rng.randint(2, 8)} \u00b1 {rng.randint(1, 9)}.{rng.randint(0,9)} "
                    f"where n >= {rng.randint(3, 30)}")
        return (f"Figure {rng.randint(1, 12)}. {subject.title()} "
                f"{rng.choice(('schematic','flow','matrix','trend'))} "
                f"{rng.randint(1, 9)} {rng.randint(10, 99)} {rng.randint(100, 999)}")

    def _code_table(self, keys: tuple[str, ...]) -> str:
        for key in keys:
            picked = self._codes(key, self.rng.randint(3, 6))
            if not picked:
                continue
            cs = self.pack.code_system(key)
            rows = "\n".join(f"| `{c}` | {m} |" for c, m in picked)
            return (
                f"\n\n**{cs.name}** — {cs.authority}, format `{cs.fmt}`.\n\n"
                f"| Code | Meaning |\n| --- | --- |\n{rows}\n"
            )
        return ""

    # -- document assembly -------------------------------------------------

    def build_document(self, dtype: DocType, unit: str, index: int) -> dict:
        p = self.pack
        rng = self.rng
        eff = self.dates.draw()

        # Date provenance. A real document estate does not know when every file
        # is from: some carry a signed revision stamp, some only a filesystem
        # timestamp, and some nothing at all. Generating every document with a
        # perfect effective date would make Traceability read 100 on every
        # tenant, which measures the generator rather than the corpus.
        roll = rng.random()
        if roll < 0.62:
            date_source, authoritative = "approved_revision_stamp", True
        elif roll < 0.78:
            date_source, authoritative = "system_of_record_timestamp", False
        elif roll < 0.88:
            date_source, authoritative = "date_in_body_text", False
        else:
            date_source, authoritative = "unknown", False
        review = eff + dt.timedelta(days=rng.choice([365, 545, 730]))
        subject = rng.choice(p.subjects)

        # Roughly one document in six is a stub — a notice, a single-section
        # memo, a placeholder someone never finished. Real estates are full of
        # them, and they are precisely what Depth exists to surface.
        thin = rng.random() < 0.17
        site = rng.choice(p.sites)
        year = eff.year
        doc_id = f"{dtype.id_grammar}-{year}-{index:04d}"
        of, os_, orole = self._owner()
        title = f"{dtype.name} — {subject.title()}"
        ctx = {"unit": unit, "site": site, "subject": subject}
        self._used: set[str] = set()
        debris_heavy = (not thin) and rng.random() < self.layout_density

        # Pick the instances this document is about. Two or three kinds, a
        # handful each — enough that documents overlap heavily on entities
        # without any single document becoming a directory listing.
        cited: list = []
        if self.world_kinds:
            kinds = rng.sample(self.world_kinds,
                               k=min(2, len(self.world_kinds)))
            for k in kinds:
                pool = self.world.get(k) or []
                if pool:
                    cited += rng.sample(pool, k=min(rng.randint(1, 2), len(pool)))
        ctx["cited"] = cited

        lines: list[str] = []
        lines.append(f"# {title}")
        lines.append("")

        # -- control header ------------------------------------------------
        classification = self._classification()
        rev = self.ids.version()
        lines.append("| Control field | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Document ID | `{doc_id}` |")
        lines.append(f"| Document type | {dtype.name} ({dtype.abbrev}) |")
        lines.append(f"| Revision | {rev} |")
        lines.append(f"| Status | Effective |")
        if date_source == "unknown":
            lines.append("| Effective date | Not recorded |")
        else:
            lines.append(f"| Effective date | {eff.isoformat()} "
                         f"({date_source.replace('_', ' ')}) |")
        lines.append(f"| Next review | {review.isoformat()} |")
        lines.append(f"| Owning unit | {unit} |")
        lines.append(f"| Document owner | {of} {os_}, {orole} |")
        lines.append(f"| Governing authority | {dtype.authority} |")
        lines.append(f"| System of record | {dtype.system} |")
        lines.append(f"| Classification | {classification} |")
        lines.append(f"| Retention | {rng.choice(RETENTION_RULES)} |")
        lines.append("")

        # -- body sections --------------------------------------------------
        code_keys = tuple(c.key for c in p.code_systems)
        code_placed = False
        entities: set[str] = {unit, site, dtype.system, dtype.authority}

        sections = dtype.sections
        if thin:
            sections = dtype.sections[:rng.randint(1, 2)]

        for i, section in enumerate(sections, start=1):
            lines.append(f"## {i}. {section}")
            lines.append("")
            n_paras = 1 if thin else rng.randint(1, 3)
            for _ in range(n_paras):
                lines.append(textwrap.fill(
                    self._paragraph(dtype, section, subject, ctx), 96))
                lines.append("")
            # Extraction debris. These corpora stand in for PDF-derived
            # estates, where flattened table rows and running headers land in
            # the text stream and get indexed as if they were sentences. Some
            # documents are layout-heavy and some are not, which is what makes
            # Readability vary rather than sit at a constant.
            if debris_heavy and rng.random() < 0.55:
                # Volume scales with layout density as well as frequency. A
                # table-heavy document does not carry one stray row, it carries
                # a whole flattened table — and it is the volume, not the
                # incidence, that determines how much of the index is unusable.
                for _ in range(1 + int(self.layout_density * 4)):
                    lines.append(self._debris(dtype, subject, eff))
                    lines.append("")

            # Place the controlled-vocabulary table once, in a middle section,
            # where a real document would put it rather than in the intro.
            if not code_placed and i >= 2 and rng.random() < 0.55:
                tbl = self._code_table(code_keys)
                if tbl:
                    lines.append(tbl.strip())
                    lines.append("")
                    code_placed = True

        # -- definitions -----------------------------------------------------
        lines.append(f"## {len(sections) + 1}. Definitions and Acronyms")
        lines.append("")
        terms = rng.sample(list(p.lexicon), k=min(5, len(p.lexicon)))
        for t in terms:
            lines.append(f"- **{t}** — as used in {p.industry.lower()} practice "
                         f"within {p.tenant}.")
        lines.append("")

        # -- entities in scope ------------------------------------------------
        # A real controlled document names what it applies to. This block is
        # also the machine-readable anchor the graph builder reads, so entity
        # edges trace to a specific table row rather than to fuzzy text matching.
        if cited:
            lines.append(f"## {len(sections) + 2}. Entities in Scope")
            lines.append("")
            lines.append("| Identifier | Type | Detail |")
            lines.append("| --- | --- | --- |")
            for inst in cited:
                detail = ", ".join(f"{k}: {v}" for k, v in inst.attrs.items()) or "—"
                lines.append(f"| `{inst.ref}` | {inst.kind} | {detail} |")
            lines.append("")

        # -- references (graph edges) ----------------------------------------
        lines.append(f"## {len(sections) + 3}. References")
        lines.append("")
        refs: list[str] = []
        pool = [r for r in self._registry if r["id"] != doc_id]
        if pool:
            for r in rng.sample(pool, k=min(rng.randint(1, 4), len(pool))):
                refs.append(r["id"])
                lines.append(f"- `{r['id']}` — {r['title']}")
        lines.append(f"- {dtype.authority}")
        lines.append("")

        # -- revision history -------------------------------------------------
        lines.append(f"## {len(sections) + 4}. Revision History")
        lines.append("")
        lines.append("| Revision | Date | Author | Description | Approver |")
        lines.append("| --- | --- | --- | --- | --- |")
        n_rev = rng.randint(2, 4)
        d = eff
        for k in range(n_rev):
            af, as_, _ = self._owner()
            pf, ps_, prole = self._owner()
            desc = rng.choice([
                "Initial issue.",
                f"Clarified {subject} acceptance criteria.",
                f"Added {rng.choice(p.lexicon)} reporting requirement.",
                "Aligned terminology with governing authority update.",
                "Revised escalation thresholds following internal review.",
                f"Incorporated findings from {site} assessment.",
            ])
            lines.append(
                f"| {round(float(rev.split('.')[0]) - k, 1)} | {d.isoformat()} "
                f"| {af} {as_} | {desc} | {pf} {ps_}, {prole} |"
            )
            d = d - dt.timedelta(days=rng.randint(120, 420))
        lines.append("")

        # -- approval ---------------------------------------------------------
        lines.append(f"## {len(sections) + 5}. Approval")
        lines.append("")
        lines.append("| Role | Name | Date |")
        lines.append("| --- | --- | --- |")
        for role in rng.sample(list(p.roles), k=min(3, len(p.roles))):
            f, s = _person(rng)
            lines.append(f"| {role} | {f} {s} | {eff.isoformat()} |")
        lines.append("")
        lines.append(f"> Synthetic document. {p.tenant} is a fictional "
                     f"organisation; all names, identifiers and events are "
                     f"invented. Standards and code systems are cited as public "
                     f"reference only.")
        lines.append("")

        body = "\n".join(lines)
        record = {
            "id": doc_id,
            "title": title,
            "type": dtype.name,
            "type_key": dtype.key,
            "abbrev": dtype.abbrev,
            "unit": unit,
            "site": site,
            "subject": subject,
            "authority": dtype.authority,
            "system": dtype.system,
            "owner": f"{of} {os_}",
            "owner_role": orole,
            "classification": classification,
            "revision": rev,
            "effective": eff.isoformat() if date_source != "unknown" else None,
            "date_source": date_source,
            "authoritative_date": authoritative,
            "thin": thin,
            "review": review.isoformat(),
            "sections": len(sections),
            "refs": refs,
            "entities": sorted(entities),
            "instances": [i.id for i in cited],
            "filename": f"{doc_id}_{_slugify(subject)}.md",
            "words": len(body.split()),
            "body": body,
        }
        self._registry.append(record)
        return record

    def build_corpus(self, target: int) -> list[dict]:
        p = self.pack
        rng = self.rng
        weights = [d.weight for d in p.doc_types]
        docs: list[dict] = []
        counter: dict[str, int] = {}
        for _ in range(target):
            dtype = rng.choices(p.doc_types, weights=weights, k=1)[0]
            unit = rng.choice(p.units)
            counter[dtype.id_grammar] = counter.get(dtype.id_grammar, 0) + 1
            docs.append(self.build_document(dtype, unit,
                                            counter[dtype.id_grammar]))
        return docs
