import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Pair,Evaluation,Comparison,Adoption,Audit
from src.schemas.business import Login,PairInput,EvaluationInput,ComparisonInput,AdoptionInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.comparison import freeze_basis,find_duplicates,duplicate_message,IncompleteBasis
from src.controllers.business import ensure_evaluation,ensure_comparison
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/pairs')
def pairs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Pair).order_by(Pair.id).all()]
@router.post('/pairs')
def create_pair(data:PairInput,u=Depends(editor),s=Depends(db)):
 r=Pair(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'顾问编号已存在，每位顾问仅保留一组前后测')
 audit(s,u,'录入顾问配对 '+r.code);return dump(r)
@router.put('/pairs/{i}')
def edit_pair(i:int,data:PairInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Pair,i)
 if not r:raise HTTPException(404,'配对记录不存在')
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'顾问编号重复')
 audit(s,u,'修订配对 '+str(i));return dump(r)
@router.get('/evaluations')
def evaluations(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Evaluation).order_by(Evaluation.id.desc()).all()]
@router.post('/evaluations')
def evaluate(data:EvaluationInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Pair).order_by(Pair.id).all() if data.team=='全部' or r.team==data.team]
 r=Evaluation(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('paired:evaluations',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交评估 '+str(r.id));return dump(r)
@router.get('/evaluations/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_evaluation(s,i))
@router.get('/evaluations/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_evaluation(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功评估可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="script-paired-{i}.json"'})
@router.get('/comparisons')
def comparisons(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Comparison).order_by(Comparison.id.desc()).all()]
@router.post('/comparisons')
def create_comparison(data:ComparisonInput,u=Depends(editor),s=Depends(db)):
 if data.basis_a==data.basis_b:raise HTTPException(422,'两版对比必须选择两份不同的评估')
 try:
  a=freeze_basis(ensure_evaluation(s,data.basis_a));b=freeze_basis(ensure_evaluation(s,data.basis_b))
 except IncompleteBasis as e:raise HTTPException(422,str(e))
 except HTTPException as e:
  if e.status_code==404:raise HTTPException(422,'所选评估不存在，无法锁定依据')
  raise
 duplicates=find_duplicates(a,b)
 if duplicates:raise HTTPException(422,duplicate_message(duplicates))
 r=Comparison(name=data.name,state='pending',basis={'a':a,'b':b});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('paired:comparisons',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交对比 '+str(r.id));return dump(r)
@router.get('/comparisons/{i}')
def comparison_detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_comparison(s,i))
@router.post('/comparisons/{i}/retry')
def retry_comparison(i:int,u=Depends(editor),s=Depends(db)):
 r=ensure_comparison(s,i)
 if r.state!='failed':raise HTTPException(409,'仅中断或失败的对比可以恢复')
 r.state='pending';r.error='';s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('paired:comparisons',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'重新提交对比 '+str(i));return dump(r)
@router.get('/comparisons/{i}/export')
def export_comparison(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_comparison(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功对比可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="script-compare-{i}.json"'})
@router.get('/adoptions')
def adoptions(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Adoption).order_by(Adoption.id.desc()).all()]
@router.post('/adoptions')
def adoption(data:AdoptionInput,u=Depends(editor),s=Depends(db)):
 if ensure_evaluation(s,data.evaluation_id).state!='done':raise HTTPException(409,'评估完成后才能起草采纳意见')
 r=Adoption(**data.model_dump());s.add(r);s.commit();audit(s,u,'起草采纳意见 '+str(r.id));return dump(r)
@router.post('/adoptions/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Adoption,i)
 if not r:raise HTTPException(404,'采纳意见不存在')
 if r.status!='draft':raise HTTPException(409,'已签核意见不可重复签核')
 r.status='approved';s.commit();audit(s,u,'签核意见 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
