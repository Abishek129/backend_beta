"""core app — the plant hierarchy imported from LoggerFast.

LoggerFast owns the STRUCTURE (group tree, memberships, roles, table bindings,
device edges); beta owns the SCREENS and the NAMEPLATE data the logger has no
concept of. So each logger object maps to one row here keyed on its logger id,
and every panel/meter gets a config row we own.

Id scheme (core.constants.FEEDER_ID_OFFSET):
    Group.id            -> distribution-panel eid (always < 1_000_000)
    FEEDER_ID_OFFSET+MFM.id -> single-panel (per-meter) eid
Logger ids are the upsert keys; rows are soft-retired, never deleted, so ids and
FE URLs stay stable across re-imports.
"""
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models

from core.constants import FEEDER_ID_OFFSET
from core.nav_categories import NAV_CATEGORY_CHOICES, NAV_CATEGORY_SOURCE_CHOICES


class DataSource(models.Model):
    """A shared timeseries DB connection — one row per distinct DSN.

    Lets the socket layer pool a SINGLE connection per source instead of one
    per meter, and lets the whole plant be repointed by editing one row.
    """
    name = models.CharField(max_length=100, unique=True,
                            help_text='Human label, e.g. "premier_energies".')
    dsn = models.CharField(max_length=255, unique=True,
                           help_text='libpq connection string (no table name).')
    logger_db_target_id = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='LoggerFast dbTargetId this source stands for.')
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'data_source'
        verbose_name = 'Data Source'
        verbose_name_plural = 'Data Sources'

    def __str__(self):
        return self.name


class Group(models.Model):
    """One LoggerFast group — a nav section, or (``is_panel``) a distribution panel.

    ``id`` doubles as the distribution-panel eid used by ``ws/mfm/<id>/`` and
    ``/api/mfm/<id>/config/``.
    """
    logger_id = models.CharField(
        max_length=64, unique=True,
        help_text='LoggerFast group id (grp_…) — the upsert key.')
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=120,
                            help_text='Stable FE identifier; unique among active groups.')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE,
                               related_name='children')
    sort_order = models.IntegerField(default=0)
    is_panel = models.BooleanField(
        default=False,
        help_text='True = distribution panel (aggregate screens + role groups).')
    nav_category = models.CharField(
        max_length=16, choices=NAV_CATEGORY_CHOICES, blank=True, default='',
        db_index=True,
        help_text='HVAC / APFC / Production / Utility only. Blank = placed by '
                  'PanelConfig.panel_kind (HT/AMF/PCC/UPS). On a plain group it '
                  'is also the fallback for the loose meters beneath it.')
    nav_category_source = models.CharField(
        max_length=8, choices=NAV_CATEGORY_SOURCE_CHOICES, default='auto',
        help_text='manual = set by hand, the importer never reseeds it.')
    twins = models.ManyToManyField('self', blank=True, symmetrical=True,
                                   help_text='Sibling panels tied to this one.')
    active = models.BooleanField(default=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'plant_group'
        verbose_name = 'Group'
        verbose_name_plural = 'Groups'
        ordering = ['sort_order', 'name']
        constraints = [
            models.UniqueConstraint(fields=['slug'], condition=models.Q(active=True),
                                    name='uniq_group_slug_active'),
        ]

    def __str__(self):
        return self.name


class MFM(models.Model):
    """One LoggerFast device = one meter = one single-panel screen set.

    ``eid`` (FEEDER_ID_OFFSET + id) is the id the FE and the sockets address.
    The timeseries binding is ``data_source`` (connection) + ``table_name``.
    """
    TABLE_BINDING_CHOICES = [
        ('none', 'None — no migrated table'),
        ('auto', 'Auto — one migrated table matched'),
        ('ambiguous', 'Ambiguous — several migrated tables'),
        ('manual', 'Manual — set by hand, importer never touches it'),
    ]
    ENERGY_DIRECTION_CHOICES = [
        ('sum', 'Sum (import + export)'),
        ('import', 'Import register only'),
        ('export', 'Export register only'),
        ('net', 'Net (import − export)'),
    ]

    logger_id = models.CharField(
        max_length=64, unique=True,
        help_text='LoggerFast device id (dev_…) — the upsert key.')
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=120,
                            help_text='Stable FE identifier; unique among active meters.')
    protocol = models.CharField(max_length=40, blank=True, default='')
    gateway_id = models.CharField(max_length=64, blank=True, default='')
    unit_id = models.IntegerField(null=True, blank=True)
    port = models.IntegerField(null=True, blank=True)
    logger_status = models.CharField(max_length=40, blank=True, default='')
    nav_category = models.CharField(
        max_length=16, choices=NAV_CATEGORY_CHOICES, blank=True, default='',
        db_index=True,
        help_text='Category for this meter when it renders as a LOOSE leaf. '
                  'Blank = inherit from the plain group it sits in. Ignored for '
                  'a meter inside a panel (the panel places it).')
    nav_category_source = models.CharField(
        max_length=8, choices=NAV_CATEGORY_SOURCE_CHOICES, default='auto',
        help_text='manual = set by hand, the importer never reseeds it.')
    # timeseries binding: shared connection (FK) + per-meter table
    data_source = models.ForeignKey(
        DataSource, null=True, blank=True, on_delete=models.PROTECT,
        related_name='mfms',
        help_text='Shared DB connection — one pool per source for the socket layer.')
    table_name = models.CharField(max_length=100, blank=True, default='')
    logger_table_id = models.CharField(max_length=64, blank=True, default='')
    table_binding = models.CharField(max_length=12, choices=TABLE_BINDING_CHOICES,
                                     default='none')
    # Which energy register carries this meter's TRUE consumption on the real
    # logger; 'sum' = the legacy blind import+export coalesce. Decade scales
    # correct CT/PT-ratio miswires (energy) and mis-programmed multipliers (power).
    energy_direction = models.CharField(max_length=8, choices=ENERGY_DIRECTION_CHOICES,
                                        default='sum')
    energy_scale = models.FloatField(default=1.0)
    power_scale = models.FloatField(default=1.0)
    active = models.BooleanField(default=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mfm'
        verbose_name = 'MFM'
        verbose_name_plural = 'MFMs'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['slug'], condition=models.Q(active=True),
                                    name='uniq_mfm_slug_active'),
        ]

    def __str__(self):
        return self.name

    @property
    def eid(self):
        """The id the FE / WS address this meter by."""
        return FEEDER_ID_OFFSET + self.id


