"""Tests for ``scripts/hooks/news_guard.py`` — the CI gate that requires a
``news/<PR>.<type>`` changelog fragment on every PR.

Failure mode these tests prevent (#858): the gate used to live inline in
``.github/workflows/news-gate.yml`` as ``grep -Fq '[skip-news:'`` — a
fixed-string PREFIX match. It needed no reason and no closing bracket, so
``[skip-news:]``, a dangling ``[skip-news:``, and **any PR body that merely
mentioned the token** (a docs PR quoting ``news/README.md``, a brief pasting
the documented ``[skip-news: <reason>]`` placeholder) all turned the only
changelog gate into opt-in — while the job printed "Bypass token present"
and went green.

Both halves are tested by name, because a guard that refuses everything is
as broken as one that refuses nothing:

* :class:`TestBypassRejected` — the malformed / placeholder / quoted forms
  must NOT bypass, and the failure message must name why.
* :class:`TestBypassAccepted` — every ordinary honest token must still
  bypass, punctuation and all.
* :class:`TestFragment` — the no-token path: a real fragment allows, no
  fragment blocks, and near-miss filenames don't count.
* :class:`TestBypassMustStayOnOneLine` — #872, the same hole from the other
  side: the reason class matched newlines, so an unclosed opener was closed
  by any later ``]`` and swallowed the paragraphs in between.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "scripts" / "hooks" / "news_guard.py"

# The exact line news/README.md publishes. A docs PR that quotes it must not
# thereby disable the gate — that is the bug in #858.
README_QUOTE = (
    "the fragment by including the literal token `[skip-news: <reason>]`"
)


def _load_hook():
    """Import the script by path without executing ``main()``."""
    spec = importlib.util.spec_from_file_location("news_guard", str(HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(
    monkeypatch,
    *,
    title: str = "fix: something",
    body: str = "",
    added: tuple[str, ...] = (),
) -> int:
    """Invoke ``main()`` in exactly the shape ``news-gate.yml`` uses:
    ``--ci`` plus PR_TITLE / PR_BODY / ADDED_FILES from the environment.
    No shell, no argv-borne PR text (a PR body may contain ``$(...)``,
    backticks and quotes)."""
    mod = _load_hook()
    monkeypatch.setattr(sys, "argv", ["news_guard.py", "--ci"])
    monkeypatch.setenv("PR_TITLE", title)
    monkeypatch.setenv("PR_BODY", body)
    monkeypatch.setenv("ADDED_FILES", "\n".join(added))
    return mod.main()


# ── the bypass token must not fire on a malformed or quoted token ─────────


class TestBypassRejected:
    """#858: none of these shapes carries a reason a reviewer can read, so
    none of them may disable the gate."""

    def test_empty_reason_blocks_and_names_the_problem(self, monkeypatch, capsys):
        rc = _run(monkeypatch, body="nothing to record [skip-news:]")
        assert rc != 0
        err = capsys.readouterr().err
        assert "its reason is empty" in err

    def test_whitespace_only_reason_blocks(self, monkeypatch, capsys):
        rc = _run(monkeypatch, title="fix: typo [skip-news:   ]")
        assert rc != 0
        assert "its reason is empty" in capsys.readouterr().err

    def test_unclosed_bracket_blocks_and_names_the_problem(
        self, monkeypatch, capsys
    ):
        """``grep -Fq`` never read past the colon, so a token whose bracket
        never closes passed. It must now block, and say so rather than
        printing the generic "no fragment found" text."""
        rc = _run(monkeypatch, body="[skip-news: comment typo\nrest of body")
        assert rc != 0
        err = capsys.readouterr().err
        assert "closing `]`" in err

    def test_placeholder_reason_blocks(self, monkeypatch, capsys):
        """The documented placeholder pasted verbatim from a brief or a
        template. It is the *documentation* of the token, not a reason."""
        rc = _run(monkeypatch, body="## What\nstuff\n[skip-news: <reason>]\n")
        assert rc != 0
        err = capsys.readouterr().err
        assert "placeholder" in err

    def test_placeholder_quoted_in_backticks_blocks(self, monkeypatch, capsys):
        """A docs PR that quotes news/README.md in prose — the motivating
        case in #858. Backticks are not magic; the point is that the reason
        is the literal placeholder."""
        rc = _run(monkeypatch, body=f"Documented the bypass:\n{README_QUOTE}\n")
        assert rc != 0
        assert "placeholder" in capsys.readouterr().err

    def test_bare_token_name_in_prose_blocks(self, monkeypatch, capsys):
        """``... you can skip-news: this ...`` — no bracket at all, so it was
        never a token; the old prefix grep did not fire here either, but the
        message must stay the plain no-fragment one."""
        rc = _run(monkeypatch, body="We could skip-news: this if we wanted.")
        assert rc != 0
        err = capsys.readouterr().err
        assert "no news/<PR>.<type> fragment" in err
        assert "placeholder" not in err


# ── every honest token must still bypass ──────────────────────────────────


class TestBypassAccepted:
    """The false-positive half. A gate that rejects legitimate bypasses
    stops the project just as effectively as one that rejects nothing."""

    def test_real_reason_in_body_bypasses(self, monkeypatch):
        rc = _run(monkeypatch, body="Body text.\n[skip-news: comment typo]\n")
        assert rc == 0

    def test_real_reason_in_title_bypasses(self, monkeypatch):
        rc = _run(monkeypatch, title="chore: bump lockfile [skip-news: no user diff]")
        assert rc == 0

    def test_punctuation_heavy_reason_bypasses(self, monkeypatch):
        """A real reason carries commas, colons, dashes, slashes, #refs and
        parentheses — only ``]`` ends the token."""
        rc = _run(
            monkeypatch,
            body=(
                "[skip-news: CI-only change (see #858): .github/workflows/*.yml "
                "+ tests — no user-visible behaviour, 100% internal]"
            ),
        )
        assert rc == 0

    def test_reason_after_leading_whitespace_bypasses(self, monkeypatch):
        """Whitespace BEFORE a real reason is formatting, not a blank
        reason — the two must not be confused."""
        rc = _run(monkeypatch, title="x [skip-news:    real reason here]")
        assert rc == 0

    def test_single_character_reason_bypasses(self, monkeypatch):
        """The bar is "non-blank", not "long" — don't raise it silently."""
        rc = _run(monkeypatch, body="whatever [skip-news: x] more")
        assert rc == 0

    def test_reason_mentioning_the_placeholder_word_bypasses(self, monkeypatch):
        """Only a reason that IS exactly ``<reason>`` is rejected. A real
        reason that happens to contain the word must still bypass."""
        rc = _run(
            monkeypatch,
            body="[skip-news: docs change: renames the <reason> placeholder]",
        )
        assert rc == 0

    def test_vestigial_empty_token_with_a_fragment_still_passes(self, monkeypatch):
        """A blank token alongside a real fragment is harmless — the gate's
        purpose is already met, so don't block on the leftover."""
        rc = _run(
            monkeypatch,
            body="[skip-news:]",
            added=("news/860.bugfix",),
        )
        assert rc == 0


