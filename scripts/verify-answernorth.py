#!/usr/bin/env python3
"""Release gate for answernorth.com — read-only verifier.

Usage:
    python3 scripts/verify-answernorth.py                 # verify production
    python3 scripts/verify-answernorth.py http://localhost:8765   # verify a local build

Checks the entity layer the SEO plan requires: canonicals, titles, descriptions,
JSON-LD presence and required @types, no unresolved [[placeholders]], robots and
sitemap correctness, noindex on /fund/, shared assets, and the link spine.
Spine sources that are not live yet are reported as "skip (not live)" — that is
expected until later phases ship them.

This script never fixes anything. It prints PASS or FAIL and exits accordingly.

Grow SPINE and SCHEMA_REQUIRED as the graph grows (see the plan, Phase 4).
"""

import json
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

PROD = "https://answernorth.com"

# Every indexable page: path -> set of JSON-LD @types that must appear.
# An empty set means "page must be well-formed but no schema is required".
SCHEMA_REQUIRED = {
    "/": {"Organization", "Person", "WebSite", "CreativeWork"},
    "/astra-q/": {"CreativeWork"},
    "/tltll/": {"CreativeWork"},
    "/company/": {"Organization", "AboutPage"},
    "/people/aj-gordon/": {"Person", "ProfilePage"},
    "/research/": set(),
    "/research/research-that-argues-with-itself/": {"ScholarlyArticle"},
    "/privacy/": set(),
    "/terms/": set(),
}

# Pages that must carry an og:image (the entity layer plus the article).
OG_IMAGE_REQUIRED = {
    "/", "/astra-q/", "/tltll/", "/company/", "/people/aj-gordon/",
    "/research/", "/research/research-that-argues-with-itself/",
}

# The enforced link graph: source page -> pages it must link to.
# A source that 404s is skipped (not yet live); once live, its edges are enforced.
SPINE = {
    # live today
    "/": ["/astra-q/", "/tltll/", "/company/"],
    "/astra-q/": ["/research/research-that-argues-with-itself/", "/company/"],
    "/tltll/": ["/astra-q/", "/company/"],
    "/company/": ["/people/aj-gordon/", "/astra-q/", "/tltll/", "/research/"],
    "/people/aj-gordon/": ["/company/", "/astra-q/", "/tltll/"],
    "/research/research-that-argues-with-itself/": ["/astra-q/", "/people/aj-gordon/"],
    # phase 2 (skip until live)
    "/astra-q/methodology/": ["/astra-q/"],
    "/research/governed-ai/": ["/astra-q/methodology/"],
    # phase 3 (skip until live)
    "/research/astra-q-external-oracle-validation/": ["/astra-q/methodology/", "/astra-q/"],
    "/research/astra-q-momentum-replication/": ["/astra-q/methodology/", "/astra-q/"],
}

SITEMAP_EXPECTED = {
    PROD + p for p in (
        "/", "/astra-q/", "/tltll/", "/company/", "/people/aj-gordon/",
        "/research/", "/research/research-that-argues-with-itself/",
        "/privacy/", "/terms/",
    )
}

UA = "answernorth-verifier/3 (+https://answernorth.com)"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonicals = []
        self.metas = {}          # name-or-property -> content
        self.title = ""
        self._in_title = False
        self.jsonld = []
        self._in_jsonld = False
        self._jsonld_buf = []
        self.hrefs = []
        self.h1 = 0
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonicals.append(a.get("href", ""))
        elif tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.metas[key.lower()] = a.get("content", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_buf = []
        elif tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        elif tag == "h1":
            self.h1 += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buf))

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        self.text_parts.append(data)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # DNS, timeout, TLS…
        return None, str(e)


def ld_types(node, out):
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            out.add(t)
        elif isinstance(t, list):
            out.update(x for x in t if isinstance(x, str))
        for v in node.values():
            ld_types(v, out)
    elif isinstance(node, list):
        for v in node:
            ld_types(v, out)


