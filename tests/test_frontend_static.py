"""Cheap checks on the single-page frontend that catch the mistakes edits tend
to introduce: syntax errors, two functions with the same name (the later one
silently replaces the earlier), and lookups of elements that don't exist."""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

HTML = (Path(__file__).resolve().parent.parent / "backend" / "static" / "index.html").read_text(encoding="utf-8")
SCRIPT = re.search(r"<script>(.*)</script>", HTML, re.S).group(1)


def test_no_duplicate_top_level_functions_or_variables():
    names = re.findall(r"^(?:async )?function (\w+)", SCRIPT, re.M)
    names += re.findall(r"^(?:const|let|var) (\w+)", SCRIPT, re.M)
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    assert duplicates == []


def test_every_element_looked_up_by_id_exists():
    looked_up = set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    defined = set(re.findall(r'id="([\w-]+)"', HTML))
    defined |= set(re.findall(r"\.id = '([\w-]+)'", SCRIPT))  # elements created in code
    assert sorted(looked_up - defined) == []


def test_script_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--check", "-"], input=SCRIPT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
