#!/usr/bin/env python3
"""Test a Grew rule file against sentences that say what they expect.

    uv run tests/run_grew_tests.py french-post-parse.grs tests/french-post-parse.tests.conllu
    uv run tests/run_grew_tests.py other-languages/italian/italian-post-parse.grs tests/italian-post-parse.tests.conllu
    uv run tests/run_grew_tests.py other-languages/italian/italian-post-parse.grs tests/italian-post-parse.known-bugs.conllu

The known-bugs file states the correct result for cases the grammar gets wrong
today, so it is expected to fail until each bug is fixed.

Each test sentence is ordinary CoNLL-U (the parser's output, before any rule)
with one or more expectations in its comment lines:

    # expect_fix = aller_a          the rule fires: its name is added to a fix= in MISC
    # expect_no_fix = aller_a       the rule does not fire on this sentence
    # expect_tree = valid           the result is one rooted tree without cycles
    # expect_words = de le chat     the word forms after the rewrite, space-separated
    # expect_mwt = 2-3 du           the multiword-token lines after the rewrite, "; "-separated
    # expect_token = 4 head=3 deprel=obj
                                    one token after the rewrite (numbered as in the
                                    OUTPUT); column names form lemma upos xpos feats
                                    head deprel misc compare the whole column, any
                                    other Key=Value must be in FEATS or MISC
    # expect_absent = WhSubject     no token keeps this feature in FEATS or MISC

Every sentence is also checked for a valid tree, whatever it declares.
expect_words and expect_mwt pin down the tokenisation that package amalgames
produces, which childes.py relies on when it re-grids the table.
expect_token tests the rules that log nothing in fix= (Italian lemma repairs,
clitic re-attachment, cycle repair), and expect_absent the working-memory
marks a grammar must delete before the output is written.

Why each kind exists (French CHILDES, Sept 2026):
- expect_fix: a rule that never fires is silent - Grew does not warn.
- expect_no_fix: guards against over-firing, and pins the sentences that once
  broke the rewrite (one bad sentence makes Grew fail the WHOLE file).
- expect_tree: a rule that re-attaches a node under its own dependent builds a
  cycle, and deleting a root edge leaves a sentence with no root.

The whole file is also run in one go, as childes.py does, so a rule that
errors on any sentence (a loop past Grew's 10,000 steps, a fix= appended to a
node that has none) fails the test run instead of silently cancelling every
correction in the file.

Exit status 0 if all expectations hold, 1 otherwise.
"""

import os
import sys
import tempfile

from grewpy import GRS, Corpus


def blocks(path):
    comments, rows = [], []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            if rows:
                yield comments, rows
            comments, rows = [], []
        elif line.startswith("#") and not rows:
            comments.append(line)
        else:
            rows.append(line)
    if rows:
        yield comments, rows


def values(comments, key):
    return [c.split("=", 1)[1].strip() for c in comments
            if c.startswith(f"# {key} ") or c.startswith(f"# {key}=")]


def fired(rows):
    tags = set()
    for r in rows:
        c = r.split("\t")
        if len(c) >= 10:
            for part in c[9].split("|"):
                if part.startswith("fix="):
                    tags.update(part[4:].split(","))
    return tags


COLUMNS = {"form": 1, "lemma": 2, "upos": 3, "xpos": 4, "feats": 5,
           "head": 6, "deprel": 7, "misc": 9}


def token_mismatches(cols, pairs):
    """What differs between one output row and the 'key=value' pairs expected of it."""
    found = {}
    for col in (cols[5], cols[9]):
        for part in col.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                found[k] = v
    out = []
    for pair in pairs:
        key, want = pair.split("=", 1)
        got = cols[COLUMNS[key]] if key in COLUMNS else found.get(key, "(absent)")
        if got != want:
            out.append(f"has {key}={got}, expected {want}")
    return out


def tree_problem(rows):
    head = {}
    for r in rows:
        c = r.split("\t")
        if len(c) >= 8 and c[0].isdigit():
            if not c[6].isdigit():
                return f"token {c[0]} has head {c[6]}"
            head[c[0]] = c[6]
    roots = [t for t, h in head.items() if h == "0"]
    if len(roots) != 1:
        return f"{len(roots)} roots"
    for t in head:
        seen, n = set(), t
        while n != "0":
            if n in seen or n not in head:
                return f"cycle through token {t}"
            seen.add(n)
            n = head[n]
    return None


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    grs_path, test_path = sys.argv[1], sys.argv[2]
    tests = list(blocks(test_path))
    ids = [(values(c, "sent_id") or [f"test_{i}"])[0] for i, (c, _) in enumerate(tests)]
    if len(set(ids)) != len(ids):
        sys.exit("duplicate sent_id in the test file")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".conllu",
                                     delete=False) as f:
        for sid, (comments, rows) in zip(ids, tests):
            meta = [c for c in comments if not c.startswith("# sent_id")]
            f.write("\n".join([f"# sent_id = {sid}"] + meta + rows) + "\n\n")
        tmp = f.name
    try:
        # all at once, the way childes.py runs it: an error anywhere fails here
        result = GRS(grs_path).run(Corpus(tmp), strat="main")
    finally:
        os.unlink(tmp)

    failures, checked = [], 0
    for sid, (comments, rows) in zip(ids, tests):
        graphs = result.get(sid, [])
        if len(graphs) != 1:
            failures.append(f"{sid}: {len(graphs)} results, expected exactly 1")
            continue
        out = [l for l in graphs[0].to_conll().splitlines() if l and not l.startswith("#")]
        tags = fired(out)
        for rule in values(comments, "expect_fix"):
            checked += 1
            if rule not in tags:
                failures.append(f"{sid}: {rule} did not fire (fired: {', '.join(sorted(tags - {'none'})) or 'nothing'})")
        for rule in values(comments, "expect_no_fix"):
            checked += 1
            if rule in tags:
                failures.append(f"{sid}: {rule} fired but should not")
        cols = [l.split("\t") for l in out]
        for want in values(comments, "expect_words"):
            checked += 1
            got = " ".join(c[1] for c in cols if c[0].isdigit())
            if got != want:
                failures.append(f"{sid}: words are '{got}', expected '{want}'")
        for want in values(comments, "expect_mwt"):
            checked += 1
            got = "; ".join(f"{c[0]} {c[1]}" for c in cols if "-" in c[0])
            if got != want:
                failures.append(f"{sid}: multiword tokens are '{got}', expected '{want}'")
        by_id = {c[0]: c for c in cols if len(c) >= 10}
        for want in values(comments, "expect_token"):
            checked += 1
            tid, *pairs = want.split()
            if tid not in by_id:
                failures.append(f"{sid}: no token {tid} after the rules")
                continue
            for msg in token_mismatches(by_id[tid], pairs):
                failures.append(f"{sid}: token {tid} {msg}")
        for name in values(comments, "expect_absent"):
            checked += 1
            left = [c[0] for c in by_id.values()
                    if name in {p.split("=", 1)[0] for col in (c[5], c[9]) for p in col.split("|")}]
            if left:
                failures.append(f"{sid}: {name} left on token(s) {', '.join(left)}")
        checked += 1
        problem = tree_problem(out)
        if problem:
            failures.append(f"{sid}: not a tree after the rules ({problem})")

    for msg in failures:
        print("FAIL", msg)
    print(f"{len(tests)} sentences, {checked} expectations, {len(failures)} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
