"""
version_contract.py: Release numbers must be numbers, and named once

Four defects in this fork were the same mistake reached from four directions, and
every one of them was found by reading code rather than by a failing test:

  * ``installer["Version"].split(".")[0] < floor`` — a classic Mac OS "9.2.2"
    sorts *after* the "13" floor as text, so an unsupported installer was
    offered; splitting the leading component off collapses every 10.x release
    to "10", a prefix of a "10.12" floor.
  * ``sorted(products, key=lambda p: p["Version"])`` — as text, "9.2.2" lands
    after "26.0".
  * ``platform_version[0] == "10"`` — character 0 of "10" is "1", so the branch
    was dead code and ``createinstallmedia`` was never told where its installer
    lived on the releases that need to be told.
  * ``int(platform_version[1]) < 13`` — character 1 of "10" is "0", below every
    bound, so the same flag was passed to releases that reject it.

The same files also spelled release numbers out as *literals* — a bare `13` in
the `--applicationpath` test, a hardcoded `[-4]` window offset, `os_data.sequoia`
as the ceiling of a fork whose target is Tahoe — so the scan fails on a release
number written inline in a comparison where `os_data` and `CatalogVersion`
already name it.

The copies also hid in prose: six messages across `kdk_handler` and `kdk_merge`
said "Darwin 26" while the policy that decided it was declared somewhere else, so
a fork that moved that policy would have kept printing the old number. A string
that names a release the datasets declare has to derive it.

The fourth rule looks at the other end — a declaration that restates a number the
datasets own, such as ``frozenset({26})`` where the policy could have derived it
instead.

This module parses production sources and reports the shapes above. It is a build
contract as well as a test subject: `validate_source` is what the build gate
calls before it generates anything, and the test suite consumes the same scanner,
so both answer to one implementation.

Suppressing a finding requires an inline comment on the flagged line (or the line
above it) of the form ``# version-text-ok: <reason>`` or
``# release-number-ok: <reason>``. The reason must be longer than a placeholder,
and a pragma that suppresses nothing fails as well, so allowances cannot outlive
the code they excuse. The tree carries none.
"""

import ast
import functools
import io
import re
import tokenize

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

SCANNED_DIRECTORIES: tuple[str, ...] = ("opencore_legacy_patcher", "ci_tooling")
SKIPPED_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {"tests", "__pycache__", "venv", ".venv", ".venv-test", ".venv-build", "site-packages", "node_modules"}
)

PRAGMA = "# version-text-ok:"
RELEASE_PRAGMA = "# release-number-ok:"
PRAGMAS: tuple[str, ...] = (PRAGMA, RELEASE_PRAGMA)
PRAGMA_MINIMUM_REASON = 12

# The files whose version handling this fork had to repair. A scan that silently
# stopped seeing them would pass while guarding nothing.
FILES_WITH_REPAIRED_VERSION_HANDLING: tuple[str, ...] = (
    "opencore_legacy_patcher/sucatalog/products.py",
    "opencore_legacy_patcher/sucatalog/products_appledb.py",
    "opencore_legacy_patcher/support/macos_installer_handler.py",
    "opencore_legacy_patcher/datasets/os_data.py",
)

ORDERING_OPS: tuple[type, ...] = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
RELEASE_OPS: tuple[type, ...] = ORDERING_OPS + (ast.Eq, ast.NotEq)

# Names that carry a release number: a version as text, or a kernel/Darwin major.
# Deliberately loose, because the literals it finds are the point; the names the
# fork already has for these numbers (`os_data`, `CatalogVersion`) are what the
# rule wants to see instead of the literal.
RELEASEISH_NAME = re.compile(r"(version|kernel|xnu|darwin|major|minor|release|build|sdk)", re.IGNORECASE)

# A comparison against one of these is about presence, not about a release: 0 is
# the `.0` build or an unset field, and 1 counts something.
RELEASE_LITERAL_SENTINELS: frozenset[int] = frozenset({0, 1})

# The modules that declare which releases exist. Read from their source rather
# than imported, so the scan stays a pure syntax check.
DECLARED_RELEASE_MODULES: tuple[str, ...] = (
    "opencore_legacy_patcher/datasets/os_data.py",
    "opencore_legacy_patcher/sucatalog/constants.py",
)

