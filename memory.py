#!/usr/bin/env python3
"""Deterministic context router for a Markdown note vault (Obsidian-compatible).

The calling AI chooses a route and an entity slug; this script only resolves
permitted paths, checks them, and emits a bounded, source-traceable packet.
It does not interpret natural language.

Commands:
  memory.py new <kind> "<title>" [--by WRITER]
  memory.py update <kind> <slug> --expect SHA256_PREFIX --from FILE [--by WRITER]
  memory.py context <route> [<slug>] [--output PATH] [--max-chars N]
  memory.py routes [--write]
  memory.py list <kind>

--root points the script at another repository root (routes.json + ROUTING.md + vault/);
the tests and eval/ use it for fixture vaults.
"""
import argparse
import contextlib
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")  # always used with fullmatch
WRITER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
KIND_TO_CATEGORY = {"idea": "ideas", "job": "jobs", "project": "projects", "session": "sessions"}
ROUTES_BEGIN = "<!-- BEGIN GENERATED ROUTES (python3 memory.py routes --write) -->"
ROUTES_END = "<!-- END GENERATED ROUTES -->"


class MemoryError_(Exception):
    """User-facing failure; message is printed and exit code is 2."""


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_routes(root):
    return json.loads((root / "routes.json").read_text(encoding="utf-8"))


def slugify(title):
    if "\n" in title or "|" in title:
        raise MemoryError_("title must be one line without '|'")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug or not SLUG_RE.fullmatch(slug):
        raise MemoryError_(f"cannot derive a safe id from title {title!r}")
    return slug


def safe_path(root, rel):
    """Resolve rel under root; reject traversal and symlink escape."""
    root_resolved = root.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise MemoryError_(f"path escapes repository: {rel}")
    return candidate


def safe_vault_file(root, cfg, rel):
    vault = (root / cfg["vault_dir"]).resolve()
    path = safe_path(root, rel)
    if rel != "ROUTING.md" and not path.is_relative_to(vault):
        raise MemoryError_(f"path escapes vault: {rel}")
    return path


# ---------------------------------------------------------------- writes
# Every write takes one exclusive lock (out/.vault.lock), so concurrent memory.py
# processes (Claude, the bridge, a second terminal) are serialised. Obsidian does
# not take this lock; update's sha256 check still catches its edits except in the
# microseconds between the check and the rename.

@contextlib.contextmanager
def vault_lock(root):
    lock_dir = root / "out"
    lock_dir.mkdir(exist_ok=True)
    fd = os.open(lock_dir / ".vault.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the lock


def category_dir(root, cfg, category):
    """The category folder, refusing symlinked vault or category directories."""
    for rel in (cfg["vault_dir"], f"{cfg['vault_dir']}/{category}"):
        if (root / rel).is_symlink():
            raise MemoryError_(f"refusing to use a symlinked directory: {rel}")
    folder = safe_path(root, f"{cfg['vault_dir']}/{category}")
    if folder != root.resolve() / cfg["vault_dir"] / category:
        raise MemoryError_(f"unexpected location for {category}/")
    return folder


def write_atomic(path, data, precheck=None):
    """Write bytes to a unique temp file beside path, fsync, run precheck, then rename over it."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if precheck:
            precheck()  # re-verify just before the rename, after the slow fsync
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def create_exclusive(path, data):
    """Create path with data, failing if any entry with that name exists (no partial files)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)  # atomic; FileExistsError if the name (in any case on APFS) exists
    finally:
        os.unlink(tmp)


def encode_utf8(text, what):
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise MemoryError_(f"{what} is not valid UTF-8 text")


