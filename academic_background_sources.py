"""Conservative non-Wikipedia evidence for CuiSync dish history and culture.

UNESCO lists are official heritage metadata; Crossref abstracts and Google Books
publisher descriptions are *discoverable excerpts*, not independently checked full
publications. Never convert a search hit or bibliographic title into a historical
claim. Each source is linked to the matching dish AND country; unknowns are omitted.
"""
import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote

from dish_info_common import country_key, normalize

UNESCO_API = "https://data.unesco.org/api/explore/v2.1/catalog/datasets/ich001/records"
CROSSREF_API = "https://api.crossref.org/works"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
API_ENDPOINTS = frozenset((UNESCO_API, CROSSREF_API, GOOGLE_BOOKS_API))
MAX_TEXT = 2800
COUNTRY_ISO2 = {
    "PHILIPPINES": "PH", "CHINA": "CN", "JAPAN": "JP",
    "SOUTH KOREA": "KR", "NORTH KOREA": "KP", "THAILAND": "TH",
    "TURKIYE": "TR", "SAUDI ARABIA": "SA",
    "VIETNAM": "VN", "INDONESIA": "ID", "MALAYSIA": "MY",
    "SINGAPORE": "SG", "INDIA": "IN", "PAKISTAN": "PK",
    "BANGLADESH": "BD", "SRI LANKA": "LK", "NEPAL": "NP",
    "MYANMAR": "MM", "CAMBODIA": "KH", "LAOS": "LA",
    "TAIWAN": "TW", "MONGOLIA": "MN", "BRUNEI": "BN",
}


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"p", "br", "div"} and not self.skip:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "br", "div"} and not self.skip:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _plain(value):
    if not isinstance(value, str):
        return ""
    parser = _TextOnly()
    parser.feed(value[:16000])
    return " ".join(unescape("".join(parser.parts)).split())[:MAX_TEXT]


def _alias_match(value, aliases):
    haystack = normalize(value)
    return next((alias for alias in aliases if re.search(r"(?<!\w)" + re.escape(normalize(alias)) + r"(?!\w)", haystack)), None)


def _co_mentioned(text, alias, country):
    """Avoid accepting unrelated country mentions elsewhere in a long blurb."""
    return any(_alias_match(sentence, [alias]) and _country_match(sentence, country)
               for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip())


def _country_match(value, country):
    from dish_backgrounds import _has_term, _terms
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    return _has_term(normalize(str(value or "")), _terms(country))


def _country_in_heritage_record(fields, country):
    """UNESCO ich001 records use `countries` ISO-2 codes, not country names."""
    values = fields.get("countries")
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            values = [values]
    if isinstance(values, (list, tuple)):
        codes = {str(x).strip().upper() if isinstance(x, str)
                 else str(x.get("code") or x.get("iso2") or "").upper()
                 for x in values if isinstance(x, (str, dict))}
        if COUNTRY_ISO2.get(country) in codes:
            return True
    return _country_match(_country_metadata(fields), country)


def _country_metadata(fields):
    """Only fields explicitly identified as country fields can prove identity."""
    return " ".join(str(fields.get(key) or "") for key in (
        "country_en", "countries_en", "country", "countries", "country_name", "countries_name_en",
        "states_parties", "state_party", "states_parties_en", "countries_involved_en"
    ))


def _source(*, title, url, excerpt, kind, country, alias, basis, evidence_kind):
    return {"title": title[:300], "url": url, "excerpt": _plain(excerpt),
            "source_type": kind, "evidence_kind": evidence_kind,
            "retrieved_at": _now(), "matched_country": country, "matched_aliases": [alias],
            "name_match_basis": basis, "country_match_basis": "explicit_source_text_or_metadata",
            "retrieval_version": 5, "verification_status": "unreviewed"}


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _json(endpoint, params):
    from dish_backgrounds import EvidenceUnavailable, _fetch
    try:
        data = json.loads(_fetch(endpoint, params))
    except (TypeError, ValueError):
        raise EvidenceUnavailable("The background evidence API returned invalid JSON.", code="invalid_response", provider=endpoint.split('/')[2]) from None
    if not isinstance(data, dict):
        raise EvidenceUnavailable("The background evidence API returned an invalid result.", code="invalid_response", provider=endpoint.split('/')[2])
    return data


