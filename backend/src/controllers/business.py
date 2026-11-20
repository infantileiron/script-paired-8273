from fastapi import HTTPException
from src.models.entities import Evaluation,Comparison
from src.services.business import dump
def ensure_evaluation(s,i):
 row=s.get(Evaluation,i)
 if not row:raise HTTPException(404,'评估任务不存在')
 return row
def ensure_comparison(s,i):
 row=s.get(Comparison,i)
 if not row:raise HTTPException(404,'对比任务不存在')
 return row
