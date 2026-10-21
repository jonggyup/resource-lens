"""Descriptive control impact from exposed placement and accounting, never a forecast."""
from .placement import format_cpus
from .workspaces import allowed_cpus


def impact_lines(sample, plan, kind):
    sample=sample or {}
    lines=['-- Observed impact --']
    if kind=='hardware':
        affected=set(plan['affected'])
        offline=plan['setting']=='online' and plan['value']=='0'
        online={c['id'] for c in plan['before']['cpus'] if c['online']}
        if offline:lines.append('Online CPUs left on host: '+format_cpus(sorted(online-affected)))
        known=[];unknown=0
        for group in sample.get('workloads',[]):
            cpus=allowed_cpus(group)
            if cpus is None:unknown+=1;continue
            if not cpus & affected:continue
            label=group.get('unit') or group['id']
            remaining=cpus & (online-affected)
            known.append(label+(': remaining eligible '+(format_cpus(sorted(remaining)) or 'NONE in sampled set') if offline else ': allowed on affected CPUs'))
        lines.append(f'Visible groups overlapping CPUs: {len(known)}; unknown placement: {unknown}')
        lines.extend(known[:3])
        if len(known)>3:lines.append(f'{len(known)-3} more groups; inspect Applications before applying.')
    else:
        target=plan['target'];limits=target.get('limits',{})
        lines.append('Visible member PIDs: '+(str(len(target['pids'])) if 'pids' in target else 'unknown')+'; descendants are also affected.')
        if kind=='placement':
            lines.append('Parent/online eligible CPUs: '+format_cpus(plan['before']['eligible']))
            lines.append(f"Effective CPU count: {len(plan['before']['effective'])} -> {len(plan['effective'])}")
        if kind != 'placement':
            cpus=allowed_cpus(target)
            lines.append('Sampled effective CPUs: '+(format_cpus(sorted(cpus)) if cpus is not None else 'unknown'))
        if kind == 'disk':
            io_limits=[(a['path'],a.get('io.max')) for a in limits.get('ancestors',[]) if a.get('io.max')]
            lines.extend('Observed io.max '+path+': '+value for path,value in io_limits[:3])
            if not io_limits:lines.append('Ancestor disk limits: not reported; absence does not establish no limit.')
        quota=limits.get('quota_pct')
        lines.append('Observed local/ancestor CPU quota: '+('unknown' if quota is None else f'{quota:g}% (100% = one CPU)'))
        lines.append('Ancestor limits: '+('complete CPU-quota scan' if limits.get('quota_complete') else 'partial or unknown; inspect Limits'))
    if sample.get('timestamp'):
        import time
        lines.append(f"Relationship sample age: {max(0,time.time()-sample['timestamp']):.1f}s; visible groups only.")
    lines.append('Snapshot evidence only; affinity may narrow CPUs. No performance forecast.')
    return lines


def impact_summary(sample, plan, kind):
    if kind == 'hardware':
        affected = set(plan['affected'])
        online = {c['id'] for c in plan['before']['cpus'] if c['online']}
        offline = plan['setting'] == 'online' and plan['value'] == '0'
        groups = [allowed_cpus(g) for g in (sample or {}).get('workloads', [])]
        overlap = [cpus for cpus in groups if cpus is not None and cpus & affected]
        at_risk = sum(not (cpus & (online-affected)) for cpus in overlap) if offline else 0
        return [f'Impact: {len(overlap)} groups overlap; {sum(c is None for c in groups)} unknown; {at_risk} with no sampled CPUs left.',
                '[ Impact ] i: placement evidence and unknowns']
    count = len(plan['effective']) if kind == 'placement' else None
    return [('Impact: '+str(count)+' effective CPUs; child groups are affected.' if count is not None else
             'Impact: applies to this group and descendants; parent limits still apply.'),
            '[ Impact ] i: placement evidence and ancestor limits']
