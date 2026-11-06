"""Asynchronous Sessions browser and isolated offline viewers."""
import curses
from pathlib import Path
import queue
import threading
import time

from .profiling import compare_runs
from .readability import wrap_lines
from .recording import Replay
from .redaction import export_summary
from .sessions import discover,open_entry,import_path,add_outcome,presentation_sample,profile_lines


def replay_view(replay,index):
    from .ui import View
    view=View()
    view.replay=replay
    view.investigation.events=list(replay.events)
    for sample in replay.window(index):view.update(presentation_sample(sample))
    return view


def comparison_lines(result):
    lines=['SAVED RUN COMPARISON - descriptive',
           'Matched recorded metric names/units; different target lifetimes are separate runs.',
           *result['warnings'],'','-- Capture status and counter coverage --']
    for side,rows in result.get('capture_coverage',{}).items():
        for index,row in enumerate(rows):
            observed,requested=row['observed_seconds'],row['requested_seconds']
            coverage=observed/requested if observed is not None and requested else None
            lines.append(f"{side} run {index+1}: {row['status']}; "+(f"{observed:.2f}s / {requested:g}s ({100*coverage:.0f}% coverage"+
                (', incomplete)' if coverage<.8 else ')') if coverage is not None else 'counter coverage unknown'))
    lines+=['','-- Metrics: mean, spread and repeats --']
    for name,row in result['metrics'].items():
        lines.append(name+(' ['+result.get('outcome_units',{}).get(name[8:],'units in name')+']' if name.startswith('outcome.') else ''))
        for side in ('baseline','candidate'):
            value=row[side]
            lines.append(f"  {side}: {value['mean']:.4g}; n={value['count']}; min/max={value['min']:.4g}/{value['max']:.4g}; " +
                         (f"stdev={value['stdev']:.4g}" if value['stdev'] is not None else 'spread unknown (one run)'))
        lines.append(f"  Change: {row['delta']:+.4g}; "+(f"{row['change_percent']:+.2f}%" if row['change_percent'] is not None else 'zero baseline; relative change undefined'))
    if not result['metrics']:lines.append('No compatible numeric metrics were recorded on both sides.')
    lines+=['','-- Configuration differences --',str(result['environment_changes']),
            'Capture status/coverage and application outcomes must be considered separately.',
            'x Export redacted summary | Esc back']
    return lines


