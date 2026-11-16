"""Frontier optimiser search logic, with stand-ins for the 2D and 3D solvers.

The full optimiser on a real preset takes minutes; run it with
``frontier_sizing.optimise(window)`` (see BRACING_ANALYSIS.md).

Run:  python frontier_sizing_regression.py
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import unittest

from portalcalc.sizing import frontier as fs


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
        incumbent = {'bracing': options[0]['option'], 'verified': True, 'frame_cost': frame_cost(0.),
                     'total': frame_cost(0.)+options[0]['bracing_cost']}
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

    def test_floor_dearer_than_unrestrained_sizes_each_option(self):
        # Canopy test project pattern: the frame sized for the stiffest bracing
        # ($55,115) cost more than the unrestrained frame ($54,807), so the floor
        # would wrongly skip a cheaper pair at intermediate restraint.
        options = fs.frontier([point('end bays', 9000., 0.), point('girder', 9100., .4), point('girder stiff', 9500., .8)])
        frame_cost = lambda k: 54807. if k <= 0 else (54300. if k < .5 else 55115.)
        best, rows, sized, verified = self.run_search(options, frame_cost)
        self.assertEqual(sized[:2], [.8, .4])          # floor, then the least-restraint probe
        self.assertEqual(verified, [('girder', .4)])   # the floor frame is never checked
        self.assertEqual((best['bracing'], best['total']), ('girder', 63400.))
        self.assertIn('bound from the frame for the least restraint', rows[0]['status'])
        self.assertTrue(rows[-1]['status'].startswith('Skipped'))   # 9500 + 54300 > 63400

    def test_restraint_that_never_reduces_the_frame_stops_after_the_probe(self):
        # Canopy test project: every restrained frame cost more than the
        # unrestrained one, so two 2D sizes settle it (not one per option).
        options = fs.frontier([point('end bays', 9008., 0.), point('girder', 9085., .21), point('stiff', 9962., .28)])
        frame_cost = lambda k: 54807. if k <= 0 else (54993. if k < .25 else 55115.)
        best, rows, sized, verified = self.run_search(options, frame_cost)
        self.assertEqual((sized, verified), ([.28, .21], []))
        self.assertEqual(best['bracing'], 'end bays')
        self.assertEqual(len(rows), 1)
        self.assertIn('Restraint does not reduce the frame', rows[0]['status'])

    def test_no_restraining_bracing_keeps_the_incumbent(self):
        options = fs.frontier([point('end bays', 14000., 0.)])
        best, rows, sized, _ = self.run_search(options, lambda k: 35400.)
        self.assertEqual((best['bracing'], rows, sized), ('end bays', [], []))


class OptionTests(unittest.TestCase):
    """Layout options and sizing starts offered to the 3D bracing frontier."""

    def three_sided(self, bays=5):
        from portalcalc.bracing.layout import BracingLayout, LayoutGeometry
        g = LayoutGeometry.create(30000., bays*8000., 8000., 7.5, 'Gable')
        return {'layout': BracingLayout(g, blocked=[f'right_wall/bay/{b}' for b in range(bays)])}

    def test_full_roof_seed_braces_every_roof_panel_with_end_bay_walls(self):
        from portalcalc.bracing.layout_search import FULL_ROOF, full_roof_seeds
        context = self.three_sided()
        (reason, selected), *_ = list(full_roof_seeds(context))
        roof = {p for p in context['layout'].available if p.startswith('roof/')}
        self.assertEqual(reason, FULL_ROOF)
        self.assertTrue(roof <= selected)
        self.assertEqual({p for p in selected if p.startswith('left_wall/')}, {'left_wall/bay/0', 'left_wall/bay/4'})
        self.assertFalse(any(p.startswith('right_wall/') for p in selected))

    def test_full_roof_seeds_meet_the_braced_bay_spacing_on_long_sheds(self):
        from portalcalc.bracing.layout_search import full_roof_seeds
        from portalcalc.bracing.spacing import spacing_assessment
        context = self.three_sided(12)
        seeds = list(full_roof_seeds(context))
        self.assertEqual(len(seeds), 3)
        for _, selected in seeds:
            self.assertTrue(spacing_assessment(context['layout'], 5, selected)['passes'])
        self.assertGreater(len({p for p in seeds[0][1] if p.startswith('left_wall/')}), 2)

    def test_leaner_full_roof_seeds_brace_fewer_full_bays(self):
        from portalcalc.bracing.layout_search import full_roof_seeds
        context = self.three_sided(7)
        seeds = dict(full_roof_seeds(context))
        full_bays = lambda panels: {b for b in range(7) if all(f'roof/bay/{b}/strip/{j}' in panels for j in range(4))}
        self.assertEqual(full_bays(seeds['Full roof bracing in every bay']), set(range(7)))
        anchors = seeds['Full roof bracing in the anchor bays with roof girders at both eaves']
        self.assertTrue({0, 6} <= full_bays(anchors) < set(range(7)))
        self.assertTrue(all(f'roof/bay/{b}/strip/{j}' in anchors for b in range(7) for j in (0, 3)))
        self.assertEqual(full_bays(seeds['Full roof bracing in alternate bays']), {0, 2, 4, 6})

    def test_light_start_uses_the_lightest_priced_rod_in_every_brace_group(self):
        from portalcalc.bracing.schedule import rod_options
        rods = rod_options()
        angle = {'label': 'EA 75x75x6', 'area_mm2': 864.}
        candidates = {g: [rods[2], angle, rods[0], rods[1]] for g in ('roof_brace', 'wall_brace', 'end_brace', 'canopy_brace')}
        chosen = {'strut': 'S', 'end_post': 'P', 'roof_brace': angle, 'wall_brace': rods[2], 'end_brace': angle,
                  'canopy_brace': angle}
        light = fs.light_start(chosen, candidates)
        self.assertEqual({g: light[g]['label'] for g in candidates}, {g: '16 DIA ROD' for g in candidates})
        self.assertEqual((light['strut'], light['end_post']), ('S', 'P'))
        no_canopy = fs.light_start({k: v for k, v in chosen.items() if k != 'canopy_brace'}, candidates)
        self.assertNotIn('canopy_brace', no_canopy)

    def test_m28_turnbuckle_price_makes_30_mm_rod_available(self):
        from portalcalc.bracing.schedule import rod_options
        self.assertEqual(rod_options()[-1]['fitting_unit_cost'], 40.)


class SwayNodeTests(unittest.TestCase):
    def test_global_sway_is_measured_on_the_building_not_canopy_edges(self):
        from portalcalc.bracing.analysis import building_sway_node
        self.assertTrue(building_sway_node('Portal 1, roof x=29.5685 m', 8000.))
        self.assertFalse(building_sway_node('Portal 1, right canopy edge', 7633.))
        self.assertFalse(building_sway_node('front_end ground x=0 m', 0.))


class ProgressTests(unittest.TestCase):
    def test_fraction_only_moves_forward_within_phases(self):
        seen = []
        report = fs.Progress(lambda message, fraction: seen.append((message, fraction)))
        report.phase('frame')
        report('half', .5)
        report.phase('bracing')
        report.span(.4, 1.)
        report('girders', .5)
        report.phase('frame')          # an earlier phase never moves the bar back
        fractions = [f for _, f in seen]
        self.assertEqual(fractions, sorted(fractions))
        # Positions follow the phase boundaries, whatever they are retimed to.
        frame_start, frame_end = fs.PHASES['frame'][2:]
        bracing_start, bracing_end = fs.PHASES['bracing'][2:]
        self.assertAlmostEqual(seen[1][1], frame_start+.5*(frame_end-frame_start))
        self.assertAlmostEqual(seen[4][1], bracing_start+(.4+.3)*(bracing_end-bracing_start))
        self.assertTrue(seen[1][0].startswith('Step 1 of 4: Sizing the unrestrained 2D frame\nhalf'))

    def test_message_only_callbacks_still_work(self):
        messages = []
        report = fs.Progress(lambda message: messages.append(message))
        report.phase('pairs', 'Checking')
        self.assertEqual(messages, ['Step 4 of 4: Checking frame + bracing pairs\nChecking'])

    def test_cancel_inside_a_2d_auto_size_waits_for_a_safe_point(self):
        def cancel(message, fraction):
            raise fs.AnalysisError('cancelled')
        report = fs.Progress(cancel)
        report('inside auto-size', .2, safe=False)      # held, auto-size continues
        with self.assertRaisesRegex(fs.AnalysisError, 'cancelled'):
            report('next phase')

    def test_2d_auto_size_steps_drive_the_bar_and_listener_is_restored(self):
        seen = []

        class Window:
            optimisation_progress_listener = None

            def __init__(self):
                self.steps = [(1, 'Preparing', None), (6, 'Exact neighbour refinement', 4), (None, 'Local search', 9), (12, 'Done', 20)]

        window = Window()
        report = fs.Progress(lambda message, fraction: seen.append(round(fraction, 4)))
        report.phase('floor')
        original = fs.shared._size_frame
        fs.shared._size_frame = lambda w: [w.optimisation_progress_listener(*s) for s in w.steps] and {'verified': True}
        try:
            report.size_frame(window, '2D')
        finally:
            fs.shared._size_frame = original
        start, end = fs.PHASES['floor'][2:]
        self.assertEqual(seen, [round(start+f*(end-start), 4) for f in (0., 0., 1/12, .5, .5, 1.)])
        self.assertIsNone(window.optimisation_progress_listener)


class StateRestoreTests(unittest.TestCase):
    """capture/restore keep the truss panel count only when asked (exact)."""

    class Window:
        def __init__(self):
            self.preset, self.solved_with = {'depth': 1300}, None
            self.auto_web_profile_assignments = {'WEB': {1: '50*2 SHS'}}
            self.auto_web_joint_doubler_sides, self.auto_web_assignment_basis = {}, {}
            self._panel_count_override, self._suppress_panel_topology_search = 32, False
            self._panel_topology_cache_signature, self._panel_topology_cache_count = 'sig', 32

        def collect_preset_data(self):
            return dict(self.preset)

        def apply_preset_data(self, data):
            # As MainWindow: clears the panel count and topology suppression.
            self.preset, self._panel_count_override, self._suppress_panel_topology_search = dict(data), None, False

        def generate_and_solve(self):
            self.solved_with = (self._panel_count_override, self._suppress_panel_topology_search)

    def test_exact_restore_keeps_the_sized_panel_count(self):
        window = self.Window()
        state = fs.shared.capture_state(window)
        window._panel_count_override = 24
        fs.shared.restore_state(window, state, exact=True)
        self.assertEqual(window.solved_with, (32, True))      # solved holding 32 panels
        self.assertEqual(window._panel_count_override, 32)
        self.assertFalse(window._suppress_panel_topology_search)

    def test_warm_start_restore_uses_the_default_panel_count(self):
        window = self.Window()
        fs.shared.restore_state(window, fs.shared.capture_state(window))
        self.assertEqual(window.solved_with, (None, False))

    def test_a_held_suppression_survives_the_preset_reset(self):
        window = self.Window()
        state = fs.shared.capture_state(window)
        window._suppress_panel_topology_search = True        # e.g. the optimiser's run-wide hold
        fs.shared.restore_state(window, state, exact=True)
        self.assertTrue(window._suppress_panel_topology_search)


if __name__ == '__main__':
    unittest.main()
