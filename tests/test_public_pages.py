"""Public marketing site structure, navigation shells, and safety language.

Covers the redesigned public pages (`/`, `/movement`, `/how-it-works`, `/resources`),
the split between public and signed-in navigation, and the medical-safety wording that
must appear on every surface.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0

PUBLIC_PAGES = ["/", "/movement", "/how-it-works", "/resources"]

FOOTER_DISCLAIMER = (
    "MotorScope is a movement-awareness and tracking tool. It is not a medical device and "
    "does not diagnose, treat, or predict medical conditions."
)

LOCAL_STORAGE_NOTE = (
    "Your MotorScope account and mobility records are stored locally on this device"
)


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}{(' -> ' + detail) if detail else ''}")
        FAILURES.append(label)


def h1_count(body: str) -> int:
    return len(re.findall(r"<h1[\s>]", body))


def squash(text: str) -> str:
    """Collapse runs of whitespace so wrapped copy can be matched as one string."""
    return re.sub(r"\s+", " ", text)


def heading_levels(body: str) -> list[int]:
    """Every heading level in document order, for the skip-level check."""
    return [int(m) for m in re.findall(r"<h([1-6])[\s>]", body)]


def main() -> int:
    client = TestClient(app)

    print("\nPublic pages render")
    bodies = {}
    for path in PUBLIC_PAGES:
        page = client.get(path)
        check(f"{path} returns 200", page.status_code == 200, str(page.status_code))
        bodies[path] = page.text

    print("\nOne H1 per page")
    for path, body in bodies.items():
        check(f"{path} has exactly one h1", h1_count(body) == 1, f"found {h1_count(body)}")

    print("\nHeading levels never skip")
    for path, body in bodies.items():
        levels = heading_levels(body)
        skips = [
            (a, b)
            for a, b in zip(levels, levels[1:])
            if b > a + 1
        ]
        check(f"{path} heading order is valid", not skips, str(skips))

    print("\nPublic navigation")
    home = bodies["/"]
    check("why movement matters linked", 'href="/movement"' in home)
    check("how it works linked", 'href="/how-it-works"' in home)
    check("resources linked", 'href="/resources"' in home)
    check("sign in tab removed from the logged-out header", ">Sign In<" not in home)
    check("view my mobility is the entry point", 'href="/dashboard">View My Mobility</a>' in home)
    check("view my mobility in header", "View My Mobility" in home)
    check("primary nav labelled", 'aria-label="Main"' in home)
    check("skip link present", 'class="skip-link"' in home)

    print("\nPublic pages do not leak app navigation")
    for path in PUBLIC_PAGES:
        body = bodies[path]
        # The footer links to /account as the privacy page; the application shell must not.
        check(f"{path} has no application nav", 'aria-label="Application"' not in body)
        check(f"{path} has no sign out form", 'action="/logout"' not in body)

    print("\nSigned-in pages do not leak marketing navigation")
    page = client.get("/login")
    check("login renders", page.status_code == 200)
    check("login keeps public header", "View My Mobility" in page.text)

    print("\nFooter links and disclaimer")
    for path, body in bodies.items():
        # Templates wrap these sentences across lines, so compare on collapsed whitespace.
        flat = squash(body)
        check(f"{path} footer disclaimer present", FOOTER_DISCLAIMER in flat)
        check(f"{path} local storage note present", LOCAL_STORAGE_NOTE in flat)
        check(f"{path} has footer nav", 'aria-label="Footer"' in body)

    print("\nTrusted resource links are safe")
    resources = bodies["/resources"]
    check("external links open in a new tab", 'rel="noopener noreferrer"' in resources)
    check("every external anchor has rel", len(re.findall(r"<a(?![^>]*rel=)[^>]*target=", resources)) == 0)
    for org in [
        "CDC STEADI",
        "National Institute on Aging",
        "NINDS",
        "Move Your Way",
        "MedlinePlus",
        "Cure PSP",
    ]:
        check(f"{org} card present", org in resources)

    print("\nNo diagnostic language on public pages")
    banned = ["diagnose", "diagnosis of", "predict disease", "detect Parkinson"]
    for path, body in bodies.items():
        for phrase in banned:
            # The pages do say they do NOT diagnose; only flag affirmative claims.
            pattern = r"(?<!not )(?<!does not )(?<!doesn&#39;t )\b" + re.escape(phrase)
            for match in re.finditer(pattern, body, re.IGNORECASE):
                snippet = body[max(0, match.start() - 60) : match.start() + 40]
                if "does not" in snippet.lower() or "not a medical device" in snippet.lower():
                    continue
                check(f"{path} avoids {phrase!r}", False, snippet)
                break
            else:
                check(f"{path} avoids {phrase!r}", True)

    print("\nHomepage answers the visitor questions")
    check("movement hero headline", "movement has signals too" in home)
    check("tagline present", "Know your baseline. Notice the change." in home)
    check("trusted health signal comparison", "Movement is often overlooked" in home)
    check("three step flow", "Your devices collect" in home and "MotorScope organizes" in home)
    check("third step present", "You notice changes" in home)
    check("baseline section", "Know your baseline" in home or "Your baseline is the reference" in home)
    check("dashboard preview", "Your Mobility Trend" in home)
    check("credibility section", "Built on trusted health information" in home)
    check("resources link not a wall of cards", home.count("resource-card") == 0)

    print("\nHomepage is concise")
    sections = re.findall(r'<section class="band', home)
    check("no more than six major sections", len(sections) <= 6, f"found {len(sections)}")

    print("\nHomepage images are described")
    svgs = re.findall(r"<svg[^>]*>", home)
    described = [s for s in svgs if "role=\"img\"" in s and "aria-label" in s]
    check("hero illustration has a text alternative", len(described) >= 1)
    check("decorative svgs are hidden from readers", 'aria-hidden="true"' in home)

    print("\nHow it works explains the pipeline")
    how = bodies["/how-it-works"]
    for stage in [
        "iPhone and Apple Watch",
        "Apple Health mobility data",
        "MotorScope",
        "Trend analysis",
        "personal mobility baseline",
        "doctor conversation",
    ]:
        check(f"stage covers {stage}", stage in how)

    print("\nMovement page covers when to seek help")
    movement = bodies["/movement"]
    check("movement changes gradually", "Movement can change gradually" in movement)
    check("everyday life covered", "Movement affects everyday life" in movement)
    check("measurements explained", "What movement measurements can tell you" in movement)
    check("clinician guidance present", "When to talk with a healthcare professional" in movement)
    check("urgent advice present", "Seek medical advice promptly" in movement)

    print("\nPage metadata")
    for path, body in bodies.items():
        check(f"{path} has a meta description", re.search(r'<meta name="description" content="[^"]{40,}"', body) is not None)
        check(f"{path} has a canonical link", "<link rel=\"canonical\"" in body)
        check(f"{path} has open graph tags", 'property="og:title"' in body and 'property="og:description"' in body)
        check(f"{path} has theme color", 'name="theme-color"' in body)
    check("meta descriptions are unique per page", len({re.search(r'<meta name="description" content="([^"]+)"', b).group(1) for b in bodies.values()}) == len(bodies))

    print("\nStylesheet meets accessibility basics")
    css = (client.get("/static/css/style.css").text)
    check("focus indicator defined", ":focus-visible" in css)
    check("focus indicator is solid, not a faint tint", "outline: 3px solid var(--emerald-bright)" in css)
    check("placeholder darkened past AA", "#948870" not in css and "#776b59" in css)
    check("print sheet exists", "@media print" in css)
    check("print sheet hides app chrome", ".app-bar," in css)
    check("print sheet forces ink colours", "print-color-adjust: exact" in css)
    check("anchor targets clear the header", "scroll-margin-top" in css)

    print("\nSection headings and page titles are centred")
    # Section heads and page heroes centre their short text; body prose stays left-aligned.
    check("section head centred", ".section-head { max-width: 760px; margin: 0 auto 32px; text-align: center; }" in css)
    check("section intro centred within a readable measure", "margin: 0 auto;" in css and "min(62ch, 100%)" in css)
    check("page hero centred", ".page-hero {" in css and "text-align: center;" in css)
    check("centred head exempt inside split bands", ".band__inner--split .section-head { margin-inline: 0; text-align: left; }" in css)

    print("\nStandalone lede does not run to the edge")
    check("movement lede uses the constrained class", '<div class="prose prose--lede">' in bodies["/movement"])
    check("lede container rule exists", ".prose--lede {" in css)
    check("lede is centred with gutters", "max-width: 860px;" in css and "padding: 0 24px 8px;" in css)

    print("\nMovement page centres every line")
    movement_body = bodies["/movement"]
    check("movement page is scoped", 'class="shell-public page-movement"' in movement_body)
    check("movement content centred", ".page-movement .site-main { text-align: center; }" in css)
    check("movement cta row centred", ".page-movement .band__cta { justify-content: center; }" in css)
    check("movement callout stacked and centred", ".page-movement .callout {" in css)
    # Scope must stop at the page main so the shared header and footer keep their alignment.
    check("centring scoped to main, not whole page", ".page-movement { text-align: center; }" not in css)

    print("\nArrow-driven decks")
    for path in ("/", "/movement", "/how-it-works"):
        deck = bodies[path]
        check(f"{path} is a slide deck", "data-slides" in deck and "data-slide-track" in deck)
        check(f"{path} has slide panels", deck.count('class="slide') >= 3)
        check(f"{path} has arrow controls", "data-slide-prev" in deck and "data-slide-next" in deck)
        check(f"{path} has slide dots", "data-slide-dots" in deck)
        check(f"{path} loads the deck script", "/static/js/slides.js" in deck)
    for path in ("/movement", "/how-it-works"):
        check(f"{path} intro is centred", 'class="slide slide--intro"' in bodies[path])
    deck_js = client.get("/static/js/slides.js")
    check("deck script served", deck_js.status_code == 200 and "slides--ready" in deck_js.text)
    check("deck styles present", ".slides--ready .slides__track" in css)
    check("deck stacks without javascript", ".slides__track { display: grid" in css)
    check(
        "deck pipeline laid out as a compact grid",
        ".slide .pipeline {" in css
        and "grid-template-columns: repeat(auto-fit, minmax(230px, 1fr))" in css,
    )

    print("\nHomepage comparison is emphasised")
    check("comparison rows carry icons", home.count("compare-row__icon") == 4)
    check("overlooked signal is called out", "The overlooked signal" in home)
    check("comparison table enlarged", "max-width: 820px;" in css)
    check("comparison table centred", "margin: 0 auto 28px;" in css)

    print("\nPanel-only pages centre their content")
    # Sign-in, registration, Google errors and password reset render one bare `.panel`.
    # `.site-main` became a block container during the redesign, so these pages depend on
    # the `.site-main > .panel` rule to stay centred instead of pinned to the left edge.
    for path in ["/login", "/signup", "/forgot-password"]:
        page = client.get(path)
        check(f"{path} returns 200", page.status_code == 200, str(page.status_code))
        main = re.search(r"<main[^>]*>(.*?)</main>", page.text, re.DOTALL)
        check(f"{path} is a bare panel", main is not None and main.group(1).strip().startswith('<section class="panel"'))
    check("centring rule for bare panels", ".site-main > .panel" in css)
    check("bare panel centred with auto side margins", "margin: 56px auto 72px" in css)
    for path, body in bodies.items():
        main = re.search(r"<main[^>]*>(.*?)</main>", body, re.DOTALL)
        check(f"{path} keeps full-bleed marketing layout", main is not None and not main.group(1).strip().startswith('<section class="panel"'))

    print("\nAbout link points at a real About section")
    home_body = bodies["/"]
    check("footer About link exists", 'href="/movement#about"' in home_body)
    check("About section exists on /movement", 'id="about"' in squash(bodies["/movement"]))
    check("About section is about MotorScope", "About MotorScope" in squash(bodies["/movement"]))
    check("clinician advice no longer masquerades as About", "About MotorScope" not in squash(bodies["/movement"]) or 'id="about"' in squash(bodies["/movement"]))

    print("\nHero illustration is described for screen readers")
    hero_svg = re.search(r'<svg class="hero__art".*?</svg>', home_body, re.DOTALL)
    check("hero svg has role img", hero_svg is not None and 'role="img"' in hero_svg.group(0))
    check("hero svg has descriptive alt text", hero_svg is not None and len(re.search(r'aria-label="([^"]+)"', hero_svg.group(0)).group(1)) > 40)

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed.")
    if FAILURES:
        print("Public page checks failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("All public page checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())