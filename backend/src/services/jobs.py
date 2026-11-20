"""后台任务的具体执行步骤：从冻结依据算到可读结果或失败。"""
import json,os
from pathlib import Path
from src.models.entities import Evaluation,Comparison
from src.services.analysis import evaluate
from src.services.comparison import compare


def _artifact(name,payload):
    directory=Path(os.environ.get('ARTIFACT_DIR','/artifacts'));directory.mkdir(parents=True,exist_ok=True)
    (directory/name).write_text(json.dumps(payload,ensure_ascii=False))


def process_evaluation(s,row):
    if not row or row.state!='queued':return False
    snapshot=row.result['snapshot'];row.state='running';s.commit()
    try:
        row.result={**evaluate(snapshot),'snapshot':snapshot};row.state='done'
        _artifact(str(row.id)+'.json',row.result)
    except Exception as e:row.state='failed';row.error=str(e)
    s.commit();return True


def process_comparison(s,row):
    if not row or row.state!='pending':return False
    basis=row.basis;row.state='running';s.commit()
    try:
        result=compare(basis['a'],basis['b']);row.result=result;row.state='done';row.error=''
        _artifact('compare-'+str(row.id)+'.json',result)
    except ValueError as e:row.state='failed';row.error=str(e)
    except Exception as e:row.state='failed';row.error='计算异常：'+str(e)
    s.commit();return True
