import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resourcelens.energy import EnergyMeter, summarize, lines
from resourcelens.profiling import capture, compare, ProfileView


class EnergyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.now=0.
        self.zone=self.root/'devices/virtual/powercap/intel-rapl/intel-rapl:0'
        self.zone.mkdir(parents=True)
        (self.zone/'name').write_text('package-0')
        (self.zone/'energy_uj').write_text('1000000')
        (self.zone/'max_energy_range_uj').write_text('1000000000')

    def meter(self):
        return EnergyMeter(self.root,clock=lambda:self.now)

    def test_domains_are_deduplicated_but_not_summed(self):
        aliases=self.root/'class/powercap';aliases.mkdir(parents=True)
        (aliases/'intel-rapl:0').symlink_to(self.zone)
        child=self.zone/'intel-rapl:0:0';child.mkdir()
        (child/'name').write_text('core');(child/'energy_uj').write_text('500000')
        meter=self.meter();a=meter.sample()
        self.now=1
        (self.zone/'energy_uj').write_text('11000000')
        (child/'energy_uj').write_text('4500000')
        b=meter.sample();result=summarize([a,b],2,'job')
        self.assertEqual(len(result['domains']),2)
        parent=next(d for d in result['domains'] if d['label']=='package-0')
        self.assertEqual(parent['observed_joules'],10)
        self.assertEqual(parent['average_watts'],10)
        self.assertEqual(parent['joules_per_work_unit'],5)
        self.assertNotIn('total_joules',result)

    def test_wrap_reset_gaps_never_create_invented_energy(self):
        meter=self.meter();samples=[meter.sample()]
        self.now=1;(self.zone/'energy_uj').write_text('100')
        samples.append(meter.sample())
        self.assertEqual(samples[-1]['domains'][0]['status'],'gap')
        self.now=2;(self.zone/'energy_uj').write_text('1000100')
        samples.append(meter.sample())
        result=summarize(samples,10,'request')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['domains'][0]['observed_joules'],1)
        self.assertIsNone(result['domains'][0]['joules_per_work_unit'])
        self.now=10
        self.assertEqual(meter.sample()['domains'][0]['status'],'gap')

    def test_missing_invalid_and_reappearing_sensor(self):
        meter=self.meter();a=meter.sample()
        (self.zone/'energy_uj').unlink();self.now=1;b=meter.sample()
        self.assertEqual(b['domains'][0]['status'],'unavailable')
        (self.zone/'energy_uj').write_text(str(2**80));self.now=2
        self.assertIsNone(meter.sample()['domains'][0]['counter_uj'])
        (self.zone/'energy_uj').write_text('2000000');self.now=3;c=meter.sample()
        self.assertEqual(c['domains'][0]['status'],'warming_up')
        self.assertEqual(summarize([a,b,c])['status'],'unavailable')

    def test_hwmon_units_label_and_source(self):
        chip=self.root/'class/hwmon/hwmon0';chip.mkdir(parents=True)
        (chip/'name').write_text('test-energy')
        (chip/'energy1_input').write_text('1000000')
        (chip/'energy1_label').write_text('socket0')
        meter=self.meter();meter.sample();self.now=1
        (chip/'energy1_input').write_text('3000000')
        d=next(d for d in meter.sample()['domains'] if d['source']=='hwmon')
        self.assertEqual(d['label'],'test-energy: socket0')
        self.assertEqual(d['watts'],2)

    def test_absence_and_unreadable_discovery_are_not_zero(self):
        result=EnergyMeter(self.root/'empty').sample()
        self.assertEqual(result['status'],'unavailable')
        summary=summarize([result])
        self.assertIn('Unknown energy is not zero',' '.join(lines(summary)))
        self.assertEqual(summary['domains'],[])

    def test_capture_records_energy_and_normalized_work(self):
        meter=self.meter()
        original=meter.sample
        def sample():
            self.now+=.25
            (self.zone/'energy_uj').write_text(str(int(self.now*4000000)))
            return original()
        with patch('resourcelens.energy.EnergyMeter',return_value=meter),patch.object(meter,'sample',side_effect=sample):
            r=capture(self.root/'session',pid=os.getpid(),duration=.3,perf=False,energy=True,work_units=2,work_unit='job')
        self.assertEqual(r['energy']['status'],'complete')
        self.assertEqual(r['energy']['domains'][0]['average_watts'],4)
        self.assertTrue(any(k.endswith('joules_per_work_unit') for k in r['metrics']))
        self.assertEqual((self.root/'session/session.json').stat().st_mode & 0o777,0o600)
        self.assertIn('no process',r['energy']['scope'])

    def test_compare_rejects_energy_unit_and_host_mismatch(self):
        base=dict(schema=1,status='complete',label='same',target={'pid':1},environment={},profile={'status':'disabled'},
                  metrics={'energy.fake.joules':1,'energy.fake.joules_per_work_unit':.5},
                  energy={'status':'complete','host':'host1','work_unit':'job','domains':[]})
        for name in ('a','b'):
            (self.root/name).mkdir();(self.root/name/'session.json').write_text(json.dumps(base))
        base['energy']['work_unit']='request'
        (self.root/'b/session.json').write_text(json.dumps(base))
        r=compare(self.root/'a',self.root/'b')
        self.assertNotIn('energy.fake.joules_per_work_unit',r['metrics'])
        self.assertIn('energy.fake.joules',r['metrics'])
        base['energy']['host']='host2'
        (self.root/'b/session.json').write_text(json.dumps(base))
        self.assertFalse(compare(self.root/'a',self.root/'b')['metrics'])

    def test_invalid_work_counts_and_no_implicit_energy_capture(self):
        for count in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):capture(self.root/'bad',pid=os.getpid(),energy=True,work_units=count)
        with self.assertRaises(ValueError):capture(self.root/'bad',pid=os.getpid(),work_units=1)
        with patch('resourcelens.profiling.capture',return_value={}) as job:
            view=ProfileView({'pid':os.getpid()},choose=True)
            view.key(ord('3'),24)
            # Wait for the background call, without relying on thread scheduling.
            import time
            deadline=time.monotonic()+1
            while not job.called and time.monotonic()<deadline:time.sleep(.001)
            self.assertTrue(job.call_args.kwargs['energy'])
            self.assertFalse(job.call_args.kwargs['perf'])
            self.assertFalse(job.call_args.kwargs['scheduler'])

    def test_sensor_replacement_is_a_gap_even_with_increasing_value(self):
        meter=self.meter();meter.sample()
        replacement=self.zone/'replacement';replacement.write_text('2000000')
        replacement.replace(self.zone/'energy_uj');self.now=1
        self.assertEqual(meter.sample()['domains'][0]['status'],'gap')

    def test_cancelled_capture_cannot_claim_normalized_energy(self):
        import threading
        stop=threading.Event();stop.set()
        with patch('resourcelens.energy.EnergyMeter',return_value=self.meter()):
            result=capture(self.root/'cancel',pid=os.getpid(),duration=10,perf=False,energy=True,work_units=1,stop=stop)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['energy']['status'],'partial')
        self.assertIsNone(result['energy']['domains'][0]['joules_per_work_unit'])
        self.assertFalse(any(k.startswith('energy.') for k in result['metrics']))