def _unesco(recipe, aliases, country):
    """Use UNESCO's public ICH records, with exact dish and country checks.

    The dataset identifies heritage *practices*. A listing of a broad cuisine
    never establishes that an individual dish has that heritage designation.
    """
    found = []
    for alias in aliases[:3]:
        if len(normalize(alias)) < 4:
            continue
        data = _json(UNESCO_API, {"where": '"' + alias.replace('"', '')[:90] + '"', "limit": 20})
        for item in data.get("results", [])[:20]:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else item
            title = _plain(fields.get("title_en") or fields.get("title") or "")
            matched = _alias_match(title, [alias])
            if not matched or not _country_in_heritage_record(fields, country):
                continue
            # A heritage *title* supports only the limited statement that UNESCO
            # lists the practice, not an invented history of the dish.
            description = _plain(fields.get("description_en") or fields.get("summary_en") or fields.get("short_description_en") or "")
            if not description:
                description = f'UNESCO lists the cultural heritage practice "{title}" for {country}.'
            # Direct UNESCO links are preferable; the dataset browse page is a
            # truthful reference if the record does not publish a stable link.
            candidate = str(fields.get("http_url_en") or fields.get("url_en") or fields.get("url") or fields.get("link_en") or "")
            url = candidate if candidate.startswith("https://ich.unesco.org/en/") else "https://data.unesco.org/explore/dataset/ich001/"
            found.append(_source(title=title, url=url, excerpt=description, kind="unesco_data",
                                 country=country, alias=matched, basis="official_heritage_record",
                                 evidence_kind="official_listing"))
            if len(found) >= 2:
                return found
    return found


def _crossref(recipe, aliases, country):
    found = []
    for alias in aliases[:3]:
        # Bibliographic search considers more publication metadata than title-only
        # search. The abstract itself must still prove dish/country relevance.
        params = {"query.bibliographic": f'{alias} {country} culinary history culture',
                  "rows": 12, "select": "DOI,title,abstract,type"}
        contact = os.environ.get("CROSSREF_CONTACT_EMAIL", "").strip()
        if contact and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact):
            params["mailto"] = contact
        data = _json(CROSSREF_API, params)
        for work in (data.get("message") or {}).get("items", [])[:12]:
            if not isinstance(work, dict):
                continue
            titles = work.get("title") or []
            title = _plain(titles[0]) if isinstance(titles, list) and titles else ""
            abstract = _plain(work.get("abstract"))
            doi = str(work.get("DOI") or "").strip()
            if (not re.fullmatch(r"10\.\d{4,9}/[^\s<>]+", doi, re.I)
                    or not _alias_match(title + " " + abstract, [alias])
                    or not _alias_match(abstract, [alias])
                    or not _co_mentioned(abstract, alias, country)
                    or len(abstract) < 100):
                continue  # A paper *title* alone is not evidence of its content.
            found.append(_source(title=title, url="https://doi.org/" + quote(doi, safe="/.-_()"),
                                 excerpt=abstract, kind="crossref", country=country,
                                 alias=alias, basis="research_abstract_dish_country",
                                 evidence_kind="publication_abstract"))
            if len(found) >= 2:
                return found
    return found


def _google_books(recipe, aliases, country):
    found = []
    for alias in aliases[:3]:
        params = {"q": '"' + alias.replace('"', '') + '" ' + country + " cuisine history",
                  "maxResults": 12, "printType": "books"}
        if os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip():
            params["key"] = os.environ["GOOGLE_BOOKS_API_KEY"].strip()
        data = _json(GOOGLE_BOOKS_API, params)
        for work in data.get("items", [])[:12]:
            if not isinstance(work, dict):
                continue
            volume = work.get("volumeInfo") or {}
            if not isinstance(volume, dict):
                continue
            title = _plain(volume.get("title") or "")
            description = _plain(volume.get("description"))
            volume_id = str(work.get("id") or "")
            if (not re.fullmatch(r"[A-Za-z0-9_-]{4,60}", volume_id)
                    or not _alias_match(description, [alias])
                    or not _co_mentioned(description, alias, country)
                    or len(description) < 80):
                continue  # Publisher blurbs, not the book's text.
            found.append(_source(title=title, url="https://books.google.com/books?id=" + quote(volume_id),
                                 excerpt=description, kind="google_books", country=country,
                                 alias=alias, basis="publisher_description_dish_country",
                                 evidence_kind="publisher_description"))
            if len(found) >= 2:
                return found
    return found



