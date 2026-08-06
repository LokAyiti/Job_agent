"""Auto-answer custom application questions using profile + job context.

The agent fills unmapped required fields by:
  1. Looking up a cached answer by normalized question text + options.
  2. Skipping legally protected questions (EEO) unless a "decline" option exists.
  3. Generating a concise answer via an LLM when no cache hit exists.
  4. Using :class:`RobustFieldFiller` to write the answer into the live form.

Generated answers are cached so the same question pattern is answered once and
reused across postings.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from job_agent.config import Settings, get_settings
from job_agent.models import JobApplication
from job_agent.sites.field_filler import RobustFieldFiller


# Legally protected categories that should not be auto-answered unless the user
# explicitly chooses a "decline to self-identify" type option.
PROTECTED_KEYWORDS = [
    "gender",
    "sexual orientation",
    "transgender",
    "race",
    "ethnicity",
    "hispanic",
    "latino",
    "disability",
    "veteran",
    "military",
    "marital status",
    "religion",
    "religious",
    "citizenship",
    "national origin",
    "age",
    "date of birth",
]

DECLINE_OPTION_KEYWORDS = [
    "decline",
    "prefer not",
    "do not wish",
    "not to answer",
    "rather not",
]


FieldAuditSource = Literal[
    "profile",
    "cache",
    "llm",
    "decline_option",
    "skipped",
    "skipped_broken",
    "skipped_numeric_date",
    "skipped_honeypot",
    "skipped_protected",
    "needs_human",
    "needs_human_profile_missing_state",
    "unidentifiable",
    "hidden_ignored",
    "not_applicable",
]

FieldAuditDisposition = Literal[
    "filled",
    "skipped",
    "needs_human",
    "failed",
]


@dataclass
class FieldAudit:
    """Per-field record of how a form question was answered and verified."""

    label: str
    field_type: str
    required: bool
    visible: bool
    selector: str
    answer_source: FieldAuditSource
    value: Any = None
    fill_success: bool = False
    browser_verified: bool = False
    validation_error: str | None = None
    disposition: FieldAuditDisposition = "skipped"
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FillResult:
    """Result of filling the unmapped fields of a form."""

    answers: dict[str, Any] = field(default_factory=dict)
    audit: list[FieldAudit] = field(default_factory=list)
    needs_human: bool = False
    required_protected_no_decline: bool = False
    required_numeric_date: bool = False
    unidentifiable_required: bool = False


def _normalize(text: str) -> str:
    """Stable normalization for question cache keys."""
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _build_cache_key(field: dict[str, Any]) -> str:
    """Build a cache key from the question text and available options."""
    parts = [_normalize(field.get("label", "") or "")]
    options = field.get("options_sample") or field.get("options") or []
    for opt in options:
        text = opt.get("text") or opt.get("label") or opt.get("value") or ""
        parts.append(_normalize(text))
    return " | ".join(p for p in parts if p)


def _extract_options(field: dict[str, Any]) -> list[dict[str, str]]:
    """Return a list of option dicts {value, text} for select/radio fields."""
    options = field.get("options_sample") or field.get("options") or []
    out = []
    for opt in options:
        if isinstance(opt, dict):
            out.append(
                {
                    "value": str(opt.get("value", "")),
                    "text": str(opt.get("text") or opt.get("label") or opt.get("value", "")),
                }
            )
        else:
            out.append({"value": str(opt), "text": str(opt)})
    return out


class QuestionAnsweringAgent:
    """Generate and cache answers for custom application questions."""

    DEFAULT_MAX_TOKENS = 256
    DEFAULT_TEMPERATURE = 0.2

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.cache_path = getattr(
            self.settings, "answer_cache_file", Path("data/answer_cache.json")
        )
        if not self.cache_path.is_absolute():
            self.cache_path = Path(__file__).resolve().parent.parent.parent / self.cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = self._load_cache()

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"Could not load answer cache from {self.cache_path}: {exc}")
            return {}

    def _save_cache(self) -> None:
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _is_honeypot(self, field: dict[str, Any]) -> bool:
        """Return True if the field is likely a spam honeypot and should be skipped."""
        combined = " ".join(
            str(field.get(k, "")) for k in ("label", "name", "id", "class", "placeholder", "aria_label")
        ).lower()
        if "honeypot" in combined or "hp" in combined:
            return True
        # Hidden fields are also suspect, but we let the caller decide visibility.
        return False

    def _is_protected_question(self, field: dict[str, Any]) -> bool:
        """Return True if the question asks for a legally protected category."""
        text = " ".join(
            str(field.get(k, "")) for k in ("label", "aria_label", "placeholder", "name")
        ).lower()
        return any(keyword in text for keyword in PROTECTED_KEYWORDS)

    def _find_decline_option(self, options: list[dict[str, str]]) -> dict[str, str] | None:
        for opt in options:
            opt_text = opt["text"].lower()
            if any(keyword in opt_text for keyword in DECLINE_OPTION_KEYWORDS):
                return opt
        return None

    def _cache_key(self, field: dict[str, Any]) -> str:
        return _build_cache_key(field)

    def _map_yes_no(self, options: list[dict[str, str]], raw_value: str) -> str | None:
        """If the options are a Yes/No pair, map a profile value to Yes/No."""
        texts = [opt["text"].lower() for opt in options]
        if "yes" not in texts or "no" not in texts:
            return None
        val = raw_value.lower()
        if any(pos in val for pos in ("yes", "citizen", "authorized", "permanent", "no sponsorship", "no visa")):
            return "Yes"
        if any(neg in val for neg in ("no", "not authorized", "need sponsorship", "require visa", "cannot")):
            return "No"
        return None


    def _get_cached_answer(self, key: str) -> str | None:
        entry = self._cache.get(key)
        if not isinstance(entry, dict):
            return None
        return entry.get("answer")

    def _record_cache_use(self, key: str) -> None:
        entry = self._cache.get(key)
        if isinstance(entry, dict):
            entry["uses"] = entry.get("uses", 0) + 1
            self._save_cache()

    def _set_cached_answer(self, key: str, answer: str) -> None:
        self._cache[key] = {
            "answer": answer,
            "uses": self._cache.get(key, {}).get("uses", 0) + 1,
        }
        self._save_cache()

    def _build_context(self, job: JobApplication, profile: dict[str, Any]) -> str:
        """Summarize the candidate profile and job for the LLM prompt."""
        lines: list[str] = []
        personal = profile.get("personal_info", {})
        if personal.get("name"):
            lines.append(f"Candidate name: {personal['name']}")
        if personal.get("location"):
            lines.append(f"Current location: {personal['location']}")
        if personal.get("work_authorization"):
            lines.append(f"Work authorization: {personal['work_authorization']}")

        prefs = profile.get("preferences", {})
        target_roles = prefs.get("target_roles", [])
        if target_roles:
            lines.append(f"Target roles: {', '.join(target_roles)}")
        skills = profile.get("skills", [])
        if skills:
            lines.append(f"Key skills: {', '.join(skills)}")
        highlights = profile.get("experience_highlights", [])
        if highlights:
            lines.append("Experience highlights:")
            for h in highlights:
                lines.append(f"- {h}")

        lines.append(f"Job title: {job.title}")
        lines.append(f"Company: {job.company}")
        if job.location:
            lines.append(f"Job location: {job.location}")
        if job.description:
            desc = job.description[:1500].replace("\n", " ")
            lines.append(f"Job description excerpt: {desc}")
        if job.requirements:
            req = job.requirements[:800].replace("\n", " ")
            lines.append(f"Requirements excerpt: {req}")

        return "\n".join(lines)

    def _build_prompt(
        self,
        field: dict[str, Any],
        job: JobApplication,
        profile: dict[str, Any],
    ) -> str:
        context = self._build_context(job, profile)
        field_type = field.get("field_type", "text")
        label = field.get("label", "")
        options = _extract_options(field)

        option_lines = ""
        if options:
            option_lines = "\n".join(f"- {i+1}. {opt['text']}" for i, opt in enumerate(options))

        rules = [
            "Answer honestly and concisely based on the candidate's profile and the job.",
            "Do not invent degrees, companies, or tools the candidate has not used.",
            "Do not answer questions that ask for legally protected personal information.",
        ]
        if options:
            rules.append(
                "For multiple-choice questions, return ONLY the exact text of the best matching option."
            )
        elif field_type in ("radio", "checkbox"):
            rules.append("Return ONLY 'Yes' or 'No'.")
        elif field_type == "textarea":
            rules.append("Answer in 3-5 sentences.")
        else:
            rules.append("Answer in 1-2 sentences unless the question asks for a list or dates.")

        prompt = f"""{context}

