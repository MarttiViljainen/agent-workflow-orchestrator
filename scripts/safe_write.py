"""
Shared helper: write model-proposed files without letting the model choose
where they land.

test_updater.py and fix_failure.py both take a path out of an LLM response and
open() it. Those writes are committed and pushed, so an unvalidated path is a
write primitive into the calling repo — "tests/../../.github/workflows/ci.yml"
normalises to a workflow file, and a naive startswith() check waves it through.
Every model-proposed path goes through contain() first.
"""
import os


def contain(path, base_dir):
    """Resolve a model-proposed path, forcing the result inside base_dir.

    Paths already inside base_dir keep their structure (so nested test
    packages still work). Anything else — absolute, ../ escapes, a sibling
    directory — is flattened to base_dir/<basename>.

    Returns a path relative to the current working directory, which is the
    repo root under Actions and what git wants.
    """
    root = os.path.abspath(os.curdir)
    base = os.path.abspath(base_dir)
    candidate = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))

    try:
        inside = candidate != base and os.path.commonpath([base, candidate]) == base
    except ValueError:
        inside = False  # different drives on Windows

    if not inside:
        candidate = os.path.join(base, os.path.basename(candidate))
    return os.path.relpath(candidate, root)


def write_model_file(path, content, base_dir):
    """Contain the path, create missing parent dirs, write. Returns final path."""
    final = contain(path, base_dir)
    if os.path.normpath(final) != os.path.normpath(path):
        print(f"REDIRECTED: model proposed {path!r} outside {base_dir!r} -> {final}")

    parent = os.path.dirname(final)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(final, "w", encoding="utf-8") as f:
        f.write(content)
    return final
