import sys
import unittest
from pathlib import Path
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ksc_winds_placefile as w

class ApiTests(unittest.TestCase):
    def row(self, **changes):
        r = dict(reading_datetime="2026-10-16T09:49:00", tower="0002SE",
                 prevailing="SE", hgt=54, lat=28.4, lon=-80.6,
                 avg_dir_1min=130, avg_spd_1min=7, peak_spd_1min=9,
                 tmp=81.7, dp=999, rh=None)
        r.update(changes)
        return r

    def test_units_missing_and_side(self):
        o = w.parse_api([self.row()])[0]
        self.assertEqual((o.site, o.side_measured, o.side_upwind), ("0002", "SE", "SE"))
        self.assertEqual((o.height_ft, o.avg_spd, o.temp_f), (54, 7, 81.7))
        self.assertIsNone(o.dew_f)
        self.assertIsNone(o.rh)
        self.assertEqual(w.observation_coords([o], {})["0002"], (28.4, -80.6))

    def test_rain_only_and_pad_ids(self):
        obs = w.parse_api([self.row(hgt=None), self.row(tower="LC39BNE")])
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].site, "LC39BNE")

    def test_latest_and_preferred_side(self):
        old, new, preferred = w.parse_api([
            self.row(reading_datetime="2026-10-16T09:48:00"),
            self.row(tower="0002NW"), self.row()])
        self.assertIs(w.choose_side([old, new]), new)
        self.assertIs(w.choose_side([new, preferred]), preferred)
        self.assertEqual(len(w.latest_time_rows([old, new, preferred], "0002")), 2)

    def test_calm_preserved_and_invalid_response(self):
        o = w.parse_api([self.row(avg_spd_1min=0, avg_dir_1min=0)])[0]
        self.assertEqual(o.avg_spd, 0)
        with self.assertRaises(ValueError):
            w.parse_api({"error": "failed"})
        with self.assertRaises(ValueError):
            w.parse_api([])

if __name__ == "__main__":
    unittest.main()