class Membership(models.Model):
    """One device inside one group. The same MFM may sit in many groups."""
    ROLE_CHOICES = [
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
        ('spare', 'Spare'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    mfm = models.ForeignKey(MFM, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, null=True, blank=True,
                            help_text='Set iff the group is a panel; NULL in a plain group.')
    position = models.IntegerField(default=0,
                                   help_text='Index in the logger group devices[] array.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'membership'
        verbose_name = 'Membership'
        verbose_name_plural = 'Memberships'
        ordering = ['group', 'position']
        constraints = [
            models.UniqueConstraint(fields=['group', 'mfm'], name='uniq_membership_group_mfm'),
        ]

    def __str__(self):
        return f'{self.group.slug}/{self.mfm.slug}'

    def clean(self):
        try:                                   # unsaved parent still resolves via the FK cache
            group = self.group
        except ObjectDoesNotExist:
            group = None
        if group is None:
            return
        if group.is_panel and not self.role:
            raise ValidationError('role is required for a membership in a panel group.')
        if not group.is_panel and self.role:
            raise ValidationError('role must be empty for a membership in a plain group.')


class FeedEdge(models.Model):
    """A directed device-to-device feed edge from the logger's device graph."""
    from_mfm = models.ForeignKey(MFM, on_delete=models.CASCADE, related_name='edges_out')
    to_mfm = models.ForeignKey(MFM, on_delete=models.CASCADE, related_name='edges_in')
    edge_type = models.CharField(max_length=20, default='feeds')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'feed_edge'
        verbose_name = 'Feed Edge'
        verbose_name_plural = 'Feed Edges'
        constraints = [
            models.UniqueConstraint(fields=['from_mfm', 'to_mfm', 'edge_type'],
                                    name='uniq_feed_edge'),
            models.CheckConstraint(condition=~models.Q(from_mfm=models.F('to_mfm')),
                                   name='feed_edge_no_self_edge'),
        ]

    def __str__(self):
        return f'{self.from_mfm.slug} → {self.to_mfm.slug} ({self.edge_type})'


class NameplateBase(models.Model):
    """Per-row nameplate / limits — the data LoggerFast does not have.

    Field-for-field the backend2 ``EquipmentConfig.CONFIG_FIELDS`` set, so every
    copied consumer and the FE config mappers read the same key names. All
    nullable, so an override can be partial; resolution is override -> type
    default (``core.config_defaults``) -> None.
    """
    # nameplate / capacity
    rated_kva = models.FloatField(null=True, blank=True)
    rated_kw = models.FloatField(null=True, blank=True)
    contracted_kw = models.FloatField(null=True, blank=True)
    critical_load_kw = models.FloatField(null=True, blank=True)
    # voltage
    nominal_voltage_v = models.FloatField(null=True, blank=True)
    voltage_statutory_deviation_pct = models.FloatField(null=True, blank=True)
    # current
    rated_current_a = models.FloatField(null=True, blank=True)
    current_tolerance_pct = models.FloatField(null=True, blank=True)
    current_unbalance_watch_pct = models.FloatField(
        null=True, blank=True,
        help_text='Current-unbalance Normal/Watch threshold.')
    current_high_threshold_a = models.FloatField(null=True, blank=True)
    # energy
    energy_target_kwh_today = models.FloatField(null=True, blank=True)
    subsidy_limit_kw = models.FloatField(null=True, blank=True)
    subsidy_limit_mvah = models.FloatField(
        null=True, blank=True,
        help_text='Cumulative-energy subsidy limit in MVAh (Energy & Power screen).')
    # operations & runtime
    service_interval_hours = models.FloatField(
        null=True, blank=True,
        help_text='Service-interval ceiling, h (the runtime progress bar maxes here).')
    service_warn_pct = models.FloatField(
        null=True, blank=True, help_text='Warn marker on the service bar (% of ceiling).')
    demand_limit_kw = models.FloatField(
        null=True, blank=True,
        help_text='Demand-limit reference line on the Power Energy Analysis chart, kW.')
    target_efficiency_pct = models.FloatField(null=True, blank=True)
    # power quality limits
    thd_v_limit_pct = models.FloatField(null=True, blank=True)
    thd_i_limit_pct = models.FloatField(null=True, blank=True)
    true_pf_min = models.FloatField(null=True, blank=True)
    neutral_current_limit_a = models.FloatField(null=True, blank=True)
    # Voltage & Current row-severity edges (Other Panels table)
    vc_vdev_warn_pct = models.FloatField(
        null=True, blank=True, help_text='V&C row WARN when |V deviation| exceeds this % (default 3).')
    vc_vdev_danger_pct = models.FloatField(
        null=True, blank=True, help_text='V&C row DANGER when |V deviation| exceeds this % (default 5).')
    vc_iunbal_warn_pct = models.FloatField(
        null=True, blank=True, help_text='V&C row WARN when current unbalance exceeds this % (default 5).')
    vc_iunbal_danger_pct = models.FloatField(
        null=True, blank=True, help_text='V&C row DANGER when current unbalance exceeds this % (default 8).')
    # SLD / overview nameplate header
    frequency_hz = models.FloatField(
        null=True, blank=True,
        help_text='Nominal supply frequency (Hz) for the nameplate, e.g. 50.')
    fault_level_ka = models.FloatField(
        null=True, blank=True,
        help_text='Switchgear short-circuit fault level / breaking capacity (kA). FE key: fault_level_ka.')
    nameplate_voltage_kv = models.FloatField(
        null=True, blank=True,
        help_text='Nameplate system voltage (kV) — wins over the FE deriving L-L. FE key: nameplate_voltage_kv.')
    # score-history chart bands (0-100)
    ready_threshold = models.FloatField(
        null=True, blank=True, help_text='Ready Threshold line + "Ready" envelope marker (0-100).')
    moderate_zone = models.FloatField(
        null=True, blank=True, help_text='Moderate zone band floor (0-100).')
    watch_zone = models.FloatField(
        null=True, blank=True, help_text='Watch zone band floor (0-100).')
    readiness_floor = models.FloatField(
        null=True, blank=True, help_text='Composite "Readiness" watchpoint line (0-100).')
    notes = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    # fields exposed at /api/mfm/{id}/config/ (resolved with type defaults).
    # Order copied verbatim from backend2 EquipmentConfig.CONFIG_FIELDS.
    CONFIG_FIELDS = [
        'rated_kva', 'rated_kw', 'contracted_kw', 'critical_load_kw',
        'nominal_voltage_v', 'voltage_statutory_deviation_pct',
        'rated_current_a', 'current_tolerance_pct', 'current_unbalance_watch_pct',
        'current_high_threshold_a',
        'energy_target_kwh_today', 'subsidy_limit_kw', 'subsidy_limit_mvah',
        'service_interval_hours', 'service_warn_pct', 'demand_limit_kw',
        'target_efficiency_pct', 'thd_v_limit_pct', 'thd_i_limit_pct',
        'true_pf_min', 'neutral_current_limit_a',
        'vc_vdev_warn_pct', 'vc_vdev_danger_pct',
        'vc_iunbal_warn_pct', 'vc_iunbal_danger_pct',
        'frequency_hz', 'fault_level_ka', 'nameplate_voltage_kv',
        'ready_threshold', 'moderate_zone', 'watch_zone', 'readiness_floor',
    ]

    class Meta:
        abstract = True


class PanelConfig(NameplateBase):
    """Nameplate for a distribution panel (an ``is_panel`` Group)."""
    # Doubles as the nav axis for the four STRUCTURAL categories (see
    # core.nav_categories.PANEL_KIND_CATEGORY): ht/amf/pcc/ups_panel each place
    # the panel in its sidebar category. 'distribution_panel' places nothing —
    # such a row falls through to Group.nav_category.
    PANEL_KIND_CHOICES = [
        ('distribution_panel', 'Distribution Panel'),
        ('pcc_panel', 'PCC Panel'),
        ('ht_panel', 'HT Panel'),
        ('amf_panel', 'AMF Panel'),
        ('ups_panel', 'UPS Panel'),
    ]
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='config')
    panel_kind = models.CharField(max_length=24, choices=PANEL_KIND_CHOICES,
                                  default='distribution_panel',
                                  help_text='Drives the frame mfm_type served to the FE, '
                                            'and places the panel in its nav category.')

    class Meta:
        db_table = 'panel_config'
        verbose_name = 'Panel Config'
        verbose_name_plural = 'Panel Configs'

    def __str__(self):
        return f'config[{self.group.slug}]'


class MfmConfig(NameplateBase):
    """Nameplate for one meter (a single-panel screen set)."""
    DEVICE_TYPE_CHOICES = [
        ('lt_panel', 'LT Panel'),
        ('transformer', 'Transformer'),
        ('ups', 'UPS'),
        ('solar', 'Solar'),
        ('dg', 'DG'),
    ]
    mfm = models.OneToOneField(MFM, on_delete=models.CASCADE, related_name='config')
    device_type = models.CharField(max_length=24, choices=DEVICE_TYPE_CHOICES,
                                   default='lt_panel',
                                   help_text='Drives WS dispatch + the RTM feeder type.')

    class Meta:
        db_table = 'mfm_config'
        verbose_name = 'MFM Config'
        verbose_name_plural = 'MFM Configs'

    def __str__(self):
        return f'config[{self.mfm.slug}]'


class RtmThreshold(models.Model):
    """Per-metric 5-band thresholds for a panel's Real-Time Monitoring grid
    (Low / Normal / Moderate / High / Critical).

    One row per (group, metric); the 4 numbers are the band upper edges. The band
    direction and the normalized basis each metric is banded on live in
    ``core.rtm_defaults`` — admins only set the 4 numbers.
    """
    METRIC_CHOICES = [
        ('kw', 'kW'), ('kvar', 'kVAr'), ('pf', 'Power Factor'),
        ('volt', 'Voltage'), ('amp', 'Amps'), ('i_unbal', 'I Unbalance'),
        ('kva', 'kVA'), ('v_unbal', 'V Unbalance'),
    ]
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='rtm_thresholds')
    metric = models.CharField(max_length=12, choices=METRIC_CHOICES)
    low_max = models.FloatField(help_text='upper edge of the Low band')
    normal_max = models.FloatField(help_text='upper edge of the Normal band')
    moderate_max = models.FloatField(help_text='upper edge of the Moderate band')
    high_max = models.FloatField(help_text='upper edge of the High band (above = Critical)')

    class Meta:
        db_table = 'rtm_threshold'
        verbose_name = 'RTM Threshold'
        verbose_name_plural = 'RTM Thresholds'
        constraints = [
            models.UniqueConstraint(fields=['group', 'metric'], name='uniq_rtm_group_metric'),
        ]

    def bounds(self):
        return (self.low_max, self.normal_max, self.moderate_max, self.high_max)

    def __str__(self):
        return f'rtm[{self.group.slug}/{self.metric}]'