class SessionBrowser:
    def __init__(self,state,extra=()):
        self.state=Path(state);self.extra=tuple(extra)
        self.entries=[];self.warnings=[];self.selected=0;self.scroll=0
        self.stage='list';self.query='';self.message='';self.profile=None;self.current=None
        self.baseline=None;self.comparison=None;self.record_view=None
        self.pending=None;self.results=queue.SimpleQueue();self.epoch=0;self.closed=False;self.version=0
        self.regions=[];self.play_deadline=0
        self.deferred_close=[]
        self.start('scan',lambda:discover(self.state,self.extra))

    @staticmethod
    def dispose(value):
        if isinstance(value,Replay):value.close()
        elif hasattr(value,'replay') and isinstance(value.replay,Replay):value.replay.close()

    def start(self,kind,function):
        if self.pending:return
        token=self.epoch;self.pending=kind;self.version+=1
        def worker():
            try:value,error=function(),None
            except Exception as exc:value,error=None,str(exc)
            if self.closed or token!=self.epoch:
                self.dispose(value);value=None
                for replay in self.deferred_close:replay.close()
                self.deferred_close=[]
            self.results.put((token,kind,value,error))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        while not self.results.empty():
            token,kind,value,error=self.results.get();self.pending=None;self.version+=1
            if token!=self.epoch or self.closed:self.dispose(value);continue
            if error:self.message=error;continue
            self.message=''
            if kind=='scan':
                previous=self.entries[self.selected]['path'] if self.entries else None
                self.entries,self.warnings=value['entries'],value['warnings']
                self.selected=next((i for i,e in enumerate(self.entries) if e['path']==previous),0)
            elif kind=='import':
                self.extra=tuple(dict.fromkeys(self.extra+(value['path'],)));self.stage='list'
                self.start('scan',lambda:discover(self.state,self.extra))
            elif kind in ('open','seek'):
                if hasattr(value,'replay'):
                    old=self.record_view
                    if kind=='seek' and old:
                        for key in ('page','scroll','browser','application_mode','application_process','application_process_manual',
                                    'workload_id','workload_identity','workload_detail','filter','workload_filter','app_tab','overlay'):
                            setattr(value,key,getattr(old,key))
                        value.replay_playing=old.replay_playing
                        value.investigation.window_seconds=old.investigation.window_seconds
                        value.investigation.selected=min(old.investigation.selected,len(value.investigation.events)-1)
                    self.record_view=value;self.stage='recording'
                    self.message='Re-analysis uses current rule versions; captured findings remain in recorded events.'
                else:
                    self.profile=value;self.stage='profile';self.scroll=0
                    self.current=dict(self.current,digest=value.get('_artifact_digest'))
            elif kind=='compare':self.comparison=value;self.stage='comparison';self.scroll=0
            elif kind=='outcome':self.profile=value;self.stage='profile';self.message='Outcome saved separately; original capture retained.'
            elif kind=='export':self.message='Saved redacted summary: '+value
        if self.record_view:
            view=self.record_view;before=repr(view.modal);view.poll_actions()
            if repr(view.modal)!=before:self.version+=1
            if not self.pending and (view.replay_delta or view.replay_playing and time.monotonic()>=self.play_deadline):
                replay=view.replay
                index=max(0,min(len(replay.offsets)-1,replay.index+view.replay_delta+(1 if view.replay_playing else 0)))
                view.replay_delta=0;self.play_deadline=time.monotonic()+1
                view.replay_playing=False if index==len(replay.offsets)-1 else view.replay_playing
                self.start('seek',lambda:replay_view(replay,index))

    def close(self):
        self.closed=True;self.epoch+=1
        if self.record_view:
            if self.pending:self.deferred_close.append(self.record_view.replay)
            else:self.record_view.replay.close()

    def leave_recording(self):
        self.record_view.replay_playing=False
        if self.pending:
            self.epoch+=1;self.deferred_close.append(self.record_view.replay)
        else:self.record_view.replay.close()
        self.record_view=None;self.stage='list';self.version+=1

    def selected_entry(self):
        if not self.entries:return None
        self.selected=max(0,min(len(self.entries)-1,self.selected))
        return self.entries[self.selected]

    def open(self,row):
        if not row:return
        self.current=dict(row);self.message=''
        def load():
            evidence=open_entry(row)
            if isinstance(evidence,Replay):
                try:return replay_view(evidence,len(evidence.offsets)-1)
                except Exception:evidence.close();raise
            if row['kind']=='profile_set':raise ValueError('Choose one profile to open; a repeat set can be selected as a baseline/candidate')
            return evidence
        self.start('open',load)

    def compare(self,row):
        if not self.baseline:self.message='Choose a baseline with b first.';return
        if not row or row['kind']=='recording' or self.baseline['kind']=='recording':
            self.message='Run comparisons use profiles/repeat sets. Recordings have event windows in Timeline.';return
        baseline,candidate=dict(self.baseline),dict(row)
        def load(row):
            value=open_entry(row)
            return value if isinstance(value,list) else [value]
        def calculate():
            a,b=load(baseline),load(candidate)
            result=compare_runs(a,b)
            result['capture_coverage']={side:[dict(status=r['status'],requested_seconds=r.get('requested_seconds'),
                observed_seconds=(r['samples'][-1]['elapsed']-r['samples'][0]['elapsed'] if len(r.get('samples',[]))>1 else None))
                for r in rows] for side,rows in (('baseline',a),('candidate',b))}
            if any(r['observed_seconds'] is None or not r['requested_seconds'] or r['observed_seconds']<.8*r['requested_seconds']
                   for rows in result['capture_coverage'].values() for r in rows):
                result['warnings'].append('Counter coverage is incomplete/unknown in at least one run; resource changes remain descriptive.')
            result['outcomes']={side:[dict(values=r.get('outcomes',{}),metadata=r.get('outcome_metadata',{})) for r in rows]
                                for side,rows in (('baseline',a),('candidate',b))}
            result['sources']=[{key:row[key] for key in ('path','name','kind')} for row in (baseline,candidate)]
            return result
        self.start('compare',calculate)

    def export(self):
        if self.stage=='comparison':value=dict(comparison=self.comparison)
        elif self.stage=='profile':
            value={key:self.profile.get(key) for key in ('label','target','status','metrics','requested_seconds','elapsed_seconds','outcomes','outcome_metadata','environment')}
            value['source']={key:self.current[key] for key in ('path','name','kind')}
            value['captured_findings']=self.profile.get('capture_context',{}).get('findings',[])
        else:self.message='Open a profile/comparison to export; recordings use R in replay.';return
        self.start('export',lambda:export_summary(value,self.state/'reports'))

    def key(self,key,height):
        self.poll()
        if self.record_view:
            if key==ord('L'):
                self.leave_recording();return True
            if not self.record_view.key(key,height):
                self.leave_recording()
            self.version+=1;return True
        if self.stage in ('import','outcome'):
            if key==27:self.stage='profile' if self.stage=='outcome' else 'list';self.query=''
            elif key in (10,13) and not self.pending:
                text=self.query
                if self.stage=='import':self.start('import',lambda:import_path(text,self.state))
                else:
                    row=dict(self.current);self.start('outcome',lambda:add_outcome(row,text))
            elif key in (curses.KEY_BACKSPACE,127,8):self.query=self.query[:-1]
            elif 32<=key<127 and len(self.query)<4096:self.query+=chr(key)
            self.version+=1;return True
        if key in (27,ord('q')):
            if self.stage=='list':self.close();return False
            self.stage='list';self.scroll=0;self.version+=1;return True
        if key==ord('i'):self.stage='import';self.query=''
        elif key==ord('r') and self.stage=='list':self.start('scan',lambda:discover(self.state,self.extra))
        elif key==ord('b'):
            row=self.current if self.stage=='profile' else self.selected_entry()
            if row and row['kind']!='recording':self.baseline=dict(row);self.message='Baseline: '+row['name']
            else:self.message='Choose a saved profile or repeat set as the baseline.'
        elif key==ord('c'):self.compare(self.current if self.stage=='profile' else self.selected_entry())
        elif key==ord('o') and self.stage=='profile':self.stage='outcome';self.query=''
        elif key==ord('x'):self.export()
        elif self.stage=='list':
            if key in (10,13):self.open(self.selected_entry())
            elif key in (curses.KEY_DOWN,ord('j')):self.selected=min(len(self.entries)-1,self.selected+1)
            elif key in (curses.KEY_UP,ord('k')):self.selected=max(0,self.selected-1)
            elif key==curses.KEY_HOME:self.selected=0
            elif key==curses.KEY_END:self.selected=max(0,len(self.entries)-1)
            elif key in (curses.KEY_NPAGE,curses.KEY_PPAGE):self.selected=max(0,min(len(self.entries)-1,self.selected+(height-8)*(1 if key==curses.KEY_NPAGE else -1)))
        elif key in (curses.KEY_DOWN,ord('j'),curses.KEY_NPAGE):self.scroll+=height-7 if key==curses.KEY_NPAGE else 1
        elif key in (curses.KEY_UP,ord('k'),curses.KEY_PPAGE):self.scroll=max(0,self.scroll-(height-7 if key==curses.KEY_PPAGE else 1))
        self.version+=1;return True

    def mouse(self,x,y,state,height):
        if self.record_view:return self.record_view.mouse(x,y,state)
        if state&(curses.BUTTON1_CLICKED|curses.BUTTON1_PRESSED):
            for row,start,end,key in self.regions:
                if row==y and start<=x<end:
                    if isinstance(key,tuple):self.selected=key[1];self.open(self.selected_entry())
                    else:self.key(key,height)
                    return
        if state&curses.BUTTON4_PRESSED:self.key(curses.KEY_UP,height)
        elif state&getattr(curses,'BUTTON5_PRESSED',0):self.key(curses.KEY_DOWN,height)

    def draw(self,screen,width,height):
        from .ui import draw,put
        self.regions=[]
        if self.record_view:
            try:draw(screen,self.record_view,1)
            except (ValueError,TypeError,KeyError,IndexError,AttributeError) as exc:
                self.message='Recorded presentation is incomplete: '+str(exc)
                self.record_view.replay.close();self.record_view=None;self.stage='list';screen.erase()
            else:
                put(screen,0,1,'SAVED RECORDING / OFFLINE | L: Sessions',curses.A_BOLD)
                put(screen,height-2,1,'Current rules for re-analysis | J: captured findings | L: Sessions',limit=width-2)
                screen.refresh();return
        screen.erase();put(screen,0,1,'RESOURCE LENS / SESSIONS',curses.A_BOLD)
        if self.stage=='list':
            put(screen,2,1,'Recordings, profiles and repeated runs',limit=width-2)
            capacity=max(1,height-8);offset=max(0,self.selected-capacity+1)
            for i,row in enumerate(self.entries[offset:offset+capacity],offset):
                kind={'profile':'Profile','profile_set':'Repeated runs','recording':'Recording'}[row['kind']]
                label=('> ' if i==self.selected else '  ')+kind+' / '+row['name']
                put(screen,4+i-offset,1,label,curses.A_REVERSE if i==self.selected else 0,width-2)
                self.regions.append((4+i-offset,1,width-1,('entry',i)))
            if not self.entries:put(screen,4,1,'No saved evidence found. i: import a file/directory',limit=width-2)
            footer=[('Enter Open',10),('i Import',ord('i')),('b Baseline',ord('b')),('c Compare',ord('c')),('r Refresh',ord('r')),('Esc Back',27)]
        else:
            if self.stage=='import':lines=['IMPORT LOCAL EVIDENCE','Path to recording.jsonl, session.json, profile/repeat directory:',self.query+'_',
                'Import remembers this path and keeps the original files.','Enter: import | Esc: cancel']
            elif self.stage=='outcome':lines=['MEASURED APPLICATION OUTCOME','Only enter measurements for this capture window:',
                str([self.profile.get('started'),self.profile.get('elapsed_seconds')]),
                'Format: name | unit | value | measurement source','Example: p99 | ms | 12.5 | load generator log',self.query+'_',
                'Saved alongside this profile; the original capture stays unchanged.','Enter: save | Esc: cancel']
            elif self.stage=='comparison':lines=comparison_lines(self.comparison)
            else:lines=profile_lines(self.profile)
            wrapped=wrap_lines(lines,max(10,width-3));capacity=max(1,height-6)
            if self.stage in ('import','outcome'):self.scroll=max(0,len(wrapped)-capacity)
            self.scroll=min(self.scroll,max(0,len(wrapped)-capacity))
            for i,line in enumerate(wrapped[self.scroll:self.scroll+capacity]):put(screen,2+i,1,line,limit=width-2)
            footer=[('Esc Back',27),('b Baseline',ord('b')),('c Compare',ord('c')),('o Outcome',ord('o')),('x Export',ord('x'))]
            if self.stage in ('import','outcome'):footer=[('Enter Confirm',10),('Esc Cancel',27)]
        put(screen,height-3,1,('Working: '+self.pending if self.pending else self.message or '; '.join(self.warnings)),limit=width-2)
        put(screen,height-2,1,'Offline / read-only',limit=width-2)
        x=1
        for label,key in footer:
            if x+len(label)>=width:continue
            put(screen,height-1,x,label,curses.A_REVERSE);self.regions.append((height-1,x,x+len(label),key));x+=len(label)+2
        screen.refresh()
