"""core app — backend-owned plant hierarchy with optional LoggerFast links.

Groups, memberships, roles, equipment metadata and nameplates live here. Rows
carrying a LoggerFast id may be synchronized from its hierarchy/catalog; rows
without one are entirely local and are never touched by that importer.

Id scheme (core.constants.FEEDER_ID_OFFSET):
    Group.id            -> distribution-panel eid (always < 1_000_000)
    FEEDER_ID_OFFSET+MFM.id -> single-panel (per-meter) eid
Logger ids are the upsert keys; rows are soft-retired, never deleted, so ids and
FE URLs stay stable across re-imports. A row with NO logger id was built by hand
in the admin and is invisible to the importer.
"""
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import RegexValidator
from django.db import models

from core.constants import FEEDER_ID_OFFSET
from core.nav_categories import (MFM_NAV_CATEGORY_CHOICES, NAV_CATEGORY_CHOICES,
                                 NAV_CATEGORY_SOURCE_CHOICES)


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


_LOGGER_IDENTIFIER = RegexValidator(
    regex=r'^[A-Za-z_][A-Za-z0-9_]*$',
    message='Use a safe SQL identifier (letters, numbers and underscores; not starting with a number).',
)


class LoggerSchema(models.Model):
    """Versioned identity of a reusable LoggerFast field layout."""

    code = models.CharField(
        max_length=64,
        help_text='LoggerFast schema code, for example sch_EM6400_NG+.',
    )
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=120)
    logger_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True,
        help_text='LoggerFast internal schema id (sch_…), when known.',
    )
    timestamp_column = models.CharField(
        max_length=100, default='timestamp', validators=[_LOGGER_IDENTIFIER],
    )
    description = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'logger_schema'
        ordering = ['code', 'version']
        constraints = [
            models.UniqueConstraint(
                fields=['code', 'version'], name='uniq_logger_schema_code_version'),
            models.CheckConstraint(
                condition=models.Q(version__gte=1), name='logger_schema_version_gte_1'),
        ]

    def __str__(self):
        return f'{self.name} ({self.code}_v{self.version})'


class LoggerSchemaField(models.Model):
    """One measured or derived field in a versioned LoggerFast schema."""

    KIND_CHOICES = [('measured', 'Measured'), ('derived', 'Derived')]

    logger_schema = models.ForeignKey(
        LoggerSchema, on_delete=models.CASCADE, related_name='fields')
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    data_type = models.CharField(max_length=40)
    unit = models.CharField(max_length=40, blank=True, default='')
    address = models.CharField(max_length=64, blank=True, default='')
    scale = models.FloatField(null=True, blank=True)
    formula = models.TextField(blank=True, default='')
    evaluation_order = models.PositiveIntegerField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'logger_schema_field'
        ordering = ['logger_schema', 'position', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['logger_schema', 'name'], name='uniq_logger_schema_field_name'),
            models.UniqueConstraint(
                fields=['logger_schema', 'evaluation_order'],
                condition=models.Q(evaluation_order__isnull=False),
                name='uniq_logger_schema_field_eval_order'),
        ]

    def clean(self):
        if self.kind == 'measured' and not self.address:
            raise ValidationError({'address': 'A measured field requires a register address.'})
        if self.kind == 'derived' and not self.formula:
            raise ValidationError({'formula': 'A derived field requires a formula.'})

    def __str__(self):
        return f'{self.logger_schema.code}/{self.name}'


class PanelType(models.Model):
    """Stable classification for panel-like equipment."""
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = 'core_paneltype'
        ordering = ['name']

    def __str__(self):
        return self.name


class AssetType(models.Model):
    """Stable classification for non-panel assets such as DG, UPS and solar."""
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = 'core_assettype'
        ordering = ['name']

    def __str__(self):
        return self.name


