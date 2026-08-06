"""Lightweight consistency checks between the profile, job, and form audit."""
from __future__ import annotations

import re
from typing import Any


class ConsistencyAgent:
    """Flag contradictions between profile data and filled form answers."""

    # Common English words that should not be treated as hallucinated skills.
    _COMMON_WORDS = {
        "a", "about", "all", "also", "am", "an", "and", "are", "as", "at", "be", "been",
        "being", "but", "by", "can", "could", "data", "do", "does", "did", "doing", "etc",
        "for", "from", "had", "has", "have", "having", "i", "in", "is", "it", "its", "knowledge",
        "learning", "machine", "make", "makes", "made", "making", "many", "me", "more", "my",
        "no", "not", "of", "on", "one", "only", "or", "our", "out", "over", "role", "science",
        "should", "so", "some", "such", "team", "than", "that", "the", "their", "them", "then",
        "there", "these", "they", "this", "those", "to", "tool", "tools", "up", "use", "used",
        "using", "was", "we", "were", "what", "when", "where", "which", "while", "who", "will",
        "with", "work", "worked", "working", "works", "would", "years", "year", "analysis",
        "analytics", "engineering", "engineer", "project", "projects", "experience", "experienced",
    }

    def check(
        self,
        profile: dict[str, Any],
        job: Any,
        form_audit: list[dict[str, Any]],
    ) -> list[str]:
        """Return a list of human-readable contradiction strings."""
        issues: list[str] = []
        personal = profile.get("personal_info", {})
        work_auth = (personal.get("work_authorization", "") or "").lower()
        is_us_worker = any(
            phrase in work_auth for phrase in ("us citizen", "permanent resident", "green card")
        )
        profile_skills = {s.lower() for s in profile.get("skills", [])}

        for entry in form_audit:
            label = entry.get("label") or ""
            label_lower = label.lower()
            value = entry.get("value")
            value_str = "" if value is None else str(value)
            value_lower = value_str.lower()
            required = bool(entry.get("required", False))
            disposition = entry.get("disposition", "")
            answer_source = entry.get("answer_source", "")
            field_type = entry.get("field_type", "")
            visible = bool(entry.get("visible", True))
            browser_verified = bool(entry.get("browser_verified", False))

            # Hidden fields are not blockers for the final decision.
            if field_type == "hidden" or not visible:
                continue

            # A US worker should not answer "Yes" to a visa-sponsorship question.
            if is_us_worker and (
                "require visa sponsorship" in label_lower or "need sponsorship" in label_lower
            ):
                if "yes" in value_lower:
                    issues.append(
                        f"Profile work authorization is '{work_auth}' but answered "
                        f"'{value_str}' to '{label}'"
                    )

            # A US worker should not answer "No" to an authorized-to-work question.
            if "authorized to work" in label_lower or "legally authorized" in label_lower:
                if "no" in value_lower and is_us_worker:
                    issues.append(
                        f"Profile says US worker but answered '{value_str}' to '{label}'"
                    )

            # Years of experience should not exceed profile claims.
            if re.search(r"years of .* experience", label_lower):
                claimed_years = self._parse_profile_years(profile.get("experience_highlights", []))
                answer_years = self._parse_years(value_str)
                if (
                    claimed_years is not None
                    and answer_years is not None
                    and answer_years > claimed_years
                ):
                    issues.append(
                        f"Answered {answer_years} years for '{label}' but profile "
                        f"claims {claimed_years} years"
                    )

            # Generated answers should not introduce skills not listed in the profile.
            if answer_source == "llm" and value_str:
                issues.extend(
                    self._check_skills(value_str, profile_skills, label)
                )

            # Required fields that were skipped or need human review are blockers.
            if required and disposition in ("needs_human", "skipped"):
                # If the field is hidden and browser_verified is False, do not flag it.
                if field_type == "hidden" or not visible:
                    continue
                issues.append(
                    f"Required field '{label}' (disposition={disposition}) was not successfully filled"
                )

            # A visible required field that was not browser-verified is a blocker.
            if required and visible and not browser_verified and disposition not in (
                "needs_human",
                "skipped",
            ):
                issues.append(
                    f"Required field '{label}' was not verified in the browser"
                )

        return issues

    def _check_skills(
        self,
        answer: str,
        profile_skills: set[str],
        label: str,
    ) -> list[str]:
        """Flag tokens in the answer that look like skills but are not in the profile."""
        issues: list[str] = []
        # Split by common delimiters used in skill lists, keeping original case for messages.
        candidates = re.split(r"[,;/]|\band\b|\bor\b", answer)
        for candidate in candidates:
            token = candidate.strip()
            token_lower = token.lower()
            if not token or len(token) <= 2 or token_lower in self._COMMON_WORDS:
                continue
            # Normalize multi-word skills to a single space token.
            token_lower = re.sub(r"\s+", " ", token_lower)
            if token_lower in profile_skills:
                continue
            # Treat tokens that are likely normal prose words as not skills.
            if token.isalpha() and token_lower not in profile_skills:
                issues.append(
                    f"Generated answer for '{label}' may contain a skill not in profile: '{token}'"
                )
        return issues

    @staticmethod
    def _parse_years(text: str) -> int | None:
        """Extract a numeric year count from the answer text."""
        text = text.strip().lower()
        # Plain number (e.g. "7") or "7 years".
        if text.isdigit():
            return int(text)
        match = re.search(r"(\d+)\+?\s*years?", text)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _parse_profile_years(highlights: list[str]) -> int | None:
        """Extract the maximum year count claimed in the profile experience highlights."""
        if not highlights:
            return None
        years: list[int] = []
        for highlight in highlights:
            for match in re.finditer(r"(\d+)\+?\s*years?", highlight.lower()):
                years.append(int(match.group(1)))
        return max(years) if years else None
