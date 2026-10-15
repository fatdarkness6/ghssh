"""Lossless edits of one explicit github.com Host block; fail closed on ambiguity."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .utils import GhsshError, Layout, atomic_write, read_bytes


@dataclass(frozen=True)
class Directive:
    name: str
    values: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    patterns: tuple[str, ...]
    directives: tuple[Directive, ...]
    kind: str = "host"

    @property
    def content_end(self) -> int:
        return self.directives[-1].line + 1 if self.directives else self.start + 1

    @property
    def github(self) -> bool:
        def matches(pattern: str) -> bool:
            # OpenSSH supports * and ?; unlike fnmatch it does not treat [] as a character class.
            expression = "".join(
                ".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern.lower()
            )
            return re.fullmatch(expression, "github.com") is not None

        positives = [p for p in self.patterns if not p.startswith("!")]
        excluded = any(matches(p[1:]) for p in self.patterns if p.startswith("!"))
        return not excluded and any(matches(p) for p in positives)


class SSHConfig:
    def __init__(self, text: str):
        if "\x00" in text or "\r" in text.replace("\r\n", ""):
            raise GhsshError("Malformed SSH config: invalid control characters.")
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.globals: list[Directive] = []
        headers: list[tuple[int, Directive]] = []
        parsed: list[Directive] = []
        for number, line in enumerate(self.lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # OpenSSH accepts Keyword=value as well as Keyword value.
            normalized = re.sub(r"^([A-Za-z][A-Za-z0-9]*)\s*=\s*", r"\1 ", stripped)
            try:
                tokens = shlex.split(normalized, comments=True)
            except ValueError as exc:
                raise GhsshError(f"Malformed SSH config on line {number + 1}: {exc}") from exc
            if len(tokens) < 2 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", tokens[0]):
                raise GhsshError(f"Malformed SSH config on line {number + 1}.")
            directive = Directive(tokens[0].lower(), tuple(tokens[1:]), number)
            parsed.append(directive)
            if directive.name in {"host", "match"}:
                headers.append((number, directive))
            elif not headers:
                self.globals.append(directive)
        self.blocks = []
        for index, (start, header) in enumerate(headers):
            end = headers[index + 1][0] if index + 1 < len(headers) else len(self.lines)
            self.blocks.append(
                Block(
                    start,
                    end,
                    header.values,
                    tuple(d for d in parsed if start < d.line < end),
                    header.name,
                )
            )
        self.directives = parsed

    def target(self) -> Block | None:
        candidates = [
            b
            for b in self.blocks
            if b.kind == "host" and b.github and any(p.lower() == "github.com" for p in b.patterns)
        ]
        if len(candidates) > 1:
            raise GhsshError(
                "Multiple github.com Host entries are ambiguous; consolidate them first."
            )
        if not candidates:
            return None
        block = candidates[0]
        if len(block.patterns) != 1:
            raise GhsshError("github.com shares a Host block with other hosts; split it first.")
        return block

    def validate_edit(self) -> None:
        self.target()
        if any(d.name in {"include", "match"} for d in self.directives):
            raise GhsshError(
                "Include/Match configuration cannot be edited safely; use an explicit simple config."
            )
        sensitive = {
            "identityfile",
            "hostname",
            "user",
            "identitiesonly",
            "certificatefile",
            "identityagent",
        }
        if any(d.name in sensitive for d in self.globals):
            raise GhsshError(
                "Global identity settings affect github.com; move them to explicit Host blocks first."
            )
        for block in self.blocks:
            if (
                block.github
                and block is not self.target()
                and any(d.name in sensitive for d in block.directives)
            ):
                raise GhsshError(
                    "A wildcard/shared Host block supplies GitHub identity settings; split it first."
                )
        target = self.target()
        if target and any(
            d.name in {"certificatefile", "identityagent"} for d in target.directives
        ):
            raise GhsshError("GitHub certificate/custom-agent configuration needs manual editing.")

    def identity(self, layout: Layout) -> Path | None:
        block = self.target()
        values = [d for d in block.directives if d.name == "identityfile"] if block else []
        if len(values) > 1 or (values and len(values[0].values) != 1):
            raise GhsshError(
                "Multiple GitHub IdentityFile values are ambiguous; choose one manually."
            )
        return layout.identity_path(values[0].values[0]) if values else None

    def section(self) -> str:
        block = self.target()
        return "".join(self.lines[block.start : block.content_end]) if block else ""

    def insertion_position(self) -> int:
        # Keep the comment preamble attached to the first existing Host block.
        return self.globals[-1].line + 1 if self.globals else 0

    def references_elsewhere(
        self, path: Path, layout: Layout, *, conservative: bool = True
    ) -> bool:
        target = self.target()
        for block in self.blocks:
            if block is target:
                continue
            for directive in block.directives:
                if directive.name == "identityfile":
                    for value in directive.values:
                        try:
                            if layout.identity_path(value) == path:
                                return True
                        except GhsshError:
                            # An unresolvable identity reference cannot prove exclusivity.
                            if conservative:
                                return True
        return False

    def upsert(self, identity: str, *, disable_agent_additions: bool = False) -> str:
        self.validate_edit()
        block = self.target()
        nl = self.newline
        replacements = {
            "hostname": "github.com",
            "user": "git",
            "identityfile": identity,
            "identitiesonly": "yes",
        }
        canonical = {
            "hostname": "HostName",
            "user": "User",
            "identityfile": "IdentityFile",
            "identitiesonly": "IdentitiesOnly",
            "addkeystoagent": "AddKeysToAgent",
        }
        if disable_agent_additions:
            replacements["addkeystoagent"] = "no"
        if block:
            body = self.lines[block.start : block.content_end].copy()
            seen: set[str] = set()
            for directive in reversed(block.directives):
                if directive.name in replacements:
                    original = body[directive.line - block.start]
                    indent = re.match(r"\s*", original).group()
                    # Retain inline comments on edited lines.
                    comment = self._comment(original)
                    body[directive.line - block.start] = (
                        f"{indent}{canonical[directive.name]} {replacements[directive.name]}{comment}{nl}"
                    )
                    seen.add(directive.name)
            # Do not duplicate a directive: first-value semantics can hide errors.
            for name in replacements:
                if sum(d.name == name for d in block.directives) > 1:
                    raise GhsshError(
                        f"Duplicate {name} in GitHub Host block; consolidate it first."
                    )
            if body and not body[-1].endswith(("\n", "\r")):
                body[-1] += nl
            body.extend(
                f"    {canonical[name]} {value}{nl}"
                for name, value in replacements.items()
                if name not in seen
            )
            return "".join(self.lines[: block.start] + body + self.lines[block.content_end :])
        section = (
            f"Host github.com{nl}"
            + "".join(f"    {canonical[name]} {value}{nl}" for name, value in replacements.items())
            + nl
        )
        # Insert before Host defaults, after global directives (preserving their scope).
        position = self.insertion_position()
        prefix = "".join(self.lines[:position])
        if prefix and not prefix.endswith("\n"):
            prefix += nl
        return prefix + section + "".join(self.lines[position:])

    @staticmethod
    def _comment(line: str) -> str:
        quote = None
        escaped = False
        for i, char in enumerate(line):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char in {"'", '"'}:
                quote = None if quote == char else (char if quote is None else quote)
            elif char == "#" and quote is None:
                return "  " + line[i:].rstrip("\r\n")
        return ""

    def remove(self) -> str:
        self.validate_edit()
        block = self.target()
        if not block:
            return self.text
        # Comments and whitespace belong to the user; retain them even when removing the block.
        preserved = [
            line
            for line in self.lines[block.start + 1 : block.end]
            if not line.strip() or line.lstrip().startswith("#")
        ]
        return "".join(self.lines[: block.start] + preserved + self.lines[block.end :])

    def restore_section(self, section: str) -> str:
        self.validate_edit()
        parsed = SSHConfig(section)
        parsed.validate_edit()
        if not parsed.target() or len(parsed.blocks) != 1 or parsed.globals:
            raise GhsshError("Backup GitHub config section is invalid.")
        block = self.target()
        if section and not section.endswith("\n"):
            section += self.newline
        if block:
            return (
                "".join(self.lines[: block.start])
                + section
                + "".join(self.lines[block.content_end :])
            )
        position = self.insertion_position()
        prefix = "".join(self.lines[:position])
        if prefix and not prefix.endswith("\n"):
            prefix += self.newline
        return prefix + section + "".join(self.lines[position:])


class ConfigManager:
    def __init__(self, layout: Layout):
        self.layout = layout

    def read(self) -> SSHConfig:
        path = self.layout.checked(self.layout.config)
        try:
            return SSHConfig(read_bytes(path).decode("utf-8") if path.exists() else "")
        except UnicodeError as exc:
            raise GhsshError("SSH config is not valid UTF-8; edit it manually.") from exc

    def write(self, text: str, expected: str) -> None:
        if self.read().text != expected:
            raise GhsshError("SSH config changed during the operation; refusing to overwrite it.")
        SSHConfig(text).validate_edit()
        atomic_write(self.layout, self.layout.config, text.encode(), 0o600)
