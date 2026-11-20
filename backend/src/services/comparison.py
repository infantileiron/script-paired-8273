"""两版话术评估对比：分别聚合完整配对，再按重叠顾问估计两版相关。"""
import hashlib,json,math
import numpy as np
from scipy import stats

LEVEL=0.95
MIN_OVERLAP_FOR_CI=3


class IncompleteBasis(ValueError):
    """评估尚未完成，或冻结结果缺快照/校验失败。"""


class DuplicateEvidence(ValueError):
    def __init__(self,duplicates):
        self.duplicates=duplicates
        super().__init__(duplicate_message(duplicates))


def checksum(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _fmt(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and float(value).is_integer():
        return str(int(value))
    return str(value)


def duplicate_message(duplicates,limit=10):
    parts=[f"{d['code']}（前测{_fmt(d['before'])}/后测{_fmt(d['after'])}）" for d in duplicates[:limit]]
    tail=f" 等共{len(duplicates)}位" if len(duplicates)>limit else ''
    return ('两版复用了同一前后得分：'+'、'.join(parts)+tail+
            '。同一顾问的同一份测量不能作为两版独立证据，请先区分两版抽检记录后再提交')


def freeze_basis(row):
    """把一份已完成评估的完整快照冻结为对比依据；不做任何补零。"""
    if row is None:
        raise IncompleteBasis('评估不存在')
    if row.state!='done':
        raise IncompleteBasis(f'评估 #{row.id} 尚未完成（当前 {row.state}），两份都完成后才能锁定依据')
    result=row.result
    if not isinstance(result,dict):
        raise IncompleteBasis(f'评估 #{row.id} 没有完整结果，拒绝以半份依据提交对比')
    rows=result.get('snapshot')
    if not isinstance(rows,list) or not rows:
        raise IncompleteBasis(f'评估 #{row.id} 缺少冻结配对快照，拒绝以半份依据提交对比')
    seen=set()
    for item in rows:
        if not isinstance(item,dict) or not item.get('code'):
            raise IncompleteBasis(f'评估 #{row.id} 的冻结快照缺少顾问编号')
        if item['code'] in seen:
            raise IncompleteBasis(f"评估 #{row.id} 中顾问 {item['code']} 重复，无法作为对比依据")
        seen.add(item['code'])
        for key in ('before','after'):
            value=item.get(key)
            if value is not None and (not isinstance(value,(int,float)) or isinstance(value,bool)):
                raise IncompleteBasis(f"评估 #{row.id} 中顾问 {item['code']} 的{key}不是数值")
    digest=checksum(rows)
    if result.get('checksum')!=digest:
        raise IncompleteBasis(f'评估 #{row.id} 结果校验失败（可能仍在写入），本次不接收半份依据')
    return {'evaluation_id':row.id,'name':row.name,'team':row.team,'checksum':digest,'snapshot':rows}


def _verify(basis):
    rows=basis.get('snapshot') if isinstance(basis,dict) else None
    if not isinstance(rows,list) or not rows:
        raise IncompleteBasis('冻结依据缺少完整配对快照')
    if checksum(rows)!=basis.get('checksum'):
        raise IncompleteBasis('冻结依据校验失败，无法继续计算')


def _kept(rows):
    kept=[];excluded=0
    for item in rows:
        if item.get('before') is None or item.get('after') is None:
            excluded+=1;continue
        kept.append({'code':item['code'],'before':float(item['before']),'after':float(item['after'])})
    return kept,excluded


def find_duplicates(basis_a,basis_b):
    """同一顾问在两版中前后得分完全相同，即视为复用同一条测量。"""
    left,_=_kept(basis_a['snapshot']);right,_=_kept(basis_b['snapshot'])
    by_left={r['code']:r for r in left};by_right={r['code']:r for r in right}
    duplicates=[]
    for code in sorted(set(by_left)&set(by_right)):
        a=by_left[code];b=by_right[code]
        if a['before']==b['before'] and a['after']==b['after']:
            duplicates.append({'code':code,'before':a['before'],'after':a['after']})
    return duplicates


def _contribution(values,overlap,exclusive,total):
    overlap_mean=float(np.mean([values[c] for c in overlap])) if overlap else None
    exclusive_mean=float(np.mean([values[c] for c in exclusive])) if exclusive else None
    return {
        'overlap_share':len(overlap)/total if total else 0.0,
        'overlap_mean':overlap_mean,
        'overlap_contribution':(len(overlap)/total*overlap_mean) if overlap else 0.0,
        'exclusive_share':len(exclusive)/total if total else 0.0,
        'exclusive_mean':exclusive_mean,
        'exclusive_contribution':(len(exclusive)/total*exclusive_mean) if exclusive else 0.0,
        'mean':float(np.mean(list(values.values()))) if values else None,
    }


def compare(basis_a,basis_b):
    """difference = B 版平均改善 − A 版平均改善。

    两版各自使用全部完整配对；单边顾问只进入各自均值。
    重叠顾问提供同一顾问两版差值的协方差，用于把两边变化放进同一区间。
    """
    _verify(basis_a);_verify(basis_b)
    if basis_a.get('evaluation_id')==basis_b.get('evaluation_id'):
        raise ValueError('两版对比必须使用两份不同的评估')
    rows_a,excluded_a=_kept(basis_a['snapshot'])
    rows_b,excluded_b=_kept(basis_b['snapshot'])
    if len(rows_a)<2 or len(rows_b)<2:
        raise ValueError('每一版至少需要2位完整配对顾问，缺失记录不会被补零')
    duplicates=find_duplicates(basis_a,basis_b)
    if duplicates:
        raise DuplicateEvidence(duplicates)

    da={r['code']:r['after']-r['before'] for r in rows_a}
    db={r['code']:r['after']-r['before'] for r in rows_b}
    n_a=len(da);n_b=len(db)
    mean_a=float(np.mean(list(da.values())));mean_b=float(np.mean(list(db.values())))
    var_a=float(np.var(list(da.values()),ddof=1));var_b=float(np.var(list(db.values()),ddof=1))
    sd_a=math.sqrt(var_a);sd_b=math.sqrt(var_b)
    difference=mean_b-mean_a
    overlap=sorted(set(da)&set(db));m=len(overlap)
    a_only=sorted(set(da)-set(db));b_only=sorted(set(db)-set(da))

    covariance=None;variance=var_a/n_a+var_b/n_b
    if m>=2:
        covariance=float(np.cov([da[c] for c in overlap],[db[c] for c in overlap],ddof=1)[0,1])
        variance-=2*m/(n_a*n_b)*covariance

    se=df=t_value=p_value=ci=None
    ci_usable=False;method=''
    if m==0:
        method='independent'
        if variance>0:
            se=math.sqrt(variance)
            df=(variance**2)/(((sd_a**2/n_a)**2/(n_a-1))+((sd_b**2/n_b)**2/(n_b-1)))
            t_value=difference/se
            p_value=float(2*stats.t.sf(abs(t_value),df))
            margin=float(stats.t.ppf(1-(1-LEVEL)/2,df))*se
            ci=[difference-margin,difference+margin]
            ci_usable=True
            ci_note='两版没有共同顾问，区间按独立样本（Welch 自由度）计算，不涉及同一顾问的相关项。'
        else:
            ci_note='方差估计非正，区间不可用。'
    elif m>=MIN_OVERLAP_FOR_CI:
        method='correlated-overlap'
        if variance>0:
            se=math.sqrt(variance)
            df=m-1
            t_value=difference/se
            p_value=float(2*stats.t.sf(abs(t_value),df))
            margin=float(stats.t.ppf(1-(1-LEVEL)/2,df))*se
            ci=[difference-margin,difference+margin]
            ci_usable=True
            ci_note=(f'重叠顾问 {m} 位，区间已纳入同一顾问两版得分变化的相关项；'
                     f'自由度按重叠人数取 m−1={df}。')
        else:
            ci_note='重叠协方差估计导致方差非正，区间不可用。'
    else:
        method='insufficient-overlap'
        ci_note=(f'重叠顾问仅 {m} 位，少于{MIN_OVERLAP_FOR_CI}位，无法稳定估计两版相关；'
                 '区间不可用，点估计仅供描述。')
    contributions={
        'a':_contribution(da,overlap,a_only,n_a),
        'b':_contribution(db,overlap,b_only,n_b),
    }
    overlap_delta=float(np.mean([db[c]-da[c] for c in overlap])) if overlap else None

    warnings=[]
    if m!=0:
        warnings.append('重叠顾问把同一人的两版变化一起纳入区间；单边顾问只进入各自版本均值。')
    if m==0:
        warnings.append('两版无共同顾问，无法评估同一顾问在两版下的相关性；两组客户结构差异需业务核对。')
    if 0<m<MIN_OVERLAP_FOR_CI:
        warnings.append(f'重叠顾问仅 {m} 位，不足以稳定估计相关；不能据此判定两版差异方向。')
    if a_only or b_only:
        warnings.append(f'单边顾问保留：仅 A 版 {len(a_only)} 位、仅 B 版 {len(b_only)} 位。')
    if basis_a.get('team') and basis_b.get('team') and basis_a.get('team')!=basis_b.get('team'):
        warnings.append(f"两版评估范围不同（{basis_a.get('team')} / {basis_b.get('team')}），客户结构差异需另行核对。")
    warnings.append('前后观测同时受客户结构与季节影响，差值不能直接解释为话术因果。')

    if ci_usable:
        if ci[0]>0:
            conclusion=(f'按95%区间，B 版平均改善比 A 版高 {difference:.2f} 分，区间 [{ci[0]:.2f}, {ci[1]:.2f}] 不含0；'
                        '仍需结合客户结构与季节核对，不能直接解释为话术因果。')
        elif ci[1]<0:
            conclusion=(f'按95%区间，B 版平均改善比 A 版低 {abs(difference):.2f} 分，区间 [{ci[0]:.2f}, {ci[1]:.2f}] 不含0；'
                        '仍需结合客户结构与季节核对，不能直接解释为话术因果。')
        else:
            conclusion=(f'B 版与 A 版平均改善相差 {difference:.2f} 分，95%区间 [{ci[0]:.2f}, {ci[1]:.2f}] 含0；'
                        '现有证据不足以判断两版差异。')
    else:
        conclusion=(f'两版可分别报告平均改善（A {mean_a:.2f} 分，B {mean_b:.2f} 分），描述性差值为 {difference:.2f} 分；'
                    f'但{ci_note}不能据此判定差异显著或方向，建议补齐同一顾问在两版下的完整配对后重新对比。')

    return {
        'level':LEVEL,
        'method':method,
        'n_a':n_a,'n_b':n_b,'n_overlap':m,'a_only':len(a_only),'b_only':len(b_only),
        'excluded_a':excluded_a,'excluded_b':excluded_b,
        'mean_a':mean_a,'mean_b':mean_b,'difference':difference,
        'sd_a':sd_a,'sd_b':sd_b,'covariance':covariance,
        'se':se,'df':df,'t':t_value,'p_value':p_value,
        'ci':ci,'ci_usable':ci_usable,'ci_note':ci_note,
        'overlap_mean_delta':overlap_delta,
        'contributions':contributions,
        'overlap_codes':overlap,'a_only_codes':a_only,'b_only_codes':b_only,
        'basis':{
            'a':{'evaluation_id':basis_a.get('evaluation_id'),'name':basis_a.get('name'),'team':basis_a.get('team'),'checksum':basis_a.get('checksum')},
            'b':{'evaluation_id':basis_b.get('evaluation_id'),'name':basis_b.get('name'),'team':basis_b.get('team'),'checksum':basis_b.get('checksum')},
        },
        'warnings':warnings,
        'conclusion':conclusion,
    }
