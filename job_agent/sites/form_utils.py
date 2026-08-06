"""Shared helpers for form field discovery and profile-value mapping.

These utilities are used by built-in adapters (Workday, Greenhouse, etc.) and by
the adapter generator so every adapter reports the same structured JSON schema.
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from playwright.async_api import Page


def get_profile_values(profile: dict[str, Any]) -> dict[str, str]:
    """Return canonical application values from the unified profile.json.

    Falls back to legacy flat keys (``my_name``, ``my_email``, etc.) when the
    nested ``personal_info`` block is absent so existing configurations keep
    working.
    """
    personal = profile.get("personal_info", {})
    if not isinstance(personal, dict):
        personal = {}

    full_name = (
        personal.get("name")
        or profile.get("my_name", "")
        or ""
    ).strip()
    name_parts = full_name.split() if full_name else []
    first_name = name_parts[0] if name_parts else ""
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    return {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": (
            personal.get("email")
            or profile.get("my_email", "")
            or ""
        ).strip(),
        "phone": (
            personal.get("phone")
            or profile.get("my_phone", "")
            or ""
        ).strip(),
        "linkedin": (
            personal.get("linkedin")
            or profile.get("my_linkedin", "")
            or ""
        ).strip(),
        "location": (
            personal.get("location", "")
            or ""
        ).strip(),
        "work_authorization": (
            personal.get("work_authorization", "")
            or ""
        ).strip(),
    }


# Map fragments found in label/name/id/placeholder/aria-label to canonical fields.
# Order matters: more specific hints should appear before generic ones (e.g.
# "work authorization" before "state" so "United States" does not mis-map).
FIELD_HINTS: list[tuple[list[str], str, str]] = [
    # (fragments, canonical_field, value_source)
    (["first name", "firstname", "first_name", "given-name", "givenname"], "first_name", "personal_info.name.first"),
    (["last name", "lastname", "last_name", "family-name", "familyname", "surname"], "last_name", "personal_info.name.last"),
    (["full name", "fullname", "name"], "full_name", "personal_info.name"),
    (["email", "e-mail"], "email", "personal_info.email"),
    (["phone", "mobile", "cell", "telephone"], "phone", "personal_info.phone"),
    (["linkedin", "linked in"], "linkedin", "personal_info.linkedin"),
    (["resume", "cv", "upload resume"], "resume", "assets.base_resume_pdf"),
    (["cover letter"], "cover_letter", "assets.base_cover_letter"),
    (["work authorization", "authorized to work", "eligibility", "us citizen"], "work_authorization", "personal_info.work_authorization"),
    (["address"], "address", "personal_info.location.address"),
    (["city"], "city", "personal_info.location.city"),
    (["state", "province", "region"], "state", "personal_info.location.state"),
    (["zip", "postal"], "zip", "personal_info.location.zip"),
    (["country"], "country", "personal_info.location.country"),
    (["website", "portfolio", "github"], "website", "personal_info.website"),
    (["salary", "compensation", "pay"], "salary_expectation", "preferences.salary_floor_usd"),
    (["hear about", "how did you hear", "source", "referral"], "referral_source", "preferences.referral_source"),
    (["submit", "apply"], "submit", "_action_"),
]


# Labels / text that indicate a field is mandatory even when the ``required``
# attribute is not set on the element.
REQUIRED_HINTS = ["*", "required", "mandatory"]


def _is_required(field: dict[str, Any]) -> bool:
    """Return True if the field appears mandatory based on DOM or label hints."""
    if field.get("required"):
        return True
    combined = " ".join(
        str(field.get(k, "")) for k in ("label", "placeholder", "aria_label", "title")
    ).lower()
    return any(hint in combined for hint in REQUIRED_HINTS)


def _normalize_text(text: Any) -> str:
    """Return a lowercased, stripped version of the input text."""
    return str(text or "").lower().strip()


def _field_text(field: dict[str, Any], *keys: str) -> str:
    """Concatenate text values from the requested keys for matching."""
    parts = [str(field.get(k, "") or "") for k in keys]
    return " ".join(p for p in parts if p).lower()


def _first_hint_match(text: str) -> tuple[str, str]:
    """Return the first (canonical, source) pair that matches ``text``."""
    if not text:
        return "", ""
    lowered = text.lower()
    for fragments, canonical, source in FIELD_HINTS:
        if any(fragment in lowered for fragment in fragments):
            return canonical, source
    return "", ""


def map_field_to_profile(field: dict[str, Any]) -> tuple[str, str]:
    """Infer canonical field name and profile value source from a field record.

    Mapping is type-aware so name/id fragments do not override the actual control
    type.  For example, an ``<input type="email" name="css_loginName">`` is
    mapped to ``email`` even though the name contains ``loginName``.
    """
    tag = _normalize_text(field.get("tag"))
    ftype = _normalize_text(field.get("type") or field.get("field_type", ""))

    # Buttons / submit actions are always classified by their tag/type first.
    if tag == "button" or ftype == "submit":
        return "submit", "_action_"

    # Email and telephone controls are authoritative about their data type.
    if ftype == "email":
        return "email", "personal_info.email"
    if ftype == "tel":
        return "phone", "personal_info.phone"

    # File uploads are mapped to resume/cover-letter based on label/name.
    if ftype == "file":
        label_name_id = _field_text(field, "label", "name", "id")
        if "cover" in label_name_id or "cover_letter" in label_name_id:
            return "cover_letter", "assets.base_cover_letter"
        return "resume", "assets.base_resume_pdf"

    # Numeric inputs are treated as custom questions; never auto-map to a profile field.
    if ftype == "number":
        return "", ""

    # Labels that are phrased as questions are custom application questions, not
    # static profile fields, even if they contain a matching keyword. Strip
    # required markers first so "What state are you in? *" is still recognised.
    label = _field_text(field, "label")
    cleaned_label = label.rstrip("*").strip()
    if "?" in cleaned_label:
        return "", ""

    # For other text-like inputs, let the visible label/placeholder/aria-label/title win.
    label_match = _first_hint_match(_field_text(field, "label", "placeholder", "aria_label", "aria-label", "title"))
    if label_match[0]:
        return label_match

    # Fallback: name/id/placeholder/aria-label/title, but never let name/id fragments
    # override the type-based mappings we already handled (full_name/name hints are
    # intentionally skipped here because labels already handled them).
    skip_canonicals = {"full_name", "first_name", "last_name", "submit", "email", "phone"}
    combined = _field_text(field, "name", "id", "placeholder", "aria_label", "aria-label", "title")
    lowered = combined
    for fragments, canonical, source in FIELD_HINTS:
        if canonical in skip_canonicals:
            continue
        if any(fragment in lowered for fragment in fragments):
            return canonical, source
    return "", ""


def _build_selector(field: dict[str, Any]) -> str:
    """Build a Playwright-compatible CSS selector from a field record."""
    tag = (field.get("tag") or "").lower()
    ftype = (field.get("type") or "").lower()
    field_id = field.get("id", "")
    name = field.get("name", "")

    if field_id:
        return f"#{field_id}"
    if name:
        base = tag if tag else ("input" if ftype else "*")
        return f'{base}[name="{name}"]'
    if tag:
        return tag
    return "input, select, textarea"


async def extract_fields(page: Page) -> list[dict[str, Any]]:
    """Return all input/select/textarea elements on the page with metadata.

    The extraction runs in the browser context so it captures live DOM state,
    including fields rendered by JavaScript.
    """
    try:
        return await page.evaluate(
            """
            () => {
                const fields = [];
                const inputs = document.querySelectorAll('input, select, textarea, button[type="submit"]');
                inputs.forEach((el, index) => {
                    const tag = el.tagName.toLowerCase();
                    let type = el.type || tag;
                    if (tag === 'button') type = 'submit';

                    let label = '';
                    if (el.id) {
                        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (l) label = l.textContent.trim();
                    }
                    if (!label) {
                        let p = el.parentElement;
                        for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
                            if (p.tagName.toLowerCase() === 'label') {
                                label = p.textContent.trim();
                                break;
                            }
                        }
                    }

                    // Skip the hidden required placeholder input that React-select
                    // injects next to each dropdown; it will be populated by the
                    // component when a visible option is chosen.
                    if (
                        tag === 'input' &&
                        (el.getAttribute('aria-hidden') === 'true' || el.className.includes('requiredInput')) &&
                        (el.closest('.select__container') || el.closest('.select'))
                    ) {
                        return;
                    }

                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const isRendered = (
                        style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        parseFloat(style.opacity) > 0.05 &&
                        rect.width > 0 &&
                        rect.height > 0
                    );

                    // React-select renders a visible <input role="combobox">
                    // inside a .select__container. Treat it as a select so the
                    // filler knows to pick an option instead of typing free text.
                    const isReactSelect = (
                        tag === 'input' &&
                        (el.getAttribute('role') === 'combobox' || el.closest('.select__container') || el.closest('.select'))
                    );
                    if (tag === 'select' || isReactSelect) {
                        type = 'select';
                    }

                    const entry = {
                        index,
                        tag,
                        type,
                        name: el.name || '',
                        id: el.id || '',
                        class: el.className || '',
                        label,
                        placeholder: el.placeholder || '',
                        aria_label: el.getAttribute('aria-label') || '',
                        aria_labelled_by: el.getAttribute('aria-labelledby') || '',
                        title: el.title || '',
                        required: el.required || false,
                        visible: isRendered || (tag === 'input' && el.type === 'hidden'),
                        react_select: isReactSelect,
                    };

                    if (tag === 'select') {
                        entry.options = Array.from(el.options).map(o => ({
                            value: o.value,
                            text: o.text.trim(),
                            selected: o.selected,
                        }));
                    }
                    fields.push(entry);
                });
                return fields;
            }
            """
        )
    except Exception as exc:
        logger.warning(f"DOM field extraction failed: {exc}")
        return []


def build_form_schema(
    fields: list[dict[str, Any]],
    platform: str,
    url: str,
    known_selectors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the structured field schema used for dry-run verification.

    Output shape (field name -> metadata including type and value source):

    .. code-block:: json

        {
          "platform": "greenhouse",
          "url": "...",
          "fields": {
            "first_name": {
              "field_type": "text",
              "label": "First Name",
              "selector": "#first_name",
              "required": true,
              "value_source": "personal_info.name.first"
            }
          },
          "unmapped_fields": [...],
          "summary": {...}
        }
    """
    mapped: dict[str, Any] = {}
    unmapped: list[dict[str, Any]] = []
    known_selectors = known_selectors or {}

    seen_canonical: set[str] = set()

    for field in fields:
        label = field.get("label", "")
        name = field.get("name", "")
        field_id = field.get("id", "")
        placeholder = field.get("placeholder", "")
        aria_label = field.get("aria_label", "")
        title = field.get("title", "")
        ftype = field.get("type", "")

        canonical, source = map_field_to_profile(field)

        schema_entry = {
            "field_type": ftype or field.get("tag", "text"),
            "label": label,
            "name": name,
            "id": field_id,
            "selector": _build_selector(field),
            "required": _is_required(field),
            "visible": bool(field.get("visible", True)),
            "value_source": source,
        }

        # Apply platform-specific known selector only to the primary mapped field.
        if canonical and canonical not in seen_canonical and canonical in known_selectors:
            schema_entry["selector"] = known_selectors[canonical]

        if field.get("react_select"):
            schema_entry["react_select"] = True

        if field.get("options"):
            schema_entry["options_sample"] = field["options"][:10]

        if canonical and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            mapped[canonical] = schema_entry
        elif canonical:
            # Duplicate canonical mapping; keep the first but note alternatives.
            mapped[canonical].setdefault("alternatives", []).append(schema_entry)
        else:
            unmapped.append(schema_entry)

    summary = {
        "total_fields": len(fields),
        "mapped_fields": len(mapped),
        "unmapped_fields": len(unmapped),
        "required_fields": sum(1 for f in mapped.values() if f.get("required")),
        "has_submit": "submit" in mapped,
    }

    return {
        "platform": platform,
        "url": url,
        "fields": mapped,
        "unmapped_fields": unmapped,
        "summary": summary,
    }


def canonical_value(profile_values: dict[str, str], canonical: str) -> str:
    """Return the value for a canonical field from :func:`get_profile_values`."""
    return profile_values.get(canonical, "")
