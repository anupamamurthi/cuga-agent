"""
Skills Manager - loads markdown skill files from a directory and formats them for
injection into the agent's system prompt.

Inspired by OpenClaw's skills-as-markdown pattern: drop a .md file into a skills/
directory and the agent automatically picks it up. Each file defines one named
skill — a focused block of guidance the agent can apply.

Skill file format (any .md file):

    # Skill Name
    Short description of what this skill enables.

    ## When to use
    Describe the scenarios where this skill applies.

    ## Guidelines
    - Bullet point guidance
    - More guidance

The `# H1` heading becomes the displayed skill name. Everything below it is
injected verbatim. Files are sorted alphabetically so load order is predictable.

Usage:
    # Load from an explicit directory
    skills_content = SkillsManager.load_from_directory("./my_skills")

    # Load from the default .cuga/skills/ sub-folder
    skills_content = SkillsManager.load_from_cuga_folder(".cuga")

    # Pass result to CugaAgent
    agent = CugaAgent(tools=[...], skills_dir="./my_skills")
"""

from pathlib import Path
from loguru import logger


class SkillsManager:
    """Loads and formats markdown skill files for system-prompt injection."""

    @staticmethod
    def load_from_directory(skills_dir: str | Path) -> str:
        """Load all .md files from *skills_dir* and return a formatted string.

        Each file is treated as one skill. Files are sorted alphabetically so
        load order is deterministic. Missing or empty directories return "".

        Args:
            skills_dir: Path to the directory containing .md skill files.

        Returns:
            A formatted string ready for injection into the system prompt,
            or "" if the directory is empty / doesn't exist.
        """
        skills_path = Path(skills_dir).expanduser().resolve()

        if not skills_path.exists():
            logger.debug(f"Skills directory not found, skipping: {skills_path}")
            return ""

        if not skills_path.is_dir():
            logger.warning(f"Skills path is not a directory: {skills_path}")
            return ""

        skill_files = sorted(skills_path.glob("*.md"))
        if not skill_files:
            logger.debug(f"No .md files found in skills directory: {skills_path}")
            return ""

        loaded = []
        for skill_file in skill_files:
            content = SkillsManager._read_skill_file(skill_file)
            if content:
                loaded.append(content)

        if not loaded:
            return ""

        logger.info(f"Loaded {len(loaded)} skill(s) from {skills_path}")

        header = "# SKILLS\n\nThe following skills provide domain-specific guidance. Apply them when relevant.\n"
        divider = "\n\n---\n\n"
        return header + divider + divider.join(loaded)

    @staticmethod
    def load_from_cuga_folder(cuga_folder: str | Path = ".cuga") -> str:
        """Load skills from the `skills/` sub-directory inside *cuga_folder*.

        This mirrors the OpenClaw pattern where users drop skill files into a
        well-known project folder (`.cuga/skills/`) and the agent auto-loads them.

        Args:
            cuga_folder: Path to the .cuga folder (defaults to ".cuga").

        Returns:
            Formatted skills string, or "" if no skills are found.
        """
        skills_dir = Path(cuga_folder) / "skills"
        return SkillsManager.load_from_directory(skills_dir)

    @staticmethod
    def _read_skill_file(path: Path) -> str:
        """Read a single skill file and return its stripped content."""
        try:
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                logger.debug(f"Skill file is empty, skipping: {path.name}")
                return ""
            logger.debug(f"Loaded skill: {path.name}")
            return content
        except Exception as exc:
            logger.warning(f"Failed to read skill file {path}: {exc}")
            return ""

    @staticmethod
    def merge(*skill_contents: str) -> str:
        """Merge multiple pre-loaded skill strings into one.

        Useful when combining skills from several directories (e.g. a shared
        library plus a project-specific folder).

        Args:
            *skill_contents: Strings previously returned by load_from_directory.

        Returns:
            A single merged skills string, or "" if all inputs are empty.
        """
        non_empty = [s for s in skill_contents if s and s.strip()]
        if not non_empty:
            return ""
        if len(non_empty) == 1:
            return non_empty[0]

        # Strip individual headers so the merged result has only one
        def _strip_header(s: str) -> str:
            lines = s.splitlines()
            # Drop the leading "# SKILLS\n\n..." block (first 3 lines)
            for i, line in enumerate(lines):
                if line.startswith("---"):
                    return "\n".join(lines[i + 1 :]).strip()
            return s

        header = "# SKILLS\n\nThe following skills provide domain-specific guidance. Apply them when relevant.\n"
        divider = "\n\n---\n\n"
        bodies = [_strip_header(s) for s in non_empty]
        return header + divider + divider.join(bodies)
