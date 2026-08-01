"""Fit-scoring agent combining LLM semantic matching and keyword overlap."""
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from job_agent.config import Settings


FIT_SCORE_PROMPT = """You are an expert career advisor. Score how well the candidate fits the job below.

Candidate Profile:
{profile_text}

Job:
Title: {title}
Company: {company}
Location: {location}
Description: {description}
Requirements: {requirements}

Instructions:
- Return ONLY a JSON object with keys: "score" (integer 0-100) and "reason" (one sentence).
- 80-100: strong match, candidate clearly meets most requirements.
- 60-79: decent match, candidate meets many requirements with minor gaps.
- 40-59: partial match, significant gaps.
- 0-39: poor match or irrelevant.
- Be objective; do not inflate scores.
"""


# Common English stopwords to ignore in keyword overlap.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "and", "or", "for", "with", "in", "on", "at", "by", "from",
    "as", "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "we", "our", "us", "you", "your", "i", "me", "my", "he", "she", "his", "her",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "must", "can", "shall", "about", "above", "across", "after",
    "against", "along", "among", "around", "before", "behind", "below", "beneath",
    "beside", "between", "beyond", "during", "inside", "into", "near", "off", "onto",
    "outside", "over", "under", "until", "upon", "within", "without", "through",
    "throughout", "toward", "towards", "up", "down", "out", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why", "how", "all",
    "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just", "now", "also",
    "new", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}


