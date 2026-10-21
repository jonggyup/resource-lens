"""Bounded, on-demand /proc memory inspection. No physical-page identity inferred."""
import bisect
import curses
import os
from pathlib import Path
import re
import threading
import time

HEADER = re.compile(r'^([0-9a-f]+)-([0-9a-f]+)\s+([rwxps-]{4})\s+([0-9a-f]+)\s+([0-9a-f]+):([0-9a-f]+)\s+(\d+)\s*(.*)$')
FIELDS = ('Rss','Pss','Private_Clean','Private_Dirty','Shared_Clean','Shared_Dirty',
          'Pss_Anon','Pss_File','Pss_Shmem','Swap','SwapPss','Shared_Hugetlb','Private_Hugetlb')


def identity(directory):
    value=(directory/'stat').read_text()
    return int(value[value.rindex(')')+2:].split()[19])


def bounded_read(path, limit):
    with path.open('r',errors='replace') as file:
        value=file.read(limit+1)
    if len(value)>limit:
        raise ValueError('file exceeds inspection limit')
    return value


def totals(text):
    values={}
    for line in text.splitlines():
        parts=line.split()
        if len(parts)==3 and parts[0].rstrip(':') in FIELDS and parts[1].isdigit() and parts[2]=='kB':
            values[parts[0][:-1]]=int(parts[1])*1024
    return values


def mappings(text):
    result=[]
    for line in text.splitlines():
        match=HEADER.match(line)
        if match:
            start,end,flags,offset,major,minor,inode,name=match.groups()
            result.append(dict(key=(int(major,16),int(minor,16),int(inode)),
                               offset=int(offset,16),length=int(end,16)-int(start,16),flags=flags,name=name))
    return result


def backing_index(rows):
    ranges={}
    for row in rows:
        if row['key'][2]:
            ranges.setdefault(row['key'],[]).append((row['offset'],row['offset']+row['length']))
    result={}
    for key,intervals in ranges.items():
        merged=[]
        for start,end in sorted(intervals):
            if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
            else:merged.append((start,end))
        result[key]=([x[0] for x in merged],[x[1] for x in merged])
    return result


def common_backings(index, rows):
    found={}
    for row in rows:
        ranges=index.get(row['key'])
        if ranges:
            starts,ends=ranges
            i=bisect.bisect_left(starts,row['offset']+row['length'])-1
            if i>=0 and ends[i]>row['offset']:
                found[row['key']]=row['name'] or '(unnamed file-backed mapping)'
    return found