Application form question:
Label: {label}
Field type: {field_type}
{option_lines}

Rules:
{chr(10).join(f"- {r}" for r in rules)}

What answer should be provided for this question? Return ONLY the answer text, nothing else."""
        return prompt

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM via the Track A bridge, falling back to a direct OpenRouter call."""
        bridge = Path(__file__).resolve().parent.parent.parent / "job_application_system" / "llm_bridge.py"
        if bridge.exists():
            try:
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False, encoding="utf-8"
                ) as f:
                    json.dump(
                        {
                            "prompt": prompt,
                            "temperature": self.DEFAULT_TEMPERATURE,
                            "max_tokens": self.DEFAULT_MAX_TOKENS,
                        },
                        f,
                    )
                    prompt_path = Path(f.name)

                proc = subprocess.run(
                    [
                        sys.executable,
                        str(bridge),
                        "--prompt",
                        str(prompt_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=bridge.parent,
                )
                prompt_path.unlink(missing_ok=True)
                if proc.returncode == 0:
                    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                    if lines:
                        result = json.loads(lines[-1])
                        if result.get("ok"):
                            return result.get("content", "").strip()
            except Exception as exc:
                logger.warning(f"LLM bridge failed, falling back to direct call: {exc}")

        # Fallback to direct OpenRouter.
        import os

        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.warning("No OPENROUTER_API_KEY; cannot generate custom answer")
            return ""
        try:
            import requests

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You answer job application questions honestly and concisely."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self.DEFAULT_TEMPERATURE,
                    "max_tokens": self.DEFAULT_MAX_TOKENS,
                },
                timeout=60,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning(f"Direct LLM call failed: {exc}")
            return ""

    def generate_answer(
        self,
        field: dict[str, Any],
        job: JobApplication,
        profile: dict[str, Any],
    ) -> str:
        """Generate (or retrieve from cache) an answer for one form field."""
        cache_key = _build_cache_key(field)
        cached = self._get_cached_answer(cache_key)
        if cached:
            self._record_cache_use(cache_key)
            logger.info(f"Answer cache hit for question: {field.get('label', '')[:60]}")
            return cached

        answer = self._call_llm(self._build_prompt(field, job, profile))
        if answer:
            self._set_cached_answer(cache_key, answer)
            logger.info(f"Generated fresh answer for question: {field.get('label', '')[:60]}")
        return answer

    def answer_for_field(
        self,
        field: dict[str, Any],
        job: JobApplication,
        profile: dict[str, Any],
    ) -> str | list[str] | None:
        """Return the answer to use for a single unmapped field, or None to skip.

        Checkbox groups may return a list of option texts to select.
        """
        label = field.get("label", "")
        field_type = field.get("field_type", "text")
        options = _extract_options(field)
        personal = profile.get("personal_info", {})
        work_auth = (personal.get("work_authorization", "") or "").lower()
        is_us_worker = any(
            phrase in work_auth for phrase in ("us citizen", "permanent resident", "green card")
        )

        # Protected questions: choose a decline option if available, otherwise skip.
        if self._is_protected_question(field):
            decline = self._find_decline_option(options)
            if decline:
                logger.info(f"Using decline option for protected question: {label[:60]}")
                return decline["text"]
            logger.warning(f"Skipping protected question with no decline option: {label[:60]}")
            return None

        # Work-authorization questions should be answered directly from the profile.
        label_lower = label.lower()
        if any(k in label_lower for k in ("work authorization", "authorized to work", "legally authorized")):
            if is_us_worker:
                return self._map_yes_no(options, "Yes") if options else "Yes"
            return self._map_yes_no(options, "No") if options else "No"

        if any(k in label_lower for k in ("require visa sponsorship", "need sponsorship", "need visa")):
            if is_us_worker:
                return self._map_yes_no(options, "No") if options else "No"
            return self._map_yes_no(options, "Yes") if options else "Yes"

        # State/location questions are too risky to hallucinate when the profile
        # does not contain an explicit state.
        if "state" in label_lower and "?" in label:
            location = personal.get("location", "")
            if isinstance(location, dict):
                state = (location.get("state", "") or "").strip()
            else:
                # Try to parse "City, State" or "State" from a string.
                parts = str(location).split(",")
                state = parts[-1].strip() if len(parts) > 1 else ""
            if not state:
                logger.warning(f"Skipping state question; no state in profile: {label[:60]}")
                return None
            return state

        # Numeric/short factual questions that are risky to guess should not be answered.
        if field_type in ("number", "date"):
            logger.warning(f"Skipping auto-answer for numeric/date question: {label[:60]}")
            return None

        if not options:
            if field_type == "radio":
                # A radio with no options is a broken form control; skip it.
                logger.warning(f"Skipping {field_type} with no options: {label[:60]}")
                return None
            if field_type == "select" and not field.get("react_select"):
                # A native select with no rendered options is likely broken.
                logger.warning(f"Skipping native select with no options: {label[:60]}")
                return None
            # For React-select dropdowns and free-text fields, generate an answer.
            # The filler will map the answer to the matching option after opening
            # the dropdown menu.
            return self.generate_answer(field, job, profile)

        # Checkbox groups: return a list of matching option texts.
        if field_type == "checkbox" and options:
            raw_answer = self.generate_answer(field, job, profile)
            if not raw_answer:
                return None
            selected = self._match_options(raw_answer, options)
            if not selected:
                logger.warning(
                    f"Could not match LLM answer '{raw_answer}' to any checkbox option; defaulting to first option"
                )
                selected = [options[0]["text"]]
            return selected

        # Multiple choice: generate and then match to the closest option.
        raw_answer = self.generate_answer(field, job, profile)
        if not raw_answer:
            return None

        for opt in options:
            if opt["text"].lower() == raw_answer.lower() or opt["value"].lower() == raw_answer.lower():
                return opt["text"]

        # Fuzzy match: pick the option whose text appears in the answer.
        for opt in options:
            if opt["text"].lower() in raw_answer.lower():
                return opt["text"]

        # Default to the first option if none matched (safest fallback).
        logger.warning(
            f"Could not match LLM answer '{raw_answer}' to options; defaulting to first option"
        )
        return options[0]["text"]

    @staticmethod
    def _match_options(raw_answer: str, options: list[dict[str, str]]) -> list[str]:
        """Return a list of option texts that match the raw answer."""
        selected: list[str] = []
        parts = [p.strip().lower() for p in raw_answer.split(",")]
        for opt in options:
            opt_text = opt["text"].lower()
            opt_value = opt["value"].lower()
            if any(part == opt_text or part == opt_value or opt_text in part or opt_value in part for part in parts):
                selected.append(opt["text"])
        return selected

    @staticmethod
    def _is_generic_selector(selector: str) -> bool:
        """Return True if the selector is just a bare tag or tag list (e.g. 'input')."""
        if not selector:
            return True
        return not any(c in selector for c in "#[.")

    def _classify_answer_source(
        self,
        field: dict[str, Any],
        answer: Any,
        profile: dict[str, Any],
    ) -> FieldAuditSource:
        """Determine the source label for an answer produced by ``answer_for_field``."""
        options = _extract_options(field)
        label = field.get("label", "")
        field_type = field.get("field_type", "text")

        if self._is_protected_question(field):
            if self._find_decline_option(options):
                return "decline_option"
            return "protected_no_decline"  # caller decides skipped vs needs_human

        if any(k in label.lower() for k in ("work authorization", "authorized to work", "sponsorship")):
            personal = profile.get("personal_info", {})
            if personal.get("work_authorization"):
                return "profile"

        if field_type in ("number", "date"):
            return "skipped_numeric_date"

        # React-select dropdowns render their options lazily, so "no options"
        # does not mean the control is broken.
        if not options and field_type in ("select", "radio") and not field.get("react_select"):
            return "skipped_broken"

        if answer is None or answer == "":
            if "state" in label.lower() and "?" in label:
                return "needs_human_profile_missing_state"
            return "needs_human"

        cache_key = _build_cache_key(field)
        if self._get_cached_answer(cache_key) is not None:
            return "cache"
        return "llm"

    async def fill_unmapped_fields(
        self,
        page: Any,
        form_schema: dict[str, Any],
        job: JobApplication,
        profile: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> FillResult:
        """Fill all required unmapped fields in the form schema.

        Returns a :class:`FillResult` containing both the legacy label->answer
        mapping and a per-field audit list.
        """
        from playwright.async_api import Page

        if not isinstance(page, Page):
            # Allow MagicMock in tests.
            pass

        filler = RobustFieldFiller(page)
        result = FillResult()
        unmapped = form_schema.get("unmapped_fields", [])

        for field in unmapped:
            label = field.get("label", "")
            field_type = field.get("field_type", "text")
            required = bool(field.get("required"))
            visible = bool(field.get("visible", True))
            selector = field.get("selector", "")

            audit = FieldAudit(
                label=label,
                field_type=field_type,
                required=required,
                visible=visible,
                selector=selector,
                answer_source="not_applicable",
            )

            # Non-visible, non-required fields are not blockers.
            if not visible and not required:
                audit.answer_source = "hidden_ignored"
                audit.disposition = "skipped"
                audit.fill_success = True
                result.audit.append(audit)
                continue

            # Controls handled by the adapter or browser itself.
            if field_type in ("submit", "file", "hidden"):
                audit.answer_source = "not_applicable"
                audit.disposition = "skipped"
                audit.fill_success = True
                result.audit.append(audit)
                continue

            # Honeypots should never be filled.
            if self._is_honeypot(field):
                audit.answer_source = "skipped_honeypot"
                audit.disposition = "skipped"
                audit.fill_success = True
                result.audit.append(audit)
                continue

            # Protected (EEO) questions: use a decline option when available.
            if self._is_protected_question(field):
                options = _extract_options(field)
                decline = self._find_decline_option(options)
                if decline:
                    answer = decline["text"]
                    audit.value = answer
                    audit.answer_source = "decline_option"
                    audit.disposition = "filled"
                    audit.fill_success = True
                    result.answers[label] = answer
                    result.audit.append(audit)
                    try:
                        await filler.fill(
                            answer,
                            field_id=field.get("id"),
                            name=field.get("name"),
                            label=label,
                            aria_label=field.get("aria_label"),
                            selectors=[selector] if selector else None,
                        )
                    except Exception as exc:
                        logger.warning(f"Could not fill protected decline option for '{label[:60]}': {exc}")
                        audit.fill_success = False
                        audit.disposition = "failed"
                        audit.reason = str(exc)
                    continue

                if required:
                    audit.answer_source = "needs_human"
                    audit.disposition = "needs_human"
                    audit.reason = "protected_required_question_no_decline_option"
                    result.required_protected_no_decline = True
                    result.needs_human = True
                    result.audit.append(audit)
                    continue
                else:
                    audit.answer_source = "skipped_protected"
                    audit.disposition = "skipped"
                    audit.fill_success = True
                    result.audit.append(audit)
                    continue

            # Numeric/date questions are too risky to auto-answer when required.
            if field_type in ("number", "date") and required:
                audit.answer_source = "needs_human"
                audit.disposition = "needs_human"
                audit.reason = "required_numeric_date_question_not_answered"
                result.required_numeric_date = True
                result.needs_human = True
                result.audit.append(audit)
                continue

            # A broken radio with no options cannot be filled automatically.
            # Selects (including React-select dropdowns) often have options that are
            # only rendered after opening the menu, so we still attempt to answer them.
            options = _extract_options(field)
            if not options and field_type == "radio":
                audit.answer_source = "skipped_broken"
                audit.disposition = "skipped"
                audit.fill_success = True
                result.audit.append(audit)
                continue

            # A required field with no identifying metadata cannot be answered safely.
            if (
                required
                and not label
                and not field.get("name")
                and not field.get("id")
                and self._is_generic_selector(selector)
            ):
                if not visible:
                    # Hidden required inputs that we cannot identify (e.g. anti-CSRF
                    # tokens, React-select hidden required placeholders) must not
                    # falsely block a dry-run.
                    audit.answer_source = "hidden_ignored"
                    audit.disposition = "skipped"
                    audit.fill_success = True
                    audit.reason = "hidden_required_field_ignored"
                    result.audit.append(audit)
                    continue
                audit.answer_source = "unidentifiable"
                audit.disposition = "needs_human"
                audit.reason = "unidentifiable_required_field"
                result.unidentifiable_required = True
                result.needs_human = True
                result.audit.append(audit)
                continue

            # Default: generate/retrieve an answer.
            answer = self.answer_for_field(field, job, profile)
            source = self._classify_answer_source(field, answer, profile)

            if answer is None or answer == "":
                if required:
                    if source == "skipped_numeric_date":
                        audit.reason = "required_numeric_date_question_not_answered"
                        result.required_numeric_date = True
                    elif source == "protected_no_decline":
                        audit.reason = "protected_required_question_no_decline_option"
                        result.required_protected_no_decline = True
                    elif source == "needs_human_profile_missing_state":
                        audit.reason = "profile_missing_state"
                    else:
                        audit.reason = "llm_unavailable_and_cache_missing"
                    audit.answer_source = "needs_human"
                    audit.disposition = "needs_human"
                    result.needs_human = True
                    result.audit.append(audit)
                else:
                    if source == "skipped_numeric_date":
                        audit.answer_source = "skipped_numeric_date"
                    elif source == "skipped_broken":
                        audit.answer_source = "skipped_broken"
                    elif source == "protected_no_decline":
                        audit.answer_source = "skipped_protected"
                    elif source == "needs_human_profile_missing_state":
                        audit.answer_source = "skipped"
                        audit.reason = "profile_missing_state"
                    elif source == "needs_human":
                        audit.answer_source = "skipped"
                    else:
                        audit.answer_source = "skipped"
                    audit.disposition = "skipped"
                    audit.fill_success = True
                    result.audit.append(audit)
                continue

            audit.value = answer
            audit.answer_source = source
            audit.disposition = "filled"
            result.answers[label] = answer
            log_preview = str(answer)[:60]
            logger.info(f"Answering custom question: {label[:60]} -> {log_preview}")

            try:
                if field_type in ("select", "radio"):
                    selected = await filler.fill(
                        answer,
                        field_id=field.get("id"),
                        name=field.get("name"),
                        label=label,
                        aria_label=field.get("aria_label"),
                        selectors=[selector] if selector else None,
                    )
                    audit.fill_success = bool(selected)
                    if not selected:
                        for opt in options:
                            if opt["text"].lower() == answer.lower():
                                await page.select_option(
                                    selector or "select",
                                    opt["value"],
                                )
                                audit.fill_success = True
                                break
                elif field_type == "checkbox":
                    checked_any = False
                    if isinstance(answer, list):
                        base_selector = selector or 'input[type="checkbox"]'
                        for opt in options:
                            if opt["text"] in answer:
                                try:
                                    opt_value = opt["value"].replace('"', '\\"')
                                    opt_selector = base_selector + '[value="' + opt_value + '"]'
                                    loc = page.locator(opt_selector).first
                                    if await loc.count() == 0:
                                        loc = page.locator(
                                            'input[type="checkbox"]'
                                        ).filter(has_text=opt["text"]).first
                                    if await loc.count() > 0:
                                        await loc.check(timeout=2000)
                                        checked_any = True
                                except Exception as exc:
                                    logger.debug(f"Could not check option '{opt['text'][:60]}': {exc}")
                    elif answer.lower() in ("yes", "true", "checked"):
                        loc = page.locator(selector or 'input[type="checkbox"]').first
                        if await loc.count() > 0:
                            try:
                                await loc.check(timeout=2000)
                                checked_any = True
                            except Exception:
                                clicked = await page.evaluate(
                                    """(selector) => {
                                        const input = document.querySelector(selector);
                                        if (!input) return false;
                                        let clickable = null;
                                        if (input.id) {
                                            clickable = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
                                        }
                                        if (!clickable) {
                                            let node = input.parentElement;
                                            for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
                                                if (node.tagName.toLowerCase() === 'label' || node.className.includes('checkbox') || node.className.includes('row') || node.getAttribute('role') === 'checkbox') {
                                                    clickable = node;
                                                    break;
                                                }
                                            }
                                        }
                                        if (clickable) {
                                            clickable.scrollIntoView({ block: 'center' });
                                            clickable.click();
                                            return true;
                                        }
                                        return false;
                                    }""",
                                    selector or 'input[type="checkbox"]',
                                )
                                if not clicked:
                                    logger.warning(f"Could not click hidden checkbox for '{label[:60]}'")
                                else:
                                    checked_any = True
                                    logger.debug(f"Clicked hidden checkbox via label/container for '{label[:60]}'")
                    audit.fill_success = checked_any
                else:
                    filled = await filler.fill(
                        answer,
                        field_id=field.get("id"),
                        name=field.get("name"),
                        label=label,
                        aria_label=field.get("aria_label"),
                        placeholder=field.get("placeholder"),
                        selectors=[selector] if selector else None,
                    )
                    audit.fill_success = bool(filled)
            except Exception as exc:
                logger.warning(f"Could not fill custom question '{label[:60]}': {exc}")
                audit.fill_success = False
                audit.disposition = "failed"
                audit.reason = str(exc)
                if required:
                    audit.disposition = "needs_human"
                    audit.reason = f"fill_failed_required_field: {exc}"
                    result.needs_human = True

            result.audit.append(audit)

        return result
