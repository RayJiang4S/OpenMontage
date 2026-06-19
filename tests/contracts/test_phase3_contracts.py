"""Phase 3 contract tests — instruction-driven architecture.

Tests the new tools (TTS, music gen), pipeline manifests, style playbooks,
stage director skills, meta skills, and the animated-explainer pipeline.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.pipeline_loader import (
    load_pipeline,
    get_stage_order,
    get_required_tools,
    get_stage_skill,
    get_stage_review_focus,
    list_pipelines,
)
from lib.checkpoint import STAGES
from schemas.artifacts import list_schemas
from styles.playbook_loader import load_playbook, list_playbooks, validate_playbook
from tools.base_tool import ToolTier
from tools.audio.azure_tts import AzureTTS
from tools.audio.music_gen import MusicGen
from tools.tool_registry import ToolRegistry
from tools.audio.doubao_tts import DoubaoTTS
from tools.audio.elevenlabs_tts import ElevenLabsTTS
from tools.audio.google_tts import GoogleTTS
from tools.audio.openai_audio_tts import OpenAIAudioTTS
from tools.audio.openai_tts import OpenAITTS
from tools.audio.piper_tts import PiperTTS
from tools.audio.minimax_tts import MiniMaxTTS
from tools.audio.tts_selector import TTSSelector


# ---- TTS Provider Tools ----

class TestElevenLabsTTS:
    def test_identity(self):
        tool = ElevenLabsTTS()
        info = tool.get_info()
        assert info["name"] == "elevenlabs_tts"
        assert info["tier"] == "voice"
        assert info["capability"] == "tts"
        assert info["provider"] == "elevenlabs"

    def test_cost_estimate(self):
        tool = ElevenLabsTTS()
        cost = tool.estimate_cost({"text": "Hello world, this is a test."})
        assert cost > 0
        assert cost < 0.01  # short text should be cheap

    def test_capabilities(self):
        tool = ElevenLabsTTS()
        assert "text_to_speech" in tool.capabilities
        assert "text_to_dialogue" in tool.capabilities
        assert "expressive_audio_tags" in tool.capabilities
        assert "voice_selection" in tool.capabilities


class TestPiperTTS:
    def test_identity(self):
        tool = PiperTTS()
        info = tool.get_info()
        assert info["name"] == "piper_tts"
        assert info["tier"] == "voice"
        assert info["capability"] == "tts"
        assert info["provider"] == "piper"

    def test_cost_is_free(self):
        tool = PiperTTS()
        assert tool.estimate_cost({"text": "anything"}) == 0.0

    def test_capabilities(self):
        tool = PiperTTS()
        assert "text_to_speech" in tool.capabilities
        assert "offline_generation" in tool.capabilities


class TestGoogleTTS:
    def test_identity(self):
        tool = GoogleTTS()
        info = tool.get_info()
        assert info["name"] == "google_tts"
        assert info["tier"] == "voice"
        assert info["capability"] == "tts"
        assert info["provider"] == "google_tts"

    def test_gemini_prompt_model_supported(self):
        tool = GoogleTTS()
        assert tool._resolve_model({"model": "gemini"}) == "gemini-3.1-flash-tts-preview"
        tool._validate_inputs({"text": "Hello", "model": "gemini-3.1-flash-tts-preview"})
        assert "style_prompting" in tool.capabilities
        assert tool._resolve_voice(None) == "Kore"
        assert tool.VOICE_OPTIONS["Kore"] == "Firm"
        assert len(tool.VOICE_OPTIONS) == 30
        assert tool.input_schema["properties"]["model"]["enum"] == sorted(tool._MODELS)
        assert "Kore" in tool.input_schema["properties"]["voice"]["enum"]

    def test_status_uses_gemini_api_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        tool = GoogleTTS()
        assert tool.get_status().value == "available"

    def test_gemini_api_payload_combines_prompt_and_voice(self):
        tool = GoogleTTS()
        payload = tool._payload(
            {
                "text": "Have a wonderful day!",
                "prompt": "Say cheerfully:",
            },
            voice_name="Kore",
        )
        text = payload["contents"][0]["parts"][0]["text"]
        assert text.startswith("Say cheerfully:")
        assert text.endswith("Have a wonderful day!")
        assert payload["generationConfig"]["responseModalities"] == ["AUDIO"]
        assert (
            payload["generationConfig"]["speechConfig"]["voiceConfig"]
            ["prebuiltVoiceConfig"]["voiceName"]
            == "Kore"
        )

    def test_audio_tags_are_inserted_before_spoken_text(self):
        tool = GoogleTTS()
        payload = tool._payload(
            {
                "text": "系统已经准备好。",
                "prompt": "Use calm Mandarin narration.",
                "audio_tags": ["serious", "[confident]"],
            },
            voice_name="Kore",
        )
        text = payload["contents"][0]["parts"][0]["text"]
        assert text.endswith("[serious] [confident] 系统已经准备好。")

    def test_rejects_invalid_audio_tags(self):
        tool = GoogleTTS()
        with pytest.raises(ValueError, match="audio_tags"):
            tool._validate_inputs({"text": "Hello", "audio_tags": ["[broken"]})

    def test_rejects_unknown_voice(self):
        tool = GoogleTTS()
        with pytest.raises(ValueError, match="voice"):
            tool._validate_inputs({"text": "Hello", "voice": "NotARealGeminiVoice"})

    def test_delivery_preset_and_duration_target_become_prompt_guidance(self):
        tool = GoogleTTS()
        payload = tool._payload(
            {
                "text": "遇到生产问题时，先让 Agent 汇总日志线索。",
                "delivery_preset": "technical_briefing",
                "duration_target_seconds": 5.2,
                "prompt": "Use a mature female Mandarin voice direction.",
            },
            voice_name="Kore",
        )
        text = payload["contents"][0]["parts"][0]["text"]
        assert "focused technical product briefing" in text
        assert "approximately 5.2 seconds" in text
        assert "Use a mature female Mandarin voice direction." in text
        assert text.endswith("遇到生产问题时，先让 Agent 汇总日志线索。")

    def test_rejects_unknown_delivery_preset(self):
        tool = GoogleTTS()
        with pytest.raises(ValueError, match="delivery_preset"):
            tool._validate_inputs({"text": "Hello", "delivery_preset": "sleepy_anchor"})

    def test_rejects_non_positive_duration_target(self):
        tool = GoogleTTS()
        with pytest.raises(ValueError, match="duration_target_seconds"):
            tool._validate_inputs({"text": "Hello", "duration_target_seconds": 0})

    def test_multi_speaker_payload_maps_speakers_to_voices(self):
        tool = GoogleTTS()
        payload = tool._payload(
            {
                "text": "Host: Welcome.\nGuest: Thanks.",
                "speaker_voice_configs": [
                    {"speaker": "Host", "voice": "Kore"},
                    {"speaker": "Guest", "voice": "Puck"},
                ],
            },
            voice_name="Kore",
        )
        configs = (
            payload["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]
            ["speakerVoiceConfigs"]
        )
        assert configs[0]["speaker"] == "Host"
        assert configs[1]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"

    def test_rejects_more_than_two_speakers(self):
        tool = GoogleTTS()
        with pytest.raises(ValueError, match="at most two speakers"):
            tool._validate_inputs(
                {
                    "text": "Dialogue",
                    "speaker_voice_configs": [
                        {"speaker": "A", "voice": "Kore"},
                        {"speaker": "B", "voice": "Puck"},
                        {"speaker": "C", "voice": "Aoede"},
                    ],
                }
            )

    def test_extract_gemini_audio_accepts_inline_data(self):
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "data": "aGVsbG8=",
                                }
                            }
                        ]
                    }
                }
            ]
        }
        assert GoogleTTS._extract_audio(payload) == b"hello"


class TestMiniMaxTTS:
    def test_identity(self):
        tool = MiniMaxTTS()
        info = tool.get_info()
        assert info["name"] == "minimax_tts"
        assert info["tier"] == "voice"
        assert info["capability"] == "tts"
        assert info["provider"] == "minimax"

    def test_cost_estimate(self):
        tool = MiniMaxTTS()
        cost = tool.estimate_cost({"text": "Hello world, this is a test."})
        assert cost > 0
        assert cost < 0.01

    def test_capabilities(self):
        tool = MiniMaxTTS()
        assert "text_to_speech" in tool.capabilities
        assert "voice_selection" in tool.capabilities
        assert "timestamp_alignment" in tool.capabilities


class TestMusicGen:
    def test_identity(self):
        tool = MusicGen()
        info = tool.get_info()
        assert info["name"] == "music_gen"
        assert info["tier"] == "generate"

    def test_cost_estimate_scales_with_duration(self):
        tool = MusicGen()
        cost_30 = tool.estimate_cost({"prompt": "ambient", "duration_seconds": 30})
        cost_60 = tool.estimate_cost({"prompt": "ambient", "duration_seconds": 60})
        assert cost_60 > cost_30

    def test_capabilities(self):
        tool = MusicGen()
        assert "generate_background_music" in tool.capabilities
        assert "music_v2" in tool.capabilities
        assert "instrumental_control" in tool.capabilities

    def test_status_can_disable_known_unentitled_music_api(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
        monkeypatch.setenv("ELEVENLABS_MUSIC_API_ENABLED", "false")

        result = MusicGen().execute({"prompt": "ambient piano", "duration_seconds": 3})

        assert MusicGen().get_status().value == "unavailable"
        assert result.success is False
        assert "ELEVENLABS_MUSIC_API_ENABLED=false" in result.error

    def test_execute_requires_duration_without_cost_crash(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
        monkeypatch.delenv("ELEVENLABS_MUSIC_API_ENABLED", raising=False)

        result = MusicGen().execute({"prompt": "ambient piano"})

        assert result.success is False
        assert "duration_seconds is required" in result.error

    def test_execute_posts_music_v2_payload(self, monkeypatch, tmp_path):
        import requests

        captured = {}

        class FakeResponse:
            content = b"mp3-bytes"

            def raise_for_status(self):
                return None

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
        monkeypatch.delenv("ELEVENLABS_MUSIC_API_ENABLED", raising=False)
        monkeypatch.setattr(requests, "post", fake_post)

        output = tmp_path / "bed.mp3"
        result = MusicGen().execute(
            {
                "prompt": "calm documentary bed with soft piano",
                "duration_seconds": 3,
                "model_id": "music_v2",
                "force_instrumental": True,
                "output_format": "mp3_48000_192",
                "output_path": str(output),
            }
        )

        assert result.success is True
        assert output.read_bytes() == b"mp3-bytes"
        assert captured["url"] == "https://api.elevenlabs.io/v1/music"
        assert captured["json"]["model_id"] == "music_v2"
        assert captured["json"]["force_instrumental"] is True
        assert captured["json"]["music_length_ms"] == 3000
        assert captured["params"] == {"output_format": "mp3_48000_192"}
        assert result.data["model"] == "music_v2"
        assert result.data["format"] == "mp3"


class TestNewToolsRegistry:
    def test_all_register(self):
        reg = ToolRegistry()
        reg.register(ElevenLabsTTS())
        reg.register(PiperTTS())
        reg.register(MusicGen())
        assert len(reg.list_all()) == 3

    def test_voice_tier_tools(self):
        reg = ToolRegistry()
        reg.register(AzureTTS())
        reg.register(DoubaoTTS())
        reg.register(ElevenLabsTTS())
        reg.register(GoogleTTS())
        reg.register(MiniMaxTTS())
        reg.register(OpenAIAudioTTS())
        reg.register(OpenAITTS())
        reg.register(PiperTTS())
        voice_tools = reg.get_by_tier(ToolTier.VOICE)
        assert len(voice_tools) == 8
        names = {t.name for t in voice_tools}
        assert names == {
            "azure_tts",
            "doubao_tts",
            "elevenlabs_tts",
            "google_tts",
            "minimax_tts",
            "openai_audio_tts",
            "openai_tts",
            "piper_tts",
        }


class TestCapabilityMetadata:
    def test_tts_tools_expose_capability_provider_and_location(self):
        tool = ElevenLabsTTS()
        info = tool.get_info()
        assert info["capability"] == "tts"
        assert info["provider"] == "elevenlabs"
        assert info["usage_location"].endswith("tools\\audio\\elevenlabs_tts.py") or info["usage_location"].endswith("tools/audio/elevenlabs_tts.py")
        assert "related_skills" in info
        assert "fallback_tools" in info

    def test_provider_specific_tts_tools_register(self):
        reg = ToolRegistry()
        reg.register(AzureTTS())
        reg.register(DoubaoTTS())
        reg.register(ElevenLabsTTS())
        reg.register(GoogleTTS())
        reg.register(MiniMaxTTS())
        reg.register(OpenAIAudioTTS())
        reg.register(OpenAITTS())
        reg.register(PiperTTS())
        reg.register(TTSSelector())
        assert {tool.name for tool in reg.get_by_capability("tts")} == {
            "azure_tts",
            "doubao_tts",
            "elevenlabs_tts",
            "google_tts",
            "minimax_tts",
            "openai_audio_tts",
            "openai_tts",
            "piper_tts",
            "tts_selector",
        }
        assert {tool.name for tool in reg.get_by_provider("azure")} == {"azure_tts"}
        assert {tool.name for tool in reg.get_by_provider("doubao")} == {"doubao_tts"}
        assert {tool.name for tool in reg.get_by_provider("elevenlabs")} == {"elevenlabs_tts"}
        assert {tool.name for tool in reg.get_by_provider("google_tts")} == {"google_tts"}
        assert {tool.name for tool in reg.get_by_provider("minimax")} == {"minimax_tts"}
        assert {tool.name for tool in reg.get_by_provider("openai_audio")} == {"openai_audio_tts"}

    def test_registry_catalog_views(self):
        reg = ToolRegistry()
        reg.register(ElevenLabsTTS())
        reg.register(OpenAITTS())
        reg.register(PiperTTS())
        catalog = reg.capability_catalog()
        assert "tts" in catalog
        providers = {item["provider"] for item in catalog["tts"] if item["provider"] != "selector"}
        assert providers == {
            "azure",
            "doubao",
            "elevenlabs",
            "google_tts",
            "minimax",
            "openai_audio",
            "openai",
            "piper",
        }


# ---- Animated Explainer Pipeline ----

class TestAnimatedExplainerManifest:
    def test_loads(self):
        manifest = load_pipeline("animated-explainer")
        assert manifest["name"] == "animated-explainer"
        assert manifest["version"] == "2.0"

    def test_all_stages_present(self):
        manifest = load_pipeline("animated-explainer")
        stage_names = get_stage_order(manifest)
        expected = ["research", "proposal", "script", "scene_plan", "assets", "edit", "compose", "publish"]
        assert stage_names == expected

    def test_every_stage_has_skill(self):
        manifest = load_pipeline("animated-explainer")
        for stage in manifest["stages"]:
            assert "skill" in stage, f"Stage {stage['name']} missing skill"
            skill = get_stage_skill(manifest, stage["name"])
            assert skill is not None
            assert skill.startswith("pipelines/explainer/")

    def test_every_stage_has_review_focus(self):
        manifest = load_pipeline("animated-explainer")
        for stage in manifest["stages"]:
            focus = get_stage_review_focus(manifest, stage["name"])
            assert len(focus) >= 3, f"Stage {stage['name']} needs more review focus items"

    def test_required_tools_complete(self):
        manifest = load_pipeline("animated-explainer")
        tools = get_required_tools(manifest)
        expected = {"tts_selector", "image_selector", "video_compose", "audio_mixer"}
        for t in expected:
            assert t in tools, f"Missing required tool: {t}"

    def test_creative_stages_require_human_approval(self):
        manifest = load_pipeline("animated-explainer")
        approval_stages = {"proposal", "script", "scene_plan", "publish"}
        for stage in manifest["stages"]:
            if stage["name"] in approval_stages:
                assert stage.get("human_approval_default") is True, (
                    f"Stage {stage['name']} should require human approval"
                )

    def test_listed(self):
        assert "animated-explainer" in list_pipelines()


# ---- Style Playbooks ----

class TestStylePlaybooks:
    def test_all_listed(self):
        playbooks = list_playbooks()
        assert "clean-professional" in playbooks
        assert "flat-motion-graphics" in playbooks
        assert "minimalist-diagram" in playbooks

    @pytest.mark.parametrize("name", ["clean-professional", "flat-motion-graphics", "minimalist-diagram"])
    def test_loads_and_validates(self, name):
        pb = load_playbook(name)
        assert pb["identity"]["name"]
        assert pb["identity"]["category"]

    @pytest.mark.parametrize("name", ["clean-professional", "flat-motion-graphics", "minimalist-diagram"])
    def test_has_required_sections(self, name):
        pb = load_playbook(name)
        assert "visual_language" in pb
        assert "typography" in pb
        assert "motion" in pb
        assert "audio" in pb
        assert "asset_generation" in pb
        assert "quality_rules" in pb
        assert len(pb["quality_rules"]) >= 3

    @pytest.mark.parametrize("name", ["clean-professional", "flat-motion-graphics", "minimalist-diagram"])
    def test_color_palette_complete(self, name):
        pb = load_playbook(name)
        palette = pb["visual_language"]["color_palette"]
        assert "primary" in palette
        assert "accent" in palette
        assert "background" in palette
        assert "text" in palette

    @pytest.mark.parametrize("name", ["clean-professional", "flat-motion-graphics", "minimalist-diagram"])
    def test_pacing_rules_present(self, name):
        pb = load_playbook(name)
        pacing = pb["motion"]["pacing_rules"]
        assert "min_scene_hold_seconds" in pacing
        assert "max_scene_hold_seconds" in pacing

    def test_compatible_with_manifest(self):
        manifest = load_pipeline("animated-explainer")
        available = list_playbooks()
        compat = manifest.get("compatible_playbooks", {})
        # compatible_playbooks is a dict with recommended/also_works lists
        playbook_names = compat.get("recommended", []) + compat.get("also_works", [])
        for name in playbook_names:
            assert name in available, f"Manifest references unavailable playbook: {name}"


# ---- Skills Existence ----

class TestSkillsExist:
    SKILLS_DIR = PROJECT_ROOT / "skills"

    @pytest.mark.parametrize("skill_path", [
        "pipelines/explainer/idea-director.md",
        "pipelines/explainer/script-director.md",
        "pipelines/explainer/scene-director.md",
        "pipelines/explainer/asset-director.md",
        "pipelines/explainer/edit-director.md",
        "pipelines/explainer/compose-director.md",
        "pipelines/explainer/publish-director.md",
    ])
    def test_director_skills_exist(self, skill_path):
        full_path = self.SKILLS_DIR / skill_path
        assert full_path.exists(), f"Missing director skill: {skill_path}"
        content = full_path.read_text(encoding="utf-8")
        assert len(content) > 500, f"Skill too short to be useful: {skill_path}"

    @pytest.mark.parametrize("skill_path", [
        "meta/reviewer.md",
        "meta/checkpoint-protocol.md",
        "meta/skill-creator.md",
    ])
    def test_meta_skills_exist(self, skill_path):
        full_path = self.SKILLS_DIR / skill_path
        assert full_path.exists(), f"Missing meta skill: {skill_path}"
        content = full_path.read_text(encoding="utf-8")
        assert len(content) > 500, f"Skill too short to be useful: {skill_path}"

    @pytest.mark.parametrize("skill_path", [
        "pipelines/explainer/idea-director.md",
        "pipelines/explainer/script-director.md",
        "pipelines/explainer/scene-director.md",
        "pipelines/explainer/asset-director.md",
        "pipelines/explainer/edit-director.md",
        "pipelines/explainer/compose-director.md",
        "pipelines/explainer/publish-director.md",
    ])
    def test_director_skills_have_required_sections(self, skill_path):
        content = (self.SKILLS_DIR / skill_path).read_text(encoding="utf-8")
        assert "## When to Use" in content
        assert "## Process" in content or "## Protocol" in content
        assert "Self-Evaluate" in content or "self-evaluate" in content.lower()

    @pytest.mark.parametrize("skill_path", [
        "meta/reviewer.md",
        "meta/checkpoint-protocol.md",
        "meta/skill-creator.md",
    ])
    def test_meta_skills_have_required_sections(self, skill_path):
        content = (self.SKILLS_DIR / skill_path).read_text(encoding="utf-8")
        assert "## When to Use" in content
        assert "## Protocol" in content or "## Process" in content


# ---- Remotion Scaffold ----

class TestRemotionScaffold:
    REMOTION_DIR = PROJECT_ROOT / "remotion-composer"

    def test_package_json_exists(self):
        assert (self.REMOTION_DIR / "package.json").exists()

    def test_entry_point_exists(self):
        assert (self.REMOTION_DIR / "src" / "index.tsx").exists()

    def test_root_composition_exists(self):
        assert (self.REMOTION_DIR / "src" / "Root.tsx").exists()

    def test_explainer_component_exists(self):
        assert (self.REMOTION_DIR / "src" / "Explainer.tsx").exists()

    def test_text_card_component_exists(self):
        assert (self.REMOTION_DIR / "src" / "components" / "TextCard.tsx").exists()

    def test_stat_card_component_exists(self):
        assert (self.REMOTION_DIR / "src" / "components" / "StatCard.tsx").exists()


# ---- Video Compose Operations ----

class TestVideoComposeOperations:
    def test_render_operation_exists(self):
        from tools.video.video_compose import VideoCompose
        tool = VideoCompose()
        ops = tool.input_schema["properties"]["operation"]["enum"]
        assert "render" in ops
        assert "remotion_render" in ops

    def test_render_rejects_missing_inputs(self):
        from tools.video.video_compose import VideoCompose
        tool = VideoCompose()
        result = tool.execute({"operation": "render"})
        assert not result.success
        assert "edit_decisions" in result.error
