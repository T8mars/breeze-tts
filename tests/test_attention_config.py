from types import SimpleNamespace

from models.breeze import _resolve_text_encoder_attn_implementation


def _config(*, caller=None, preferred=None, text_encoder=None):
    return SimpleNamespace(
        _attn_implementation=caller,
        _text_encoder_attn_implementation=text_encoder,
        text_encoder_config=SimpleNamespace(
            preferred_attn_implementation=preferred
        ),
    )


def test_explicit_attention_choice_overrides_checkpoint_preference() -> None:
    config = _config(caller="eager", preferred="flash_attention_2")

    assert _resolve_text_encoder_attn_implementation(config) == "eager"


def test_explicit_text_encoder_choice_supports_split_attention_backends() -> None:
    config = _config(
        caller="eager",
        preferred="sdpa",
        text_encoder="flash_attention_2",
    )

    assert _resolve_text_encoder_attn_implementation(config) == "flash_attention_2"


def test_checkpoint_attention_preference_is_used_without_explicit_choice() -> None:
    config = _config(caller=None, preferred="sdpa")

    assert _resolve_text_encoder_attn_implementation(config) == "sdpa"


def test_text_encoder_attention_keeps_legacy_default() -> None:
    config = _config(caller=None, preferred=None)

    assert _resolve_text_encoder_attn_implementation(config) == "flash_attention_2"