def cmd_new(root, kind, title, by=None, description=None):
    cfg = load_routes(root)
    if kind not in KIND_TO_CATEGORY:
        raise MemoryError_(f"unknown kind {kind!r}; expected one of {sorted(KIND_TO_CATEGORY)}")
    category = KIND_TO_CATEGORY[kind]
    encode_utf8(title, "title")
    slug = slugify(title)
    desc = (description or "(add one-line description)").replace("|", "/").replace("\n", " ")
    encode_utf8(desc, "description")
    note_rel = f"{cfg['vault_dir']}/{category}/{slug}.md"
    with vault_lock(root):
        cat_dir = category_dir(root, cfg, category)
        note = cat_dir / f"{slug}.md"
        if note.exists() or note.is_symlink():
            raise MemoryError_(f"refusing to overwrite existing note: {note_rel}")
        template = (cat_dir / "_TEMPLATE.md").read_text(encoding="utf-8")
        stamp = now_iso()
        body = (template.replace("{{id}}", f"{kind}-{slug}")
                        .replace("title: {{title}}", f"title: {json.dumps(title, ensure_ascii=False)}")
                        .replace("{{title}}", title)
                        .replace("{{created}}", stamp)
                        .replace("{{updated}}", stamp))
        if by:
            body = re.sub(r"(?m)^provenance: .*$", lambda _: f"provenance: created by {by} via memory.py", body, count=1)
        row = f"| {title} | `{category}/{slug}.md` | {desc} |\n"
        body_b = encode_utf8(body, "note")
        encode_utf8(row, "index row")  # fail before touching disk
        try:
            create_exclusive(note, body_b)
        except FileExistsError:
            raise MemoryError_(f"refusing to overwrite existing note: {note_rel}")
        # Read INDEX.md as late as possible: Obsidian does not take our lock.
        index = cat_dir / "INDEX.md"
        text = index.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        write_atomic(index, (text + row).encode("utf-8"))
    print(note_rel)


def file_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