HISTORY_CUES = re.compile(r"\b(?:histori(?:cal|c)?|origin|evolv|centur(?:y|ies)|develop|introduc|"
                          r"past|traditional|heritage|ancient|colonial|emerg|documented)\b", re.I)
CULTURE_CUES = re.compile(r"\b(?:cultur(?:al|e)|tradition|custom|communit|famil(?:y|ies)|"
                          r"festival|ceremon|ritual|celebrat|heritage|practice|sharing|"
                          r"regional|identity|social|occasion)\b", re.I)


def evidence_richness(source):
    """Rank *available excerpt depth*, not search-provider credibility alone.

    Source metadata or a title without supporting prose cannot establish a
    historical claim. Scoring is used only to select reading material for GPT.
    """
    excerpt = str(source.get("excerpt") or "")
    words = len(excerpt.split())
    score = min(words / 45, 6)
    score += 1.5 if HISTORY_CUES.search(excerpt) else 0
    score += 1.5 if CULTURE_CUES.search(excerpt) else 0
    score += {"unesco": 2.5, "unesco_data": 2.5,
              "crossref": 1.5, "google_books": 0.75}.get(source.get("source_type"), 0)
    return round(score, 2)


# Search priority is evidence policy, not a claim that the first source is
# always sufficient to substantiate history *and* culture.
SOURCE_PRIORITY = {"unesco_data": 0, "unesco": 0,
                   "crossref": 1, "google_books": 2}


def choose_diverse_evidence(evidence, limit=7):
    """Keep credible, varied excerpts in UNESCO > Crossref > Books order.

    Within a provider, prefer the most substantive excerpt. A title alone
    never constitutes supporting evidence. Wikipedia is NOT admitted here;
    it is handled separately after the three primary APIs return no evidence.
    """
    candidates = {}
    for item in evidence:
        if not isinstance(item, dict) or not item.get("url") or not item.get("excerpt"):
            continue
        if item.get("source_type") not in SOURCE_PRIORITY:
            continue
        url = item["url"]
        if url not in candidates or evidence_richness(item) > evidence_richness(candidates[url]):
            candidates[url] = item
    ordered = sorted(candidates.values(), key=lambda x: (
        SOURCE_PRIORITY[x["source_type"]], -evidence_richness(x), x["url"]))
    chosen, providers = [], set()
    # Include one from each available API so supported history/culture can be
    # drawn from different evidence, while keeping the desired provider order.
    for item in ordered:
        provider = item["source_type"]
        if provider not in providers:
            chosen.append(item)
            providers.add(provider)
        if len(chosen) >= limit:
            return chosen
    for item in ordered:
        if item not in chosen:
            chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def retrieve_academic_evidence(recipe):
    """Search exactly the three primary APIs, in explicit priority order.

    Curated dish-by-dish catalogs and encyclopedia sources are excluded.
    Providers are all checked so UNESCO culture-only evidence may be
    supplemented by Crossref research or Google Books descriptions.
    """
    from dish_backgrounds import EvidenceUnavailable, _aliases
    aliases = _aliases(recipe)
    country = country_key(recipe.get("country", ""))
    if not aliases or not country:
        return []
    evidence, errors = [], []
    for lookup in (_unesco, _crossref, _google_books):
        try:
            evidence.extend(lookup(recipe, aliases, country))
        except EvidenceUnavailable as error:
            errors.append(error)
        except (KeyError, IndexError, TypeError, ValueError):
            # Unexpected provider payloads must not turn into historical claims.
            continue
    unique = choose_diverse_evidence(evidence)
    if not unique and errors:
        raise errors[0]
    return unique
