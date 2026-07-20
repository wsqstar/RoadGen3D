from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.parameter_proposals import (
    ParameterProposalError,
    create_parameter_proposal,
)
from roadgen3d.services.street_design_parameters import (
    build_street_design_parameter_spec,
    compile_street_design_parameter_spec,
)


class _FakeLlm:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat_json(self, messages):
        self.calls.append(messages)
        return self.payload


def _base_spec():
    return build_street_design_parameter_spec(
        "balanced_complete",
        source_revision=4,
        source_fingerprint="frozen-source",
    )


def test_llm_proposal_is_side_effect_free_and_compiles_parametrically():
    client = _FakeLlm({
        "patch": {
            "skeleton": {"sidewalkWidthM": 4.0},
            "furniture": {"categories": {"bench": {"targetCountPer100M": 5}}},
        },
        "changed_fields": [
            {"field": "skeleton.sidewalkWidthM", "reason": "Improve clear walking space", "confidence": 0.8}
        ],
        "warnings": [],
    })

    proposal = create_parameter_proposal(
        llm_client=client,
        current_spec=_base_spec(),
        design_goals=["walkability"],
        structured_weaknesses={"clear_width": 42},
        scene_summary={"road_count": 2},
    )

    assert proposal["sideEffectFree"] is True
    assert proposal["generationMode"] == "parametric"
    assert proposal["knowledgeSource"] == "none"
    assert proposal["proposedSpec"]["source"] == _base_spec()["source"]
    assert proposal["proposedSpec"]["skeleton"]["sidewalkWidthM"] == 4.0
    sidewalk_change = next(
        item for item in proposal["changedFields"] if item["field"] == "skeleton.sidewalkWidthM"
    )
    assert sidewalk_change["reason"] == "Improve clear walking space"
    compiled = compile_street_design_parameter_spec(
        proposal["proposedSpec"],
        field_sources={item["field"]: "llm_suggestion" for item in proposal["changedFields"]},
    )
    assert compiled.generation_options["skip_llm"] is True
    assert compiled.generation_options["knowledge_source"] == "none"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"source": {"sourceFingerprint": "changed"}},
        {"geometry": {"centerlines": []}},
        {"buildings": {"footprintLocked": False}},
        {"furniture": {"categories": {"tree": {"assetRefs": [{"assetId": "invented"}]}}}},
    ],
)
def test_llm_proposal_rejects_geometry_and_asset_mutations(patch):
    with pytest.raises(ParameterProposalError):
        create_parameter_proposal(
            llm_client=_FakeLlm({"patch": patch}),
            current_spec=_base_spec(),
            design_goals=["beauty"],
        )