# This module declares the rules rather than a release: its
# `MINIMUM_DECLARED_RELEASE_NUMBER = 10` is a threshold, and a number written
# here is a rule parameter, not a second copy of a release.
RULE_MODULE: str = "ci_tooling/build_modules/version_contract.py"
DECLARATION_EXEMPT_MODULES: tuple[str, ...] = DECLARED_RELEASE_MODULES + (RULE_MODULE,)

# A declaration *of a release*, as opposed to a declaration that happens to hold
# a number of the same size: `apfs_zlib_version` names a compression library and
# `BASE_VERSION` a GPU compiler, and neither is a macOS release.
RELEASE_DECLARATION_NAME = re.compile(
    r"(darwin|mac_?os|os_(major|minor|version)|major|minor|release|kernel|xnu|"
    r"tahoe|sequoia|sonoma|ventura|monterey|big_sur|catalina|mojave|"
    r"high_sierra|sierra|el_capitan|yosemite|mavericks)",
    re.IGNORECASE,
)

# A message that talks about a release has to name it through its declaration.
PROSE_RELEASE_WORD = re.compile(r"\b(Darwin|macOS|OS X|XNU|kernel)\b", re.IGNORECASE)
RELEASE_NUMBER_TOKEN = re.compile(r"(?<![0-9A-Za-z_.])(\d+)(?![0-9A-Za-z_.])")

# Numbers below this are counts and durations ("30min+", "cpus=4") far more often
# than they are releases.
MINIMUM_DECLARED_RELEASE_NUMBER = 10

# A release number, or a release component ("26", "10.16").
VERSION_LITERAL = re.compile(r"^\d+(?:\.\d+)*$")

# Names that carry a version as text. Deliberately narrow: this only widens the
# character-index rule, and an identifier called something else can be handled
# with a pragma if it ever needs one.
VERSIONISH_NAME = re.compile(r"version", re.IGNORECASE)

# Keys of product dictionaries that hold a version as text.
VERSION_FIELD_NAMES: frozenset[str] = frozenset(
    {"version", "osversion", "productversion", "rawversion", "buildversion"}
)

# Calls that turn version text into something numerically orderable. A version
# read inside one of these is fine: that is the fix, not the defect.
NUMERIC_PARSER_NAMES: frozenset[str] = frozenset(
    {
        "packaging.version.parse",
        "packaging.version.Version",
        "version.parse",
        "version.Version",
        "_parse_numeric_version",
        "_parse_version",
        "int",
        "float",
        "os_data",
        "os_to_kernel",
        "kernel_to_os",
    }
)
NUMERIC_PARSER_TAILS: frozenset[str] = frozenset({"parse", "Version", "parse_version", "_parse_numeric_version"})


class VersionContractError(RuntimeError):
    """Production source mishandles a release number."""


class Violation:
    """One place production code mishandles a release number"""

    def __init__(self, kind: str, lineno: int, text: str) -> None:
        self.kind = kind
        self.lineno = lineno
        self.text = text.strip()

    def __str__(self) -> str:
        return f"{self.kind} at line {self.lineno}: {self.text}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Violation({self.kind!r}, {self.lineno}, {self.text!r})"


