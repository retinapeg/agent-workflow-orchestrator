"""Tool-free subscription builders reuse the harness's validated file operations."""

from pathlib import Path

from agent_arena.providers.api import apply_file_operations, extract_json_object

from .engine import git, safe_scope, within


def repository_context(workspace, project, max_bytes=120000):
    files, omitted, used = {}, [], 0
    for name in git(workspace, "ls-files").splitlines():
        if not within(name, project["scope"] + project["protected"]):
            continue
        try:
            safe_scope([name])
            path = Path(workspace) / name
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != workspace):
                raise ValueError("symlink")
            if path.stat().st_size > 32000:
                raise ValueError("large file")
            content = path.read_text()
            size = len(content.encode())
            if used + size > max_bytes:
                raise ValueError("context budget")
            files[name] = content
            used += size
        except (ValueError, OSError):
            omitted.append(name)
    return {"files": files, "omitted": omitted}


def apply_scoped_response(text, workspace, task, project):
    payload = extract_json_object(text)
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("Builder must return nonempty file operations")
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(operation.get("path"), str):
            raise ValueError("Malformed file operation")
        path = safe_scope([operation["path"]])[0]
        if not within(path, task["scope"]) or within(path, project["protected"]):
            raise ValueError("Builder operation exceeds authorized task scope")
    apply_file_operations(
        workspace, operations, max_operations=32, max_file_bytes=100000, max_total_bytes=250000
    )
    return str(payload.get("summary", "Applied validated file operations"))
