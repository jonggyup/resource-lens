import unittest

from resourcelens.comparison import compare_windows, comparison_lines


def sample(stamp, value=10, elapsed=1, inode=7, scope='boot'):
    return dict(timestamp=stamp, elapsed_s=elapsed, collection_scope={'id':scope},
                workloads=[dict(id='/job', inode=inode, device=1)], values={'Rate B/s':value, 'RAM bytes':value})


def metrics(item, target=None):
    return item['values']


def event(**kwargs):
    return dict(timestamp=10, started_at=10, verified_at=12, status='verified', scope_id='boot',
                target={'id':'/job','inode':7,'device':1}, **kwargs)


class ComparisonTests(unittest.TestCase):
    def report(self, samples, marker=None, seconds=5, events=()):
        return compare_windows(samples, marker or event(), metrics, seconds, events)

    def test_unequal_intervals_are_weighted_and_transition_is_excluded(self):
        samples = [sample(5), sample(6,10), sample(9,30,3), sample(10,50),
                   sample(11,999), sample(12,999), sample(13,20), sample(17,40,4)]
        report = self.report(samples)
        before, after = report['before']['metrics'], report['after']['metrics']
        self.assertEqual(before['Rate B/s']['mean'], 30)
        self.assertEqual(after['Rate B/s']['mean'], 36)
        self.assertEqual(after['Rate B/s']['covered_seconds'], 5)
        self.assertTrue(after['Rate B/s']['complete'])
        self.assertEqual(after['Rate B/s']['min'],20)
        self.assertEqual(after['Rate B/s']['max'],40)
        self.assertEqual(report['transition_end'], 12)

    def test_interval_crossing_boundary_is_clipped(self):
        samples = [sample(4), sample(8,20,4), sample(13,30,5)]
        report = self.report(samples)
        self.assertEqual(report['before']['metrics']['Rate B/s']['covered_seconds'], 5)
        self.assertEqual(report['after']['metrics']['Rate B/s']['covered_seconds'], 1)

    def test_gaps_missing_nonfinite_and_unknown_durations_reduce_coverage(self):
        samples = [sample(5), sample(6,10), sample(9,None,None), sample(10,float('nan')),
                   sample(12), sample(13,0), sample(17,None,None)]
        report = self.report(samples)
        self.assertEqual(report['before']['metrics']['Rate B/s']['covered_seconds'],1)
        self.assertFalse(report['before']['metrics']['Rate B/s']['complete'])
        self.assertEqual(report['after']['metrics']['Rate B/s']['mean'],0)
        self.assertEqual(report['after']['metrics']['Rate B/s']['coverage'],.2)
        for invalid in (True, 10**400):
            self.assertFalse(self.report([sample(12,invalid),sample(13,invalid)])['after']['metrics'])

    def test_gauges_use_left_hold_with_a_bound_on_missing_observations(self):
        report = self.report([sample(12,8), sample(17,80,5)])
        ram = report['after']['metrics']['RAM bytes']
        self.assertEqual(ram['mean'],8)
        self.assertEqual(ram['covered_seconds'],2.5)
        self.assertFalse(ram['complete'])

    def test_replacement_scope_changes_and_unknown_identity_do_not_cross(self):
        for key,value in (('inode',8),('scope','new')):
            samples = [sample(12), sample(13), sample(14,**{key:value}), sample(15)]
            report = self.report(samples)
            self.assertEqual(report['after']['metrics']['Rate B/s']['covered_seconds'],1)
        samples = [sample(12),sample(13)]
        samples[0]['workloads'] = []
        self.assertFalse(self.report(samples)['after']['metrics'])

    def test_unconfirmed_has_no_post_window_and_zero_baseline_has_no_ratio(self):
        marker = event(); marker['verified_at']=None; marker['status']='unconfirmed'
        report = self.report([sample(12),sample(13)], marker)
        self.assertFalse(report['post_available'])
        self.assertFalse(report['after']['metrics'])
        marker = event()
        report = self.report([sample(5,0),sample(10,0,5),sample(12),sample(17,10,5)], marker)
        self.assertIn('relative change undefined',' '.join(comparison_lines(report)))

    def test_settling_and_subsequent_control_attempts_exclude_contaminated_time(self):
        marker = event(settling_s=1)
        samples = [sample(i) for i in range(5,19)]
        previous = dict(timestamp=7, completed_at=8, domain='cpu')
        following = dict(timestamp=15, domain='disk')
        report = self.report(samples, marker, events=(previous, marker, following))
        self.assertEqual(report['before']['metrics']['Rate B/s']['covered_seconds'],2)
        self.assertEqual(report['after']['start'],13)
        self.assertEqual(report['after']['metrics']['Rate B/s']['covered_seconds'],2)
        self.assertEqual(report['after']['metrics']['Rate B/s']['coverage'],.4)

    def test_duplicate_samples_and_large_elapsed_cannot_double_coverage(self):
        samples = [sample(12),sample(13,20,20),sample(13,20,20),sample(14,40,20)]
        report = self.report(samples)
        self.assertEqual(report['after']['metrics']['Rate B/s']['covered_seconds'],2)
        self.assertEqual(report['after']['metrics']['Rate B/s']['mean'],30)

    def test_new_device_metric_has_no_interval_start_and_backwards_verification_has_no_post(self):
        samples=[sample(12),sample(13)]
        samples[1]['values']['New disk B/s']=999
        self.assertNotIn('New disk B/s',self.report(samples)['after']['metrics'])
        marker=event();marker['verified_at']=9
        self.assertFalse(self.report(samples,marker)['post_available'])
        report=self.report([sample(12),sample(13),sample(12.5),sample(14)])
        self.assertFalse(report['after']['metrics'])
        self.assertIn('clock moved backwards',' '.join(report['limitations']))


if __name__ == '__main__': unittest.main()
