"""Tests for the scrollytelling case-study report style.

Covers:
  - Markdown directives: {{scene}}, {{step}}, {{reveal}}, {{image}}, {{cta}}
  - create_case_study_artifact validation and happy path
  - Filename prefix and on-disk discovery
"""

from datetime import datetime

import pytest

import cerebro_mcp.tools.visualization.charts as viz


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(viz, "_chart_registry", {})
    monkeypatch.setattr(viz, "_chart_counter", 0)
    monkeypatch.setattr(viz, "_REPORT_CACHE", {})


class TestCaseStudyDirectives:
    """Markdown directives are only parsed when case_study_mode=True."""

    def _register_chart(self, chart_id):
        viz._chart_registry[chart_id] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }

    def test_scene_block_renders_sticky_visual_with_narrative(self):
        self._register_chart("chart_1")
        md = (
            '{{scene chart="chart_1" side="left"}}\n'
            "Narrative paragraph explaining the trend.\n\n"
            "Another paragraph.\n"
            "{{/scene}}\n"
        )
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'class="cs-scene cs-scene--left"' in html
        assert 'class="cs-scene-visual"' in html
        assert 'class="cs-scene-narrative"' in html
        assert 'id="chart-chart_1"' in html
        assert "Narrative paragraph" in html

    def test_scene_right_side_class(self):
        self._register_chart("chart_1")
        md = '{{scene chart="chart_1" side="right"}}\nBody.\n{{/scene}}\n'
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert "cs-scene--right" in html

    def test_scene_with_image_visual(self):
        md = (
            '{{scene image="https://x/y.png" caption="A caption"}}\n'
            "Prose goes here.\n"
            "{{/scene}}\n"
        )
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'class="cs-visual cs-visual--image"' in html
        assert "https://x/y.png" in html
        assert "A caption" in html

    def test_scene_with_missing_chart_emits_placeholder(self):
        md = '{{scene chart="missing"}}\nbody\n{{/scene}}\n'
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert "cs-visual--missing" in html
        assert "missing" in html

    def test_step_blocks_get_indices_and_state(self):
        self._register_chart("chart_1")
        md = (
            '{{scene chart="chart_1"}}\n'
            '{{step chart="chart_1" state="highlight=A"}}\n'
            "Beat one.\n"
            "{{/step}}\n"
            '{{step chart="chart_1" state="highlight=B"}}\n'
            "Beat two.\n"
            "{{/step}}\n"
            "{{/scene}}\n"
        )
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'data-step-index="1"' in html
        assert 'data-step-index="2"' in html
        assert 'data-step-state="highlight=A"' in html
        assert 'data-step-state="highlight=B"' in html
        assert 'data-step-chart="chart_1"' in html

    def test_reveal_block_emits_reveal_wrapper(self):
        md = (
            "{{reveal}}\n"
            "- First bullet\n"
            "- Second bullet\n"
            "{{/reveal}}\n"
        )
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'class="cs-reveal"' in html
        assert 'data-cs-reveal="true"' in html
        assert "First bullet" in html
        assert "Second bullet" in html

    def test_image_directive_with_full_bleed(self):
        md = (
            '{{image src="https://x/y.jpg" caption="cap" full_bleed=true}}\n'
        )
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'class="cs-image cs-image--full"' in html
        assert "https://x/y.jpg" in html
        assert "cap" in html

    def test_image_directive_without_full_bleed(self):
        md = '{{image src="https://x/y.jpg" caption="cap"}}\n'
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'class="cs-image"' in html
        assert "cs-image--full" not in html

    def test_image_missing_src_emits_placeholder(self):
        md = '{{image caption="nothing"}}\n'
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert "cs-image--missing" in html

    def test_cta_directive(self):
        md = '{{cta label="Book a call" href="https://example.com/demo"}}\n'
        html = viz._markdown_to_html(md, case_study_mode=True)
        assert 'class="cs-cta"' in html
        assert 'href="https://example.com/demo"' in html
        assert "Book a call" in html

    def test_directives_ignored_when_not_case_study_mode(self):
        md = '{{scene chart="chart_1"}}\nbody\n{{/scene}}\n'
        html = viz._markdown_to_html(md, case_study_mode=False)
        assert "cs-scene" not in html
        # The opener text passes through as a paragraph.
        assert "scene" in html

    def test_chart_placeholder_still_works_in_case_study_mode(self):
        self._register_chart("chart_9")
        html = viz._markdown_to_html(
            "{{chart:chart_9}}\n", case_study_mode=True
        )
        assert 'id="chart-chart_9"' in html