class Equipment(models.Model):
    """Canonical thing measured by an MFM.

    Navigation placement remains ``Group``/``Membership`` because beta permits
    the same MFM to appear in more than one panel.  ``group`` optionally ties a
    distribution-panel equipment row to its structural Group.
    """
    key = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=150)
    panel_type = models.ForeignKey(
        PanelType, null=True, blank=True, on_delete=models.PROTECT,
        related_name='equipment')
    asset_type = models.ForeignKey(
        AssetType, null=True, blank=True, on_delete=models.PROTECT,
        related_name='equipment')
    group = models.OneToOneField(
        'Group', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='equipment')
    distribution_panel = models.BooleanField(default=False)
    metered = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'equipment'
        ordering = ['name']
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(panel_type__isnull=False) & models.Q(asset_type__isnull=True))
                    | (models.Q(panel_type__isnull=True) & models.Q(asset_type__isnull=False))
                ),
                name='equipment_exactly_one_type_family'),
        ]

    def clean(self):
        if (self.panel_type_id is None) == (self.asset_type_id is None):
            raise ValidationError('Choose exactly one of panel type or asset type.')

    def __str__(self):
        return self.name


class Group(models.Model):
    """One LoggerFast group — a nav section, or (``is_panel``) a distribution panel.

    ``id`` doubles as the distribution-panel eid used by ``ws/mfm/<id>/`` and
    ``/api/mfm/<id>/config/``.
    """
    # Blank (NULL) = built in the admin: the importer never updates, retires or
    # strips the memberships of such a row.
    logger_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True,
        help_text='LoggerFast group id (grp_…) — the import upsert key. '
                  'Blank = created in the admin; the importer never touches it.')
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


class DistributionPanel(Group):
    """Admin face of an ``is_panel`` Group: one Category dropdown plus its
    Incomers / Outgoers / Spares. Same table, same ids."""

    class Meta:
        proxy = True
        verbose_name = 'Distribution Panel'
        verbose_name_plural = 'Distribution Panels'


class EmsNav(Group):
    """Admin-only page: the eight categories exactly as the sidebar renders
    them, with an add-panel / add-MFM link on each. Owns no data."""

    class Meta:
        proxy = True
        verbose_name = 'EMS Nav'
        verbose_name_plural = 'EMS Nav'


