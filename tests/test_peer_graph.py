"""Tests for pipeline/peer_graph/builder.py"""

import json
import pytest
import pandas as pd
from pathlib import Path

from pipeline.peer_graph.builder import build_peer_graph, haversine_distance


# Two meters ~100m apart in Bangalore
METER_A = {"meter_id": "MA", "lat": 12.9716, "lng": 77.5946}
METER_B = {"meter_id": "MB", "lat": 12.9725, "lng": 77.5946}  # ~100m north

# Meter far away (~5km)
METER_C = {"meter_id": "MC", "lat": 13.0200, "lng": 77.5946}


def _make_registry(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestHaversineDistance:
    def test_same_point_is_zero(self):
        d = haversine_distance(12.9716, 77.5946, 12.9716, 77.5946)
        assert d == pytest.approx(0.0, abs=0.01)

    def test_known_distance_approx_100m(self):
        # ~100m north
        d = haversine_distance(12.9716, 77.5946, 12.9725, 77.5946)
        assert 80 < d < 120

    def test_known_distance_approx_5km(self):
        d = haversine_distance(12.9716, 77.5946, 13.0200, 77.5946)
        assert d > 4000


class TestBuildPeerGraph:
    def test_meters_within_200m_are_neighbors(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_B])
        graph = build_peer_graph(registry, output_path=output)
        assert "MB" in graph["MA"]
        assert "MA" in graph["MB"]

    def test_meters_beyond_200m_are_not_neighbors(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_C])
        graph = build_peer_graph(registry, output_path=output)
        assert "MC" not in graph["MA"]
        assert "MA" not in graph["MC"]

    def test_isolated_meter_has_empty_list(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_C])
        graph = build_peer_graph(registry, output_path=output)
        assert graph["MA"] == []
        assert graph["MC"] == []

    def test_all_meters_present_in_output(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_B, METER_C])
        graph = build_peer_graph(registry, output_path=output)
        assert "MA" in graph
        assert "MB" in graph
        assert "MC" in graph

    def test_output_is_valid_json(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_B])
        build_peer_graph(registry, output_path=output)
        with open(output) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_idempotent_same_output_on_rerun(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_B, METER_C])
        graph1 = build_peer_graph(registry, output_path=output)
        graph2 = build_peer_graph(registry, output_path=output)
        assert graph1 == graph2

    def test_bidirectional_relationship(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A, METER_B])
        graph = build_peer_graph(registry, output_path=output)
        # If A→B then B→A
        assert "MB" in graph["MA"]
        assert "MA" in graph["MB"]

    def test_single_meter_has_empty_list(self, tmp_path):
        output = str(tmp_path / "peer_graph.json")
        registry = _make_registry([METER_A])
        graph = build_peer_graph(registry, output_path=output)
        assert graph["MA"] == []