class TestCaseStudyArtifact:
    """create_case_study_artifact validation + metadata passthrough."""

    def _register_chart(self, chart_id="chart_1"):
        viz._chart_registry[chart_id] = {
            "option": {"xAxis": {"data": ["a"]}, "series": [{"data": [1]}]},
            "title": "Test Chart",
            "chart_type": "line",
            "data_points": 1,
            "created_at": datetime.now(),
        }

    def test_happy_path_writes_case_study_file_with_metadata(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._register_chart("chart_1")
        out = viz.create_case_study_artifact(
            title="How Acme Unlocked 3x Growth",
            deck="A narrative case study of Acme's path to PMF.",
            content_markdown=(
                "## The setup\n\n"
                '{{scene chart="chart_1" side="left"}}\n'
                "Baseline context paragraph.\n\n"
                '{{step chart="chart_1" state="highlight=Q1"}}\n'
                "Beat 1.\n"
                "{{/step}}\n'"
                '{{step chart="chart_1" state="highlight=Q2"}}\n'
                "Beat 2.\n"
                "{{/step}}\n"
                "{{/scene}}\n\n"
                "{{reveal}}\n"
                "- Drove 3x growth\n"
                "- Cut CAC by 40%\n"
                "- Retained 95% of cohort\n"
                "{{/reveal}}\n\n"
                '{{image src="https://x/hero.png" caption="Hero" full_bleed=true}}\n'
                '{{cta label="Get in touch" href="https://acme.example/demo"}}\n'
            ),
            authors=["Case Team"],
            category="Customer Story",
            key_points=[
                "Acme 3x'd revenue in 12 months",
                "Cerebro cut reporting time by 80%",
                "Exec team unblocked decisions in hours, not weeks",
            ],
            hero_chart_id="chart_1",
            cta={"label": "Book a demo", "href": "https://acme.example/demo"},
            enforce_quality_gate=False,
            reset_session_state=False,
        )

        assert out["report_path"].name.startswith("cerebro_case_study_")
        structured = out["structured"]
        assert structured["presentation_mode"] == "scrollytelling"
        meta = structured["case_study_metadata"]
        assert meta["deck"].startswith("A narrative")
        assert meta["category"] == "Customer Story"
        assert meta["hero_chart_id"] == "chart_1"
        assert meta["cta"]["label"] == "Book a demo"
        assert len(meta["key_points"]) == 3
        assert meta["reading_minutes"] >= 1

        sections = structured["sections_html"]
        assert "cs-scene" in sections
        assert "cs-reveal" in sections
        assert "cs-image--full" in sections
        assert "cs-cta" in sections
        assert 'data-step-index="1"' in sections

    def test_requires_non_empty_deck(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._register_chart("chart_1")
        with pytest.raises(ValueError, match="deck"):
            viz.create_case_study_artifact(
                title="X",
                deck="",
                content_markdown="{{chart:chart_1}}\n",
                key_points=["a", "b", "c"],
                enforce_quality_gate=False,
                reset_session_state=False,
            )

    def test_requires_3_to_6_key_points(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._register_chart("chart_1")
        with pytest.raises(ValueError, match="key_points"):
            viz.create_case_study_artifact(
                title="X",
                deck="A deck.",
                content_markdown="{{chart:chart_1}}\n",
                key_points=["only one"],
                enforce_quality_gate=False,
                reset_session_state=False,
            )

    def test_deck_length_cap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        self._register_chart("chart_1")
        with pytest.raises(ValueError, match="Deck is too long"):
            viz.create_case_study_artifact(
                title="X",
                deck="x" * 300,
                content_markdown="{{chart:chart_1}}\n",
                key_points=["a", "b", "c"],
                enforce_quality_gate=False,
                reset_session_state=False,
            )


class TestCaseStudyFilenames:
    def test_report_filename_case_study_prefix(self):
        name = viz._report_filename(
            "abc-123", "Acme Growth Story", kind="case_study"
        )
        assert name.startswith("cerebro_case_study_")
        assert name.endswith("_abc-123.html")

    def test_find_report_on_disk_matches_case_study(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CEREBRO_REPORT_DIR", str(tmp_path))
        fn = (
            tmp_path
            / "cerebro_case_study_20260101T000000Z_acme_abcd1234-def6-7890-1234-567890abcdef.html"
        )
        fn.write_text("<html></html>")
        found = viz._find_report_on_disk("abcd1234")
        assert found == fn
        assert viz._report_kind_from_path(fn) == "case_study"