def inspect_memory(target, candidates, root='/proc', stop=None, peer_limit=256, seconds=2):
    root=Path(root);directory=root/str(target['pid'])
    stop=stop or threading.Event()
    deadline=time.monotonic()+seconds
    result=dict(pid=target['pid'],starttime=target.get('starttime'),name=target['name'],timestamp=time.time(),
                totals={},threads=[],peers=[],warnings=[],checked=0,unreadable=0,partial=False)
    def current():return identity(directory)==target.get('starttime')
    def expired():return stop.is_set() or time.monotonic()>deadline
    if not current():raise ValueError('Selected process exited or PID was reused; select it again.')
    try:result['totals']=totals(bounded_read(directory/'smaps_rollup',65536))
    except (OSError,ValueError) as exc:result['warnings'].append('Resident/shared totals unavailable: '+str(exc))
    try:
        with os.scandir(directory/'task') as tasks:
            for entry in tasks:
                if len(result['threads'])>=1024 or expired():
                    result['partial']=True;break
                if not entry.name.isdigit():continue
                try:
                    fields={k:v.strip() for line in bounded_read(Path(entry.path)/'status',16384).splitlines() if ':' in line for k,v in [line.split(':',1)]}
                    if int(fields.get('Tgid','-1'))==target['pid']:
                        result['threads'].append(dict(tid=int(entry.name),name=fields.get('Name','?'),state=fields.get('State','?')))
                except (OSError,ValueError):result['partial']=True
    except OSError as exc:result['warnings'].append('Thread list unavailable: '+str(exc))
    result['threads'].sort(key=lambda t:t['tid'])
    try:selected_maps=mappings(bounded_read(directory/'maps',2*1024*1024))
    except (OSError,ValueError) as exc:
        selected_maps=[];result['warnings'].append('Mapping relationships unavailable: '+str(exc))
    index=backing_index(selected_maps)
    shared_index=backing_index([m for m in selected_maps if m['flags'].endswith('s')])
    consumed=0
    for peer in candidates:
        if not index:break
        if peer['pid']==target['pid']:continue
        if result['checked']>=peer_limit or consumed>=32*1024*1024 or expired():
            result['partial']=True;break
        result['checked']+=1
        peer_dir=root/str(peer['pid'])
        try:
            if identity(peer_dir)!=peer.get('starttime'):raise ValueError('changed identity')
            text=bounded_read(peer_dir/'maps',min(2*1024*1024,32*1024*1024-consumed));consumed+=len(text)
            rows=mappings(text)
            common=common_backings(index,rows)
            shared=common_backings(shared_index,[m for m in rows if m['flags'].endswith('s')])
            if identity(peer_dir)!=peer.get('starttime'):raise ValueError('changed identity')
            if common:
                result['peers'].append(dict(pid=peer['pid'],starttime=peer.get('starttime'),name=peer['name'],
                    common_count=len(common),shared_count=len(shared),
                    examples=(["shared-mode: "+name for name in shared.values()]+
                              ["common backing: "+name for key,name in common.items() if key not in shared])[:3]))
        except (OSError,ValueError):
            result['unreadable']+=1
            result['partial']=True
    if not current():raise ValueError('Selected process changed identity during inspection; results discarded.')
    result['peers'].sort(key=lambda p:(-p['shared_count'],-p['common_count'],p['pid']))
    return result


