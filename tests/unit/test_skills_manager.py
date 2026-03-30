"""
Unit tests for the SkillsManager — skills-as-markdown feature.

Tests cover:
- Loading skills from a directory of .md files
- Auto-discovery via .cuga/skills/
- Merging multiple skill sources
- Edge cases (empty dir, missing dir, empty files, non-.md files)
- Prompt injection: the formatted skills string appears in the rendered mcp_prompt
"""

import pytest
from pathlib import Path
from jinja2 import Template


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill_file(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a skill file and return its path."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# SkillsManager unit tests
# ---------------------------------------------------------------------------

class TestSkillsManagerLoadFromDirectory:
    def test_loads_single_skill(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        _make_skill_file(
            tmp_path,
            "greet.md",
            "# Greeting Skill\n\nAlways greet the user warmly.",
        )
        result = SkillsManager.load_from_directory(tmp_path)

        assert "# SKILLS" in result
        assert "Greeting Skill" in result
        assert "Always greet the user warmly." in result

    def test_loads_multiple_skills_alphabetically(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        _make_skill_file(tmp_path, "b_second.md", "# Second Skill\n\nSecond content.")
        _make_skill_file(tmp_path, "a_first.md", "# First Skill\n\nFirst content.")

        result = SkillsManager.load_from_directory(tmp_path)

        # Both skills present
        assert "First Skill" in result
        assert "Second Skill" in result
        # Alphabetical order: a_first before b_second
        assert result.index("First Skill") < result.index("Second Skill")

    def test_ignores_non_md_files(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        _make_skill_file(tmp_path, "skill.md", "# Real Skill\n\nReal content.")
        (tmp_path / "notes.txt").write_text("should be ignored")
        (tmp_path / "config.toml").write_text("also ignored = true")

        result = SkillsManager.load_from_directory(tmp_path)

        assert "Real Skill" in result
        assert "should be ignored" not in result
        assert "also ignored" not in result

    def test_skips_empty_files(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        _make_skill_file(tmp_path, "empty.md", "   \n  ")
        _make_skill_file(tmp_path, "real.md", "# Valid Skill\n\nContent here.")

        result = SkillsManager.load_from_directory(tmp_path)

        assert "Valid Skill" in result
        # Empty file should not produce a stray separator
        assert result.count("---") == 1  # only one skill means one separator (header + body)

    def test_returns_empty_string_for_missing_directory(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        result = SkillsManager.load_from_directory(tmp_path / "nonexistent")
        assert result == ""

    def test_returns_empty_string_for_empty_directory(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        result = SkillsManager.load_from_directory(tmp_path)
        assert result == ""

    def test_skills_header_present(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        _make_skill_file(tmp_path, "any.md", "# Any Skill\n\nSome guidance.")
        result = SkillsManager.load_from_directory(tmp_path)

        assert result.startswith("# SKILLS")

    def test_skills_have_dividers_between_them(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        _make_skill_file(tmp_path, "skill_a.md", "# Skill A\n\nContent A.")
        _make_skill_file(tmp_path, "skill_b.md", "# Skill B\n\nContent B.")

        result = SkillsManager.load_from_directory(tmp_path)

        # Two skills → at least two --- dividers (one after header, one between skills)
        assert result.count("---") >= 2


class TestSkillsManagerLoadFromCugaFolder:
    def test_loads_from_cuga_skills_subfolder(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        skills_dir = tmp_path / ".cuga" / "skills"
        skills_dir.mkdir(parents=True)
        _make_skill_file(skills_dir, "privacy.md", "# Privacy\n\nMask PII in output.")

        result = SkillsManager.load_from_cuga_folder(tmp_path / ".cuga")

        assert "Privacy" in result
        assert "Mask PII in output." in result

    def test_returns_empty_when_no_skills_subfolder(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        (tmp_path / ".cuga").mkdir()
        result = SkillsManager.load_from_cuga_folder(tmp_path / ".cuga")
        assert result == ""


class TestSkillsManagerMerge:
    def test_merge_two_sources(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        _make_skill_file(dir_a, "skill_a.md", "# Alpha\n\nAlpha content.")
        _make_skill_file(dir_b, "skill_b.md", "# Beta\n\nBeta content.")

        skills_a = SkillsManager.load_from_directory(dir_a)
        skills_b = SkillsManager.load_from_directory(dir_b)
        merged = SkillsManager.merge(skills_a, skills_b)

        assert "Alpha" in merged
        assert "Beta" in merged
        # Only one # SKILLS header in the merged result
        assert merged.count("# SKILLS") == 1

    def test_merge_ignores_empty_strings(self, tmp_path):
        from cuga.configurations.skills_manager import SkillsManager

        dir_a = tmp_path / "a"
        dir_a.mkdir()
        _make_skill_file(dir_a, "skill.md", "# Only Skill\n\nContent.")

        skills_a = SkillsManager.load_from_directory(dir_a)
        merged = SkillsManager.merge(skills_a, "", "")

        assert "Only Skill" in merged
        assert merged.count("# SKILLS") == 1

    def test_merge_all_empty_returns_empty_string(self):
        from cuga.configurations.skills_manager import SkillsManager

        assert SkillsManager.merge("", "", "") == ""


# ---------------------------------------------------------------------------
# Prompt injection integration: skills appear in the rendered mcp_prompt
# ---------------------------------------------------------------------------

class TestSkillsInjectedIntoPrompt:
    """Verify that the skills string makes it into the rendered mcp_prompt.jinja2."""

    @pytest.fixture
    def mcp_template(self):
        template_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "cuga"
            / "backend"
            / "cuga_graph"
            / "nodes"
            / "cuga_lite"
            / "prompts"
            / "mcp_prompt.jinja2"
        )
        with open(template_path, "r") as f:
            return Template(f.read())

    def _minimal_context(self, skills=None, special_instructions=None):
        return {
            "base_prompt": None,
            "apps": [],
            "allow_user_clarification": True,
            "return_to_user_cases": None,
            "instructions": None,
            "tools": [],
            "task_loaded_from_file": False,
            "is_autonomous_subtask": False,
            "enable_find_tools": False,
            "special_instructions": special_instructions,
            "skills": skills,
        }

    def test_skills_section_rendered_when_provided(self, mcp_template):
        skills_content = (
            "# SKILLS\n\nThe following skills provide domain-specific guidance.\n\n"
            "---\n\n# Currency Conversion\n\nAlways show both original and converted values."
        )
        ctx = self._minimal_context(skills=skills_content)
        rendered = mcp_template.render(ctx)

        assert "# SKILLS" in rendered
        assert "Currency Conversion" in rendered
        assert "Always show both original and converted values." in rendered

    def test_skills_section_absent_when_none(self, mcp_template):
        ctx = self._minimal_context(skills=None)
        rendered = mcp_template.render(ctx)

        assert "# SKILLS" not in rendered

    def test_skills_section_absent_when_empty_string(self, mcp_template):
        ctx = self._minimal_context(skills="")
        rendered = mcp_template.render(ctx)

        assert "# SKILLS" not in rendered

    def test_skills_and_special_instructions_both_rendered(self, mcp_template):
        skills_content = "# SKILLS\n\n---\n\n# My Skill\n\nSkill guidance."
        ctx = self._minimal_context(
            skills=skills_content,
            special_instructions="## Policy\nAlways confirm before deleting.",
        )
        rendered = mcp_template.render(ctx)

        assert "My Skill" in rendered
        assert "Always confirm before deleting." in rendered

    def test_skills_appear_after_special_instructions(self, mcp_template):
        """Skills section should come after special_instructions in the rendered output."""
        skills_content = "# SKILLS\n\n---\n\n# Ordering Test\n\nSkill content here."
        ctx = self._minimal_context(
            skills=skills_content,
            special_instructions="SPECIAL_MARKER",
        )
        rendered = mcp_template.render(ctx)

        special_pos = rendered.index("SPECIAL_MARKER")
        skills_pos = rendered.index("Ordering Test")
        assert skills_pos > special_pos

    def test_end_to_end_with_skills_manager_and_template(self, mcp_template, tmp_path):
        """Full end-to-end: write a skill file → load → render → verify in prompt."""
        from cuga.configurations.skills_manager import SkillsManager

        skill_content = (
            "# Date Formatting\n\n"
            "Convert epoch timestamps to YYYY-MM-DD HH:MM UTC before displaying."
        )
        _make_skill_file(tmp_path, "date_formatting.md", skill_content)

        skills = SkillsManager.load_from_directory(tmp_path)
        ctx = self._minimal_context(skills=skills)
        rendered = mcp_template.render(ctx)

        assert "# SKILLS" in rendered
        assert "Date Formatting" in rendered
        assert "Convert epoch timestamps" in rendered


# ---------------------------------------------------------------------------
# Example skills directory sanity check
# ---------------------------------------------------------------------------

class TestExampleSkills:
    """Verify the bundled example skill files are valid and loadable."""

    def test_example_skills_directory_loads(self):
        from cuga.configurations.skills_manager import SkillsManager

        examples_dir = (
            Path(__file__).parent.parent.parent
            / "src"
            / "cuga"
            / "configurations"
            / "skills"
            / "examples"
        )
        result = SkillsManager.load_from_directory(examples_dir)

        assert result != "", "Expected example skills to load non-empty content"
        assert "# SKILLS" in result

    def test_example_skills_contain_expected_skills(self):
        from cuga.configurations.skills_manager import SkillsManager

        examples_dir = (
            Path(__file__).parent.parent.parent
            / "src"
            / "cuga"
            / "configurations"
            / "skills"
            / "examples"
        )
        result = SkillsManager.load_from_directory(examples_dir)

        assert "Currency Conversion" in result
        assert "Data Privacy" in result
        assert "Date Formatting" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
