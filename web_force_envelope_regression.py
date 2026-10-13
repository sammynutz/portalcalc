"""Compression must survive a larger tensile force in the web-sizing envelope."""
import copy
import unittest
from main import MainWindow, merge_web_member_demand


class WebForceEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.compression = dict(axial_kn=29.4, compression_kn=29.4, tension_kn=0,
                                combo="Compression wind case", member=104, length_mm=1085,
                                connection_ratio=.8, connection_demand_kn=29.4,
                                connection_combo="Compression joint", connection={"context": "compression"})
        self.tension = dict(axial_kn=32.1, compression_kn=0, tension_kn=32.1,
                            combo="Larger tension wind case", member=104, length_mm=1085,
                            connection_ratio=.7, connection_demand_kn=32.1,
                            connection_combo="Tension joint", connection={"context": "tension"})

    def test_opposite_sign_demands_and_sources_survive_in_either_order(self):
        for first, second in ((self.compression, self.tension), (self.tension, self.compression)):
            merged = merge_web_member_demand(first, second)
            self.assertEqual(merged["compression_kn"], 29.4)
            self.assertEqual(merged["tension_kn"], 32.1)
            self.assertEqual(merged["compression_combo"], self.compression["combo"])
            self.assertEqual(merged["tension_combo"], self.tension["combo"])
            self.assertEqual(merged["connection"], self.compression["connection"])
            # The largest absolute force is tension, but compression utilisation governs.
            envelope = MainWindow.web_member_demand_envelope(None, merged)
            self.assertGreater(envelope["compression_kn"]["value"] / 9.12,
                               envelope["tension_kn"]["value"] / 40)
            self.assertEqual(envelope["compression_kn"]["combo"], self.compression["combo"])

    def test_group_merge_retains_local_component_maxima(self):
        first = merge_web_member_demand(self.compression, self.tension)
        lower = dict(self.compression, axial_kn=35., tension_kn=35., compression_kn=10., combo="Other combo")
        merged = merge_web_member_demand(first, lower)
        self.assertEqual(merged["compression_kn"], 29.4)
        self.assertEqual(merged["tension_kn"], 35.)
        self.assertEqual(merged["compression_combo"], self.compression["combo"])

    def test_merge_does_not_mutate_inputs_or_share_connection_context(self):
        before = copy.deepcopy(self.compression)
        result = merge_web_member_demand(self.compression, self.tension)
        result["connection"]["context"] = "changed"
        self.assertEqual(self.compression, before)


if __name__ == "__main__":
    unittest.main()
