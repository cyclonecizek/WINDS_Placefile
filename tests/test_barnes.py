import sys,unittest,math
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import ksc_winds_placefile as w
import ksc_winds_barnes_divergence as b
class BarnesTests(unittest.TestCase):
 def test_api_precision_height_freshness_and_preferred_side(self):
  now=datetime.now(timezone.utc)
  def row(**extra):
   value=dict(reading_datetime=now.isoformat(),tower='0002SE',prevailing='SE',hgt=54,lat=28.5,lon=-80.6,avg_spd_1min=12.34,avg_dir_1min=123.45);value.update(extra);return value
  obs=w.parse_api([row(),row(tower='0002NW',avg_spd_1min=30),row(tower='0003',hgt=12),row(tower='0004',reading_datetime=(now-timedelta(minutes=6)).isoformat()),row(tower='0005',avg_spd_1min=9999)])
  stations=b.stations_from_observations(obs,w.observation_coords(obs,{}),now)
  self.assertEqual(len(stations),1);self.assertEqual(stations[0].speed_kt,12.34);self.assertEqual(stations[0].direction_deg,123.45)
  self.assertAlmostEqual(stations[0].u,-12.34*b.KT_TO_MPS*math.sin(math.radians(123.45)))
 def test_divergence_sign_and_units(self):
  nx=ny=5;dx=dy=500
  u=[[i*dx*1e-4 for i in range(nx)] for j in range(ny)]
  v=[[j*dy*2e-4 for i in range(nx)] for j in range(ny)]
  mask=[[True]*nx for j in range(ny)]
  self.assertAlmostEqual(b.compute_divergence(u,v,mask,dx,dy)[2][2],3)
  self.assertAlmostEqual(b.compute_divergence([[-x for x in row] for row in u],[[-x for x in row] for row in v],mask,dx,dy)[2][2],-3)
 def test_insufficient_stations_omit_contours(self):
  text,diag=b.build_placefile([]);self.assertEqual(diag['status'],'insufficient_stations');self.assertNotIn('Line:',text)