class MemorySharingView:
    tabs=('Allocation','Threads','Peer processes')
    def __init__(self,target,candidates,root='/proc'):
        self.target=dict(target);self.candidates=tuple(candidates)
        self.root=root;self.tab=0;self.scroll=0;self.result=None;self.error=None;self.regions=[]
        self.stop=threading.Event()
        def run():
            try:
                result=inspect_memory(self.target,self.candidates,self.root,self.stop)
                if not self.stop.is_set():self.result=result
            except Exception as exc:
                if not self.stop.is_set():self.error=str(exc)
        threading.Thread(target=run,daemon=True).start()

    def key(self,key,height):
        if key in (27,ord('q'),curses.KEY_F10):self.stop.set();return False
        if key in (9,ord(']'),curses.KEY_RIGHT):self.tab=(self.tab+1)%3;self.scroll=0
        elif key in (curses.KEY_BTAB,ord('['),curses.KEY_LEFT):self.tab=(self.tab-1)%3;self.scroll=0
        elif key in (curses.KEY_DOWN,ord('j')):self.scroll+=1
        elif key in (curses.KEY_UP,ord('k')):self.scroll=max(0,self.scroll-1)
        elif key==curses.KEY_NPAGE:self.scroll+=max(1,height-8)
        elif key==curses.KEY_PPAGE:self.scroll=max(0,self.scroll-max(1,height-8))
        elif key==curses.KEY_HOME:self.scroll=0
        elif key==curses.KEY_END:self.scroll=10**9
        return True

    def lines(self,width):
        from .ui import size,bar
        if self.error:return [self.error,'Esc returns; select the process again if it restarted.']
        if self.result is None:return ['Inspecting memory in the background... Esc returns immediately.']
        result=self.result;values=result['totals']
        if self.tab==0:
            private=sum(values[k] for k in ('Private_Clean','Private_Dirty')) if all(k in values for k in ('Private_Clean','Private_Dirty')) else None
            shared=sum(values[k] for k in ('Shared_Clean','Shared_Dirty')) if all(k in values for k in ('Shared_Clean','Shared_Dirty')) else None
            rss=values.get('Rss')
            lines=['-- Memory in RAM --',
                   'Resident memory is in RAM; virtual reservation is separate.',
                   'RSS '+size(rss)+' | PSS '+size(values.get('Pss'))+' | private resident '+size(private),
                   'Shared resident mappings '+size(shared),
                   'Private '+bar(100*private/rss if rss and private is not None else None,min(30,max(8,width-15))),
                   'Shared  '+bar(100*shared/rss if rss and shared is not None else None,min(30,max(8,width-15))),
                   '', '-- Reading these values --',
                   'RSS counts resident mappings. PSS divides shared pages among their users.',
                   'Private resident = Private_Clean + Private_Dirty (USS-style estimate).',
                   'Shared counters may include multiple mappings within one process.',
                   'Do not add thread RSS: threads in a process share one address space.',
                   '', '-- Memory breakdown --',
                   'PSS categories: anonymous '+size(values.get('Pss_Anon'))+' | file '+size(values.get('Pss_File'))+' | shmem '+size(values.get('Pss_Shmem')),
                   'Swap '+size(values.get('Swap'))+' | proportional swap '+size(values.get('SwapPss')),
                   'Hugetlb: shared '+size(values.get('Shared_Hugetlb'))+' | private '+size(values.get('Private_Hugetlb')),
                   'Hugetlb counters are separate; RSS/PSS may exclude these pages.',
                   '', '-- Measurement limits --',
                   'Physical page IDs, DIMM placement and exact shared bytes between processes are unavailable.']
        elif self.tab==1:
            lines=[f"{len(result['threads'])} observed threads in TGID {result['pid']}: one shared address space.",
                   'Stacks/TLS are per-thread regions within that address space; resident totals are not per-thread.','']
            lines += [f"TID {t['tid']:<8} {t['name']} | {t['state']}" for t in result['threads']]
        else:
            lines=['COMMON FILE-BACKED MAPPINGS — not proof of identical resident RAM pages',
                   'Matches require device + inode + overlapping file offsets, not just the filename.',
                   'Shared-mode = both mappings use MAP_SHARED; either side may have nonresident pages.',
                   'Private mappings can diverge through copy-on-write. Anonymous fork/KSM peers are not identified.',
                   f"Peers checked {result['checked']} | unreadable/exited {result['unreadable']} | {'PARTIAL bounded scan' if result['partial'] else 'candidate scan complete'}",'']
            for p in result['peers']:
                lines += [f"PID {p['pid']} {p['name']} | common objects {p['common_count']} | shared-mode objects {p['shared_count']}",*['  '+n for n in p['examples']]]
            if not result['peers']:lines.append('No matching visible peers found; this does not prove there is no shared memory.')
        return lines+(['PARTIAL: scan limits or unreadable/exited entries; absence is not proof of no sharing.'] if result['partial'] else [])+['',*result['warnings'], 'Inspection is a point-in-time, non-atomic observation; r refreshes.']

    def draw(self,screen,width,height):
        from .readability import draw_text, wrap_lines
        from .ui import put
        put(screen,2,1,f"MEMORY SHARING / PID {self.target['pid']} {self.target['name']}",curses.A_BOLD)
        self.regions=[];x=1
        for tab,name in enumerate(self.tabs):
            label=' '+name+' '
            put(screen,3,x,label,curses.A_REVERSE if self.tab==tab else curses.A_BOLD)
            self.regions.append((x,x+len(label),3,tab));x+=len(label)+1
        if self.result:put(screen,4,1,'Observed '+time.strftime('%H:%M:%S',time.localtime(self.result['timestamp']))+' | r refresh | Esc back')
        lines=wrap_lines(self.lines(width-3),width-3)
        capacity=max(1,height-7);self.scroll=min(self.scroll,max(0,len(lines)-capacity))
        for i,line in enumerate(lines[self.scroll:self.scroll+capacity]):draw_text(screen,5+i,1,line)