def norm_path(url):
    p = urlparse(url).path or "/"
    if not p.endswith("/") and "." not in p.rsplit("/", 1)[-1]:
        p += "/"
    return p


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else PROD).rstrip("/")
    fails, skips = [], []

    def ok(msg):
        print(f"ok    {msg}")

    def fail(msg):
        fails.append(msg)
        print(f"FAIL  {msg}")

    def skip(msg):
        skips.append(msg)
        print(f"skip  {msg}")

    pages = {}

    # ---- indexable pages -------------------------------------------------
    for path, want_types in SCHEMA_REQUIRED.items():
        status, body = fetch(base + path)
        if status != 200:
            fail(f"{path} HTTP {status}")
            continue
        p = Page()
        p.feed(body)
        pages[path] = p

        if len(p.canonicals) != 1:
            fail(f"{path} has {len(p.canonicals)} canonical links (want exactly 1)")
        elif p.canonicals[0] != PROD + path:
            fail(f"{path} canonical is {p.canonicals[0]!r} (want {PROD + path!r})")
        else:
            ok(f"{path} canonical")

        if not p.title.strip():
            fail(f"{path} has no <title>")
        if not p.metas.get("description", "").strip():
            fail(f"{path} has no meta description")
        if p.h1 != 1:
            fail(f"{path} has {p.h1} <h1> elements (want exactly 1)")
        if "[[" in "".join(p.text_parts):
            fail(f"{path} contains unresolved [[placeholder]] text")
        if path in OG_IMAGE_REQUIRED and not p.metas.get("og:image", "").strip():
            fail(f"{path} missing og:image")

        found_types = set()
        parse_err = False
        for blob in p.jsonld:
            try:
                ld_types(json.loads(blob), found_types)
            except json.JSONDecodeError as e:
                parse_err = True
                fail(f"{path} JSON-LD does not parse: {e}")
        if want_types and not p.jsonld and not parse_err:
            fail(f"{path} has no JSON-LD (want types {sorted(want_types)})")
        elif want_types - found_types:
            fail(f"{path} JSON-LD missing types {sorted(want_types - found_types)}")
        elif want_types:
            ok(f"{path} schema {sorted(want_types)}")

    # ---- /fund/ stays out of the index ----------------------------------
    status, body = fetch(base + "/fund/")
    if status != 200:
        fail(f"/fund/ HTTP {status}")
    else:
        p = Page()
        p.feed(body)
        robots_meta = p.metas.get("robots", "")
        if "noindex" not in robots_meta:
            fail("/fund/ is missing <meta name=robots content=noindex>")
        else:
            ok("/fund/ noindex")

    # ---- robots.txt ------------------------------------------------------
    status, body = fetch(base + "/robots.txt")
    if status != 200:
        fail(f"/robots.txt HTTP {status}")
    else:
        if "Disallow: /fund/" not in body:
            fail("robots.txt does not disallow /fund/")
        if f"Sitemap: {PROD}/sitemap.xml" not in body:
            fail("robots.txt does not declare the sitemap")
        if "Disallow: /fund/" in body and f"Sitemap: {PROD}/sitemap.xml" in body:
            ok("/robots.txt")

    # ---- sitemap.xml -----------------------------------------------------
    status, body = fetch(base + "/sitemap.xml")
    if status != 200:
        fail(f"/sitemap.xml HTTP {status}")
    else:
        import re
        locs = set(re.findall(r"<loc>\s*(.*?)\s*</loc>", body))
        if locs != SITEMAP_EXPECTED:
            missing = sorted(SITEMAP_EXPECTED - locs)
            extra = sorted(locs - SITEMAP_EXPECTED)
            fail(f"sitemap URL set wrong (missing {missing}, extra {extra})")
        elif "<priority>" in body:
            fail("sitemap contains <priority> (plan says none)")
        else:
            ok(f"/sitemap.xml ({len(locs)} URLs)")

    # ---- shared assets ---------------------------------------------------
    for asset in ("/og-default.png", "/logo.png", "/favicon.svg"):
        status, _ = fetch(base + asset)
        if status != 200:
            fail(f"{asset} HTTP {status}")
        else:
            ok(asset)

    # ---- link spine ------------------------------------------------------
    for source, targets in SPINE.items():
        if source in pages:
            p = pages[source]
        else:
            status, body = fetch(base + source)
            if status == 404:
                skip(f"spine source {source} (not live)")
                continue
            if status != 200:
                fail(f"spine source {source} HTTP {status}")
                continue
            p = Page()
            p.feed(body)
        link_paths = {norm_path(urljoin(PROD + source, h)) for h in p.hrefs}
        for target in targets:
            if target in link_paths:
                ok(f"spine {source} -> {target}")
            else:
                fail(f"spine {source} does not link to {target}")

    # ---- verdict ---------------------------------------------------------
    print()
    print(f"{len(fails)} failed, {len(skips)} skipped, base={base}")
    if fails:
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
