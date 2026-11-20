import os,time,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Evaluation,Comparison
from src.services.jobs import process_evaluation,process_comparison
r=redis.Redis.from_url(os.environ['REDIS_URL'])
with Session() as recovery:
 for pending in recovery.query(Evaluation).filter(Evaluation.state.in_(['queued','running'])).all():
  pending.state='queued';r.rpush('paired:evaluations',pending.id)
 for pending in recovery.query(Comparison).filter(Comparison.state.in_(['pending','running'])).all():
  pending.state='pending';r.rpush('paired:comparisons',pending.id)
 recovery.commit()
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=r.blpop(['paired:evaluations','paired:comparisons'],timeout=3)
  if not item:continue
  queue,raw=item[0].decode(),int(item[1])
  with Session() as s:
   if queue=='paired:comparisons':process_comparison(s,s.get(Comparison,raw))
   else:process_evaluation(s,s.get(Evaluation,raw))
 except Exception:logging.exception('worker');time.sleep(2)
