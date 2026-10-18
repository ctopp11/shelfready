import unittest

from shelfready import kdp_rules as r
from shelfready.app import validate_brief
from shelfready.ollama_client import OllamaError, parse_json

GOOD_DESC = ("<p>Slow down with fifty calm mountain scenes.</p><ul><li>50 single-sided designs</li>"
             "<li>Large 8.5 x 11 in pages</li><li>Cabins, wildlife and quiet forests</li></ul>"
             "<p>A thoughtful gift for anyone who loves the outdoors and needs an evening to unwind.</p>") * 2


def levels(issues, field=None):
    return [(i.field, i.level) for i in issues if field is None or i.field == field]


class TitleRules(unittest.TestCase):
    def test_clean_title_passes(self):
        self.assertEqual(r.check_title("Rocky Mountain Retreat", "An Adult Coloring Book of Cozy Cabins and Wildlife"), [])

    def test_combined_length_limit(self):
        issues = r.check_title("A" * 120, "B" * 80)
        self.assertIn(("title", "error"), levels(issues))

    def test_long_title_warns(self):
        self.assertIn(("title", "warn"), levels(r.check_title("x" * 61, "")))

    def test_promotional_words_blocked(self):
        msgs = " ".join(i.message for i in r.check_title("Bestselling Cabin Coloring Book", "Free Bonus Pages"))
        self.assertIn("bestselling", msgs)
        self.assertIn("free", msgs)

    def test_hyphenated_compound_is_not_a_claim(self):
        self.assertEqual([i for i in r.check_title("Stress-Free Mountain Moments", "") if i.level == "error"], [])

    def test_trademark_blocked(self):
        self.assertIn(("subtitle", "error"), levels(r.check_title("Snow Day", "Featuring Bluey and Friends")))

    def test_generic_word_stuffing(self):
        issues = r.check_title("Coloring Book Coloring Book for Adults", "")
        self.assertTrue(any("repeated" in i.message for i in issues))


class KeywordRules(unittest.TestCase):
    def test_length_and_quotes(self):
        issues = r.check_keywords(['"cabin art"', "x" * 51] + ["a b"] * 0)
        self.assertEqual(sum(i.level == "error" for i in issues), 2)

    def test_plural_variants_flagged(self):
        issues = r.check_keywords(["mountain cabin", "mountain cabins"])
        self.assertTrue(any("variant" in i.message for i in issues))

    def test_us_spelling_hint_only_on_dot_com(self):
        self.assertTrue(any("colour" in i.message for i in r.check_keywords(["colouring for seniors"])))
        self.assertFalse(any("colour" in i.message for i in r.check_keywords(["colouring for seniors"], marketplace="amazon.ca")))

    def test_title_repeat_detection(self):
        repeat = r.check_keywords(["rockies cabins"], "Rockies", "Cozy Cabins")
        self.assertTrue(any("repeats" in i.message for i in repeat))
        new_angle = r.check_keywords(["canadian wildlife art"], "Canadian Rockies", "Cabins and Wildlife")
        self.assertFalse(any("repeats" in i.message for i in new_angle))

    def test_program_names_blocked(self):
        self.assertTrue(any(i.level == "error" for i in r.check_keywords(["kindle unlimited coloring"])))

    def test_normalise(self):
        self.assertEqual(r.normalise_keyword('  "Cozy, Cabin"  <b>Art</b> '), "cozy cabin art")


class DescriptionRules(unittest.TestCase):
    def test_good_description(self):
        self.assertEqual(r.check_description(GOOD_DESC), [])

    def test_disallowed_html(self):
        self.assertIn(("description", "error"), levels(r.check_description(GOOD_DESC + "<h2>Hi</h2><a href='x'>y</a>")))

    def test_contact_details_and_emoji(self):
        msgs = " ".join(i.message for i in r.check_description(GOOD_DESC + " Visit www.example.com or mail me@x.com 😀"))
        self.assertIn("URL", msgs)
        self.assertIn("emoji", msgs)

    def test_review_request(self):
        self.assertTrue(any("review" in i.message.lower() for i in r.check_description(GOOD_DESC + " Please leave a review!")))

    def test_length_limit(self):
        self.assertTrue(any(i.level == "error" for i in r.check_description("<p>" + "a" * 4001 + "</p>")))


class Sanitiser(unittest.TestCase):
    def test_sanitise_is_mechanical(self):
        out = r.sanitise_listing({"title": " <b>Hi</b>  there ", "subtitle": "", "description": "<h2>X</h2><strong>y</strong> 🎨",
                                  "keywords": ['"A"', "a", "b, c", "d", "e", "f", "g", "h", "i"]})
        self.assertEqual(out["title"], "Hi there")
        self.assertEqual(out["description"], "<h4>X</h4><b>y</b>")
        self.assertEqual(out["keywords"], ["a", "b c", "d", "e", "f", "g", "h"])

    def test_score(self):
        self.assertEqual(r.score([]), 100)
        self.assertEqual(r.score([r.Issue("title", "error", ""), r.Issue("keywords", "warn", "")]), 75)


class Fixes(unittest.TestCase):
    def test_wraps_bare_paragraphs(self):
        out, fixes = r.apply_fixes({"description": "Hook line.\n\n<ul>\n <li>a</li>\n</ul>\n\nClose.", "keywords": []})
        self.assertEqual(out["description"], "<p>Hook line.</p><ul><li>a</li></ul><p>Close.</p>")
        self.assertEqual(len(fixes), 1)

    def test_tidy_real_gemma_output(self):
        raw = ("<p>Hook.</p><br><ul><li><b>A:</b> one</li><li><b>B:</b> two.</p><br> "
               "<p>Gift line.</p>")
        out, fixes = r.apply_fixes({"description": raw, "keywords": []})
        self.assertEqual(out["description"], "<p>Hook.</p><ul><li><b>A:</b> one</li><li><b>B:</b> two.</li></ul><p>Gift line.</p>")
        self.assertTrue(any("Repaired" in f for f in fixes))

    def test_tidy_drops_disallowed_tags_and_keeps_entities(self):
        self.assertEqual(r.tidy_html('<div><p>Tom &amp; Jerry<script>x</script></p></div><li>solo</li>'),
                         "<p>Tom &amp; Jerry</p><ul><li>solo</li></ul>")

    def test_unclosed_tags_warn(self):
        desc = GOOD_DESC + "<ul><li>open item"
        self.assertTrue(any("closed" in i.message for i in r.check_description(desc)))

    def test_us_spelling_only_for_dot_com(self):
        out, fixes = r.apply_fixes({"description": "<p>x</p>", "keywords": ["adult colouring", "cabins"]})
        self.assertEqual(out["keywords"], ["adult coloring", "cabins"])
        out, fixes = r.apply_fixes({"description": "<p>x</p>", "keywords": ["adult colouring"]}, "amazon.ca")
        self.assertEqual((out["keywords"], fixes), (["adult colouring"], []))


class BriefAndParsing(unittest.TestCase):
    def test_brief_validation(self):
        with self.assertRaises(ValueError):
            validate_brief({"theme": "x"})
        with self.assertRaises(ValueError):
            validate_brief({"theme": "Cabins", "pages": "lots"})
        with self.assertRaises(ValueError):
            validate_brief({"theme": "Cabins", "audience": "aliens"})
        self.assertEqual(validate_brief({"theme": " Cabins ", "pages": "50"})["pages"], 50)

    def test_parse_json_tolerates_fences(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(OllamaError):
            parse_json("no json here")


if __name__ == "__main__":
    unittest.main()