def _dotted_name(node: ast.AST) -> str:
    """Full dotted name of an attribute/name chain, or "" if it is not one"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _last_name(node: ast.AST) -> str:
    name = _dotted_name(node)
    return name.rsplit(".", 1)[-1] if name else ""


def _is_numeric_parse(node: ast.AST) -> bool:
    """Whether a call turns version text into a number before it is ordered"""
    if not isinstance(node, ast.Call):
        return False
    name = _dotted_name(node.func)
    if name in NUMERIC_PARSER_NAMES:
        return True
    return _last_name(node.func) in NUMERIC_PARSER_TAILS


def _is_version_literal(node: ast.AST) -> bool:
    """Whether a constant spells out a release number"""
    if not isinstance(node, ast.Constant):
        return False
    if isinstance(node.value, bool):
        return False
    if isinstance(node.value, (int, float)):
        return True
    return isinstance(node.value, str) and bool(VERSION_LITERAL.match(node.value))


def _is_split_call(node: ast.AST) -> bool:
    """
    ``x.split(".")`` — the text form of a version

    The separator has to be spelled out. ``boot_args.split()`` is a word split
    whose result is never ordered as a version, and treating it as one made the
    scan fire on the AMFIPass boot-argument check.
    """
    if not isinstance(node, ast.Call):
        return False
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "split"):
        return False
    if len(node.args) != 1 or node.keywords:
        return False
    separator = node.args[0]
    return isinstance(separator, ast.Constant) and separator.value == "."


def _is_version_component(node: ast.AST) -> bool:
    """``x.split(".")[0]`` — one component of a version, as text"""
    return isinstance(node, ast.Subscript) and _is_split_call(node.value)


def _is_version_field(node: ast.AST) -> bool:
    """``installer["Version"]`` — a version kept in a dictionary, as text"""
    if not isinstance(node, ast.Subscript):
        return False
    slice_node = node.slice
    if not (isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str)):
        return False
    return slice_node.value.lower() in VERSION_FIELD_NAMES


def _character_index_of_version(node: ast.AST) -> str | None:
    """
    Name of the version variable indexed by *character* position, if any

    ``platform_version[0]`` reads a character, not a component: "10"[0] is "1".
    """
    if not isinstance(node, ast.Subscript):
        return None
    slice_node = node.slice
    if not (isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int)):
        return None
    if isinstance(slice_node.value, bool):
        return None
    base = node.value
    name = base.id if isinstance(base, ast.Name) else _last_name(base)
    if not name or not VERSIONISH_NAME.search(name):
        return None
    return name


def _int_cast_of_character_index(node: ast.AST) -> str | None:
    """``int(platform_version[1])`` — a character read, then compared as a number"""
    if not isinstance(node, ast.Call) or _last_name(node.func) not in {"int", "float"}:
        return None
    if len(node.args) != 1:
        return None
    return _character_index_of_version(node.args[0])


def _is_releaseish(node: ast.AST) -> bool:
    """
    Whether an expression names a release number

    Covers ``version.major``, ``self.constants.detected_os_minor``,
    ``self._xnu_minor`` and ``int(build_match.group(1))`` — each of the places a
    release number turned up as a literal in this fork.
    """
    if isinstance(node, (ast.Attribute, ast.Name)):
        return bool(RELEASEISH_NAME.search(_dotted_name(node)))
    if isinstance(node, ast.Call):
        if _last_name(node.func) in {"int", "float"} and len(node.args) == 1:
            return _is_releaseish(node.args[0])
        return bool(RELEASEISH_NAME.search(_dotted_name(node.func)))
    if isinstance(node, ast.Subscript):
        return _is_releaseish(node.value)
    return False


@functools.cache
def declared_release_numbers() -> frozenset[int]:
    """
    Every release number the datasets declare

    The `os_data` members and the `CatalogVersion` entries, read from their
    declarations. A message naming one of these has a declaration to name it
    through, which is what stops the number being copied into prose.
    """
    numbers: set[int] = set()
    for relative in DECLARED_RELEASE_MODULES:
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or isinstance(value.value, bool):
                continue
            if isinstance(value.value, int):
                if value.value >= MINIMUM_DECLARED_RELEASE_NUMBER:
                    numbers.add(value.value)
            elif isinstance(value.value, str) and VERSION_LITERAL.match(value.value):
                major = int(value.value.split(".")[0])
                if major >= MINIMUM_DECLARED_RELEASE_NUMBER:
                    numbers.add(major)
    return frozenset(numbers)


def _is_declared_release_in_prose(node: ast.AST) -> bool:
    """
    Whether a string names a release the datasets declare

    Only a string that also talks about an OS: ``"Creating macOS installers can
    take 30min+"`` is prose about a duration, and a build identifier like
    ``"25G83"`` is not a release number at all.
    """
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    if not PROSE_RELEASE_WORD.search(node.value):
        return False
    declared = declared_release_numbers()
    return any(
        int(match.group(1)) in declared for match in RELEASE_NUMBER_TOKEN.finditer(node.value)
    )


def _literal_release_numbers(node: ast.AST | None) -> set[int]:
    """
    Every release number a literal spells out

    Shallow containers count, because that is the shape a policy takes:
    ``frozenset({26})`` and ``(24, 25)`` are declarations of release numbers, and
    ``{25: "..."}`` declares one in its key.
    """
    numbers: set[int] = set()
    if node is None:
        return numbers
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        if isinstance(node.value, int):
            numbers.add(node.value)
        elif isinstance(node.value, str) and VERSION_LITERAL.match(node.value):
            numbers.add(int(node.value.split(".")[0]))
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for element in node.elts:
            numbers |= _literal_release_numbers(element)
    elif isinstance(node, ast.Dict):
        for key in node.keys:
            numbers |= _literal_release_numbers(key)
        for value in node.values:
            numbers |= _literal_release_numbers(value)
    elif isinstance(node, ast.Call) and _dotted_name(node.func) in {"frozenset", "set", "tuple", "list"}:
        for argument in node.args:
            numbers |= _literal_release_numbers(argument)
    return numbers


def _duplicated_declaration(node: ast.AST) -> Violation | None:
    """
    A declaration of a release number the datasets already declare

    `frozenset({26})` was the shape: a policy that restated a number the datasets
    own, so the two could disagree. A declaration that derives its number
    (`frozenset({TAHOE_DARWIN_MAJOR + 1})`) has no literal to flag.
    """
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return None
    if not any(RELEASE_DECLARATION_NAME.search(_dotted_name(target)) for target in targets):
        return None
    if not declared_release_numbers() & _literal_release_numbers(node.value):
        return None
    return Violation("duplicated-release-declaration", node.lineno, "")


def _is_release_literal(node: ast.AST) -> bool:
    """Whether a constant spells a release number out instead of naming it"""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return False
    if not isinstance(node.value, int):
        return False
    return node.value not in RELEASE_LITERAL_SENTINELS


def _readable_version_text_outside_parser(node: ast.AST) -> bool:
    """
    Whether an expression reads version text without parsing it as a number

    Descends into subexpressions, stopping at calls that do parse — a key such as
    ``packaging.version.parse(v["RawVersion"])`` is numerically ordered and is
    exactly what this scan wants to see.
    """
    if _is_numeric_parse(node):
        return False
    if _is_version_component(node):
        return True
    if _is_version_field(node):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_readable_version_text_outside_parser(element) for element in node.elts)
    if isinstance(node, ast.BoolOp):
        return any(_readable_version_text_outside_parser(value) for value in node.values)
    if isinstance(node, ast.IfExp):
        return any(
            _readable_version_text_outside_parser(part)
            for part in (node.body, node.orelse, node.test)
        )
    return False


def _key_argument(call: ast.Call) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == "key":
            return keyword.value
    return None


def _sorting_violations(node: ast.AST) -> list[Violation]:
    """``sorted(x, key=lambda p: p["Version"])`` and friends, ordered as text"""
    call: ast.Call | None = None
    if isinstance(node, ast.Call):
        name = _dotted_name(node.func)
        if name in {"sorted", "min", "max"} or _last_name(node.func) == "sort":
            call = node
    if call is None:
        return []
    key = _key_argument(call)
    if key is None:
        return []
    body = key.body if isinstance(key, ast.Lambda) else key
    if not _readable_version_text_outside_parser(body):
        return []
    return [Violation("version-text-sort", node.lineno, "")]


def _docstring_ids(tree: ast.AST) -> frozenset[int]:
    """
    The docstring nodes of a module

    A patchset docstring that says "Supported on macOS 11.0 and newer" is
    documentation, and documentation cannot derive a number from anything.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return frozenset(docstrings)


