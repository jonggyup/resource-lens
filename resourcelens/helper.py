"""Optional one-shot root helper: exact unit allowlist, opaque server-side receipts.

Install as a root-owned isolated zipapp. It cannot execute caller-supplied commands,
write arbitrary files, modify hardware, or operate outside the administrator's policy.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from .controls import ControlError, ControlSession
from .disk_controls import DiskSession


def trusted(path):
    path = Path(path)
    if not path.is_absolute():
        raise ControlError('Helper/policy paths must be absolute')
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ControlError('Helper/policy and parents must be root-owned, without symlinks or group/other writes')
    return path


def dispatch(request, uid, policy_path=Path('/etc/resource-lens/helper.json'), state=Path('/run/resource-lens-helper')):
    policy_path = trusted(policy_path)
    if policy_path.stat().st_size > 65536:
        raise ControlError('Policy too large')
    allowed = json.loads(policy_path.read_text()).get(str(uid), [])
    if not isinstance(allowed, list) or not all(isinstance(unit,str) for unit in allowed):
        raise ControlError('Invalid helper policy')
    if request.get('action') not in ('prepare','apply','undo'):
        raise ControlError('Unsupported helper action')
    if not state.exists():
        state.mkdir(mode=0o700)
    trusted(state)
    if state.stat().st_mode & 0o077:
        raise ControlError('Helper receipt directory must be private (0700)')
    action = request['action']
    if action == 'prepare':
        domain = request.get('domain')
        target = request.get('target', {})
    else:
        token = request.get('token','')
        if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{48}',token):
            raise ControlError('Invalid receipt')
        path = trusted(state/token)
        if path.stat().st_size > 65536:
            raise ControlError('Invalid receipt size')
        receipt = json.loads(path.read_text())
        if receipt['uid'] != uid:
            raise ControlError('Receipt belongs to another caller')
        domain, target = receipt['domain'], receipt['plan']['target']
    if domain not in ('cpu','disk') or target.get('manager') != 'system' or target.get('unit') not in allowed:
        raise ControlError('Helper policy does not authorize this system unit/action domain')
    session = ControlSession() if domain == 'cpu' else DiskSession()
    if action == 'prepare':
        args = request.get('args', [])
        if not isinstance(args,list) or len(args) != (2 if domain=='cpu' else 3) or not all(isinstance(x,str) and len(x)<128 for x in args):
            raise ControlError('Invalid control arguments')
        plan = session.prepare(target, *args)
        if len(list(state.iterdir())) >= 10000:
            raise ControlError('Helper receipt budget reached; administrator cleanup required')
        token = secrets.token_hex(24)
        path = state/token
        receipt = dict(uid=uid,domain=domain,plan=plan,event=None)
        with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600),'w') as file:
            json.dump(receipt,file)
        return dict(result=dict(plan, helper_token=token))
    # Serialize each receipt against concurrent apply/Undo and refresh under lock.
    import fcntl
    with path.open('r+') as file:
        fcntl.flock(file,fcntl.LOCK_EX)
        receipt = json.load(file)
        try:
            if action == 'apply':
                if receipt['event'] is not None:
                    raise ControlError('Receipt already applied; inspect or Undo')
                plan = receipt['plan']
                # Persist uncertainty before touching systemd, so termination or a
                # lost response leaves a recoverable receipt instead of no history.
                pending = dict(timestamp=time.time(), target=plan['target'], before=plan['before'], status='unconfirmed')
                if domain == 'cpu':
                    pending.update(property=plan['property'], requested=plan['value'])
                else:
                    pending['plan'] = plan
                receipt['event'] = pending
                file.seek(0); json.dump(receipt,file); file.truncate(); file.flush(); os.fsync(file.fileno())
                result = session.apply(plan)
            else:
                if receipt['event'] is None:
                    raise ControlError('Receipt has no attempted change')
                session.events = [receipt['event']]
                result = session.undo()
            response = dict(result=dict(result, helper_token=token))
        except (ControlError,OSError,ValueError) as exc:
            response = dict(error=str(exc))
        if session.events:
            receipt['event'] = session.events[-1]
        if receipt['event'] is not None:
            response['event'] = dict(receipt['event'], helper_token=token)
            file.seek(0); json.dump(receipt,file); file.truncate(); file.flush(); os.fsync(file.fileno())
        return response


def main():
    try:
        if os.geteuid() != 0:
            raise ControlError('Helper must be invoked through an administrator-approved sudo rule')
        uid = int(os.environ.get('SUDO_UID', '0'))
        # Do not honor caller DBus addresses, PATH or Python environment variables.
        os.environ.clear()
        os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin',LANG='C.UTF-8')
        text = sys.stdin.buffer.read(65537)
        if len(text)>65536:
            raise ControlError('Request exceeds 64 KiB')
        request = json.loads(text)
        if not isinstance(request,dict):
            raise ControlError('Request must be an object')
        response = dispatch(request,uid)
    except (ControlError,OSError,ValueError,KeyError,TypeError) as exc:
        response = dict(error=str(exc))
    print(json.dumps(response,allow_nan=False))
    return 0 if 'error' not in response else 1


class RemoteSession:
    def __init__(self, executable, domain):
        self.executable = str(trusted(executable))
        self.domain = domain
        self.events = []

    def call(self, request):
        trusted(self.executable)
        try:
            result = subprocess.run(['/usr/bin/sudo','-n','--',self.executable], input=json.dumps(request),
                capture_output=True,text=True,timeout=20)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ControlError('Helper did not respond; inspect the unit before retrying') from exc
        try:
            response = json.loads(result.stdout)
        except ValueError as exc:
            raise ControlError('Helper unavailable or denied: '+result.stderr[:300]) from exc
        if response.get('event'):
            event = response['event']
            self.events = [e for e in self.events if e.get('helper_token') != event['helper_token']] + [event]
        if response.get('error'):
            raise ControlError(response['error'])
        return response['result']

    def prepare(self, target, *args):
        return self.call(dict(action='prepare', domain=self.domain, target=target, args=list(args)))

    def apply(self, plan):
        try:
            return self.call(dict(action='apply', token=plan['helper_token']))
        except ControlError:
            if not any(e.get('helper_token') == plan['helper_token'] for e in self.events):
                self.events.append(dict(helper_token=plan['helper_token'], target=plan['target'],
                    plan=plan, before=plan['before'], status='unconfirmed', timestamp=0))
            raise

    def undo(self):
        event = next((e for e in reversed(self.events) if e['status'] != 'undone'),None)
        if not event:
            raise ControlError('No helper changes to Undo')
        return self.call(dict(action='undo',token=event['helper_token']))


if __name__ == '__main__':
    raise SystemExit(main())