FRONTMATTER_LINE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: (?![ \t]*[|>&*!%@`]).*)?")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n?^---[ \t]*\n", re.S | re.M)


def normalise_frontmatter(new, kind, slug, by):
    """Validate the replacement note's frontmatter and stamp updated/last_writer inside it only."""
    m = FRONTMATTER_RE.match(new)
    if not m:
        raise MemoryError_("replacement must start with a --- frontmatter block")
    lines = m.group(1).split("\n") if m.group(1) else []
    keys = []
    for ln in lines:  # plain `key: value` lines only, so text checks match what YAML readers see
        km = FRONTMATTER_LINE_RE.fullmatch(ln)
        if not km:
            raise MemoryError_(f"frontmatter lines must be plain 'key: value' (got {ln[:40]!r})")
        keys.append(km.group(1))
    if len(keys) != len(set(keys)):
        raise MemoryError_("frontmatter has a duplicated key")
    if f"id: {kind}-{slug}" not in lines:
        raise MemoryError_(f"frontmatter must contain the line 'id: {kind}-{slug}'")
    kept = [ln for ln in lines if FRONTMATTER_LINE_RE.fullmatch(ln).group(1) not in ("updated", "last_writer")]
    kept.append(f"updated: {now_iso()}")
    if by:
        kept.append(f"last_writer: {by}")
    return "---\n" + "\n".join(kept) + "\n---\n" + new[m.end():]


def cmd_update(root, kind, slug, expect, source, by=None):
    """Replace a note only if it still matches the revision the writer last read."""
    cfg = load_routes(root)
    if kind not in KIND_TO_CATEGORY:
        raise MemoryError_(f"unknown kind {kind!r}; expected one of {sorted(KIND_TO_CATEGORY)}")
    if not SLUG_RE.fullmatch(slug):
        raise MemoryError_(f"invalid entity id {slug!r} (expected lowercase slug)")
    if not expect or len(expect) < 12 or not re.fullmatch(r"[0-9a-f]+", expect):
        raise MemoryError_("--expect needs at least 12 hex chars of the sha256 you last read")
    rel = f"{cfg['vault_dir']}/{KIND_TO_CATEGORY[kind]}/{slug}.md"
    with open(source, encoding="utf-8", newline="") as fh:
        raw = fh.read()
    new = normalise_frontmatter(re.sub(r"\r\n?", "\n", raw), kind, slug, by)
    new_b = encode_utf8(new, "replacement")
    with vault_lock(root):
        cat_dir = category_dir(root, cfg, KIND_TO_CATEGORY[kind])
        note = cat_dir / f"{slug}.md"
        if note.is_symlink():
            raise MemoryError_(f"refusing to write through a symlink: {rel}")
        if f"{slug}.md" not in os.listdir(cat_dir) or not note.is_file():  # exact name, not case-folded
            raise MemoryError_(f"no such note: {rel} (use `new` to create)")
        current = note.read_text(encoding="utf-8")
        current_sha = file_sha(current)
        if not current_sha.startswith(expect):
            raise MemoryError_(f"conflict: {rel} changed since you read it (expected {expect}, "
                               f"now {current_sha[:12]}); re-read and retry. Nothing written.")
        def still_current():  # an unlocked editor (Obsidian) may have saved during the fsync
            if file_sha(note.read_text(encoding="utf-8")) != current_sha:
                raise MemoryError_(f"conflict: {rel} changed while writing; nothing written. Re-read and retry.")
        write_atomic(note, new_b, precheck=still_current)
    diff = difflib.unified_diff(current.splitlines(), new.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="")
    print("\n".join(diff))
    print(f"updated {rel}: {current_sha[:12]} -> {file_sha(new)[:12]}")


def cmd_list(root, kind):
    cfg = load_routes(root)
    if kind not in KIND_TO_CATEGORY:
        raise MemoryError_(f"unknown kind {kind!r}")
    cat_dir = safe_path(root, f"{cfg['vault_dir']}/{KIND_TO_CATEGORY[kind]}")
    for p in sorted(cat_dir.glob("*.md")):
        if p.name not in ("INDEX.md", "_TEMPLATE.md"):
            print(p.stem)


# ---------------------------------------------------------------- context

def resolve_route(cfg, route_name, entity):
    """Return (ordered relative paths, mode description)."""
    routes = cfg["routes"]
    if route_name not in routes:
        raise MemoryError_(f"unknown route {route_name!r}; expected one of {sorted(routes)}")
    route = routes[route_name]
    paths = list(cfg["default_set"])
    if route["entity"] == "none":
        if entity:
            raise MemoryError_(f"route {route_name!r} takes no entity")
        paths += route["extra_files"]
        mode = "default set + route files"
    elif entity is None:
        paths.append(f"{cfg['vault_dir']}/{route['category']}/INDEX.md")
        mode = "entity unspecified: category index only; pick one entry and rerun"
    else:
        if not SLUG_RE.fullmatch(entity):
            raise MemoryError_(f"invalid entity id {entity!r} (expected lowercase slug)")
        paths.append(f"{cfg['vault_dir']}/{route['category']}/{entity}.md")
        paths += route["extra_files"]
        mode = f"entity {entity}"
    seen, ordered = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered, mode


def build_packet(root, route_name, entity=None, max_chars=None):
    cfg = load_routes(root)
    limit = max_chars if max_chars is not None else cfg["max_chars"]
    rels, mode = resolve_route(cfg, route_name, entity)
    return assemble_packet(root, cfg, rels, route_name, mode, limit)


def assemble_packet(root, cfg, rels, route_name, mode, limit):
    """Read rels (checked to stay inside the vault) and render one bounded packet.

    Shared with eval/ so that baselines are measured in exactly the same format.
    """
    files, missing = [], []
    for rel in rels:
        path = safe_vault_file(root, cfg, rel)
        if not path.is_file():
            missing.append(rel)
            continue
        text = path.read_text(encoding="utf-8")
        files.append({
            "path": rel,
            "chars": len(text),
            "sha256": file_sha(text)[:12],
            "mtime": dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "text": text,
        })
    if missing:
        raise MemoryError_("required source unavailable (not located/verified): " + ", ".join(missing))

    head = [
        "# Context packet (derived, not canonical; regenerate after any note edit)",
        f"- route: {route_name}",
        f"- mode: {mode}",
        f"- generated: {now_iso()}",
        "- resume: answer only from the files below; cite path + sha256; "
        "say 'not in retrieved notes' for anything absent.",
        "",
        "## Manifest",
        "| path | chars | sha256[:12] | mtime |",
        "| --- | ---: | --- | --- |",
    ]
    head += [f"| {f['path']} | {f['chars']} | {f['sha256']} | {f['mtime']} |" for f in files]
    body = []
    for f in files:
        body += ["", f"===== BEGIN FILE: {f['path']} (sha256 {f['sha256']}) =====",
                 f["text"].rstrip("\n"), f"===== END FILE: {f['path']} ====="]
    packet = "\n".join(head + body) + "\n"
    footer_tmpl = "\n- total packet chars: {n} (limit {limit}; characters, not tokens)\n"
    # Footer length depends on n; settle it by iterating to a fixed point.
    n = len(packet)
    for _ in range(3):
        n = len(packet) + len(footer_tmpl.format(n=n, limit=limit))
    packet += footer_tmpl.format(n=n, limit=limit)
    if len(packet) > limit:
        sizes = ", ".join(f"{f['path']}={f['chars']}" for f in files)
        raise MemoryError_(f"packet would be {len(packet)} chars, over limit {limit}; "
                           f"nothing written. File sizes: {sizes}. Use --max-chars only if intended.")
    return packet, files


def cmd_context(root, route_name, entity, output, max_chars):
    packet, _ = build_packet(root, route_name, entity, max_chars)
    if output:
        cfg = load_routes(root)
        out = Path(output)
        out = out if out.is_absolute() else root / out
        out = out.resolve()
        if out.is_relative_to((root / cfg["vault_dir"]).resolve()):
            raise MemoryError_("refusing to write a derived packet inside the vault")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(packet, encoding="utf-8")
        tmp.replace(out)  # atomic: no partial output
        print(f"wrote {out} ({len(packet)} chars)")
    else:
        sys.stdout.write(packet)


# ---------------------------------------------------------------- routes

def routes_table(root):
    cfg = load_routes(root)
    lines = ["| route | task | entity | extra files after default set | exclusions |",
             "| --- | --- | --- | --- | --- |"]
    for name, r in cfg["routes"].items():
        if r["entity"] == "none":
            ent = "none"
        else:
            ent = f"`<slug>` → `{cfg['vault_dir']}/{r['category']}/<slug>.md`; omitted → `INDEX.md`"
        extras = ", ".join(f"`{p}`" for p in r["extra_files"]) or "—"
        lines.append(f"| `{name}` | {r['task']} | {ent} | {extras} | {r['exclusions']} |")
    return "\n".join(lines) + "\n"


def render_routing(root):
    doc = (root / "ROUTING.md").read_text(encoding="utf-8")
    start, end = doc.index(ROUTES_BEGIN) + len(ROUTES_BEGIN), doc.index(ROUTES_END)
    return doc[:start] + "\n" + routes_table(root) + doc[end:]


def cmd_routes(root, write):
    if write:
        (root / "ROUTING.md").write_text(render_routing(root), encoding="utf-8")
        print("ROUTING.md regenerated from routes.json")
    else:
        sys.stdout.write(routes_table(root))


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="repository root holding routes.json, ROUTING.md and vault/ (default: this script's folder)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new"); p.add_argument("kind"); p.add_argument("title"); p.add_argument("--by")
    p.add_argument("--description", help="one-line description for the category INDEX.md")
    p = sub.add_parser("update"); p.add_argument("kind"); p.add_argument("slug")
    p.add_argument("--expect", required=True); p.add_argument("--from", dest="source", required=True)
    p.add_argument("--by")
    p = sub.add_parser("context"); p.add_argument("route"); p.add_argument("entity", nargs="?")
    p.add_argument("--output"); p.add_argument("--max-chars", type=int)
    p = sub.add_parser("routes"); p.add_argument("--write", action="store_true")
    p = sub.add_parser("list"); p.add_argument("kind")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else SCRIPT_ROOT
    try:
        if getattr(args, "by", None) and not WRITER_RE.fullmatch(args.by):
            raise MemoryError_(f"invalid --by {args.by!r}; use letters, digits, space, . _ -")
        if args.cmd == "new":
            cmd_new(root, args.kind, args.title, args.by, args.description)
        elif args.cmd == "update":
            cmd_update(root, args.kind, args.slug, args.expect, args.source, args.by)
        elif args.cmd == "context":
            cmd_context(root, args.route, args.entity, args.output, args.max_chars)
        elif args.cmd == "routes":
            cmd_routes(root, args.write)
        elif args.cmd == "list":
            cmd_list(root, args.kind)
    except MemoryError_ as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
