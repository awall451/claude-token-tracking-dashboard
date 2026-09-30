"""
Per-turn project attribution.

A session is launched in one directory but often works across several projects
(e.g. a "hub" dir holding many repos). Each assistant turn is attributed to a
project from the files its tool calls touch, smoothed per user exchange, with an
optional manual `/tag <name>` override. Projects and tags come from a TOML config:

    [[hub]]                      # first path segment under a hub = project name
    path = "~/work"
    tags = { client = "acme" }

    [[project]]                  # explicit project; longest prefix wins over hubs
    path = "~/work/site"
    name = "acme-site"           # optional, defaults to the dir basename
    tags = { kind = "web" }

    [skills]                     # subagent attributionSkill → extra tags
    deep-research = { kind = "research" }

No filesystem access is needed: matching is purely on path strings, so it works
inside the container that only mounts ~/.claude.
"""

import os
import pathlib
import re
import shlex
import tomllib
from dataclasses import dataclass, field

TAGS_CONFIG = pathlib.Path(os.environ.get(
    "TAGS_CONFIG", str(pathlib.Path.home() / ".config" / "claude-token-tracker" / "tags.toml")))
# Home dir that `~` in the config (and in Bash `cd ~/...`) refers to. Inside Docker this is
# the host user's home, not the container's.
HOME_DIR = os.environ.get("HOME_DIR", str(pathlib.Path.home()))

_PATH_KEYS = ("file_path", "notebook_path", "path")
_CD_RE = re.compile(r"^\s*cd\s+([^;&|]+?)\s*(?:&&|;|\|\||$)")


@dataclass
class Project:
    path: str
    name: str
    tags: dict


@dataclass
class Config:
    projects: list = field(default_factory=list)   # explicit, sorted longest path first
    hubs: list = field(default_factory=list)
    skills: dict = field(default_factory=dict)

    def by_name(self, name: str) -> Project | None:
        for p in self.projects + self.hubs:
            if p.name == name:
                return p
        return None


def _expand(path: str, home: str) -> str:
    if path == "~" or path.startswith("~/"):
        path = home + path[1:]
    return os.path.normpath(path)


def load_config_text(text: str, home: str = HOME_DIR) -> Config:
    raw = tomllib.loads(text)

    def build(entries):
        out = []
        for e in entries:
            path = _expand(e["path"], home)
            out.append(Project(path=path, name=e.get("name") or os.path.basename(path), tags=dict(e.get("tags", {}))))
        return sorted(out, key=lambda p: len(p.path), reverse=True)

    return Config(projects=build(raw.get("project", [])), hubs=build(raw.get("hub", [])),
                  skills={k: dict(v) for k, v in raw.get("skills", {}).items()})


def load_config(path: pathlib.Path = TAGS_CONFIG, home: str = HOME_DIR) -> Config:
    try:
        return load_config_text(path.read_text(), home=home)
    except FileNotFoundError:
        return Config()


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def resolve(path: str, config: Config) -> tuple[str, dict] | None:
    """Map an absolute path to (project name, tags), or None if no config entry covers it."""
    path = os.path.normpath(path)
    for p in config.projects:
        if _under(path, p.path):
            return p.name, dict(p.tags)
    for hub in config.hubs:
        if _under(path, hub.path):
            rest = path[len(hub.path):].strip("/")
            if not rest:
                return hub.name, dict(hub.tags)
            return rest.split("/", 1)[0], dict(hub.tags)
    return None


def extract_paths(blocks: list, cwd: str, home: str = HOME_DIR) -> list[str]:
    """Absolute paths touched by the tool_use blocks of one assistant request."""
    paths = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        inp = b.get("input") or {}
        candidates = [inp[k] for k in _PATH_KEYS if isinstance(inp.get(k), str) and inp.get(k)]
        if b.get("name") == "Bash" and isinstance(inp.get("command"), str):
            m = _CD_RE.match(inp["command"])
            if m:
                try:
                    candidates.extend(shlex.split(m.group(1))[:1])
                except ValueError:
                    pass
        for c in candidates:
            c = _expand(c, home)
            if not os.path.isabs(c):
                if not cwd:
                    continue
                c = os.path.normpath(os.path.join(cwd, c))
            paths.append(c)
    return paths


def attribute_turns(turns: list[dict], session_project: str, config: Config,
                    skill: str = "", inherited: tuple[str, dict] | None = None) -> None:
    """Set `project` and `tags` on each turn dict in place.

    Turns carry `exchange` (index of the user prompt they answer), `paths` and an
    optional `tag` (manual /tag active from that exchange on).
    """
    fallback = inherited or resolve(session_project, config) or (session_project, {})
    skill_tags = config.skills.get(skill, {}) if skill else {}

    exchanges: dict[int, list[dict]] = {}
    for t in turns:
        exchanges.setdefault(t.get("exchange", 0), []).append(t)

    current: tuple[str, dict] | None = None
    manual: tuple[str, dict] | None = None
    for idx in sorted(exchanges):
        ex_turns = exchanges[idx]
        tag = next((t["tag"] for t in ex_turns if t.get("tag")), None)
        if tag:
            p = config.by_name(tag)
            manual = (tag, dict(p.tags) if p else {})
        if manual:
            chosen = manual
        else:
            counts: dict[str, int] = {}
            resolved: dict[str, dict] = {}
            for t in ex_turns:
                for path in t.get("paths", []):
                    r = resolve(path, config)
                    if r:
                        counts[r[0]] = counts.get(r[0], 0) + 1
                        resolved[r[0]] = r[1]
            if counts:
                name = max(counts, key=counts.get)  # ties → first touched (dict order)
                chosen = (name, resolved[name])
            else:
                chosen = current or fallback
        current = chosen
        for t in ex_turns:
            t["project"] = chosen[0]
            t["tags"] = {**chosen[1], **skill_tags}


def attribute_sessions(sessions: list, config: Config) -> None:
    """Attribute every session's turns; subagents inherit the parent's project at spawn time."""
    parents = {s.session_id: s for s in sessions if not s.is_subagent}
    for s in parents.values():
        attribute_turns(s.turns_raw, s.project_path, config)
    for s in sessions:
        if not s.is_subagent:
            continue
        inherited = None
        parent = parents.get(s.parent_session_id)
        if parent:
            before = [t for t in parent.turns_raw if t["ts"] <= s.start_ts and "project" in t]
            if before:
                inherited = (before[-1]["project"], {k: v for k, v in before[-1]["tags"].items()})
        attribute_turns(s.turns_raw, s.project_path, config, skill=s.attribution_skill, inherited=inherited)