class MFM(models.Model):
    """One meter = one single-panel screen set, optionally linked to LoggerFast.

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
        max_length=64, unique=True, null=True, blank=True,
        help_text='LoggerFast device id (dev_…) this meter reads. Blank = not '
                  'linked yet; the importer never touches an unlinked meter.')
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=120,
                            help_text='Stable FE identifier; unique among active meters.')
    protocol = models.CharField(max_length=40, blank=True, default='')
    gateway_id = models.CharField(max_length=64, blank=True, default='')
    unit_id = models.IntegerField(null=True, blank=True)
    port = models.IntegerField(null=True, blank=True)
    logger_status = models.CharField(max_length=40, blank=True, default='')
    equipment = models.ForeignKey(
        Equipment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='mfms',
        help_text='The canonical equipment or asset measured by this MFM.')
    reference = models.ForeignKey(
        Group, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='referenced_mfms',
        help_text='Primary/legacy physical panel. Memberships remain authoritative '
                  'when a meter appears in more than one panel.')
    nav_category = models.CharField(
        max_length=16, choices=MFM_NAV_CATEGORY_CHOICES, blank=True, default='',
        db_index=True,
        help_text='Only for a meter that is NOT in a distribution panel: it then '
                  'shows directly under this category. Ignored for a panel '
                  'incomer/outgoer/spare (the panel places it).')
    nav_category_source = models.CharField(
        max_length=8, choices=NAV_CATEGORY_SOURCE_CHOICES, default='auto',
        help_text='manual = set by hand, the importer never reseeds it.')
    # timeseries binding: shared connection (FK) + per-meter table
    data_source = models.ForeignKey(
        DataSource, null=True, blank=True, on_delete=models.PROTECT,
        related_name='mfms',
        help_text='Shared DB connection — one pool per source for the socket layer.')
    logger_schema = models.ForeignKey(
        LoggerSchema, null=True, blank=True, on_delete=models.PROTECT,
        related_name='mfms',
        help_text='Versioned LoggerFast field layout used by this meter table.',
    )
    table_name = models.CharField(max_length=100, blank=True, default='')
    logger_table_id = models.CharField(max_length=64, blank=True, default='')
    series_id = models.CharField(
        max_length=100, blank=True, default='', db_index=True,
        help_text='Optional series discriminator when one logger table holds multiple series.')
    table_binding = models.CharField(max_length=12, choices=TABLE_BINDING_CHOICES,
                                     default='none')
    # Which energy register carries this meter's TRUE consumption on the real
    # logger; 'sum' = the legacy blind import+export coalesce. Decade scales
    # correct CT/PT-ratio miswires (energy) and mis-programmed multipliers (power).
    energy_direction = models.CharField(max_length=8, choices=ENERGY_DIRECTION_CHOICES,
                                        default='sum')
    energy_scale = models.FloatField(default=1.0)
    power_scale = models.FloatField(default=1.0)
    asset_category = models.CharField(max_length=64, blank=True, default='')
    load_profile = models.CharField(max_length=64, blank=True, default='')
    parent_series = models.CharField(max_length=100, blank=True, default='')
    sheet_row = models.IntegerField(null=True, blank=True)
    section = models.CharField(max_length=100, blank=True, default='')
    zone = models.CharField(max_length=100, blank=True, default='')
    # Finest-grain per-meter nameplate overrides. MfmConfig remains supported;
    # these direct fields win during config resolution for backend2 parity.
    rated_capacity_kva = models.FloatField(null=True, blank=True)
    nominal_voltage_v = models.FloatField(null=True, blank=True)
    nameplate_voltage_kv = models.FloatField(null=True, blank=True)
    frequency_hz = models.FloatField(null=True, blank=True)
    fault_level_ka = models.FloatField(null=True, blank=True)
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


class DistributionPanelThreshold(PanelConfig):
    """Focused admin view of Group-owned panel limits, not duplicate settings."""
    class Meta:
        proxy = True
        verbose_name = 'Distribution Panel Threshold'
        verbose_name_plural = 'Distribution Panel Thresholds'


class EquipmentConfig(NameplateBase):
    """Nameplate/configuration owned by a canonical Equipment row."""
    equipment = models.OneToOneField(
        Equipment, on_delete=models.CASCADE, related_name='config')
    rating = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        db_table = 'equipment_config'
        verbose_name = 'Equipment Config'
        verbose_name_plural = 'Equipment Configs'

    def __str__(self):
        return f'config[{self.equipment.key}]'


class Nameplate(models.Model):
    """Traceable reference fact; operational config is kept separately."""
    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name='nameplates')
    key = models.CharField(max_length=100)
    value = models.CharField(max_length=255, blank=True, default='')
    unit = models.CharField(max_length=40, blank=True, default='')
    normalized_value = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'nameplate'
        ordering = ['equipment', 'key']
        constraints = [
            models.UniqueConstraint(fields=['equipment', 'key'],
                                    name='uniq_nameplate_equipment_key'),
        ]

    def __str__(self):
        return f'{self.equipment.key}/{self.key}'


class ViewerAsset(models.Model):
    """A catalogued 3D object and its model-level viewer defaults."""
    slug = models.SlugField(max_length=120, unique=True)
    label = models.CharField(max_length=150)
    url = models.CharField(max_length=500)
    rating = models.CharField(max_length=100, blank=True, default='')
    defaults = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = 'viewer_asset'
        ordering = ['label']

    def __str__(self):
        return self.label


class ViewerRule(models.Model):
    """Logical Equipment.key/type to ViewerAsset binding."""
    for_key = models.CharField(max_length=120, blank=True, default='', db_index=True)
    for_type = models.CharField(max_length=64, blank=True, default='', db_index=True)
    rating = models.CharField(max_length=100, blank=True, default='')
    asset = models.ForeignKey(
        ViewerAsset, on_delete=models.PROTECT, related_name='rules')
    preset = models.JSONField(default=dict, blank=True)
    priority = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = 'viewer_rule'
        ordering = ['-priority', 'id']
        constraints = [
            models.CheckConstraint(
                condition=~(models.Q(for_key='') & models.Q(for_type='')),
                name='viewer_rule_has_key_or_type'),
        ]

    def __str__(self):
        target = self.for_key or self.for_type
        return f'{target} → {self.asset.slug}'


class ViewerSetting(models.Model):
    """Global viewer defaults and fallback asset slugs."""
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'viewer_setting'

    def __str__(self):
        return self.key


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


class MfmThreshold(models.Model):
    """Optional finest-grain PQ and overview thresholds for one MFM."""
    mfm = models.OneToOneField(MFM, on_delete=models.CASCADE, related_name='thresholds')
    i_thd_limit_pct = models.FloatField('Power Quality — Current THD limit (%)', null=True, blank=True)
    v_thd_limit_pct = models.FloatField('Power Quality — Voltage THD limit (%)', null=True, blank=True)
    individual_harmonic_limit_pct = models.FloatField('Power Quality — Individual harmonic limit (%)', null=True, blank=True)
    flicker_pst_limit = models.FloatField('Power Quality — Flicker Pst limit', null=True, blank=True)
    crest_factor_ideal = models.FloatField('Power Quality — Ideal crest factor', null=True, blank=True)
    overview_busbar_temp_warn_c = models.FloatField('Busbar Temperature — Warning above (°C)', null=True, blank=True)
    overview_busbar_temp_critical_c = models.FloatField('Busbar Temperature — Critical above (°C)', null=True, blank=True)
    overview_load_elevated_pct = models.FloatField('Rated Capacity Utilization — Elevated above (%)', null=True, blank=True)
    overview_load_critical_pct = models.FloatField('Rated Capacity Utilization — Critical above (%)', null=True, blank=True)
    overview_pf_good = models.FloatField('Power Factor — Good at or above', null=True, blank=True)
    overview_pf_fair = models.FloatField('Power Factor — Fair at or above', null=True, blank=True)
    overview_freq_nominal_hz = models.FloatField('Grid Frequency — Nominal (Hz)', null=True, blank=True)
    overview_freq_fair_dev_hz = models.FloatField('Grid Frequency — Fair deviation above (Hz)', null=True, blank=True)
    overview_freq_unstable_dev_hz = models.FloatField('Grid Frequency — Unstable deviation above (Hz)', null=True, blank=True)
    overview_phase_watch_pct = models.FloatField('Current Unbalance — Watch above (%)', null=True, blank=True)
    overview_phase_unbalanced_pct = models.FloatField('Current Unbalance — Unbalanced above (%)', null=True, blank=True)
    overview_voltage_dev_elevated_pct = models.FloatField('Voltage Deviation — Elevated above (%)', null=True, blank=True)
    overview_voltage_dev_critical_pct = models.FloatField('Voltage Deviation — Critical above (%)', null=True, blank=True)
    overview_energy_elevated_pct = models.FloatField('Energy Consumption Today — Elevated above target usage (%)', null=True, blank=True)
    overview_energy_critical_pct = models.FloatField('Energy Consumption Today — Critical above target usage (%)', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mfm_threshold'
        verbose_name = 'MFM Threshold'
        verbose_name_plural = 'MFM Thresholds'

    def __str__(self):
        return f'thresholds[{self.mfm.slug}]'


class Breaker(models.Model):
    """Static breaker metadata for one MFM; not live breaker state."""
    BREAKER_TYPE_CHOICES = [
        ('ACB', 'ACB'), ('MCCB', 'MCCB'), ('MCB', 'MCB'), ('VCB', 'VCB'),
        ('unknown', 'Unknown'),
    ]
    mfm = models.OneToOneField(MFM, on_delete=models.CASCADE, related_name='breaker')
    breaker_type = models.CharField(
        max_length=12, choices=BREAKER_TYPE_CHOICES, default='unknown')
    rating_a = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=100, blank=True, default='')
    glb_node = models.CharField(max_length=150, blank=True, default='')
    panel_key = models.CharField(max_length=120, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'breaker'

    def __str__(self):
        return f'{self.mfm.slug}: {self.breaker_type}'


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
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='rtm_thresholds',
                              null=True, blank=True)
    panel_type = models.ForeignKey(PanelType, on_delete=models.CASCADE,
                                   related_name='rtm_thresholds', null=True, blank=True)
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
            models.UniqueConstraint(fields=['panel_type', 'metric'], name='uniq_rtm_type_metric'),
            models.CheckConstraint(condition=(
                models.Q(group__isnull=False, panel_type__isnull=True) |
                models.Q(group__isnull=True, panel_type__isnull=False)), name='rtm_exactly_one_scope'),
        ]

    def bounds(self):
        return (self.low_max, self.normal_max, self.moderate_max, self.high_max)

    def __str__(self):
        return f'rtm[{self.group.slug if self.group_id else self.panel_type.code}/{self.metric}]'
