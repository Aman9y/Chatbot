import pytest

from app.services.stop_keywords import DEFAULT_STOP_KEYWORDS, is_opt_out, load_keywords

KW = DEFAULT_STOP_KEYWORDS


@pytest.mark.parametrize(
    "text",
    [
        "STOP",
        "stop",
        "  Stop  ",
        "stop.",
        "Stop!",
        "unsubscribe",
        "please stop sending me messages",
        "stop promotions",
        "band karo",
        "band kar do bhai",
        "mujhe mat bhejo",
        "बंद करो",
        "मत भेजो",
        "remove me from this list",
        "do not message me again",
    ],
)
def test_detects_opt_out(text):
    assert is_opt_out(text, KW) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "what is the fee for Georgia",
        "I want to book a call tomorrow",
        "can you tell me about MBBS in Russia",
        "my son appeared for NEET this year",
        "thanks for the info",
        "yes please call me",
    ],
)
def test_ignores_normal_messages(text):
    assert is_opt_out(text, KW) is False


def test_custom_keywords_with_sentinel():
    kws = load_keywords("<defaults>,rukja, hatao ")
    assert "rukja" in kws
    assert "hatao" in kws
    assert "stop" in kws
    assert is_opt_out("rukja", kws) is True


def test_empty_config_uses_defaults():
    assert load_keywords("") == DEFAULT_STOP_KEYWORDS
    assert load_keywords(None) == DEFAULT_STOP_KEYWORDS
