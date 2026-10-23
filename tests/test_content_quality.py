from ai_internship.research.content_quality import assess_content


def test_loading_placeholder_is_retryable_and_ineligible():
    assessment = assess_content("Loading ...", minimum_characters=200)

    assert assessment.eligible is False
    assert assessment.reason == "loading_placeholder"
    assert assessment.retryable is True


def test_navigation_only_markdown_is_ineligible_without_retry():
    assessment = assess_content(
        "[Home](/)\n[Products](/products)\n[Contact](/contact)",
        minimum_characters=20,
    )

    assert assessment.eligible is False
    assert assessment.reason == "navigation_only"
    assert assessment.retryable is False


def test_substantive_content_is_eligible_independent_of_host():
    assessment = assess_content("x" * 250, minimum_characters=200)

    assert assessment.eligible is True
    assert assessment.reason is None
    assert assessment.retryable is False
