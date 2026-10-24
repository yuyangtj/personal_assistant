"""Read-only tools for the chat agent: list a folder, read a file, search the code.

They work only on the registered repositories, through the server's GitHub token, and
never change anything. What they return is the repository's text: the agent is told to
treat it as data, not instructions.
"""

from __future__ import annotations

import logging
from typing import Any

from app.integrations.github import GitHubClient, GitHubError
from app.repositories.registry import RepositoryRegistry

logger = logging.getLogger(__name__)

MAX_LISTED = 200
LINES_PER_READ = 200
MAX_READ_CHARACTERS = 12_000
MAX_FILES_FOUND = 10
MAX_FRAGMENT_CHARACTERS = 300

#: How the tools are described to the model, in this order.
TOOLS = {
    "repo_files": (
        '{"repository": "<id>", "path": "<folder; empty for the top>"}',
        "the files and folders in a folder",
    ),
    "repo_read": (
        '{"repository": "<id>", "path": "<file>", "from_line": <optional, 1 by default>}',
        f"up to {LINES_PER_READ} numbered lines of a file",
    ),
    "repo_search": (
        '{"repository": "<id>", "query": "<words or code to find>"}',
        "files that contain it (GitHub code search), with the matching lines",
    ),
}


class ToolError(ValueError):
    """A tool call that can't be carried out; its message goes back to the model."""


class RepositoryReader:
    def __init__(self, registry: RepositoryRegistry, github: GitHubClient):
        self.registry = registry
        self.github = github

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        """The tool's result as text, or what went wrong (never raises)."""
        try:
            if name not in TOOLS:
                raise ToolError(f"there is no tool called {name!r}")
            repository = self._repository(arguments.get("repository"))
            if name == "repo_files":
                return self._files(repository, self._path(arguments.get("path"), folder=True))
            if name == "repo_read":
                return self._read(
                    repository, self._path(arguments.get("path")), arguments.get("from_line")
                )
            return self._search(repository, arguments.get("query"))
        except ToolError as error:
            return f"error: {error}"
        except GitHubError as error:
            logger.warning("Repository tool %s failed: %s", name, error)
            return f"error: {error}"

    def _repository(self, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ToolError("say which repository (its id)")
        try:
            return self.registry.get(value.strip()).github_repository
        except LookupError as error:
            raise ToolError(f"{value!r} is not one of the registered repositories") from error

    @staticmethod
    def _path(value: Any, *, folder: bool = False) -> str:
        path = str(value or "").strip().strip("/")
        if not path and not folder:
            raise ToolError("say which file to read")
        parts = path.split("/") if path else []
        if "\\" in path or any(part in ("", ".", "..") for part in parts):
            raise ToolError("use a plain path inside the repository, like app/main.py")
        return path

    def _files(self, repository: str, path: str) -> str:
        names = self.github.list_directory(repository=repository, path=path)
        shown = names[:MAX_LISTED]
        more = f"\n... and {len(names) - len(shown)} more" if len(names) > len(shown) else ""
        return f"{path or '(top)'}:\n" + "\n".join(shown) + more

    def _read(self, repository: str, path: str, from_line: Any) -> str:
        text = self.github.read_file(repository=repository, path=path)
        if text is None:
            raise ToolError(f"there is no file {path}")
        try:
            first = max(1, int(from_line or 1))
        except (TypeError, ValueError) as error:
            raise ToolError("from_line must be a line number") from error
        lines = text.splitlines()
        chunk = lines[first - 1 : first - 1 + LINES_PER_READ]
        numbered, size = [], 0
        for number, line in enumerate(chunk, start=first):
            size += len(line) + 8
            if size > MAX_READ_CHARACTERS:
                break
            numbered.append(f"{number:5d}  {line}")
        last = first + len(numbered) - 1
        tail = (
            f"\n(lines {first}-{last} of {len(lines)}; read on with from_line {last + 1})"
            if last < len(lines)
            else f"\n(lines {first}-{last} of {len(lines)}, the end)"
        )
        return f"{path}:\n" + "\n".join(numbered) + tail

    def _search(self, repository: str, query: Any) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ToolError("say what to search for")
        found = self.github.search_code(
            repository=repository, query=query.strip()[:200], limit=MAX_FILES_FOUND
        )
        if not found:
            return f"nothing found for {query.strip()!r}"
        lines = []
        for path, fragments in found:
            lines.append(path)
            lines += [
                "    " + " ".join(fragment.split())[:MAX_FRAGMENT_CHARACTERS]
                for fragment in fragments[:2]
            ]
        return "\n".join(lines)
