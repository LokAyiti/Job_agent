"""Browser-side form verification for dry-run audits.

``FormVerifier`` inspects the live DOM after the adapter has filled the form and
produces a per-field audit that the harness uses to decide whether the run can
be considered ``queued``.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from job_agent.agents.question_answering_agent import FieldAudit


class FormVerifier:
    """Verify filled form values by querying the browser DOM."""

    VALIDATION_ERROR_SELECTORS = [
        ".error",
        ".invalid",
        ".field-validation-error",
        ".validation-message",
        '[aria-invalid="true"]',
        '[role="alert"]',
        ".has-error",
        ".form-error",
        ".text-danger",
    ]

    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Next')",
    ]

    @staticmethod
    def _is_generic_selector(selector: str) -> bool:
        """Return True if the selector is a bare tag or tag list (e.g. 'input')."""
        if not selector:
            return True
        return not any(c in selector for c in "#[.")

    @staticmethod
    def _build_effective_selector(field: dict[str, Any]) -> str | None:
        """Return a concrete selector for the field, or None if it is unidentifiable."""
        selector = field.get("selector", "")
        if selector and not FormVerifier._is_generic_selector(selector):
            return selector

        field_id = field.get("id")
        if field_id:
            return f"#{field_id}"

        name = field.get("name")
        tag = (field.get("tag") or field.get("field_type") or "input").lower()
        if name:
            return f'{tag}[name="{name}"]'

        aria_label = field.get("aria_label") or field.get("aria-label")
        if aria_label:
            return f'[aria-label="{aria_label}"]'

        placeholder = field.get("placeholder")
        if placeholder:
            return f'[placeholder="{placeholder}"]'

        return selector if selector else None

    def _index_agent_audit(self, agent_audit: list[FieldAudit]) -> dict[tuple[str, str, str], FieldAudit]:
        """Build a lookup from the agent's per-field audit records."""
        index: dict[tuple[str, str, str], FieldAudit] = {}
        for entry in agent_audit:
            key = (entry.label, entry.field_type, entry.selector)
            index[key] = entry
        return index

    async def verify(
        self,
        page: Any,
        form_schema: dict[str, Any],
        agent_audit: list[FieldAudit],
    ) -> list[dict[str, Any]]:
        """Return a per-field audit covering every field in ``form_schema``."""
        audit_index = self._index_agent_audit(agent_audit)
        all_fields: list[dict[str, Any]] = []

        for canonical, field in form_schema.get("fields", {}).items():
            entry = dict(field)
            entry["canonical"] = canonical
            all_fields.append(entry)

        for field in form_schema.get("unmapped_fields", []):
            entry = dict(field)
            entry["canonical"] = None
            all_fields.append(entry)

        results: list[dict[str, Any]] = []
        for field in all_fields:
            audit = await self._verify_one_field(page, field, audit_index)
            results.append(audit.to_dict())
        return results

    async def _verify_one_field(
        self,
        page: Any,
        field: dict[str, Any],
        audit_index: dict[tuple[str, str, str], FieldAudit],
    ) -> FieldAudit:
        """Inspect a single field in the DOM and produce a :class:`FieldAudit`."""
        label = field.get("label", "")
        field_type = field.get("field_type", "text")
        required = bool(field.get("required"))
        visible = bool(field.get("visible", True))
        selector = field.get("selector", "")
        canonical = field.get("canonical")
        value_source = field.get("value_source", "")
        effective_selector = self._build_effective_selector(field)

        # Source mapping for known fields.
        if canonical == "submit":
            answer_source: Any = "_action_"
        elif canonical:
            answer_source = "profile"
        else:
            answer_source = "not_applicable"

        audit = FieldAudit(
            label=label,
            field_type=field_type,
            required=required,
            visible=visible,
            selector=effective_selector or selector,
            answer_source=answer_source,
        )

        # Copy agent disposition for unmapped fields.
        if canonical is None:
            agent = audit_index.get((label, field_type, selector))
            if agent is not None:
                audit.answer_source = agent.answer_source
                audit.disposition = agent.disposition
                audit.value = agent.value
                audit.fill_success = agent.fill_success
                audit.reason = agent.reason

        # Hidden required inputs with no identifying label are almost always
        # framework placeholders (React-select hidden required input, anti-CSRF
        # tokens, etc.). They should not be treated as blockers.
        if not visible and not label and not field.get("id") and not field.get("name"):
            if self._is_generic_selector(selector):
                audit.answer_source = "hidden_ignored"
                audit.disposition = "skipped"
                audit.fill_success = True
                audit.browser_verified = True
                audit.reason = "hidden_generic_required_input_ignored"
                return audit

        if effective_selector is None:
            audit.browser_verified = False
            audit.reason = "unidentifiable_selector"
            if required and audit.disposition != "needs_human":
                audit.disposition = "needs_human"
            return audit

        dom_info = await self._query_dom(page, effective_selector)
        if dom_info is None:
            audit.browser_verified = False
            if required and audit.disposition != "needs_human":
                audit.disposition = "needs_human"
                audit.reason = "required_field_empty_in_browser"
            return audit

        audit.browser_verified = True

        dom_type = dom_info.get("type", "")
        dom_value = dom_info.get("value", "")
        dom_checked = dom_info.get("checked", False)
        dom_visible = dom_info.get("visible", False)
        is_hidden = field_type == "hidden" or dom_type == "hidden" or not dom_visible

        # Hidden fields are verified by presence, unless required and empty.
        if is_hidden:
            audit.browser_verified = True
            if required and not dom_value and not dom_checked:
                audit.browser_verified = False
                audit.disposition = "needs_human"
                audit.reason = "required_field_empty_in_browser"
            return audit

        # Visible fields: verify expected state.
        empty = self._is_empty_control(dom_info, field_type)
        if empty:
            if required and audit.disposition != "needs_human":
                audit.browser_verified = False
                audit.disposition = "needs_human"
                audit.reason = "required_field_empty_in_browser"
        else:
            audit.browser_verified = True
            if audit.disposition in ("skipped", "not_applicable"):
                audit.disposition = "filled"
            if not audit.value:
                audit.value = str(dom_value)[:250]
            if canonical is None and agent is None and audit.answer_source == "not_applicable":
                audit.answer_source = "browser_detected"

        return audit

    async def _query_dom(self, page: Any, selector: str) -> dict[str, Any] | None:
        """Return tag/type/visibility/value/checked state for the first matching element."""
        script = """
        (selector) => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const tag = el.tagName.toLowerCase();
            let type = el.type || tag;
            if (tag === 'button') type = 'submit';
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const visible = !!(
                el.offsetParent !== null &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 0 &&
                rect.height > 0
            );
            const role = el.getAttribute('role') || '';
            const isReactSelect = !!(el.closest('.select__container') || el.closest('.select'));
            let value = '';
            if (isReactSelect || role === 'combobox') {
                // React-select stores the chosen label in a .select__single-value
                // div and/or in the hidden required input next to the combobox.
                const container = el.closest('.select__container') || el.closest('.select');
                if (container) {
                    const single = container.querySelector('.select__single-value');
                    if (single) value = single.textContent.trim();
                    if (!value) {
                        const hidden = container.querySelector('input[aria-hidden="true"], input.requiredInput, input[type="hidden"]');
                        if (hidden) value = hidden.value || '';
                    }
                }
                if (!value) value = el.value || '';
            } else if (tag === 'input' || tag === 'textarea') {
                value = el.value || '';
            } else if (tag === 'select') {
                value = Array.from(el.selectedOptions).map(o => o.text.trim()).join(', ');
            }
            return {
                tag,
                type,
                role,
                react_select: isReactSelect,
                visible,
                value,
                checked: el.checked === true,
                disabled: el.disabled === true,
                readonly: el.readOnly === true,
            };
        }
        """
        try:
            return await page.evaluate(script, selector)
        except Exception as exc:
            logger.debug(f"DOM query failed for selector {selector}: {exc}")
            return None

    @staticmethod
    def _is_empty_control(dom_info: dict[str, Any], field_type: str) -> bool:
        """Return True if the control is currently empty/unchecked."""
        tag = dom_info.get("tag", "")
        control_type = dom_info.get("type", "")
        role = dom_info.get("role", "")
        if tag in ("input", "textarea") and control_type in ("checkbox", "radio"):
            return not dom_info.get("checked", False)
        if tag == "select" or role == "combobox" or dom_info.get("react_select"):
            return not dom_info.get("value", "")
        return not dom_info.get("value", "")

    async def detect_validation_errors(self, page: Any) -> list[str]:
        """Return visible validation error texts found on the page."""
        messages: list[str] = []
        for selector in self.VALIDATION_ERROR_SELECTORS:
            try:
                texts = await page.locator(selector).all_inner_texts()
                for text in texts:
                    text = text.strip()
                    if text and text not in messages:
                        messages.append(text)
            except Exception as exc:
                logger.debug(f"Validation error selector {selector} failed: {exc}")
        return messages

    async def check_submit_control(
        self,
        page: Any,
        form_schema: dict[str, Any],
    ) -> str | None:
        """Check that the submit control is present and enabled.

        Returns a human-readable failure reason or ``None`` on success.
        """
        submit_field = form_schema.get("fields", {}).get("submit")
        selector = submit_field.get("selector") if submit_field else None
        if not selector:
            selector = ", ".join(self.SUBMIT_SELECTORS)

        try:
            locator = page.locator(selector).first
            count = await locator.count()
            if count == 0:
                return "submit_control_not_found"
            if await locator.is_disabled():
                return "submit_control_disabled"
            return None
        except Exception as exc:
            logger.warning(f"Submit control check failed: {exc}")
            return f"submit_control_error: {exc}"
