"""Tests para el sort numérico de segmentos en el reporte."""

from mlmonitor.report.builder import _segment_sort_key


class TestSegmentSortKey:
    def test_sorts_numerically_not_alphabetically(self):
        ids = ["s1", "s10", "s11", "s2", "s3", "s9"]
        ordered = sorted(ids, key=_segment_sort_key)
        assert ordered == ["s1", "s2", "s3", "s9", "s10", "s11"]

    def test_full_bazboost_segment_set_in_order(self):
        ids = [f"s{i}" for i in range(1, 12)]
        # Mezclamos el orden y aseguramos que el sort recupera el numérico
        shuffled = ["s11", "s3", "s1", "s10", "s7", "s2", "s5", "s9", "s4", "s8", "s6"]
        assert sorted(shuffled, key=_segment_sort_key) == ids

    def test_unknown_id_falls_back_to_end(self):
        ids = ["s2", "weird", "s1"]
        ordered = sorted(ids, key=_segment_sort_key)
        # 'weird' (key=999) queda al final
        assert ordered == ["s1", "s2", "weird"]
