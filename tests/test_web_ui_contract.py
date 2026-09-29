"""Contract tests for the zero-build knowledge-graph web frontend."""

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "data" / "kg_web" / "static"
INDEX_HTML = STATIC / "index.html"
APP_JS = STATIC / "app.js"
STYLE_CSS = STATIC / "style.css"


def _strip_js_comments(source):
    """Remove JS comments without treating comment markers in strings as comments."""
    result = []
    index = 0
    quote = None
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            result.append(char)
            if char == "\\" and index + 1 < len(source):
                index += 1
                result.append(source[index])
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
            result.append(char)
        elif char == "/" and next_char == "/":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
            result.append("\n")
        elif char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(source) and source[index:index + 2] != "*/":
                result.append("\n" if source[index] == "\n" else " ")
                index += 1
            index += 1
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _mask_js_strings(source):
    """Blank quoted contents while preserving offsets and delimiters."""
    chars = list(source)
    quote = None
    index = 0
    while index < len(chars):
        char = chars[index]
        if quote:
            if char == "\\" and index + 1 < len(chars):
                chars[index] = " "
                index += 1
                chars[index] = " "
            elif char == quote:
                quote = None
            elif char not in "\r\n":
                chars[index] = " "
        elif char in "'\"`":
            quote = char
        index += 1
    return "".join(chars)


