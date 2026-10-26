"""The HD voice tier: HD voices take no prosody wrapper, the standard tier keeps it, and a refused
HD voice falls back to the standard voice for the same language and role."""

from earshot.agents.catalog import STANDARD_FOR, VOICES_HD, VOICES_STANDARD, is_hd
from earshot.contract import Language, VoiceRole
from earshot.voice.tts import fallback_ssml, voice_for


def test_hd_is_the_default_and_standard_is_a_switch(monkeypatch):
    monkeypatch.delenv("EARSHOT_VOICE_TIER", raising=False)
    assert voice_for(Language.EN, VoiceRole.FACTS) == "en-GB-Ryan:DragonHDLatestNeural"
    monkeypatch.setenv("EARSHOT_VOICE_TIER", "standard")
    assert voice_for(Language.EN, VoiceRole.FACTS) == "en-GB-RyanNeural"
    monkeypatch.setenv("EARSHOT_VOICE_TIER", "nonsense")
    assert voice_for(Language.ES, VoiceRole.EXPLANATION) == VOICES_HD[Language.ES][1]


def test_every_language_keeps_two_distinct_voices_in_both_tiers():
    for table in (VOICES_HD, VOICES_STANDARD):
        for lang in Language:
            facts, explanation = table[lang]
            assert facts != explanation, lang


def test_fallback_swaps_only_the_hd_voice_name():
    hd = VOICES_HD[Language.ES][0]
    assert is_hd(hd)
    assert not is_hd(VOICES_STANDARD[Language.ES][0])
    ssml = f'<speak xml:lang="es-ES"><voice name="{hd}">Gol.</voice></speak>'
    assert (
        fallback_ssml(ssml)
        == f'<speak xml:lang="es-ES"><voice name="{STANDARD_FOR[hd]}">Gol.</voice></speak>'
    )
    std = f'<speak xml:lang="es-ES"><voice name="{VOICES_STANDARD[Language.ES][0]}">Gol.</voice></speak>'
    assert fallback_ssml(std) is None


def test_hd_ssml_has_no_prosody_and_standard_ssml_does(monkeypatch):
    from earshot.contract import Line, LineSource, ListeningMode
    from earshot.voice.tts import ssml_for

    line = Line(
        line_id="l1", match_id="m", moment_id=None, source=LineSource.NOTICE, language=Language.EN,
        listening_mode=ListeningMode.CASUAL, template_id="kickoff.1", slots={"club": "Sparkwheel"},
        text="x", priority=48, wall_ms=0, deadline_wall_ms=10_000,
    )  # fmt: skip
    monkeypatch.delenv("EARSHOT_VOICE_TIER", raising=False)
    assert "<prosody" not in ssml_for(line, excited=True)
    monkeypatch.setenv("EARSHOT_VOICE_TIER", "standard")
    assert "<prosody" in ssml_for(line, excited=True)