def _relative_path(filename: str) -> str:
    """Repo-relative path of a scanned file, or "" when it is not one"""
    try:
        return Path(filename).resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return ""


def _scan_node(
    node: ast.AST,
    docstrings: frozenset[int] = frozenset(),
    declarations: bool = True,
) -> list[Violation]:
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        for index, operator in enumerate(node.ops):
            left, right = operands[index], operands[index + 1]
            ordering = isinstance(operator, ORDERING_OPS)
            release = isinstance(operator, RELEASE_OPS)
            for near, far in ((left, right), (right, left)):
                if _is_version_component(near) or _is_split_call(near):
                    return [Violation("version-component-ordering", node.lineno, "")]
                if ordering and _is_version_field(near):
                    return [Violation("version-field-ordering", node.lineno, "")]
                if _is_version_literal(far):
                    if _character_index_of_version(near) or _int_cast_of_character_index(near):
                        return [Violation("character-indexed-version", node.lineno, "")]
                if release and _is_release_literal(near) and _is_releaseish(far):
                    return [Violation("bare-release-number", node.lineno, "")]
    if id(node) not in docstrings and _is_declared_release_in_prose(node):
        return [Violation("release-number-in-message", node.lineno, "")]
    if declarations:
        declaration = _duplicated_declaration(node)
        if declaration is not None:
            return [declaration]
    return _sorting_violations(node)


