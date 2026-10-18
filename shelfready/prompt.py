"""Prompt and output schema for the listing writer."""

from __future__ import annotations

import json

AUDIENCES = {
    "kids-4-8": "children aged 4 to 8 (simple shapes, thick lines; parents are the buyer)",
    "kids-8-12": "children aged 8 to 12 (more detail, fun themes; parents and grandparents buy)",
    "teens": "teens (trend-aware, expressive)",
    "adults": "adults colouring to relax and unwind",
    "seniors": "seniors and anyone who wants large, low-strain designs",
}
STYLES = {
    "bold-easy": "bold and easy: thick outlines, big shapes, low detail",
    "detailed": "detailed and intricate: fine lines, patterns, long sessions",
    "cozy": "cozy and cute: soft, friendly, everyday scenes",
    "mandala": "mandala and pattern-based",
    "realistic": "realistic illustration",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "description": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 7, "maxItems": 7},
    },
    "required": ["title", "subtitle", "description", "keywords"],
}

SYSTEM = """You are a senior Amazon KDP listing copywriter who specialises in colouring books.
You write honest, specific, buyer-focused metadata that follows KDP's content guidelines.

Hard rules (KDP will reject or suppress listings that break them):
- Title: the real book title only, ideally under 60 characters. No keyword stuffing, no repeated generic words.
- Title + subtitle together under 200 characters. The subtitle carries audience, style and page count.
- Never use: bestseller, best-selling, #1, free, sale, discount, new, limited time, Kindle Unlimited, KDP Select, Amazon.
- Never reference brands, characters, franchises, other authors or art-supply brands.
- Description: 900 to 1,800 characters. Use only these HTML tags: <p>, <br>, <b>, <i>, <em>, <u>, <h4>, <ul>, <li>.
  Structure: one hook paragraph, a <ul> of 4 to 6 concrete benefits (what's inside, paper and format facts given, who it suits),
  and a short closing paragraph about who it's a good gift for. No emoji, no URLs, no reviews, no prices, no promises you can't verify.
- Keywords: exactly 7 search phrases of 2 to 4 words, each under 50 characters, lowercase, no quotes, no commas.
  Think like a shopper: recipient, occasion, technique, mood, specific subjects. Don't just repeat title words. Use US spelling "coloring" in keywords for amazon.com.
- Only state facts provided in the brief. Do not invent page counts, paper weight or features.
Return JSON only, matching the schema."""


def build_messages(brief: dict) -> list[dict]:
    audience = AUDIENCES.get(brief.get("audience", ""), brief.get("audience", "adults"))
    style = STYLES.get(brief.get("style", ""), brief.get("style", ""))
    facts = [
        f"Theme / subject: {brief['theme']}",
        f"Audience: {audience}",
        f"Illustration style: {style}" if style else "",
        f"Number of unique designs: {brief['pages']}" if brief.get("pages") else "",
        f"Trim size: {brief['trim']}" if brief.get("trim") else "",
        "Each design is printed single-sided" if brief.get("single_sided") else "",
        "Large-print / low-vision friendly" if brief.get("large_print") else "",
        f"Marketplace: {brief.get('marketplace', 'amazon.com')}",
        f"Publisher notes and voice: {brief['notes']}" if brief.get("notes") else "",
    ]
    user = "Write a complete KDP listing for this colouring book.\n\n" + "\n".join(f"- {f}" for f in facts if f)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def build_repair_messages(brief: dict, listing: dict, errors: list[str]) -> list[dict]:
    msgs = build_messages(brief)
    msgs.append({"role": "assistant", "content": json.dumps(listing)})
    msgs.append({"role": "user", "content": "A KDP compliance check found these problems. Fix every one, keep everything else that works, and return the full JSON again:\n"
                 + "\n".join(f"- {e}" for e in errors)})
    return msgs
