"""Tests for form field-to-profile mapping utilities."""

import pytest

from job_agent.sites.form_utils import map_field_to_profile


def test_email_input_with_login_name_fragment_maps_to_email():
    """An email control should not be misclassified as full_name by its name."""
    field = {
        "tag": "input",
        "type": "email",
        "name": "css_loginName",
        "id": "emailInput",
        "label": "Email",
    }
    canonical, source = map_field_to_profile(field)
    assert canonical == "email"
    assert source == "personal_info.email"


def test_submit_button_with_email_id_maps_to_submit():
    """A submit button should not be misclassified as email by its id."""
    field = {
        "tag": "button",
        "type": "submit",
        "id": "enterEmailSubmitButton",
        "label": "Enter Email",
    }
    canonical, source = map_field_to_profile(field)
    assert canonical == "submit"
    assert source == "_action_"


def test_tel_type_maps_to_phone():
    field = {"tag": "input", "type": "tel", "name": "phoneNumber", "label": "Mobile"}
    canonical, source = map_field_to_profile(field)
    assert canonical == "phone"
    assert source == "personal_info.phone"


def test_number_input_returns_no_mapping():
    field = {"tag": "input", "type": "number", "label": "Years of experience", "name": "years"}
    assert map_field_to_profile(field) == ("", "")


def test_file_input_labelled_cover_letter_maps_to_cover_letter():
    field = {"tag": "input", "type": "file", "name": "cover_letter", "label": "Cover Letter"}
    canonical, source = map_field_to_profile(field)
    assert canonical == "cover_letter"
    assert source == "assets.base_cover_letter"


def test_file_input_without_cover_label_maps_to_resume():
    field = {"tag": "input", "type": "file", "name": "resumeUpload", "label": "Resume"}
    canonical, source = map_field_to_profile(field)
    assert canonical == "resume"
    assert source == "assets.base_resume_pdf"