def _named_function_ranges(source):
    """Return (name, start, end, body) for ordinary named JS functions."""
    masked = _mask_js_strings(source)
    functions = []
    for match in re.finditer(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", masked):
        depth = 1
        cursor = match.end()
        while cursor < len(masked) and depth:
            depth += masked[cursor] == "{"
            depth -= masked[cursor] == "}"
            cursor += 1
        functions.append((match.group(1), match.start(), cursor, source[match.end():cursor - 1]))
    return functions


def _function_body(source, name):
    """Return one named function body while ignoring braces inside strings."""
    match = re.search(
        rf"\bfunction\s+{re.escape(name)}\s*[^{{]*\{{",
        source,
    )
    if not match:
        return ""
    start = match.end()
    depth = 1
    quote = None
    index = start
    while index < len(source) and depth:
        character = source[index]
        if quote:
            if character == "\\":
                index += 1
            elif character == quote:
                quote = None
        elif character in "'\"`":
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        index += 1
    return source[start:index - 1] if depth == 0 else ""


def _preference_call_pattern(operation, key):
    verbs = "(?:read|get|load)" if operation == "read" else "(?:write|set|save)"
    return re.compile(
        rf"\b{verbs}[A-Za-z0-9_$]*(?:pref|preference)[A-Za-z0-9_$]*"
        rf"\s*\(\s*['\"]{re.escape(key)}['\"]",
        re.IGNORECASE,
    )


def _restored_during_initialization(source, key):
    read_pattern = _preference_call_pattern("read", key)
    functions = _named_function_ranges(source)
    top_level = list(source)
    for _, start, end, _ in functions:
        top_level[start:end] = " " * (end - start)
    top_level = "".join(top_level)
    if read_pattern.search(top_level):
        return True
    return any(
        read_pattern.search(body) and re.search(rf"\b{re.escape(name)}\s*\(", top_level)
        for name, _, _, body in functions
    )


def _control_is_click_bound(source, element_id):
    selector = (
        rf"(?:\$\(\s*['\"]#{re.escape(element_id)}['\"]\s*\)"
        rf"|document\.getElementById\(\s*['\"]{re.escape(element_id)}['\"]\s*\))"
    )
    direct = re.compile(
        selector + r"\s*(?:\.onclick\s*=|\.addEventListener\(\s*['\"]click['\"])",
        re.DOTALL,
    )
    if direct.search(source):
        return True
    assignment = re.compile(
        rf"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*{selector}"
    ).search(source)
    if not assignment:
        return False
    variable = re.escape(assignment.group(1))
    return bool(re.search(
        rf"\b{variable}\s*(?:\.onclick\s*=|\.addEventListener\(\s*['\"]click['\"])",
        source[assignment.end():],
    ))


class _FrontendHTMLParser(HTMLParser):
    """Collect the small set of HTML attributes covered by this contract."""

    def __init__(self):
        super().__init__()
        self.ids = Counter()
        self.stylesheet_hrefs = []
        self.script_sources = []
        self.views = []
        self._menu_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id is not None:
            self.ids[element_id] += 1
        if tag == "link" and attributes.get("href") is not None:
            self.stylesheet_hrefs.append(attributes["href"])
        if tag == "script" and attributes.get("src") is not None:
            self.script_sources.append(attributes["src"])
        if attributes.get("id") == "menu":
            self._menu_depth = 1
        elif self._menu_depth:
            self._menu_depth += 1
        classes = set(attributes.get("class", "").split())
        if self._menu_depth and tag == "button" and "menu-item" in classes:
            view = attributes.get("data-view")
            if view is not None:
                self.views.append(view)

    def handle_endtag(self, tag):
        if self._menu_depth:
            self._menu_depth -= 1

    handle_startendtag = handle_starttag


@unittest.skipUnless(INDEX_HTML.exists(), "legacy kg_web static bundle is not part of this repository")
class WebUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.executable_js = _strip_js_comments(cls.js)
        cls.css = STYLE_CSS.read_text(encoding="utf-8")
        cls.parser = _FrontendHTMLParser()
        cls.parser.feed(cls.html)

    def test_every_static_jquery_id_exists_exactly_once(self):
        queried_ids = set(
            re.findall(r"\$\(\s*['\"]#([A-Za-z][\w:.-]*)['\"]\s*\)", self.executable_js)
        )
        self.assertTrue(queried_ids, "app.js contains no static $('#...') queries")

        wrong_counts = {
            element_id: self.parser.ids[element_id]
            for element_id in sorted(queried_ids)
            if self.parser.ids[element_id] != 1
        }
        self.assertEqual(
            {},
            wrong_counts,
            "static jQuery IDs must occur exactly once in index.html",
        )

    def test_static_assets_are_referenced(self):
        self.assertIn("/assets/style.css", self.parser.stylesheet_hrefs)
        self.assertIn("/assets/app.js", self.parser.script_sources)

    def test_navigation_views_are_exactly_the_supported_nine(self):
        expected = [
            "dashboard",
            "graph",
            "entities",
            "ontology",
            "sources",
            "mindmap",
            "qa",
            "scripts",
            "eval",
        ]
        self.assertEqual(expected, self.parser.views)

    def test_frontend_api_path_families_remain_represented(self):
        api_paths = {
            next(part for part in match.groups()[1:] if part is not None)
            for match in re.finditer(
                r"\b(api|fetch)\s*\(\s*(?:`(/api/[^`]*)`|\"(/api/[^\"]*)\"|'(/api/[^']*)')",
                self.executable_js,
            )
        }
        path_families = {
            "projects": ("/api/projects",),
            "project load/delete/graph/node": (
                "/api/project/",
                "/api/graph/",
                "/api/node/",
            ),
            "search": ("/api/search/",),
            "categories": ("/api/category/",),
            "ontology/SPARQL": ("/api/ontology/", "/api/sparql/"),
            "ontology editor": ("/api/ontology-editor/source",),
            "entity relabel": ("/api/relabel-entity/",),
            "dashboard": ("/api/dashboard/",),
            "mindmap/roots": ("/api/mindmap/", "/api/roots/"),
            "sources": ("/api/sources/",),
            "QA": ("/api/qa/",),
            "aliases/merge/resolve": (
                "/api/aliases/",
                "/api/merge-entities/",
                "/api/resolve/",
            ),
            "append/history/restore": (
                "/api/append-demo/",
                "/api/append-doc/",
                "/api/history/",
                "/api/restore/",
            ),
            "eval": ("/api/eval/",),
        }
        missing = {
            family: [path for path in paths if not any(candidate.startswith(path) for candidate in api_paths)]
            for family, paths in path_families.items()
            if any(not any(candidate.startswith(path) for candidate in api_paths) for path in paths)
        }
        self.assertEqual({}, missing, "frontend API path families changed")

    def test_layout_control_hooks_exist(self):
        required = ("btn-nav-collapse", "btn-graph-focus", "btn-results-collapse")
        init_body = _function_body(self.executable_js, "initializeUiPreferences")
        defects = {
            element_id: {
                "html_count": self.parser.ids[element_id],
                "referenced_in_initializer": f'$("#{element_id}")' in init_body
                or f"$('#{element_id}')" in init_body,
            }
            for element_id in required
            if self.parser.ids[element_id] != 1
            or not (f'$("#{element_id}")' in init_body or f"$('#{element_id}')" in init_body)
        }
        self.assertIn('addEventListener("click"', init_body)
        self.assertEqual({}, defects, "layout controls must exist and be initialized")

    def test_layout_state_storage_keys_exist(self):
        states = {
            "kg.ui.navCollapsed": ("nav-collapsed", "applyNavState"),
            "kg.ui.graphFocus": ("graph-focus", "applyGraphFocusState"),
            "kg.ui.resultsCollapsed": ("results-collapsed", "applyResultsState"),
        }
        init_body = _function_body(self.executable_js, "initializeUiPreferences")
        defects = {}
        for key, (class_name, apply_function) in states.items():
            apply_body = _function_body(self.executable_js, apply_function)
            checks = {
                "preference_read": bool(_preference_call_pattern("read", key).search(init_body)),
                "preference_write": bool(_preference_call_pattern("write", key).search(init_body)),
                "class_applied": bool(apply_body and re.search(
                    rf"\.classList\.(?:toggle|add|remove)\(\s*['\"]{re.escape(class_name)}['\"]",
                    apply_body,
                )),
                "restored_on_init": bool(re.search(rf"\b{apply_function}\s*\(", init_body)),
            }
            if not all(checks.values()):
                defects[key] = checks
        self.assertRegex(self.executable_js, r"\binitializeUiPreferences\s*\(\s*\)\s*;")
        self.assertEqual({}, defects, "layout state must be persisted, applied, and restored")

    def test_css_braces_are_balanced(self):
        css_without_comments_or_strings = re.sub(
            r"/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"",
            "",
            self.css,
            flags=re.DOTALL,
        )
        depth = 0
        for offset, character in enumerate(css_without_comments_or_strings):
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                self.assertGreaterEqual(depth, 0, f"unexpected closing brace at offset {offset}")
        self.assertEqual(0, depth, "CSS contains unclosed braces")

    def test_css_supports_layout_states_and_reduced_motion(self):
        required = {
            ".nav-collapsed",
            ".graph-focus",
            ".results-collapsed",
            "prefers-reduced-motion",
        }
        missing = sorted(selector for selector in required if selector not in self.css)
        self.assertEqual([], missing, "missing CSS layout-state selectors")

    def test_governance_controls_exist_and_are_bound(self):
        required = (
            "btn-type-editor-save",
            "btn-type-editor-cancel",
            "btn-ontology-editor",
            "btn-ontology-save",
            "btn-ontology-cancel",
        )
        defects = {
            element_id: {
                "html_count": self.parser.ids[element_id],
                "click_bound": _control_is_click_bound(self.executable_js, element_id),
            }
            for element_id in required
            if self.parser.ids[element_id] != 1
            or not _control_is_click_bound(self.executable_js, element_id)
        }
        self.assertEqual({}, defects, "governance controls must exist and be click-bound")

if __name__ == "__main__":
    unittest.main()


def test_primary_ui_exposes_one_unified_ontology_workbench():
    primary = ROOT / "knowledge_service" / "web"
    html = (primary / "index.html").read_text(encoding="utf-8")
    workbench = (primary / "ontology-workbench.js").read_text(encoding="utf-8")
    assert html.count(">本体工作台</button>") == 1
    assert "/assets/ontology-workbench.js?v=candidate-evidence-1" in html
    assert "ontology-workbench" in workbench
    assert "data-workbench-stage=" in workbench
    for stage in ("discover", "design", "review", "validate", "publish"):
        assert f"'{stage}'" in workbench
