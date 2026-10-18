"""Deterministic Amazon KDP metadata checks.

The language model writes. This module decides whether what it wrote is safe to
paste into KDP. Every rule maps to KDP's published guidance:

* Metadata Guidelines for Books ....... https://kdp.amazon.com/en_US/help/topic/G201097560
* Make Your Book More Discoverable .... https://kdp.amazon.com/en_US/help/topic/G201298500
* Write a Book Description ............ https://kdp.amazon.com/en_US/help/topic/G201189630

Nothing here calls the network. It is plain Python so it can be unit tested and
audited, and so the rules never drift with the model.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

TITLE_SUBTITLE_MAX = 199          # "title and subtitle together must be fewer than 200 characters"
TITLE_SKIM_LENGTH = 60            # "customers are more likely to skim past long titles (over 60 characters)"
KEYWORD_SLOTS = 7
KEYWORD_MAX = 50                  # each KDP keyword box holds 50 characters
DESCRIPTION_MAX = 4000

ALLOWED_DESCRIPTION_TAGS = {"br", "p", "b", "em", "i", "u", "h4", "h5", "h6", "ol", "ul", "li"}

# Sales-rank, promotional and time-sensitive language KDP calls out by name.
PROMO_TERMS = [
    "bestseller", "best seller", "bestselling", "best selling", "#1", "number one",
    "free", "on sale", "sale", "discount", "limited time", "available now", "new release",
    "brand new", "new",
]
PROGRAM_TERMS = ["kindle unlimited", "kdp select", "kindle", "amazon", "prime"]
SUBJECTIVE_TERMS = ["best ever", "best book", "greatest", "amazing", "perfect", "#1 rated"]

# A short, deliberately conservative watchlist of character and art-supply
# brands that colouring-book listings get pulled for. It is not legal advice,
# just a tripwire that makes a human look twice.
TRADEMARK_WATCHLIST = [
    "disney", "pixar", "marvel", "pokemon", "pokémon", "barbie", "bluey", "minecraft",
    "hello kitty", "sanrio", "peppa pig", "paw patrol", "frozen", "star wars", "harry potter",
    "lego", "roblox", "fortnite", "sonic", "mario", "nintendo", "squishmallow", "stitch",
    "crayola", "prismacolor", "sharpie", "posca", "copic", "faber-castell", "staedtler",
    "johanna basford", "secret garden",
]
GENERIC_REPEAT_WORDS = {"coloring", "colouring", "book", "books", "gift", "gifts", "journal", "notebook", "activity"}

URL_RE = re.compile(r"(https?://|www\.)\S+|\b[\w-]+\.(com|ca|net|org|io|co)\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{8,}\d)")
TAG_RE = re.compile(r"</?\s*([a-zA-Z0-9]+)[^>]*>")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
REVIEW_RE = re.compile(r"(leave|write|post) (a|us a|your)? ?review|\breviews?\b said|★|five[- ]star", re.I)


@dataclass
class Issue:
    field: str        # "title" | "subtitle" | "keywords" | "description" | "input"
    level: str        # "error" (KDP will reject or flag it) | "warn" (likely to hurt you)
    message: str
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _word_hits(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    hits = []
    for term in terms:
        # Hyphenated compounds ("stress-free", "brand-new-to-you") are not claims, so skip them.
        pattern = r"(?<![\w#-])" + re.escape(term) + r"(?![\w-])"
        if re.search(pattern, low):
            hits.append(term)
    # Drop shorter terms that are already part of a longer hit ("sale" inside "on sale").
    return [h for h in hits if not any(h != o and h in o for o in hits)]


def strip_tags(text: str) -> str:
    return html.unescape(TAG_RE.sub("", text or ""))


def normalise_keyword(raw: str) -> str:
    """Make a model-written keyword safe to paste: no quotes, tags, commas or stray spaces."""
    kw = strip_tags(raw)
    kw = re.sub(r"[\"“”‘’'`]", "", kw)
    kw = re.sub(r"[,;|]+", " ", kw)
    kw = re.sub(r"\s+", " ", kw).strip().lower()
    return kw


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def check_input(theme: str, notes: str = "") -> list[Issue]:
    """Warn before generating if the brief itself leans on someone else's brand."""
    issues = []
    hits = _word_hits(f"{theme} {notes}", TRADEMARK_WATCHLIST)
    if hits:
        issues.append(Issue("input", "warn",
                            f"Your brief mentions {', '.join(hits)}. Unless you hold a licence, KDP treats that as unauthorised brand use.",
                            "Describe the style instead, e.g. 'cozy cartoon animals' rather than a character name."))
    return issues


