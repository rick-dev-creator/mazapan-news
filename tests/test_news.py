"""The helper's tests: which edition a locale gets, which sources are kept,
what it makes of a feed, and how sources' headlines come together.

    python3 -m unittest discover -s tests
"""

import os
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
source = (HERE / "news.py.tmpl").read_text(encoding="utf-8")
# The two things Mazapan fills in when it renders the helper.
source = source.replace("{{ quote lang }}", '"es_MX"').replace("{{ plugin }}", "news")
news = types.ModuleType("news")
news.__file__ = str(HERE / "news.py.tmpl")
exec(compile(source, news.__file__, "exec"), news.__dict__)


def feed(*items, extra=b""):
    body = b"".join(
        b"<item><title>%s</title><link>%s</link><pubDate>%s</pubDate><source url=\"https://x\">%s</source></item>"
        % (t.encode(), l.encode(), d.encode(), s.encode()) for t, l, d, s in items)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + extra + b"<rss><channel><title>Top</title>" + body + b"</channel></rss>"


class Editions(unittest.TestCase):
    def test_every_edition_once(self):
        ids = [news.edition_id(c, l) for c, l, _ in news.EDITIONS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_country_in_its_language(self):
        self.assertEqual(news.default_edition("es_MX.UTF-8"), "MX:es-419")
        self.assertEqual(news.default_edition("ja_JP"), "JP:ja")
        self.assertEqual(news.default_edition("fr_CA.UTF-8"), "CA:fr")
        self.assertEqual(news.default_edition("en_CA"), "CA:en")

    def test_country_in_another_language(self):
        # Living in Japan with English set: Japan's edition, the one it has.
        self.assertEqual(news.default_edition("en_JP.UTF-8"), "JP:ja")

    def test_language_alone(self):
        self.assertEqual(news.default_edition("es"), "MX:es-419")
        self.assertEqual(news.default_edition("en"), "US:en")
        self.assertEqual(news.default_edition("de"), "DE:de")

    def test_nothing_known(self):
        self.assertEqual(news.default_edition(""), "US:en")
        self.assertEqual(news.default_edition("C"), "US:en")
        self.assertEqual(news.default_edition("xx_YY"), "US:en")


class Sources(unittest.TestCase):
    def test_kept_as_given(self):
        raw = [{"edition": "MX:es-419", "topic": "SCIENCE"}, {"edition": "US:en", "query": "bitcoin"}]
        self.assertEqual(news.check_sources(raw), raw)

    def test_wrong_ones_left_out(self):
        raw = [{"edition": "JP:en", "topic": "TOP"},        # not an edition
               {"edition": "US:en", "topic": "CRYPTO"},     # not a section
               {"edition": "US:en"},                        # neither
               "US:en", None,
               {"edition": "US:en", "topic": "TOP"}]
        self.assertEqual(news.check_sources(raw), [{"edition": "US:en", "topic": "TOP"}])

    def test_the_same_once(self):
        raw = [{"edition": "US:en", "query": "AI"}, {"edition": "US:en", "query": " ai "}]
        self.assertEqual(news.check_sources(raw), [{"edition": "US:en", "query": "AI"}])

    def test_words_cleaned(self):
        out = news.check_sources([{"edition": "US:en", "query": "a\nb\x00c   d" + "x" * 200}])
        self.assertEqual(out[0]["query"], ("a b c d" + "x" * 200)[: news.MAX_QUERY])

    def test_at_most(self):
        raw = [{"edition": "US:en", "query": f"t{i}"} for i in range(30)]
        self.assertEqual(len(news.check_sources(raw)), news.MAX_SOURCES)

    def test_not_a_list(self):
        self.assertEqual(news.check_sources({"edition": "US:en"}), [])


class Urls(unittest.TestCase):
    def test_front_page(self):
        self.assertEqual(news.feed_url({"edition": "MX:es-419", "topic": "TOP"}),
                         "https://news.google.com/rss?hl=es-419&gl=MX&ceid=MX%3Aes-419")

    def test_section(self):
        self.assertEqual(news.feed_url({"edition": "BR:pt-419", "topic": "SCIENCE"}),
                         "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=pt-BR&gl=BR&ceid=BR%3Apt-419")

    def test_search(self):
        url = news.feed_url({"edition": "US:en", "query": "bitcoin & AI"})
        self.assertTrue(url.startswith("https://news.google.com/rss/search?q=bitcoin%20%26%20AI%20when%3A2d&hl=en-US"))


class Parse(unittest.TestCase):
    def test_headlines(self):
        out = news.parse(feed(("Lluvias en CDMX - EL PAÍS", "https://news.google.com/a", "Thu, 09 Oct 2026 10:00:00 GMT", "EL PAÍS")), 20)
        self.assertEqual(out, [{"title": "Lluvias en CDMX", "source": "EL PAÍS",
                                "link": "https://news.google.com/a", "time": 1791540000}])

    def test_entities_and_spaces(self):
        out = news.parse(feed(("AT&amp;T  &quot;cuts&quot;\n jobs - Reuters", "https://n/b", "bad date", "Reuters")), 20)
        self.assertEqual(out[0]["title"], 'AT&T "cuts" jobs')
        self.assertEqual(out[0]["time"], 0)

    def test_only_https_links(self):
        out = news.parse(feed(("A - S", "http://n/a", "", "S"), ("B - S", "javascript:x", "", "S")), 20)
        self.assertEqual(out, [])

    def test_limit(self):
        items = [(f"H{i} - S", f"https://n/{i}", "", "S") for i in range(9)]
        self.assertEqual(len(news.parse(feed(*items), 3)), 3)

    def test_dtd_refused(self):
        evil = b'<!DOCTYPE rss [<!ENTITY a "aaaaaaaa">]>'
        with self.assertRaises(ValueError):
            news.parse(feed(("&a; - S", "https://n/a", "", "S"), extra=evil), 20)


class Merge(unittest.TestCase):
    def test_newest_first_and_shared_once(self):
        a = [{"title": "Sismo en Oaxaca", "source": "X", "link": "https://n/1", "time": 100}]
        b = [{"title": "SISMO en Oaxaca!", "source": "Y", "link": "https://n/2", "time": 300},
             {"title": "Other", "source": "Y", "link": "https://n/3", "time": 200}]
        out = news.merge([("a", a), ("b", b)], {}, 50)
        self.assertEqual([i["link"] for i in out], ["https://n/3", "https://n/1"])
        self.assertEqual(out[1]["from"], ["a", "b"])
        self.assertEqual(out[0]["first_seen"], 50)

    def test_first_seen_kept(self):
        a = [{"title": "T", "source": "X", "link": "https://n/1", "time": 1}]
        out = news.merge([("a", a)], {"https://n/1": 7}, 50)
        self.assertEqual(out[0]["first_seen"], 7)


class Fetch(unittest.TestCase):
    SRCS = [{"edition": "MX:es-419", "topic": "NATION"}, {"edition": "US:en", "query": "AI"}]

    def test_first_time_nothing_new(self):
        get = lambda url: feed(("H - S", "https://n/" + str(len(url)), "", "S"))
        data = news.fetch(self.SRCS[:1], 20, {}, now=1000, get=get)
        self.assertEqual([i["first_seen"] for i in data["items"]], [0])

    def test_new_ones_since(self):
        prev = {"items": [{"title": "Old", "source": "S", "link": "https://n/old", "time": 1,
                           "first_seen": 0, "from": ["MX:es-419/NATION"]}]}
        get = lambda url: feed(("Old - S", "https://n/old", "", "S"), ("New - S", "https://n/new", "", "S"))
        data = news.fetch(self.SRCS[:1], 20, prev, now=1000, get=get)
        seen = dict((i["link"], i["first_seen"]) for i in data["items"])
        self.assertEqual(seen, {"https://n/old": 0, "https://n/new": 1000})

    def test_a_source_added_brings_nothing_new(self):
        prev = {"items": [{"title": "Old", "source": "S", "link": "https://n/old", "time": 1,
                           "first_seen": 0, "from": ["MX:es-419/NATION"]}]}
        get = lambda url: feed(("From " + url[-12:] + " - S", "https://n/" + url[-12:], "", "S"))
        data = news.fetch(self.SRCS, 20, prev, now=1000, get=get)
        added = [i for i in data["items"] if i["from"] == ["US:en/q:AI"]]
        self.assertEqual([i["first_seen"] for i in added], [0])

    def test_a_failing_source_keeps_its_last(self):
        prev = {"items": [{"title": "Kept", "source": "S", "link": "https://n/k", "time": 5,
                           "first_seen": 3, "from": ["US:en/q:AI"]}]}

        def get(url):
            if "search" in url:
                raise OSError("timed out")
            return feed(("Fresh - S", "https://n/f", "", "S"))
        data = news.fetch(self.SRCS, 20, prev, now=1000, get=get)
        self.assertEqual(data["errors"], {"US:en/q:AI": "timed out"})
        kept = [i for i in data["items"] if i["link"] == "https://n/k"][0]
        self.assertEqual((kept["first_seen"], kept["from"]), (3, ["US:en/q:AI"]))


class Show(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        news.SOURCES = os.path.join(self.tmp.name, "sources.json")
        news.SEEN = os.path.join(self.tmp.name, "seen.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_until_picked(self):
        self.assertEqual([s["id"] for s in news.show(20, {})["sources"]], ["MX:es-419/TOP"])

    def test_a_source_taken_away_takes_its_headlines(self):
        news.save_json(news.SOURCES, [{"edition": "US:en", "query": "AI"}])
        data = {"items": [
            {"title": "A", "link": "https://n/a", "from": ["US:en/q:AI"], "time": 2, "first_seen": 0},
            {"title": "B", "link": "https://n/b", "from": ["MX:es-419/TOP"], "time": 1, "first_seen": 0},
            {"title": "C", "link": "https://n/c", "from": ["MX:es-419/TOP", "US:en/q:AI"], "time": 0, "first_seen": 0}],
            "errors": {"MX:es-419/TOP": "x"}}
        out = news.show(20, data)
        self.assertEqual([(i["title"], i["from"]) for i in out["items"]],
                         [("A", ["US:en/q:AI"]), ("C", ["US:en/q:AI"])])
        self.assertEqual(out["errors"], {})


class Browser(unittest.TestCase):
    def test_its_class(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "org.mozilla.firefox.desktop").write_text(
                "[Desktop Entry]\nName=Firefox\nStartupWMClass=firefox\n[Desktop Action new]\nStartupWMClass=x\n")
            Path(d, "brave-browser.desktop").write_text("[Desktop Entry]\nName=Brave\n")
            self.assertEqual(news.browser_class("org.mozilla.firefox.desktop", [d]), "firefox")
            self.assertEqual(news.browser_class("brave-browser.desktop", [d]), "brave-browser")
            self.assertEqual(news.browser_class("chromium.desktop", [d]), "chromium")

    def test_nothing(self):
        self.assertEqual(news.browser_class("", []), "")
        self.assertEqual(news.browser_class("firefox", []), "")


if __name__ == "__main__":
    unittest.main()
