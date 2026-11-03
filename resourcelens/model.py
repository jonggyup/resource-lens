"""Pure identity, measurement-quality and constraint contracts.

No live reads, inference, authorization or UI behavior belongs in this module.
Existing schema-1 scalar telemetry remains usable without these optional records.
"""

from dataclasses import asdict, dataclass, field
import math
from typing import Literal


def _text(name, value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError(name + ' must be nonempty text without NUL')


def _integer(name, value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')


def _finite(name, value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + ' must be finite')


def _path(value):
    _text('cgroup path', value)
    if not value.startswith('/') or (value != '/' and
            any(part in ('', '.', '..') for part in value[1:].split('/'))):
        raise ValueError('cgroup path must be absolute and canonical')


@dataclass(frozen=True)
class CollectionScope:
    """Session identity plus optional observed context, not proof of host visibility."""

    id: str
    boot_id: str | None = None
    pid_namespace: str | None = None
    cgroup_namespace: str | None = None
    cgroup_root_device: int | None = None
    cgroup_root_inode: int | None = None

    def __post_init__(self):
        _text('scope id', self.id)
        for name in ('boot_id', 'pid_namespace', 'cgroup_namespace'):
            if getattr(self, name) is not None:
                _text(name, getattr(self, name))
        if (self.cgroup_root_device is None) != (self.cgroup_root_inode is None):
            raise ValueError('cgroup root device and inode must be supplied together')
        if self.cgroup_root_device is not None:
            _integer('cgroup root device', self.cgroup_root_device)
            _integer('cgroup root inode', self.cgroup_root_inode, 1)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ProcessIdentity:
    scope_id: str
    pid: int
    start_ticks: int

    def __post_init__(self):
        _text('scope id', self.scope_id)
        _integer('pid', self.pid, 1)
        _integer('start ticks', self.start_ticks)


@dataclass(frozen=True)
class CgroupIdentity:
    scope_id: str
    path: str
    device: int
    inode: int
    generation: int = 0

    def __post_init__(self):
        _text('scope id', self.scope_id)
        _path(self.path)
        _integer('device', self.device)
        _integer('inode', self.inode, 1)
        _integer('generation', self.generation)


@dataclass(frozen=True)
class TargetRef:
    """Initial process/cgroup targets; service authorization stays in the backend.

    A label is presentation only. Exact cgroup membership is a relationship, not
    part of process identity: moving groups must not create a new process.
    """

    kind: Literal['process', 'cgroup']
    identity: ProcessIdentity | CgroupIdentity
    label: str = field(default='', compare=False)

    def __post_init__(self):
        expected = {'process': ProcessIdentity, 'cgroup': CgroupIdentity}.get(self.kind)
        if expected is None or not isinstance(self.identity, expected):
            raise ValueError('Target kind and identity do not match')
        if not isinstance(self.label, str):
            raise ValueError('Target label must be text')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        try:
            kind = value['kind']
            identity_type = {'process': ProcessIdentity, 'cgroup': CgroupIdentity}[kind]
            return cls(kind, identity_type(**value['identity']), value.get('label', ''))
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid target reference') from exc


@dataclass(frozen=True)
class Coverage:
    examined: int | None = None
    omitted: int | None = None
    truncation_reason: str | None = None

    def __post_init__(self):
        for name in ('examined', 'omitted'):
            if getattr(self, name) is not None:
                _integer(name, getattr(self, name))
        if self.truncation_reason is not None:
            _text('truncation reason', self.truncation_reason)


QualityStatus = Literal['ok', 'partial', 'unavailable', 'error', 'unknown']


@dataclass(frozen=True)
class SourceQuality:
    """Unknown metadata does not mean unavailable data; freshness is separate.

    Interval bounds and observed_at_monotonic share the collection scope's clock.
    They must not be compared with the replaying machine's monotonic clock.
    """

    source_id: str
    status: QualityStatus = 'unknown'
    reason: str | None = 'not_recorded'
    scope_id: str | None = None
    sequence: int | None = None
    observed_at_wall: float | None = None
    observed_at_monotonic: float | None = None
    interval_start: float | None = None
    interval_end: float | None = None
    duration_ms: float | None = None
    coverage: Coverage = field(default_factory=Coverage)

    def __post_init__(self):
        _text('source id', self.source_id)
        if self.status not in ('ok', 'partial', 'unavailable', 'error', 'unknown'):
            raise ValueError('Invalid measurement status')
        reasons = ('permission_denied', 'not_exposed', 'disappeared', 'parse_error',
                   'warmup', 'counter_reset', 'timed_out', 'budget_exceeded',
                   'not_recorded', 'identity_changed', 'membership_changed', 'unknown')
        if self.reason is not None and self.reason not in reasons:
            raise ValueError('Invalid measurement reason')
        if self.status == 'ok' and self.reason is not None:
            raise ValueError('An ok source cannot have a failure/unknown reason')
        if self.status != 'ok' and self.reason is None:
            raise ValueError('A non-ok source needs a reason')
        if self.scope_id is not None:
            _text('scope id', self.scope_id)
        if self.sequence is not None:
            _integer('sequence', self.sequence)
        for name in ('observed_at_wall', 'observed_at_monotonic', 'interval_start',
                     'interval_end', 'duration_ms'):
            value = getattr(self, name)
            if value is not None:
                _finite(name, value)
                if name != 'observed_at_wall' and value < 0:
                    raise ValueError(name + ' cannot be negative')
        if (self.interval_start is None) != (self.interval_end is None):
            raise ValueError('Both interval bounds are required')
        if self.interval_start is not None and self.interval_end <= self.interval_start:
            raise ValueError('Measurement interval must be positive')
        if not isinstance(self.coverage, Coverage):
            raise ValueError('Invalid coverage')

    def freshness(self, now_monotonic, max_age, scope_id):
        _finite('current monotonic time', now_monotonic)
        _finite('maximum age', max_age)
        if max_age < 0:
            raise ValueError('Maximum age cannot be negative')
        if (self.scope_id is None or self.scope_id != scope_id or
                self.observed_at_monotonic is None or now_monotonic < self.observed_at_monotonic):
            return 'unknown'
        return 'stale' if now_monotonic - self.observed_at_monotonic > max_age else 'fresh'

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        try:
            value = dict(value)
            value['coverage'] = Coverage(**value.get('coverage', {}))
            return cls(**value)
        except (TypeError, KeyError) as exc:
            raise ValueError('Invalid source quality') from exc


def source_quality(sample, source_id):
    """Read optional metadata without reinterpreting existing scalar measurements."""
    rows = sample.get('source_quality', {})
    if not isinstance(rows, dict):
        raise ValueError('Invalid source quality map')
    if source_id not in rows:
        return SourceQuality(source_id)
    quality = SourceQuality.from_dict(rows[source_id])
    if quality.source_id != source_id:
        raise ValueError('Source quality key and source id disagree')
    return quality


@dataclass(frozen=True)
class Constraint:
    resource: str
    property: str
    origin: TargetRef
    value: int | float | str | None
    unit: str
    state: Literal['known', 'unlimited', 'unavailable', 'unknown']
    quality: SourceQuality
    owner: str | None = None
    ancestry_visibility: Literal['to_system_root', 'to_visible_root', 'partial', 'unknown'] = 'unknown'

    def __post_init__(self):
        for name in ('resource', 'property', 'unit'):
            _text(name, getattr(self, name))
        if not isinstance(self.origin, TargetRef) or not isinstance(self.quality, SourceQuality):
            raise ValueError('Constraint needs an origin and source quality')
        if self.quality.scope_id is not None and self.quality.scope_id != self.origin.identity.scope_id:
            raise ValueError('Constraint evidence belongs to another collection scope')
        if self.owner is not None:
            _text('owner', self.owner)
        if self.ancestry_visibility not in ('to_system_root', 'to_visible_root', 'partial', 'unknown'):
            raise ValueError('Invalid ancestry visibility')
        if self.state not in ('known', 'unlimited', 'unavailable', 'unknown'):
            raise ValueError('Invalid constraint state')
        if self.state == 'known':
            if isinstance(self.value, str):
                _text('constraint value', self.value)
            else:
                _finite('constraint value', self.value)
        elif self.value is not None:
            raise ValueError('Only a known constraint has a numeric/text value')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        try:
            value = dict(value)
            value['origin'] = TargetRef.from_dict(value['origin'])
            value['quality'] = SourceQuality.from_dict(value['quality'])
            return cls(**value)
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid constraint') from exc
