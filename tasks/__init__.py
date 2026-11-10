"""Task definitions: twenty JSON specs (data) plus ``checkers/`` (code).

The split is deliberate. A task file says *what* to ask and *how many* calls a
perfect agent would need; a checker says *whether the delivery answered*. The
checker lives in Python because comparing an answer to the truth is not
something a JSON file can do, and it lives under ``tasks/`` so the two halves of
one task cannot drift into separate directories.
"""
