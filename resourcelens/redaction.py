"""Per-export aliases preserve relationships without copying raw identifiers."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time


IDENTIFIERS={'path','cgroup','exact_cgroup','unit','hostname','host','name','label','scope','scope_id',
             'id','identity','executable','provenance','pid','ppid','inode','device','starttime','start_ticks','invocation','deduplication_key'}


def redact(value,context=None):
    aliases={}
    def alias(item):
        key=(type(item).__name__,str(item))
        if key not in aliases:aliases[key]='identifier-'+str(len(aliases)+1)
        return aliases[key]
    def collect(item):
        if isinstance(item,dict):
            for key,child in item.items():
                measurement_unit=key=='unit' and isinstance(child,str) and not child.endswith(('.service','.scope','.slice'))
                if key in IDENTIFIERS and not measurement_unit and type(child) in (str,int) and child!='':alias(child)
                if key in ('outcomes','outcome_metadata','outcome_units') and isinstance(child,dict):
                    for name in child:alias(name)
                collect(child)
        elif isinstance(item,list) or isinstance(item,tuple):
            for child in item:collect(child)
    collect(context);collect(value)
    substitutions=sorted(((original,replacement) for (kind,original),replacement in aliases.items()
                          if kind=='str' and len(original)>=2),key=lambda pair:len(pair[0]),reverse=True)
    def clean(item,key=None):
        if key=='unit' and isinstance(item,str) and not item.endswith(('.service','.scope','.slice')):return item
        if key in IDENTIFIERS and type(item) in (str,int) and item!='':return alias(item)
        if isinstance(item,dict):return {clean(k):clean(v,k) for k,v in item.items()}
        if isinstance(item,(list,tuple)):return [clean(v) for v in item]
        if isinstance(item,str):
            if key in ('status','domain','action','unit','format','verification','method','relevance'):return item
            # Apply replacements to the original string once; alias names must
            # not themselves be rewritten by a short recorded process name.
            import re
            if substitutions:
                table=dict(substitutions)
                return re.sub('|'.join(r'(?<![\w])'+re.escape(k)+r'(?![\w])' for k,_ in substitutions),lambda m:table[m[0]],item)
        return item
    return clean(deepcopy(value))


def write_new_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temporary=tempfile.mkstemp(prefix='.summary-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as file:
            json.dump(value,file,allow_nan=False,indent=2)
            file.write('\n');file.flush();os.fsync(file.fileno())
        os.link(temporary,path)  # New exports never replace an existing file.
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return str(path)


def export_summary(value,directory,context=None):
    path=Path(directory)/('session-summary-'+str(time.time_ns())+'.json')
    return write_new_json(path,dict(format='resource-lens-session-summary',version=1,redacted=True,
                                    summary=redact(value,context)))
