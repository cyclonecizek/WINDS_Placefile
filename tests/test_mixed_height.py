import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import ksc_winds_placefile as w

class MixedHeightTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    def row(self, height, **extra):
        row = dict(reading_datetime=self.now.isoformat(), tower='0002SE', prevailing='SE',
                    hgt=height, lat=28.4, lon=-80.6, avg_dir_1min=130, avg_spd_1min=7,
                    peak_spd_1min=9, tmp=None, dp=None, rh=None)
        row.update(extra)
        return row

    def test_independent_fields_and_exact_54ft_wind(self):
        rows = [self.row(6, tmp=80), self.row(12, dp=70), self.row(20, rh=72),
                self.row(54, tmp=90, dp=78, rh=80)]
        rows[0]['avg_spd_1min'] = 2
        rows[-1]['avg_spd_1min'] = 25
        obs = w.parse_api(rows)
        fields = w.pick_lowest_thermo_fields(obs)
        self.assertEqual([fields[k].height_ft for k in ['temp_f', 'dew_f', 'rh']], [6, 12, 20])
        output = w.generate_54ft_lowest_thermo(obs, {'0002': (28.4, -80.6)}, self.now)
        self.assertIn('130 deg @ 25 kt', output)
        self.assertIn('Temperature: 80.0 F @ 6 ft', output)
        self.assertIn('Dew Point: 70.0 F @ 12 ft', output)
        self.assertIn('Relative Humidity: 72% @ 20 ft', output)
        self.assertIn('Text: 31, 18, 1, "72%"', output)

    def test_missing_winds_do_not_use_lower_or_higher_winds(self):
        obs = w.parse_api([self.row(12, tmp=80), self.row(200)])
        output = w.generate_54ft_lowest_thermo(obs, {'0002': (28.4, -80.6)}, self.now)
        self.assertNotIn('Icon:', output)
        self.assertIn('54 ft wind: N/A', output)
        self.assertIn('Text: -30, 18, 1, "80"', output)

    def test_latest_time_only_and_invalid_humidity(self):
        old = self.row(6, tmp=80, dp=70, rh=50)
        old['reading_datetime'] = (self.now - timedelta(minutes=1)).isoformat()
        obs = w.parse_api([old, self.row(54, tmp=9999, rh=999), self.row(12, rh=101)])
        latest = w.latest_time_rows(obs, '0002')
        self.assertTrue(all(o is None for o in w.pick_lowest_thermo_fields(latest).values()))
        output = w.generate_54ft_lowest_thermo(obs, {'0002': (28.4, -80.6)}, self.now)
        self.assertNotIn('Text:', output.replace('Text: 31, -10, 1, "9"', ''))
        self.assertIn('Temperature: N/A', output)

    def test_same_height_prefers_upwind_side_and_zero_humidity_is_valid(self):
        rows = [self.row(6, tmp=80, rh=0), self.row(6, tmp=90, rh=60)]
        rows[1]['tower'] = '0002NW'
        selected = w.pick_lowest_thermo_fields(w.parse_api(rows))
        self.assertEqual(selected['temp_f'].temp_f, 80)
        self.assertEqual(selected['rh'].rh, 0)

if __name__ == '__main__':
    unittest.main()
