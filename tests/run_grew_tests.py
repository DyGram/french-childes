#!/usr/bin/env python3
"""Test a Grew rule file against sentences that say what they expect.

    uv run tests/run_grew_tests.py french-post-parse.grs tests/french-post-parse.tests.conllu

Each test sentence is ordinary CoNLL-U (the parser's output, before any rule)
with one or more expectations in its comment lines:

    # expect_fix = aller_a          the rule fires: its name is added to a fix= in MISC
    # expect_no_fix = aller_a       the rule does not fire on this sentence
    # expect_tree = valid           the result is one rooted tree without cycles

Every sentence is also checked for a valid tree, whatever it declares.

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
