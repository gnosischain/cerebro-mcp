"""The Dockerfile's mini-app list must match Vite's.

The Dockerfile does not ship the committed bundles. It `rm -rf`s
`static/assets`, rebuilds every app in the `ui-builder` stage, and copies the
results back — so the set of apps it enumerates IS the set of apps the image
serves. That list and `ENTRY_MAP` in `ui/vite.config.ts` are the same list
written twice, and nothing connected them.

They drifted on 2026-09-17. `poolsExplorer` was added to `vite.config.ts` and to
the Makefile's `build-ui` fan-out, but not to the Dockerfile. The image shipped
`pools_explorer.html` — which survived only because it is a committed file the
Dockerfile never overwrites — with its JS and CSS deleted by the `rm -rf` and
never rebuilt. The result was a blank page in production with no error: the HTML
loaded, its module script 404'd, and nothing rendered.

Local development could not catch it. `make dev` serves live source and
`make serve-catalog` serves the committed bundles; only the image rebuilds. It
worked on every machine and was broken everywhere it mattered.
"""

from __future__ import annotations

import pathlib
import re

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
VITE_CONFIG = (ROOT / "ui" / "vite.config.ts").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")

#: `html` is the Vite dev-server catch-all, not a shipped mini-app.
NOT_AN_APP = {"html"}


def _object_keys(source: str, name: str) -> set[str]:
    """Top-level keys of a `const <name> ... = { ... }` object literal."""
    match = re.search(rf"{name}[^{{]*\{{(.*?)\n\}}", source, re.S)
    assert match, f"{name} not found in vite.config.ts"
    return set(re.findall(r"^\s{2}(\w+)\s*:", match.group(1), re.M))


def vite_entries() -> set[str]:
    return _object_keys(VITE_CONFIG, "ENTRY_MAP") - NOT_AN_APP


def split_bundle_asset_dirs() -> dict[str, str]:
    """Split-bundle entry -> the asset DIRECTORY it is served from.

    Read from each SPLIT_BASE value (`/app/<dir>/`) rather than derived from the
    camelCase entry name: `graphExplorerWeb` serves out of `graph_explorer`, so
    a name-mangling rule invents a directory that does not exist. These are the
    apps the `rm -rf` can strand — an inline app carries its script inside the
    HTML and cannot lose it.
    """
    match = re.search(r"SPLIT_BASE[^{]*\{(.*?)\n\}", VITE_CONFIG, re.S)
    assert match, "SPLIT_BASE not found in vite.config.ts"
    pairs = re.findall(r'^\s*(\w+)\s*:\s*"/app/(\w+)/"', match.group(1), re.M)
    return {entry: directory for entry, directory in pairs if entry not in NOT_AN_APP}


def docker_built_entries() -> set[str]:
    return set(re.findall(r"CEREBRO_UI_ENTRY=(\w+)", DOCKERFILE))


def test_the_dockerfile_builds_every_vite_entry():
    missing = sorted(vite_entries() - docker_built_entries())
    assert missing == [], (
        f"vite.config.ts declares {missing} but the Dockerfile never builds them, "
        f"so the image would serve those apps' HTML with no JS"
    )


def test_the_dockerfile_builds_nothing_vite_cannot():
    """The other direction: a stale entry name fails the image build outright,
    but only once someone pushes — cheaper to catch here."""
    unknown = sorted(docker_built_entries() - vite_entries())
    assert unknown == [], f"Dockerfile builds {unknown}, absent from ENTRY_MAP"


@pytest.mark.parametrize("entry,asset_dir", sorted(split_bundle_asset_dirs().items()))
def test_every_split_bundle_app_has_its_assets_copied_into_the_image(entry, asset_dir):
    """The exact failure that shipped. `RUN rm -rf src/cerebro_mcp/static/assets`
    removes every committed asset directory, so an app whose assets are not
    copied back from the builder stage loses them silently — the HTML is a
    tracked file and still ships, which is why the symptom is a blank page
    rather than a 404 on the page itself."""
    assert re.search(
        rf"COPY --from=ui-builder\s+\S+/assets/\s+src/cerebro_mcp/static/assets/\w+/",
        DOCKERFILE,
    ), "no asset COPY lines at all — has the Dockerfile been restructured?"
    copied = set(
        re.findall(
            r"COPY --from=ui-builder\s+\S+/assets/\s+src/cerebro_mcp/static/assets/(\w+)/",
            DOCKERFILE,
        )
    )
    assert asset_dir in copied, (
        f"{entry} ships a split bundle from static/assets/{asset_dir}/ but the "
        f"Dockerfile never copies it — the rm -rf above leaves it empty and the "
        f"app renders a blank page"
    )


def test_the_rm_rf_that_makes_this_fragile_is_still_there():
    """If this ever goes away the tests above stop protecting anything, because
    the committed assets would survive. Fail loudly rather than quietly
    guarding a condition that no longer exists."""
    assert "rm -rf src/cerebro_mcp/static/assets" in DOCKERFILE, (
        "the Dockerfile no longer wipes committed assets — re-derive whether "
        "these guards are still the right ones"
    )


def test_the_makefile_fan_out_covers_every_vite_entry():
    """The third copy of the same list. `make build-ui` is what a developer runs
    before committing bundles, so an app missing here ships a stale bundle."""
    match = re.search(r"^build-ui:(.*)$", MAKEFILE, re.M)
    assert match, "build-ui target not found"
    fanned_out = set(match.group(1).split())
    # Target names do not map 1:1 to entries — `build-ui-graph-explorer` builds
    # both the inline and web graph entries — so check which entries the
    # fanned-out targets actually BUILD rather than guessing their names.
    built: set[str] = set()
    for target in fanned_out:
        body = re.search(rf"^{re.escape(target)}:.*?(?=\n\w|\Z)", MAKEFILE, re.S | re.M)
        if body:
            built.update(re.findall(r"CEREBRO_UI_ENTRY=(\w+)", body.group(0)))
    missing = sorted(vite_entries() - built)
    assert missing == [], (
        f"`make build-ui` does not build {missing}; those bundles go stale"
    )
