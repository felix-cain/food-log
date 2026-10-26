"""Cheap checks on the frontend that catch the mistakes edits tend to
introduce: syntax errors, two functions with the same name (the scripts share
one global scope, so the later one silently replaces the earlier), lookups of
elements that don't exist, and references to files that don't exist."""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "backend" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPT_PATHS = re.findall(r'<script src="/static/([\w/.-]+)\?v=\{\{VERSION\}\}"></script>', HTML)
SCRIPTS = {path: (STATIC / path).read_text(encoding="utf-8") for path in SCRIPT_PATHS}
ALL_JS = "\n".join(SCRIPTS.values())


def test_scripts_load_core_first_and_main_last():
    assert SCRIPT_PATHS[0] == "js/core.js" and SCRIPT_PATHS[-1] == "js/main.js"
    assert sorted(SCRIPT_PATHS) == sorted(f"js/{p.name}" for p in (STATIC / "js").glob("*.js")), \
        "every file in js/ should be loaded, and only once"


def test_every_referenced_static_file_exists():
    for path in re.findall(r'(?:href|src)="/static/([\w/.-]+?)(?:\?v=\{\{VERSION\}\})?"', HTML):
        assert (STATIC / path).is_file(), path


def test_no_duplicate_top_level_functions_or_variables():
    names = re.findall(r"^(?:async )?function (\w+)", ALL_JS, re.M)
    names += re.findall(r"^(?:const|let|var) (\w+)", ALL_JS, re.M)
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    assert duplicates == []


def test_every_element_looked_up_by_id_exists():
    looked_up = set(re.findall(r"getElementById\('([\w-]+)'\)", ALL_JS))
    defined = set(re.findall(r'id="([\w-]+)"', HTML + ALL_JS))
    defined |= set(re.findall(r"\.id = '([\w-]+)'", ALL_JS))  # elements created in code
    assert sorted(looked_up - defined) == []


@pytest.mark.parametrize("path", SCRIPT_PATHS)
def test_script_parses(path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--check", str(STATIC / path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_page_is_served_with_versioned_assets(client):
    response = client.get("/")
    assert response.headers["cache-control"] == "no-cache"
    assert "{{VERSION}}" not in response.text
    assert re.search(r'src="/static/js/core\.js\?v=\w+"', response.text)