def _pragma_reason(comment: str) -> str | None:
    for pragma in PRAGMAS:
        if pragma in comment:
            return comment.split(pragma, 1)[1].strip()
    return None


def _pragmas(source: str) -> list[tuple[int, str]]:
    """
    Every pragma comment, as ``(lineno, reason)``

    Only a comment suppresses a finding. A *string* that happens to contain the
    syntax — the docstring of this module, for one — must not silently excuse
    whatever is written on the next line.
    """
    pragmas: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            reason = _pragma_reason(token.string)
            if reason is not None:
                pragmas.append((token.start[0], reason))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - unparseable source
        return pragmas
    return pragmas


def scan_source(
    source: str,
    filename: str = "<string>",
    respect_pragmas: bool = True,
) -> tuple[list[Violation], list[tuple[int, str]]]:
    """
    Scan one module

    Returns the violations that are not suppressed, and every pragma found as
    ``(lineno, reason)`` so the caller can check them for rot. Pass
    ``respect_pragmas=False`` to see what the pragmas are hiding.
    """
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()
    docstrings = _docstring_ids(tree)
    # The modules that declare which releases exist are where those numbers live,
    # so restating one there is the declaration, not a copy of it.
    declarations = _relative_path(filename) not in DECLARATION_EXEMPT_MODULES

    violations: list[Violation] = []
    for node in ast.walk(tree):
        for violation in _scan_node(node, docstrings, declarations):
            violation.text = lines[violation.lineno - 1] if violation.lineno <= len(lines) else ""
            violations.append(violation)
    violations.sort(key=lambda violation: violation.lineno)

    pragmas = _pragmas(source)

    if respect_pragmas is False:
        return violations, pragmas

    suppressed: set[int] = set()
    for lineno, _ in pragmas:
        suppressed.update({lineno, lineno + 1})
    kept = [violation for violation in violations if violation.lineno not in suppressed]
    return kept, pragmas


def scan_path(path: Path, respect_pragmas: bool = True) -> tuple[list[Violation], list[tuple[int, str]]]:
    return scan_source(
        path.read_text(encoding="utf-8"),
        filename=str(path),
        respect_pragmas=respect_pragmas,
    )


def iter_production_modules() -> list[Path]:
    modules: list[Path] = []
    for directory in SCANNED_DIRECTORIES:
        root = REPO_ROOT / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIPPED_DIRECTORY_NAMES.intersection(path.parts):
                continue
            modules.append(path)
    return sorted(modules)


def mishandled_release_numbers() -> list[str]:
    """Every place production source mishandles a release number, as text"""
    offenders: list[str] = []
    for path in iter_production_modules():
        violations, _ = scan_path(path)
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}: {violation}" for violation in violations
        )
    return offenders


def validate_source() -> None:
    """
    Fail when production source mishandles a release number

    Run by the build before it deletes or generates anything, and asserted by the
    test suite, so one implementation answers both. A finding is fixed or allowed
    with a pragma on its line; there is no third option.
    """
    offenders = mishandled_release_numbers()
    if offenders:
        raise VersionContractError(
            "production code mishandles release numbers; fix the line, or allow it"
            " with a pragma on it:\n" + "\n".join(offenders)
        )