class KeywordScorer:
    """Compute keyword overlap between a job and a candidate profile."""

    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.profile_keywords = self._extract_profile_keywords(profile)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Lower-case, de-punctuate, and drop stopwords / short tokens."""
        text = re.sub(r"[^a-zA-Z0-9+#/\-]", " ", text.lower())
        tokens = {
            token.strip("-+")
            for token in text.split()
            if len(token) >= 2 and token not in _STOPWORDS
        }
        return tokens

    def _extract_profile_keywords(self, profile: dict[str, Any]) -> set[str]:
        parts: list[str] = []
        skills = profile.get("skills", [])
        parts.extend(skills)

        highlights = profile.get("experience_highlights", [])
        parts.extend(highlights)

        preferences = profile.get("preferences", {})
        target_roles = preferences.get("target_roles", [])
        parts.extend(target_roles)

        education = profile.get("education", [])
        for edu in education:
            if isinstance(edu, dict):
                parts.append(edu.get("degree", ""))
                parts.append(edu.get("institution", ""))

        personal = profile.get("personal_info", {})
        parts.append(personal.get("name", ""))

        text = " ".join(str(p) for p in parts if p)
        return self._tokenize(text)

    def score(self, job) -> tuple[int, str]:
        """Return keyword coverage score (0-100) and a one-line reason."""
        job_text = " ".join(
            str(field)
            for field in [
                job.title,
                getattr(job, "description", "") or "",
                getattr(job, "requirements", "") or "",
            ]
        )
        job_keywords = self._tokenize(job_text)
        if not job_keywords:
            return 0, "No job keywords to match against"

        matches = self.profile_keywords & job_keywords
        coverage = len(matches) / len(job_keywords)
        score = int(round(coverage * 100))

        # Title/role match bonus: if the job title contains a target role, boost.
        preferences = self.profile.get("preferences", {})
        target_roles = [role.lower() for role in preferences.get("target_roles", [])]
        title_lower = str(job.title).lower()
        role_bonus = 0
        if any(role in title_lower for role in target_roles):
            role_bonus = 10

        score = min(100, score + role_bonus)
        reason = f"Keyword overlap: {len(matches)}/{len(job_keywords)} ({score}%)"
        return score, reason


class ScoringAgent:
    """Score a job against a candidate profile using LLM + keyword overlap."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    def score(self, job) -> tuple[int, str]:
        """Return (score, reason) for a job against the loaded profile."""
        profile = self.settings.load_profile()
        keyword_scorer = KeywordScorer(profile)
        keyword_score, keyword_reason = keyword_scorer.score(job)

        # Try LLM scoring; if it fails or no key is configured, fall back to keyword.
        llm_score: int | None = None
        llm_reason: str | None = None
        try:
            profile_text = self._format_profile(profile)
            prompt = FIT_SCORE_PROMPT.format(
                profile_text=profile_text,
                title=job.title,
                company=job.company,
                location=job.location or "",
                description=getattr(job, "description", "") or "",
                requirements=getattr(job, "requirements", "") or "",
            )
            raw = self._llm_chat(prompt)
            llm_score, llm_reason = self._parse_score(raw)
        except Exception as exc:
            logger.warning(f"LLM scoring failed for {job.title}: {exc}")

        if llm_score is None:
            return keyword_score, f"Keyword-only score: {keyword_reason}"

        # Combine scores: 70% LLM + 30% keyword by default.
        llm_weight = self.settings.llm_fit_score_weight
        keyword_weight = 1.0 - llm_weight
        combined = int(round(llm_score * llm_weight + keyword_score * keyword_weight))
        combined = max(0, min(100, combined))

        reason = (
            f"LLM {llm_score} (weight {llm_weight:.0%}) + "
            f"keyword {keyword_score} (weight {keyword_weight:.0%}) = {combined}. "
            f"LLM: {llm_reason} | {keyword_reason}"
        )
        return combined, reason

    def _format_profile(self, profile: dict) -> str:
        lines = []
        personal = profile.get("personal_info", {})
        if personal.get("name"):
            lines.append(f"Name: {personal['name']}")
        skills = profile.get("skills", [])
        if skills:
            lines.append(f"Skills: {', '.join(skills)}")
        highlights = profile.get("experience_highlights", [])
        if highlights:
            lines.append("Experience:")
            for h in highlights:
                lines.append(f"- {h}")
        prefs = profile.get("preferences", {})
        target_roles = prefs.get("target_roles", [])
        if target_roles:
            lines.append(f"Target roles: {', '.join(target_roles)}")
        return "\n".join(lines)

    def _llm_chat(self, prompt: str) -> str:
        """Call the LLM client used by Track A via subprocess to avoid import issues."""
        import subprocess
        import sys
        import tempfile

        python = Path(sys.executable)
        bridge = Path(__file__).resolve().parent.parent.parent / "job_application_system" / "llm_bridge.py"

        # Fall back to a local simple OpenAI call if the bridge does not exist.
        if not bridge.exists():
            return self._simple_llm_chat(prompt)

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"prompt": prompt, "temperature": 0.2, "max_tokens": 256}, f)
            prompt_path = f.name

        try:
            proc = subprocess.run(
                [str(python), str(bridge), "--prompt", prompt_path],
                capture_output=True,
                text=True,
                check=False,
                cwd=bridge.parent,
            )
            if proc.returncode != 0:
                logger.warning(f"LLM bridge failed: {proc.stderr}; falling back")
                return self._simple_llm_chat(prompt)
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if not lines:
                return self._simple_llm_chat(prompt)
            result = json.loads(lines[-1])
            if not result.get("ok"):
                logger.warning(f"LLM bridge returned error: {result.get('error')}; falling back")
                return self._simple_llm_chat(prompt)
            return result.get("content", "")
        except Exception as exc:
            logger.warning(f"LLM bridge exception: {exc}; falling back")
            return self._simple_llm_chat(prompt)
        finally:
            Path(prompt_path).unlink(missing_ok=True)

    def _simple_llm_chat(self, prompt: str) -> str:
        """Minimal fallback using OpenRouter directly."""
        import os

        api_key = os.getenv("OPENROUTER_API_KEY") or ""
        if not api_key:
            logger.warning("No OPENROUTER_API_KEY; returning neutral score")
            return '{"score": 60, "reason": "No LLM key configured; defaulted to pass-through."}'

        try:
            import requests
        except ImportError:
            logger.warning("requests not available; returning neutral score")
            return '{"score": 60, "reason": "requests not available; defaulted."}'

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You score job fit. Return only JSON with keys score and reason."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 256,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def _parse_score(self, raw: str) -> tuple[int, str]:
        """Extract score and reason from LLM output."""
        text = raw.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract a JSON object from the text.
            match = re.search(r"\{[^}]*\"score\"[^}]*\}", text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
            else:
                logger.warning(f"Could not parse LLM score response: {raw}")
                return 0, "Could not parse LLM response"

        score = int(data.get("score", 0))
        reason = data.get("reason", "")
        return max(0, min(100, score)), reason