def check_title(title: str, subtitle: str) -> list[Issue]:
    issues: list[Issue] = []
    title, subtitle = (title or "").strip(), (subtitle or "").strip()
    if not title:
        issues.append(Issue("title", "error", "Title is empty."))
        return issues
    combined = len(title) + len(subtitle)
    if combined > TITLE_SUBTITLE_MAX:
        issues.append(Issue("title", "error",
                            f"Title + subtitle is {combined} characters; KDP requires fewer than 200.",
                            "Move detail into the description or keywords."))
    if len(title) > TITLE_SKIM_LENGTH:
        issues.append(Issue("title", "warn",
                            f"Title is {len(title)} characters. KDP notes shoppers skim past titles over 60.",
                            "Keep the hook in the title and push specifics into the subtitle."))
    for field, text in (("title", title), ("subtitle", subtitle)):
        if not text:
            continue
        if TAG_RE.search(text):
            issues.append(Issue(field, "error", "HTML is not accepted in this field."))
        for hit in _word_hits(text, PROMO_TERMS + PROGRAM_TERMS):
            issues.append(Issue(field, "error", f"'{hit}' is sales-rank, promotional or Amazon-program language, which KDP prohibits here."))
        for hit in _word_hits(text, TRADEMARK_WATCHLIST):
            issues.append(Issue(field, "error", f"'{hit}' looks like a trademark you may not be authorised to use."))
        words = [_singular(w) for w in re.findall(r"[a-z]+", text.lower())]
        for word in {_singular(w) for w in GENERIC_REPEAT_WORDS}:
            if words.count(word) > 1:
                issues.append(Issue(field, "warn", f"'{word}' is repeated. KDP prohibits stuffing generic words into titles."))
        letters = [c for c in text if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7 and len(letters) > 8:
            issues.append(Issue(field, "warn", "Mostly capital letters reads as shouting and looks low-quality on the detail page."))
    return issues


def check_keywords(keywords: list[str], title: str = "", subtitle: str = "", marketplace: str = "amazon.com") -> list[Issue]:
    issues: list[Issue] = []
    if len(keywords) > KEYWORD_SLOTS:
        issues.append(Issue("keywords", "error", f"{len(keywords)} keywords supplied; KDP has {KEYWORD_SLOTS} boxes."))
    if len(keywords) < KEYWORD_SLOTS:
        issues.append(Issue("keywords", "warn", f"Only {len(keywords)} of {KEYWORD_SLOTS} keyword boxes used. Empty boxes are free reach left on the table."))
    stop = {"a", "an", "and", "the", "of", "for", "with", "to", "in", "on", "by", "&"}
    title_words = {_singular(w) for w in re.findall(r"[a-z]+", f"{title} {subtitle}".lower()) if w not in stop}
    seen: dict[str, int] = {}
    for i, kw in enumerate(keywords, 1):
        label = f"Keyword {i} ('{kw}')"
        if not kw:
            continue
        if len(kw) > KEYWORD_MAX:
            issues.append(Issue("keywords", "error", f"{label} is {len(kw)} characters; each box holds {KEYWORD_MAX}."))
        if re.search(r"[\"“”]", kw) or TAG_RE.search(kw):
            issues.append(Issue("keywords", "error", f"{label} contains quotation marks or HTML, which KDP says not to use."))
        for hit in _word_hits(kw, PROMO_TERMS + PROGRAM_TERMS + SUBJECTIVE_TERMS):
            issues.append(Issue("keywords", "error", f"{label} uses '{hit}'. Promotional, subjective or Amazon-program terms are prohibited."))
        for hit in _word_hits(kw, TRADEMARK_WATCHLIST):
            issues.append(Issue("keywords", "error", f"{label} references '{hit}', a brand you likely don't own."))
        if kw.strip() in {"book", "books", "coloring book", "colouring book"}:
            issues.append(Issue("keywords", "warn", f"{label} is common to every item in the category, so it adds no reach."))
        words = [_singular(w) for w in re.findall(r"[a-z]+", kw)]
        sig = " ".join(sorted(words))
        if sig in seen:
            issues.append(Issue("keywords", "warn", f"{label} is a plural/order variant of keyword {seen[sig]}. KDP says variants waste a slot."))
        else:
            seen[sig] = i
        meaningful = [w for w in words if w not in stop]
        if meaningful and all(w in title_words for w in meaningful):
            issues.append(Issue("keywords", "warn", f"{label} only repeats words already in your title/subtitle.",
                                "Use the slot for an angle the title doesn't cover (occasion, recipient, technique)."))
        if marketplace == "amazon.com" and "colour" in kw:
            issues.append(Issue("keywords", "warn", f"{label} uses Canadian/UK 'colour'. Amazon.com shoppers mostly search 'coloring'."))
    return issues


def check_description(desc: str) -> list[Issue]:
    issues: list[Issue] = []
    desc = desc or ""
    if not strip_tags(desc).strip():
        return [Issue("description", "error", "Description is empty.")]
    if len(desc) > DESCRIPTION_MAX:
        issues.append(Issue("description", "error", f"Description is {len(desc)} characters including HTML; KDP's limit is {DESCRIPTION_MAX}."))
    bad_tags = sorted({t.lower() for t in TAG_RE.findall(desc)} - ALLOWED_DESCRIPTION_TAGS)
    if bad_tags:
        issues.append(Issue("description", "error", f"Unsupported HTML: <{'>, <'.join(bad_tags)}>.",
                            "KDP supports br, p, b, em, i, u, h4 to h6, ul, ol and li."))
    opened = [t.lower() for t in re.findall(r"<\s*([a-zA-Z0-9]+)[^>]*>", desc) if t.lower() != "br"]
    closed = [t.lower() for t in re.findall(r"</\s*([a-zA-Z0-9]+)\s*>", desc)]
    if sorted(opened) != sorted(closed):
        issues.append(Issue("description", "warn", "Some HTML tags aren't closed, so the Amazon page may render oddly.",
                            "Check that every <p>, <li> and <ul> has a matching closing tag."))
    text = strip_tags(desc)
    if URL_RE.search(text) or EMAIL_RE.search(text):
        issues.append(Issue("description", "error", "Contains a URL or email address. KDP does not accept contact details in descriptions."))
    if PHONE_RE.search(text):
        issues.append(Issue("description", "error", "Contains what looks like a phone number."))
    if EMOJI_RE.search(text):
        issues.append(Issue("description", "error", "Contains emoji. KDP does not accept Unicode emoji in descriptions."))
    if REVIEW_RE.search(text):
        issues.append(Issue("description", "error", "Mentions reviews or star ratings. Testimonials and review requests aren't allowed."))
    for hit in _word_hits(text, ["bestseller", "bestselling", "best seller", "free", "on sale", "discount", "limited time", "available now", "price", "kindle unlimited", "kdp select"]):
        issues.append(Issue("description", "error", f"'{hit}' is promotional, price or availability language that KDP prohibits in descriptions."))
    for hit in _word_hits(text, TRADEMARK_WATCHLIST):
        issues.append(Issue("description", "warn", f"Mentions '{hit}'. Make sure you're authorised to reference that brand."))
    if len(text) < 300:
        issues.append(Issue("description", "warn", f"Only {len(text)} characters of copy. Thin descriptions convert poorly; aim for 800 to 2,000."))
    return issues


def check_listing(listing: dict, marketplace: str = "amazon.com") -> list[Issue]:
    title, subtitle = listing.get("title", ""), listing.get("subtitle", "")
    return (check_title(title, subtitle)
            + check_keywords(listing.get("keywords", []), title, subtitle, marketplace)
            + check_description(listing.get("description", "")))


def sanitise_listing(raw: dict) -> dict:
    """Apply only safe, mechanical fixes. Anything judgement-based stays a flagged issue."""
    kws = []
    for kw in raw.get("keywords", []) or []:
        kw = normalise_keyword(str(kw))
        if kw and kw not in kws:
            kws.append(kw)
    desc = str(raw.get("description", "") or "").strip()
    desc = re.sub(r"<\s*strong\s*>", "<b>", desc, flags=re.I)
    desc = re.sub(r"<\s*/\s*strong\s*>", "</b>", desc, flags=re.I)
    desc = re.sub(r"<(/?)h[123]>", r"<\1h4>", desc, flags=re.I)
    desc = EMOJI_RE.sub("", desc).strip()
    return {
        "title": re.sub(r"\s+", " ", strip_tags(str(raw.get("title", "")))).strip(),
        "subtitle": re.sub(r"\s+", " ", strip_tags(str(raw.get("subtitle", "")))).strip(),
        "description": desc,
        "keywords": kws[:KEYWORD_SLOTS],
    }


BLOCK_TAG_RE = re.compile(r"^\s*<\s*/?\s*(p|ul|ol|li|h4|h5|h6)\b", re.I)


def wrap_paragraphs(desc: str) -> str:
    """Amazon collapses plain newlines, so bare text blocks become <p> paragraphs."""
    blocks = re.split(r"\n\s*\n", desc.strip())
    out = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if BLOCK_TAG_RE.match(block):
            out.append(re.sub(r">\s*\n\s*<", "><", block))
        else:
            out.append("<p>" + re.sub(r"\s*\n\s*", "<br>", block) + "</p>")
    return "".join(out)


class _Tidy(HTMLParser):
    """Rebuild description HTML with only KDP-allowed tags, properly nested and closed."""

    BLOCK = {"p", "ul", "ol", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skipping = 0

    def _close_to(self, keep: set[str]):
        while self.stack and self.stack[-1] not in keep:
            self.out.append(f"</{self.stack.pop()}>")

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skipping += 1
            return
        if tag not in ALLOWED_DESCRIPTION_TAGS:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag in self.BLOCK:
            self._close_to(set())          # blocks never nest in a KDP description
        elif tag == "li":
            while self.stack and self.stack[-1] not in {"ul", "ol"}:
                self.out.append(f"</{self.stack.pop()}>")
            if not self.stack:              # stray <li>: give it a list
                self.out.append("<ul>")
                self.stack.append("ul")
        self.out.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skipping = max(0, self.skipping - 1)
            return
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(data)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def result(self) -> str:
        self._close_to(set())
        text = "".join(self.out)
        block = r"(?:p|ul|ol|li|h4|h5|h6)"
        text = re.sub(rf"(</?{block}>)\s*(?:<br>\s*)+", r"\1", text)      # <br> right after a block tag
        text = re.sub(rf"(?:\s*<br>)+\s*(</?{block}>)", r"\1", text)      # <br> right before a block tag
        text = re.sub(r"<(p|li|h4|h5|h6)>\s*</\1>", "", text)              # empty blocks
        text = re.sub(r"\s*(</?(?:ul|ol|li)>)\s*", r"\1", text)
        return re.sub(r"[ \t]{2,}", " ", text).strip()


def tidy_html(desc: str) -> str:
    parser = _Tidy()
    parser.feed(desc)
    parser.close()
    return parser.result()


def apply_fixes(listing: dict, marketplace: str = "amazon.com") -> tuple[dict, list[str]]:
    """Deterministic, explainable fixes. Returns the listing and a note for each change made."""
    fixes: list[str] = []
    listing = dict(listing)
    original = listing.get("description", "")
    wrapped = wrap_paragraphs(original)
    if wrapped != original:
        fixes.append("Wrapped description paragraphs in <p> tags so Amazon keeps your line breaks.")
    tidied = tidy_html(wrapped)
    if re.sub(r"\s", "", tidied) != re.sub(r"\s", "", wrapped):
        fixes.append("Repaired the description's HTML (closed open tags, removed stray line breaks between blocks).")
    listing["description"] = tidied
    if marketplace == "amazon.com":
        converted = [re.sub(r"colour", "color", k) for k in listing.get("keywords", [])]
        changed = sum(a != b for a, b in zip(converted, listing.get("keywords", [])))
        if changed:
            listing["keywords"] = converted
            fixes.append(f"Switched {changed} keyword{'s' if changed > 1 else ''} to US spelling ('coloring'), which is what Amazon.com shoppers type.")
    return listing, fixes


def score(issues: list[Issue]) -> int:
    """A simple readiness score: errors cost 20, warnings cost 5."""
    penalty = sum(20 if i.level == "error" else 5 for i in issues if i.field != "input")
    return max(0, 100 - penalty)
