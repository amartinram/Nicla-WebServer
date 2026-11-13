"""
Does every English string on the pages and in the emails have a Spanish twin?

    python check_i18n.py

Scans the templates for _("...") and app.py for t("...") / i18n.t("...", ...),
then reports strings missing from i18n.ES and translations whose {placeholders}
or <b> markup differ from the English. A missing string is not an error at run
time -- it shows in English -- so run this after adding or rewording text.
Exits 1 if anything is wrong, so it can gate a deploy.
"""
import re
import string
import sys
from pathlib import Path

import i18n

ROOT = Path(__file__).parent
CALL = re.compile(r"""(?<![\w.])(?:_|t|i18n\.t)\(\s*(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""")


def fields(s):
    return sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)


def markup(s):
    return sorted(re.findall(r"</?\w+>", s))


def used_strings():
    found = {}
    sources = list((ROOT / "templates").glob("*.html")) + [ROOT / "app.py"]
    for path in sources:
        for m in CALL.finditer(path.read_text(encoding="utf-8")):
            text = m.group("s").encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
            found.setdefault(text, path.name)
    return found


def main():
    problems = []
    for text, where in sorted(used_strings().items()):
        if text not in i18n.ES:
            problems.append(f"missing  ({where}): {text!r}")
    for en, es in i18n.ES.items():
        if fields(en) != fields(es):
            problems.append(f"placeholders differ: {en!r} -> {es!r}")
        if markup(en) != markup(es):
            problems.append(f"markup differs: {en!r}")
    for kind, langs in i18n.EMAILS.items():
        missing = set(i18n.LANGS) - set(langs)
        if missing:
            problems.append(f"email {kind}: no {', '.join(sorted(missing))} version")
            continue
        for part, (en, es) in zip(("subject", "body"), zip(langs["en"], langs["es"])):
            if fields(en) != fields(es):
                problems.append(f"email {kind} {part}: placeholders differ")
    print("\n".join(problems) or f"OK: {len(used_strings())} strings, all translated.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
