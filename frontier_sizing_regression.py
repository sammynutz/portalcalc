"""Frontier optimiser search logic, with stand-ins for the 2D and 3D solvers.

The full optimiser on a real preset takes minutes; run it with
``frontier_sizing.optimise(window)`` (see BRACING_ANALYSIS.md).

Run:  python frontier_sizing_regression.py
"""
import unittest

import frontier_sizing as fs


def point(name, cost, k, passes=True):
    return {'option': name, 'bracing_cost': cost, 'restraint': k, 'passes': passes}


class FrontierTests(unittest.TestCase):
    def test_keeps_cheapest_bracing_for_each_restraint_level(self):
        points = [point('end bays', 100, 0.), point('end bays stiffer', 110, 0.), point('girder', 130, .4),
                  point('girder deep', 150, .35), point('girder stiff', 160, .6), point('cheap but failing', 90, .9, False)]
        self.assertEqual([p['option'] for p in fs.frontier(points)], ['end bays', 'girder', 'girder stiff'])


def sized_for_no_more(frame, option):
    """A frame sized for more restraint than the option delivers fails."""
    return frame['k'] <= option['restraint']+1e-9


class SearchTests(unittest.TestCase):
    def run_search(self, options, frame_cost, verified_extra=0., passes=sized_for_no_more, resized=lambda f, o: False):
        sized, verified = [], []

        def frame_for(k):
            sized.append(round(k, 4))
            return {'cost': frame_cost(k), 'k': k}

        def verify(frame, option):
            verified.append((option['option'], round(frame['k'], 4)))
            extra = verified_extra + (250. if resized(frame, option) else 0.)
            return {'bracing': option['option'], 'verified': passes(frame, option), 'status': 'Verified',
                    'bracing_resized': resized(frame, option), 'total': frame['cost']+option['bracing_cost']+extra}
        incumbent = {'bracing': options[0]['option'], 'verified': True, 'total': frame_cost(0.)+options[0]['bracing_cost']}
        best, rows = fs.search(options, frame_for, verify, incumbent)
        return best, rows, sized, verified

    def test_floor_skips_options_that_cannot_win(self):
        # Factory defaults pattern: restraint saves ~1,170 of frame but costs ~1,300 of bracing.
        options = fs.frontier([point('end bays', 14028., 0.), point('girder', 15327., .42), point('girder stiff', 18607., .88)])
        frame_cost = lambda k: 35404. if k <= 0 else 34233.
        best, rows, sized, verified = self.run_search(options, frame_cost)
        self.assertEqual(best['bracing'], 'end bays')
        self.assertEqual(sized, [.88])               # only the floor was sized
        self.assertEqual(verified, [])
        self.assertTrue(all(r['status'].startswith('Skipped') for r in rows))

    def test_cheaper_restrained_pair_is_sized_verified_and_adopted(self):
        options = fs.frontier([point('end bays', 14000., 0.), point('girder', 14500., .4), point('girder stiff', 16000., .8)])
        frame_cost = lambda k: 35400. if k <= 0 else (33000. if k < .5 else 32800.)
        best, rows, sized, verified = self.run_search(options, frame_cost)
        self.assertEqual(best['bracing'], 'girder')   # 33000 + 14500 < 35400 + 14000
        self.assertEqual(sized, [.8, .4])             # floor, then the cheap option
        # The floor frame fails with the girder's restraint, so its own frame is sized.
        self.assertEqual(verified, [('girder', .8), ('girder', .4)])
        self.assertEqual(best['total'], 47500.)
        self.assertTrue(rows[-1]['status'].startswith('Skipped'))   # stiff: 16000 + 32800 > 47500

    def test_floor_frame_that_verifies_skips_the_re_size(self):
        options = fs.frontier([point('end bays', 14000., 0.), point('girder', 14500., .4), point('girder stiff', 16000., .8)])
        frame_cost = lambda k: 35400. if k <= 0 else (33000. if k < .5 else 32800.)
        best, rows, sized, verified = self.run_search(options, frame_cost, passes=lambda f, o: True)
        self.assertEqual(sized, [.8])                 # only the floor
        self.assertEqual(verified, [('girder', .8)])
        self.assertEqual((best['bracing'], best['total']), ('girder', 47300.))
        self.assertIn('no re-size needed', rows[0]['status'])

    def test_floor_needing_heavier_bracing_still_sizes_the_option_frame(self):
        # The floor frame passes only with re-sized bracing, so it is not
        # proven least-cost; the option's own frame is sized and compared.
        options = fs.frontier([point('end bays', 14000., 0.), point('girder', 14500., .4), point('girder stiff', 16000., .8)])
        frame_cost = lambda k: 35400. if k <= 0 else (32900. if k < .5 else 32800.)
        best, rows, sized, verified = self.run_search(
            options, frame_cost, passes=lambda f, o: True, resized=lambda f, o: f['k'] > o['restraint'])
        self.assertEqual(sized, [.8, .4])
        self.assertEqual(verified, [('girder', .8), ('girder', .4)])
        self.assertEqual((best['bracing'], best['total']), ('girder', 47400.))   # 32900+14500 < 32800+14500+250

    def test_verified_total_decides_not_the_prediction(self):
        options = fs.frontier([point('end bays', 14000., 0.), point('girder', 14500., .4)])
        frame_cost = lambda k: 35400. if k <= 0 else 34800.
        best, _, _, verified = self.run_search(options, frame_cost, verified_extra=500.)
        self.assertEqual(verified, [('girder', .4)])  # predicted 49300 < 49400, verified 49800
        self.assertEqual(best['bracing'], 'end bays')

    def test_no_restraining_bracing_keeps_the_incumbent(self):
        options = fs.frontier([point('end bays', 14000., 0.)])
        best, rows, sized, _ = self.run_search(options, lambda k: 35400.)
        self.assertEqual((best['bracing'], rows, sized), ('end bays', [], []))


if __name__ == '__main__':
    unittest.main()
