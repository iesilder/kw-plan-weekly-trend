"""Deterministic title-only financial-institution names; no network or inference."""

import json
from pathlib import Path
import re
import unicodedata

_WORD = r"가-힣A-Za-z0-9"
_POSTPOSITIONS = "은는이가을를의에와과도로서께도"
# Legal/company derivatives still contain the institution name; unrelated noun
# continuations (캐피탈리즘, 은행잎) do not.
_NAME_END = r"(?=$|[^" + _WORD + r"]|[" + _POSTPOSITIONS + r"]|법|노조|회장|대표|사장|원장|위원장|은행장|자회사|계열|그룹|사(?:의|가|는|와|를|$))"
_KNOWN_NAME_END = r"(?:" + _NAME_END + r"|(?=장(?:$|[^" + _WORD + r"]|[" + _POSTPOSITIONS + r"])))"
_PRODUCT_CARD_ENDINGS = ("교통카드", "신용카드", "체크카드", "선불카드")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError("Institution names must be short nonempty strings")
    if value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid institution name")
    return unicodedata.normalize("NFKC", value)


def _words(value):
    if not isinstance(value, list):
        raise ValueError("Institution lists must be arrays")
    return [_text(item) for item in value]


def load_matcher(root):
    """Load editable names plus the project's existing company/subject aliases.

    The returned dictionary contains only serializable data so callers can bind
    their independent raw-list generation to the exact effective matcher.
    """
    root = Path(root)
    with (root / "config/financial_institutions.json").open(encoding="utf-8") as handle:
        config = json.load(handle)
    if (not isinstance(config, dict) or config.get("schema_version") != "1.0" or
            not isinstance(config.get("version"), str) or
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", config["version"])):
        raise ValueError("Invalid financial institution matcher configuration")
    result = {"schema_version":"1.0", "version":config["version"]}
    for key in ("suffixes", "excluded_institutions", "generic_names", "generic_prefixes"):
        result[key] = sorted(set(_words(config.get(key))), key=lambda word: (-len(word), word))
    if not result["suffixes"] or any(not re.fullmatch(r"[가-힣]{2,12}", word) for word in result["suffixes"]):
        raise ValueError("Invalid institution suffixes")
    rows = config.get("institutions")
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Invalid financial institution registry")
    with (root / "config/companies.json").open(encoding="utf-8") as handle:
        companies = json.load(handle)["companies"]
    with (root / "config/classification_rules.json").open(encoding="utf-8") as handle:
        scope = json.load(handle)["subject_scope"]
    rows = [*rows, *(row for row in companies if row.get("enabled", True)),
            *(row for key in ("related_groups", "authorities", "market_infrastructure") for row in scope[key])]
    excluded = set(result["excluded_institutions"])
    by_name = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid financial institution entry")
        name = _text(row.get("name"))
        aliases = _words(row.get("aliases", []))
        if name not in excluded:
            by_name.setdefault(name, set()).update([name, *aliases])
    # An exact institution name takes precedence over another group's alias.
    canonical = {name.casefold(): name for name in by_name}
    owners = {}
    for name, aliases in by_name.items():
        for alias in sorted(aliases):
            key = alias.casefold()
            owner = canonical.get(key, name)
            owners.setdefault(key, (alias, owner))
    institutions = {}
    for alias, owner in owners.values():
        institutions.setdefault(owner, []).append(alias)
    result["institutions"] = [{"name":name,"aliases":sorted(set(aliases), key=lambda word:(-len(word),word))}
                              for name, aliases in sorted(institutions.items())]
    return result


def _known_pattern(alias):
    # Names may contain ordinary spaces in headlines. Short abbreviations need
    # Korean boundaries too: 금융위 must not mean 금융위기, 토스 not 토스카나.
    escaped = r"\s*".join(re.escape(part) for part in alias.split())
    if re.fullmatch(r"[A-Za-z0-9 .&-]+", alias):
        return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"(?![A-Za-z0-9])", re.I)
    if len(alias.replace(" ", "")) <= 3:
        return re.compile(r"(?<![" + _WORD + r"])" + escaped + _KNOWN_NAME_END, re.I)
    if alias[0].isascii() and alias[0].isalnum():
        escaped = r"(?<![A-Za-z0-9])" + escaped
    return re.compile(escaped + _KNOWN_NAME_END, re.I)


def match_title(title, matcher):
    """Return distinct matched names in first-mention order.

    Configured names/aliases win overlapping suffix guesses. A suffix fallback
    only recognizes a contiguous proper-looking token, never a generic sector
    word; this remains a title name match rather than semantic classification.
    """
    if not isinstance(title, str) or len(title) > 10000:
        raise ValueError("Invalid title for institution matching")
    title = unicodedata.normalize("NFKC", title)
    found = []
    for row in matcher["institutions"]:
        for alias in row["aliases"]:
            for match in _known_pattern(alias).finditer(title):
                found.append((match.start(), match.end(), row["name"], True))
    # Reserve all configured-name spans before adding conservative guesses.
    suffix = "|".join(re.escape(word) for word in matcher["suffixes"])
    pattern = re.compile(r"(?<![" + _WORD + r"])([가-힣A-Za-z][가-힣A-Za-z0-9]{0,24}?(?:" + suffix + r"))" + _NAME_END)
    generic = {word.casefold() for word in matcher["generic_names"]}
    for match in pattern.finditer(title):
        name = match[1]
        prefix = next((name[:-len(ending)] for ending in matcher["suffixes"] if name.endswith(ending)), "")
        if (len(prefix) < 2 or name.casefold() in generic or
                any(name.endswith(ending) for ending in _PRODUCT_CARD_ENDINGS) or
                any(prefix.startswith(word) for word in matcher["generic_prefixes"]) or
                any(start < match.end() and match.start() < end for start,end,_,_ in found)):
            continue
        found.append((match.start(), match.end(), name, False))
    # A longer explicit occurrence suppresses overlapping shorter aliases, e.g.
    # 토스증권 should not add the parent 토스 solely because of that same token.
    selected = []
    for candidate in sorted(found, key=lambda row:(-(row[1]-row[0]),not row[3],row[0],row[2])):
        if not any(a < candidate[1] and candidate[0] < b for a,b,_,_ in selected):
            selected.append(candidate)
    names = []
    for _,_,name,_ in sorted(selected, key=lambda row:(row[0],row[2])):
        if name not in names:
            names.append(name)
    return names