# ── the no-token path: the fragment itself ────────────────────────────────


class TestFragment:
    def test_no_token_and_no_fragment_blocks(self, monkeypatch, capsys):
        rc = _run(monkeypatch, body="## What\nA real change.\n")
        assert rc != 0
        assert "no news/<PR>.<type> fragment" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "name",
        [
            "news/860.feature",
            "news/860.bugfix",
            "news/860.doc",
            "news/860.removal",
            "news/860.misc",
        ],
    )
    def test_every_documented_fragment_type_passes(self, monkeypatch, name):
        """The five suffixes in news/README.md's table are the contract
        towncrier consumes — all five must satisfy the gate."""
        rc = _run(monkeypatch, added=(name,))
        assert rc == 0

    def test_fragment_among_other_added_files_passes(self, monkeypatch):
        rc = _run(
            monkeypatch,
            added=("scripts/hooks/news_guard.py", "news/860.bugfix", "tests/t.py"),
        )
        assert rc == 0

    def test_pass_log_names_the_fragment_it_found(self, monkeypatch, capsys):
        """A green run must say WHICH fragment satisfied it — otherwise a
        triager cannot tell a real fragment from a bypass, and the old
        inline gate's "Found news fragment(s)" line is lost."""
        rc = _run(
            monkeypatch,
            added=("news/860.bugfix", "docs/whatever.md"),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "news/860.bugfix" in out
        assert "docs/whatever.md" not in out

    def test_pass_log_distinguishes_a_bypass_from_a_fragment(
        self, monkeypatch, capsys
    ):
        """The two green grounds must not look alike in the CI log — the
        #858 bug was invisible precisely because a bypass printed a success
        line nobody read closely."""
        rc = _run(monkeypatch, body="[skip-news: comment typo]")
        assert rc == 0
        assert "bypass token" in capsys.readouterr().out

    def test_unknown_suffix_does_not_count(self, monkeypatch, capsys):
        """``news/860.txt`` is not a type towncrier builds — it must not be
        mistaken for a fragment."""
        rc = _run(monkeypatch, added=("news/860.txt",))
        assert rc != 0
        assert "no news/<PR>.<type> fragment" in capsys.readouterr().err

    def test_news_readme_does_not_count_as_a_fragment(self, monkeypatch):
        """Editing the directory's own README is not a changelog entry —
        this PR is exactly that case."""
        rc = _run(monkeypatch, added=("news/README.md",))
        assert rc != 0

    def test_nested_path_does_not_count(self, monkeypatch):
        """The pattern is anchored: ``docs/news/860.bugfix`` is a different
        file in a different directory."""
        rc = _run(monkeypatch, added=("docs/news/860.bugfix",))
        assert rc != 0

    def test_blank_added_files_env_is_not_a_fragment(self, monkeypatch, capsys):
        """``ADDED_FILES`` is a newline-joined list; an empty value must
        read as "no files added", not as one empty filename."""
        rc = _run(monkeypatch, added=("",))
        assert rc != 0
        assert "no news/<PR>.<type> fragment" in capsys.readouterr().err

    def test_block_message_lists_every_fragment_type(self, monkeypatch, capsys):
        """The failure text is the whole UX of this gate — a developer who
        hits it must be able to fix it without opening the workflow."""
        rc = _run(monkeypatch)
        assert rc != 0
        err = capsys.readouterr().err
        for suffix in ("feature", "bugfix", "doc", "removal", "misc"):
            assert f".{suffix}" in err


# ── the reason may not span lines (#872) ──────────────────────────────────


# The two shapes a real PR body carries a stray `]` in, below an opener its
# author never closed: a markdown link and a task-list checkbox. Both used
# to close the token.
_LATER_BRACKET_BODIES = {
    "markdown-link": (
        "## What\n"
        "[skip-news: CI-only change\n"
        "\n"
        "See [the issue](https://example.invalid/872) for the trap.\n"
    ),
    "checklist-box": (
        "[skip-news: tooling only\n"
        "\n"
        "## Checklist\n"
        "- [ ] tests\n"
    ),
}


class TestBypassMustStayOnOneLine:
    """#872: the reason class was ``[^\\]]*``, which matches newlines. An
    opener left unclosed on its line was therefore closed by the next ``]``
    anywhere below, and every paragraph in between became "the reason" — a
    bypass nobody wrote, whose reviewer-visible reason is garbage.

    Both halves again: the swallowing shapes must block, and an honest
    one-line token in a CRLF body — the normal case, since that is how
    GitHub delivers ``PR_BODY`` — must still bypass.
    """

    @pytest.mark.parametrize("shape", sorted(_LATER_BRACKET_BODIES))
    def test_a_later_bracket_does_not_close_the_token(
        self, monkeypatch, capsys, shape
    ):
        rc = _run(monkeypatch, body=_LATER_BRACKET_BODIES[shape])
        assert rc != 0
        err = capsys.readouterr().err
        assert "closing `]` does not arrive on the same" in err

    def test_reason_split_across_a_crlf_does_not_bypass(
        self, monkeypatch, capsys
    ):
        """GitHub delivers PR bodies CRLF-terminated, so the class has to
        exclude ``\\r`` as well as ``\\n`` — excluding only ``\\n`` would
        leave the hole open on every real PR."""
        rc = _run(
            monkeypatch,
            body="[skip-news: CI-only\r\nchange, no user diff]\r\n",
        )
        assert rc != 0
        assert (
            "closing `]` does not arrive on the same"
            in capsys.readouterr().err
        )

    def test_one_line_token_in_a_crlf_body_still_bypasses(self, monkeypatch):
        """The false-positive half: a CRLF body is the ordinary case, not
        the attack. An honest token inside one must keep working."""
        rc = _run(
            monkeypatch,
            body=(
                "## What\r\nWorkflow-only change.\r\n"
                "[skip-news: CI wiring only, no user-visible diff]\r\n"
            ),
        )
        assert rc == 0

    def test_empty_token_with_its_bracket_on_the_next_line_reports_unclosed(
        self, monkeypatch, capsys
    ):
        """The #858 empty-reason twin carried the same ``\\s*`` hole: it
        read ``[skip-news:\\n]`` as an empty reason on one line. The token
        is unclosed, and the message has to say that instead."""
        rc = _run(monkeypatch, body="[skip-news:\n]\n")
        assert rc != 0
        err = capsys.readouterr().err
        assert "closing `]` does not arrive on the same" in err
        assert "its reason is empty" not in err
