from __future__ import annotations

from pathlib import Path
from typing import Any

from codeferry.conversation import ConversationManager, Message

USER_MEMORIES_RELPATH = ".codeferry/memories.md"
PROJECT_MEMORIES_RELPATH = ".codeferry/memories.md"

MEMORY_EXTRACTION_PROMPT = """\
You are a memory extraction assistant. Analyze the conversation below, extract
information worth remembering long term, and update memories.md.

Categories:
- **User Preferences**: the user's coding habits and style requirements, such as
  indentation, naming conventions, and language preferences
- **Correction Feedback**: mistakes explicitly pointed out by the user and the
  correct approach
- **Project Knowledge**: project-specific technical information, such as the tech
  stack, directory structure, and deployment method
- **References**: external links and documentation URLs

Rules:
1. Do not add duplicate entries with the same meaning as existing entries.
2. If a category has nothing worth remembering, leave it empty. Do not write any
   entries or placeholders.
3. Each memory must be a concrete item on its own line starting with `- `. Do not
   use `...` as a placeholder.
4. Output the complete memories.md content, including all four category headings.

Output format (follow strictly; do not write any entries under empty categories):
### User Preferences
- User prefers concise code style

### Correction Feedback

### Project Knowledge
- Project uses PostgreSQL 15

### References

Do not output anything else. Do not call any tools."""

_USER_LEVEL_HEADERS = {
    "User Preferences",
    "Correction Feedback",
    "\u7528\u6237\u504f\u597d",
    "\u7ea0\u6b63\u53cd\u9988",
}
_PROJECT_LEVEL_HEADERS = {
    "Project Knowledge",
    "References",
    "\u9879\u76ee\u77e5\u8bc6",
    "\u53c2\u8003\u8d44\u6599",
}


class MemoryManager:
    def __init__(self, project_root: str) -> None:
        self._user_path = Path.home() / USER_MEMORIES_RELPATH
        self._project_path = Path(project_root) / PROJECT_MEMORIES_RELPATH
        self._last_extraction_msg_count = 0


    @property
    def user_path(self) -> Path:
        return self._user_path


    @property
    def project_path(self) -> Path:
        return self._project_path

    @property
    def user_mem_dir(self) -> Path:
        """User-level memory directory (~/.codeferry/memory/).

        This is where .md memory files with frontmatter (type user/feedback)
        live. Distinct from ``user_path`` which points at the flat
        ``memories.md`` file.
        """
        return Path.home() / ".codeferry" / "memory"

    @property
    def project_mem_dir(self) -> Path:
        """Project-level memory directory (<project>/.codeferry/memory/).

        This is where .md memory files with frontmatter (type
        project/reference) live. Distinct from ``project_path`` which
        points at the flat ``memories.md`` file.
        """
        return self._project_path.parent / "memory"

    def load(self) -> str:
        sections: list[str] = []

        if self._user_path.exists():
            content = self._user_path.read_text(encoding="utf-8").strip()
            if content:
                sections.append(content)

        if self._project_path.exists():
            content = self._project_path.read_text(encoding="utf-8").strip()
            if content:
                sections.append(content)

        return "\n\n".join(sections)

    async def extract(
        self,
        client: Any,
        conversation: ConversationManager,
        protocol: str,
    ) -> None:
        from codeferry.tools.base import StreamEnd, TextDelta

        current_memories = self.load()

        recent = conversation.history[self._last_extraction_msg_count :]
        if not recent:
            return

        conv_lines: list[str] = []
        for msg in recent:
            if msg.role == "user" and msg.content:
                conv_lines.append(f"User: {msg.content}")
            elif msg.role == "assistant" and msg.content:
                conv_lines.append(f"Assistant: {msg.content}")

        if not conv_lines:
            return

        prompt = (
            f"{MEMORY_EXTRACTION_PROMPT}\n\n"
            f"## Current memories.md\n"
            f"{current_memories if current_memories else '(empty)'}\n\n"
            f"## Recent conversation\n"
            f"{chr(10).join(conv_lines)}\n\n"
            f"Please output the complete updated memories.md content."
        )

        extract_conv = ConversationManager()
        extract_conv.history = [Message(role="user", content=prompt)]

        collected = ""
        try:
            async for event in client.stream(
                extract_conv, system="You are a memory extraction assistant."
            ):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
        except Exception:
            return

        self._last_extraction_msg_count = len(conversation.history)

        collected = collected.strip()
        if not collected:
            return

        self._write_memories(collected)

    def _write_memories(self, content: str) -> None:
        user_sections: list[str] = []
        project_sections: list[str] = []

        current_header = ""
        current_lines: list[str] = []

        for line in content.split("\n"):
            if line.startswith("### "):
                if current_header:
                    self._assign_section(
                        current_header, current_lines, user_sections, project_sections
                    )
                current_header = line
                current_lines = []
            else:
                current_lines.append(line)

        if current_header:
            self._assign_section(
                current_header, current_lines, user_sections, project_sections
            )

        if user_sections:
            self._user_path.parent.mkdir(parents=True, exist_ok=True)
            self._user_path.write_text(
                "\n".join(user_sections).strip() + "\n", encoding="utf-8"
            )

        if project_sections:
            self._project_path.parent.mkdir(parents=True, exist_ok=True)
            self._project_path.write_text(
                "\n".join(project_sections).strip() + "\n", encoding="utf-8"
            )

    @staticmethod
    def _is_placeholder(line: str) -> bool:
        stripped = line.strip().lstrip("- ").strip()
        return stripped in {"", "...", "N/A", "None", "No items"}


    @staticmethod
    def _assign_section(
        header: str,
        lines: list[str],
        user_sections: list[str],
        project_sections: list[str],
    ) -> None:
        real_lines = [l for l in lines if l.strip().startswith("- ") and not MemoryManager._is_placeholder(l)]
        if not real_lines:
            return

        section_text = header + "\n" + "\n".join(real_lines)

        for keyword in _USER_LEVEL_HEADERS:
            if keyword in header:
                user_sections.append(section_text)
                return

        for keyword in _PROJECT_LEVEL_HEADERS:
            if keyword in header:
                project_sections.append(section_text)
                return


    def clear(self) -> None:
        if self._user_path.exists():
            self._user_path.write_text("", encoding="utf-8")
        if self._project_path.exists():
            self._project_path.write_text("", encoding="utf-8")

    def get_display_text(self) -> str:
        parts: list[str] = []

        if self._user_path.exists():
            content = self._user_path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"[User-level] {self._user_path}\n{content}")

        if self._project_path.exists():
            content = self._project_path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"[Project-level] {self._project_path}\n{content}")

        if not parts:
            return "There are currently no automatic memories."

        return "\n\n".join(parts)
