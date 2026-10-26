import re
from functools import partial

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.http import HttpResponseRedirect

from . import logger_devices
from .constants import FEEDER_ID_OFFSET
from .models import (AssetType, Breaker, DataSource, DistributionPanel, EmsNav,
                     Equipment, EquipmentConfig, FeedEdge, Group, Membership, MFM,
                     LoggerSchema, LoggerSchemaField, MfmConfig, MfmThreshold,
                     DistributionPanelThreshold,
                     Nameplate, PanelConfig, PanelType, RtmThreshold,
                     ViewerAsset, ViewerRule, ViewerSetting)
from .nav import build_tree, resolved_category
from .nav_categories import (CATEGORY_PANEL_KIND, MFM_NAV_CATEGORY_CHOICES, NAV_CATEGORIES,
                             NAV_CATEGORY_CHOICES, NAV_CATEGORY_LABEL, panel_axes)
from .slugs import free_slug, slug_owner
from .audited_upload import run_audited_upload
from .sheet_import import SheetError

_RTM_FIELDS = ('metric', 'low_max', 'normal_max', 'moderate_max', 'high_max')


class NavCategoryFilter(admin.SimpleListFilter):
    """The nav categories plus an explicit '— uncategorised —' facet.

    A plain ``list_filter = ('nav_category',)`` uses ChoicesFieldListFilter,
    which offers only the DECLARED choices — so there would be no facet for
    '', i.e. no way to list the rows nobody has filed yet. That is the single
    most important query the operator runs after an import.
    """
    title = 'nav category'
    parameter_name = 'nav_category'
    # NOT `choices`: that name is ListFilter's own choices(changelist) method.
    categories = NAV_CATEGORY_CHOICES

    def lookups(self, request, model_admin):
        return [('none', '— uncategorised —'), *self.categories]

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'none':
            return queryset.filter(nav_category='')
        return queryset.filter(nav_category=value) if value else queryset


class MfmNavCategoryFilter(NavCategoryFilter):
    categories = MFM_NAV_CATEGORY_CHOICES       # a meter may be filed under all eight


@admin.action(description='Pin nav category (importer stops reseeding it)')
def pin_nav_category(modeladmin, request, queryset):
    n = queryset.update(nav_category_source='manual')
    modeladmin.message_user(request, f'{n} row(s) pinned.')


@admin.action(description='Unpin nav category (let the next import reseed it)')
def unpin_nav_category(modeladmin, request, queryset):
    n = queryset.update(nav_category_source='auto')
    modeladmin.message_user(request, f'{n} row(s) unpinned.')


@admin.action(description='Clear nav category (hand it back to the importer)')
def clear_nav_category(modeladmin, request, queryset):
    # Each action sets the fields explicitly: a queryset.update() bypasses
    # save_model(), so the pin must be managed here rather than inferred.
    n = queryset.update(nav_category='', nav_category_source='auto')
    modeladmin.message_user(request, f'{n} row(s) cleared.')


_NAV_ACTIONS = (pin_nav_category, unpin_nav_category, clear_nav_category)


# --- slugs: optional in the forms, unique tree-wide ---------------------------
class _SlugFormMixin:
    def _clean_slug(self, data):
        slug = data.get('slug')
        if not slug:
            data['slug'] = free_slug(data.get('name'), self.instance)
            return
        owner = slug_owner(slug, self.instance)
        if owner:
            self.add_error('slug', f'Already used by {owner}.')


# --- distribution panels -------------------------------------------------------
_ROLE_ROW = re.compile(r'^(?P<prefix>.+)-(?P<index>\d+)-mfm$')


class _RoleForm(forms.ModelForm):
    class Meta:
        labels = {'mfm': 'MFM', 'position': 'order'}

    def _get_validation_exclusions(self):
        # (panel, meter) uniqueness is checked across ALL role inlines by
        # _RoleFormSet.clean against the submitted POST, so a meter can move
        # from one role to another in a single save; the DB constraint stays
        # the backstop.
        return set(super()._get_validation_exclusions()) | {'mfm'}


class _RoleFormSet(forms.BaseInlineFormSet):
    """One role's rows (Incomers, Outgoers or Spares) of one panel."""
    role = None

    def _construct_form(self, i, **kwargs):
        # Stamp the role BEFORE validation: Membership.clean() demands a role
        # for a membership in a panel.
        form = super()._construct_form(i, **kwargs)
        form.instance.role = self.role
        return form

    def clean(self):
        super().clean()
        mine = {}
        for form in self.forms:
            if self._should_delete_form(form) or not getattr(form, 'cleaned_data', None):
                continue
            mfm = form.cleaned_data.get('mfm')
            if mfm is None:
                continue
            if mfm.pk in mine:
                raise ValidationError(f'{mfm.name} is listed twice.')
            mine[mfm.pk] = mfm
        # The sibling role inlines post under their own prefixes in this same
        # request ('memberships', 'memberships-2', …).
        base = self.get_default_prefix()
        for key, value in self.data.items():
            m = _ROLE_ROW.match(key)
            if (not m or m['prefix'] == self.prefix
                    or not (m['prefix'] == base or m['prefix'].startswith(base + '-'))
                    or self.data.get(f"{m['prefix']}-{m['index']}-DELETE")):
                continue
            if str(value).isdigit() and int(value) in mine:
                raise ValidationError(
                    f'{mine[int(value)].name} is already listed under another role on '
                    f'this panel — a meter has one role per panel.')


class _RoleInline(admin.TabularInline):
    model = Membership
    form = _RoleForm
    formset = _RoleFormSet
    fields = ('mfm', 'position')
    autocomplete_fields = ('mfm',)
    extra = 0
    role = None

    def get_queryset(self, request):
        return super().get_queryset(request).filter(role=self.role)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.role = self.role
        return formset


class IncomerInline(_RoleInline):
    role = 'incoming'
    verbose_name, verbose_name_plural = 'Incomer', 'Incomers'


class OutgoerInline(_RoleInline):
    role = 'outgoing'
    verbose_name, verbose_name_plural = 'Outgoer', 'Outgoers'


class SpareInline(_RoleInline):
    role = 'spare'
    verbose_name, verbose_name_plural = 'Spare', 'Spares'


class PanelNameplateInline(admin.StackedInline):
    model = PanelConfig
    exclude = ('panel_kind',)            # written from the Category dropdown
    can_delete = False
    max_num = 1
    extra = 1
    verbose_name = verbose_name_plural = 'Nameplate'


class PanelConfigInline(admin.StackedInline):
    model = PanelConfig
    can_delete = True
    extra = 0


class GroupRtmInline(admin.TabularInline):      # per-panel band thresholds
    model = RtmThreshold
    fields = _RTM_FIELDS
    extra = 0


_CATEGORY_CHOICES = [('', '---------'), *((cid, label) for cid, label, _slug in NAV_CATEGORIES)]


class DistributionPanelForm(_SlugFormMixin, forms.ModelForm):
    category = forms.ChoiceField(choices=_CATEGORY_CHOICES,
                                 help_text='Where this panel shows in the EMS sidebar.')

    class Meta:
        model = DistributionPanel
        fields = ('name', 'slug', 'sort_order', 'twins', 'active')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The role inlines validate against this same instance, and
        # Membership.clean() only accepts a role inside a panel.
        self.instance.is_panel = True
        if 'slug' in self.fields:
            self.fields['slug'].required = False
            self.fields['slug'].help_text = 'Leave blank to generate it from the name.'
        if self.instance.pk and 'category' not in self.initial:
            self.initial['category'] = resolved_category(self.instance) or ''

    def clean(self):
        data = super().clean()
        if data.get('name'):
            self._clean_slug(data)
        return data


class PanelCategoryFilter(admin.SimpleListFilter):
    title = 'category'
    parameter_name = 'category'

    def lookups(self, request, model_admin):
        return [('none', '— not set on the panel —'),
                *((cid, label) for cid, label, _slug in NAV_CATEGORIES)]

    def queryset(self, request, queryset):
        value = self.value()
        structural = Q(config__panel_kind__in=list(CATEGORY_PANEL_KIND.values()))
        if value == 'none':
            return queryset.exclude(structural).filter(nav_category='')
        if value in CATEGORY_PANEL_KIND:
            return queryset.filter(config__panel_kind=CATEGORY_PANEL_KIND[value])
        if value:
            return queryset.exclude(structural).filter(nav_category=value)
        return queryset


@admin.register(DistributionPanel)
class DistributionPanelAdmin(admin.ModelAdmin):
    """A distribution panel under one of the eight categories, with its
    Incomers / Outgoers / Spares — each one an MFM."""
    form = DistributionPanelForm
    fieldsets = (
        (None, {'fields': ('name', 'category', 'slug', 'sort_order', 'active')}),
        ('Twins', {'fields': ('twins',)}),
    )
    list_display = ('name', 'category_label', 'incomers', 'outgoers', 'spares',
                    'sort_order', 'active')
    list_filter = (PanelCategoryFilter, 'active')
    search_fields = ('name', 'slug')
    autocomplete_fields = ('twins',)
    ordering = ('sort_order', 'name')
    inlines = (IncomerInline, OutgoerInline, SpareInline, PanelNameplateInline, GroupRtmInline)

    def get_queryset(self, request):
        def role_count(role):
            return Count('memberships', filter=Q(memberships__role=role))
        return (super().get_queryset(request).filter(is_panel=True)
                .select_related('config')
                .annotate(n_incoming=role_count('incoming'),
                          n_outgoing=role_count('outgoing'),
                          n_spare=role_count('spare')))

    def has_delete_permission(self, request, obj=None):
        # An imported panel is retired by the importer, never deleted here:
        # deleting would cascade its child groups and the next import would
        # recreate it with a new id.
        if obj is not None and obj.logger_id:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description='category')
    def category_label(self, obj):
        return NAV_CATEGORY_LABEL.get(resolved_category(obj), '— none —')

    @admin.display(description='incomers', ordering='n_incoming')
    def incomers(self, obj):
        return obj.n_incoming

    @admin.display(description='outgoers', ordering='n_outgoing')
    def outgoers(self, obj):
        return obj.n_outgoing

    @admin.display(description='spares', ordering='n_spare')
    def spares(self, obj):
        return obj.n_spare

    def _category_changed(self, form, change):
        return not change or 'category' in form.changed_data

    def save_model(self, request, obj, form, change):
        obj.is_panel = True
        if self._category_changed(form, change):
            # Only on a real change: re-saving an imported panel must not
            # materialise the category it merely inherits from an ancestor.
            _kind, obj.nav_category = panel_axes(form.cleaned_data['category'])
            obj.nav_category_source = 'manual'
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        form.save_m2m()
        roles = [fs for fs in formsets if isinstance(fs, _RoleFormSet)]
        # Edits and deletions across ALL roles before any new row, so moving a
        # meter from one role to another never briefly holds two rows for the
        # same (panel, meter).
        for fs in roles:
            fs.save_existing_objects()
        for fs in roles:
            fs.save_new_objects()
        for fs in formsets:
            if not isinstance(fs, _RoleFormSet):
                self.save_formset(request, form, fs, change=change)
        cfg, _created = PanelConfig.objects.get_or_create(group_id=form.instance.pk)
        if self._category_changed(form, change):
            kind, _nav = panel_axes(form.cleaned_data['category'])
            if cfg.panel_kind != kind:
                cfg.panel_kind = kind
                cfg.save(update_fields=['panel_kind'])
        from core.equipment import ensure_panel_equipment
        ensure_panel_equipment(form.instance)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    # panel_kind is shown read-only beside nav_category so the operator can see
    # BOTH placement axes at once (it lives on the PanelConfig inline, and the
    # two must never be conflated — see core/nav_categories.py).
    list_display = ('name', 'slug', 'parent', 'sort_order', 'is_panel',
                    'panel_kind', 'nav_category', 'nav_category_source',
                    'active', 'logger_id')
    list_editable = ('nav_category',)
    list_filter = (NavCategoryFilter, 'nav_category_source', 'is_panel', 'active')
    search_fields = ('name', 'slug', 'logger_id')
    autocomplete_fields = ('parent', 'twins')
    list_per_page = 50
    actions = _NAV_ACTIONS
    inlines = (PanelConfigInline, GroupRtmInline)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('config')

    @admin.display(description='panel kind', ordering='config__panel_kind')
    def panel_kind(self, obj):
        cfg = getattr(obj, 'config', None)
        return cfg.panel_kind if cfg else '—'

    def save_model(self, request, obj, form, change):
        # PIN: a hand-edited nav_category pins the row to 'manual' so the
        # importer's heuristic never reseeds it. Mirrors MFMAdmin/table_name.
        # No `change and` guard: pinning a hand-picked value on a hand-created
        # row is strictly safer than leaving it open to the next import.
        if ('nav_category' in form.changed_data
                and 'nav_category_source' not in form.changed_data):
            obj.nav_category_source = 'manual'
        super().save_model(request, obj, form, change)


# --- meters --------------------------------------------------------------------
class MfmNameplateForm(forms.ModelForm):
    class Meta:
        model = MfmConfig
        fields = '__all__'
        labels = {'energy_target_kwh_today': 'Daily energy target (kWh)'}
        help_texts = {
            'energy_target_kwh_today': (
                'Set the daily operating target used by the Overview Energy Consumption Today card. '
                'An entered value overrides the automatic rated-power × 24 target. '
                'Leave blank to use the existing fallback; zero is an explicit value, not blank.'),
        }


class MfmConfigInline(admin.StackedInline):
    model = MfmConfig
    form = MfmNameplateForm
    verbose_name = 'MFM nameplate configuration'
    verbose_name_plural = 'MFM nameplate configuration'
    fieldsets = (
        ('Daily energy target', {'fields': ('energy_target_kwh_today',)}),
        ('Ratings and other nameplate settings', {'fields': tuple(
            f.name for f in MfmConfig._meta.fields
            if f.editable and not f.primary_key and f.name not in
            {'mfm', 'energy_target_kwh_today'})}),
    )
    can_delete = True
    extra = 0


class MFMForm(_SlugFormMixin, forms.ModelForm):
    class Meta:
        model = MFM
        fields = '__all__'
        labels = {'logger_id': 'LoggerFast device', 'nav_category': 'Category'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fields = self.fields
        if 'name' in fields:
            fields['name'].required = False
            fields['name'].help_text = 'Leave blank to use the LoggerFast device name.'
        if 'slug' in fields:
            fields['slug'].required = False
            fields['slug'].help_text = 'Leave blank to generate it from the name.'
        if 'logger_id' in fields:
            self._device_picker(fields['logger_id'])
        if 'logger_schema' in fields:
            current = self.instance.logger_schema_id
            fields['logger_schema'].queryset = LoggerSchema.objects.filter(
                Q(active=True) | Q(pk=current))

    def _device_picker(self, field):
        rows = logger_devices.devices()
        if not rows:
            field.help_text = 'LoggerFast is unreachable — type the device id (dev_…).'
            return
        taken = set(MFM.objects.exclude(logger_id=None).exclude(pk=self.instance.pk)
                    .values_list('logger_id', flat=True))
        choices = [('', '— not linked —')]
        for dev in sorted(rows.values(), key=lambda d: d['name'].lower()):
            if dev['id'] not in taken:
                table = dev['table_name'] or ('several tables' if dev['ambiguous'] else 'no table')
                choices.append((dev['id'], f"{dev['name']}  ·  {table}"))
        current = self.instance.logger_id
        if current and current not in rows:
            choices.append((current, f'{current} (not in LoggerFast)'))
        field.widget = forms.Select(choices=choices)
        field.help_text = ('Picking a device fills in the table name, the data source '
                           'and the device details.')

    def linked_device(self):
        """The LoggerFast device picked in THIS save, or None."""
        if 'logger_id' not in self.changed_data:
            return None
        return logger_devices.device(self.cleaned_data.get('logger_id'))

    def clean(self):
        data = super().clean()
        dev = self.linked_device()
        if dev and not data.get('name'):
            data['name'] = dev['name']
        if data.get('name'):
            self._clean_slug(data)
        elif 'name' not in self.errors:
            self.add_error('name', 'Give the meter a name, or pick a LoggerFast device.')
        schema = data.get('logger_schema')
        if ('logger_schema' in self.changed_data and schema is not None
                and not schema.active):
            self.add_error('logger_schema', 'A retired schema cannot receive a new assignment.')
        return data


class AuditedWorkbookForm(forms.Form):
    file = forms.FileField(
        label='EMS workbook (.xlsx)',
        help_text=('Upload a workbook like ems_import_template.xlsx. Preview first; '
                   'nothing is saved until you choose Commit.'),
    )

    def clean_file(self):
        upload = self.cleaned_data['file']
        if not upload.name.lower().endswith(('.xlsx', '.xlsm')):
            raise forms.ValidationError('Upload an .xlsx or .xlsm workbook.')
        if upload.size > 10 * 1024 * 1024:
            raise forms.ValidationError('The workbook must be 10 MB or smaller.')
        return upload


class MfmThresholdInline(admin.StackedInline):
    model = MfmThreshold
    extra = 0
    max_num = 1


@admin.register(MFM)
class MFMAdmin(admin.ModelAdmin):
    form = MFMForm
    change_list_template = 'admin/core/mfm/change_list.html'
    fieldsets = (
        (None, {'fields': ('logger_id', 'name', 'slug', 'nav_category', 'active')}),
        ('Equipment and placement', {'fields': ('equipment', 'reference', 'asset_category',
                                                'load_profile', 'parent_series', 'sheet_row',
                                                'section', 'zone')}),
        ('Data binding', {'fields': ('table_name', 'data_source', 'logger_schema',
                                     'table_binding', 'logger_table_id', 'series_id')}),
        ('Per-meter nameplate', {'fields': ('rated_capacity_kva', 'nominal_voltage_v',
                                            'nameplate_voltage_kv', 'frequency_hz',
                                            'fault_level_ka')}),
        # No 'collapse' classes: Jazzmin's tab layout turns them into panes
        # that can never be shown.
        ('Energy corrections', {'fields': ('energy_direction', 'energy_scale', 'power_scale')}),
        ('Device details', {'fields': ('protocol', 'gateway_id', 'unit_id', 'port',
                                       'logger_status')}),
        ('Import', {'fields': ('nav_category_source', 'imported_at', 'retired_at')}),
    )
    readonly_fields = ('imported_at', 'retired_at')
    list_display = ('name', 'placement', 'nav_category', 'table_name', 'logger_schema', 'data_source',
                    'table_binding', 'energy_direction', 'energy_scale', 'power_scale',
                    'active', 'logger_id')
    # grid-edit the binding straight from the list (bottom Save bulk-saves).
    list_editable = ('table_name', 'table_binding', 'energy_direction',
                     'energy_scale', 'power_scale', 'nav_category')
    list_filter = ('logger_schema', 'table_binding', 'energy_direction', MfmNavCategoryFilter,
                   'nav_category_source', 'active', 'data_source')
    search_fields = ('name', 'slug', 'logger_id', 'table_name')
    autocomplete_fields = ('data_source', 'equipment', 'reference')
    list_per_page = 50
    actions = _NAV_ACTIONS
    inlines = (MfmConfigInline, MfmThresholdInline)

    def get_urls(self):
        custom = [path('import-workbook/', self.admin_site.admin_view(self.import_workbook),
                       name='core_mfm_import_workbook')]
        return custom + super().get_urls()

    def import_workbook(self, request):
        """Session-authenticated, CSRF-protected preview/commit of the plant sheet."""
        if not request.user.is_superuser:
            raise PermissionDenied
        report = None
        form = AuditedWorkbookForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            upload = form.cleaned_data['file']
            try:
                report = run_audited_upload(
                    upload.name, upload.read(), commit=request.POST.get('action') == 'commit')
            except SheetError as exc:
                form.add_error('file', str(exc))
        context = {
            **self.admin_site.each_context(request),
            'title': 'Import EMS workbook', 'opts': self.model._meta,
            'form': form, 'report': report,
            'back_url': reverse('admin:core_mfm_changelist'),
        }
        return TemplateResponse(request, 'admin/core/mfm/import_workbook.html', context)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('memberships__group')

    def get_search_results(self, request, queryset, search_term):
        qs, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        if request.path.endswith('/autocomplete/'):
            qs = qs.filter(active=True)            # the panel inlines' meter picker
        return qs, may_have_duplicates

    @admin.display(description='shows under')
    def placement(self, obj):
        rows = [m for m in obj.memberships.all() if m.group.active]
        in_panels = [f'{m.group.name} ({m.role})' for m in rows if m.group.is_panel]
        if in_panels:
            return ', '.join(in_panels)
        if rows:
            return f'group {rows[0].group.name}'
        if obj.nav_category and obj.nav_category_source == 'manual':
            return NAV_CATEGORY_LABEL.get(obj.nav_category, obj.nav_category)
        return '—'

    def save_model(self, request, obj, form, change):
        linked = getattr(form, 'linked_device', None)
        dev = linked() if linked else None
        if dev:
            obj.protocol, obj.gateway_id = dev['protocol'], dev['gateway_id']
            obj.unit_id, obj.port, obj.logger_status = dev['unit_id'], dev['port'], dev['status']
            if 'table_name' not in form.changed_data:
                obj.table_name, obj.logger_table_id = dev['table_name'], dev['table_id']
        # PIN 2026-09-09: a hand-set table_name (typed, or from the picked
        # device) pins the row to 'manual' so the importer's auto-binding never
        # overwrites it.
        if (('table_name' in form.changed_data or dev)
                and 'table_binding' not in form.changed_data):
            obj.table_binding = 'manual'
        if obj.table_name and obj.data_source_id is None:
            obj.data_source = logger_devices.data_source_for(dev['db_target_id'] if dev else '')
        # Same discipline for the nav category (see GroupAdmin.save_model); a
        # meter created straight under a category is pinned from the start.
        if ('nav_category_source' not in form.changed_data
                and ('nav_category' in form.changed_data
                     or (not change and obj.nav_category))):
            obj.nav_category_source = 'manual'
        super().save_model(request, obj, form, change)
        # the socket layer caches direction/scale per (dsn, table)
        from core.services import reset_energy_directions
        reset_energy_directions()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        from core.equipment import ensure_mfm_equipment, ensure_primary_reference
        mfm = MFM.objects.select_related('config', 'equipment').get(pk=form.instance.pk)
        ensure_mfm_equipment(mfm)
        ensure_primary_reference(mfm)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('group', 'mfm', 'role', 'position')
    list_filter = ('role', 'group__is_panel')
    search_fields = ('group__name', 'group__slug', 'mfm__name', 'mfm__slug')
    autocomplete_fields = ('group', 'mfm')


@admin.register(FeedEdge)
class FeedEdgeAdmin(admin.ModelAdmin):
    list_display = ('from_mfm', 'to_mfm', 'edge_type')
    list_filter = ('edge_type',)
    search_fields = ('from_mfm__name', 'to_mfm__name', 'from_mfm__slug', 'to_mfm__slug')
    autocomplete_fields = ('from_mfm', 'to_mfm')


@admin.register(RtmThreshold)
class RtmThresholdAdmin(admin.ModelAdmin):
    change_list_template = 'admin/core/rtmthreshold/change_list.html'
    list_display = ('panel_label', *_RTM_FIELDS[1:])
    list_editable = _RTM_FIELDS[1:]
    list_filter = (('group', admin.RelatedOnlyFieldListFilter), 'metric')
    search_fields = ('group__name', 'group__slug')
    list_per_page = 50
    autocomplete_fields = ('group', 'panel_type')
    ordering = ('group__name', 'panel_type__name', 'metric')
    list_select_related = ('group', 'panel_type')
    metric_views = (
        ('pf', 'Power Factor', 'Raw PF; higher is better. Boundaries run downward. Equality stays in the better band.'),
        ('kw', 'Active Power', 'Percentage of rated kW, not raw kW.'),
        ('kva', 'Apparent Power', 'Percentage of rated kVA, not raw kVA.'),
        ('kvar', 'Reactive Power', 'Reactive kVAr / apparent kVA × 100.'),
        ('amp', 'Current', 'Classified using percentage of rated kVA, not raw amps.'),
        ('volt', 'Voltage Deviation', 'Absolute voltage deviation from nominal (%).'),
        ('i_unbal', 'Current Unbalance', 'Current unbalance (%).'),
        ('v_unbal', 'Voltage Unbalance', 'Voltage unbalance (%).'),
    )

    @admin.display(description='Distribution panel / type default')
    def panel_label(self, obj):
        return obj.group.name if obj.group_id else f'Type default: {obj.panel_type.name}'

    def changelist_view(self, request, extra_context=None):
        selected = request.GET.get('metric__exact') or request.GET.get('metric')
        if request.method == 'GET' and not selected:
            query = request.GET.copy()
            query['metric__exact'] = 'pf'
            return HttpResponseRedirect(f'{request.path}?{query.urlencode()}')
        tabs = [{'label': label, 'url': f'?metric__exact={key}', 'active': selected == key}
                for key, label, _ in self.metric_views]
        label, help_text = next(((label, help_text) for key, label, help_text in self.metric_views
                                 if key == selected), ('Thresholds', ''))
        return super().changelist_view(request, {
            **(extra_context or {}), 'metric_tabs': tabs,
            'metric_title': label, 'metric_help': help_text,
            'metric_descending': selected == 'pf',
        })

    def get_changeform_initial_data(self, request):
        data = super().get_changeform_initial_data(request)
        metric = request.GET.get('metric__exact') or request.GET.get('metric')
        if metric in dict(RtmThreshold.METRIC_CHOICES):
            data['metric'] = metric
        return data


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'dsn', 'logger_db_target_id')
    search_fields = ('name', 'dsn', 'logger_db_target_id')


class LoggerSchemaFieldInline(admin.TabularInline):
    model = LoggerSchemaField
    extra = 0
    fields = ('position', 'name', 'kind', 'data_type', 'unit', 'address', 'scale',
              'evaluation_order', 'formula', 'description')
    ordering = ('position',)


@admin.register(LoggerSchema)
class LoggerSchemaAdmin(admin.ModelAdmin):
    list_display = ('code', 'version', 'name', 'logger_id', 'timestamp_column',
                    'active', 'field_count')
    list_filter = ('active',)
    search_fields = ('code', 'name', 'logger_id')
    inlines = (LoggerSchemaFieldInline,)

    @admin.display(description='fields', ordering='field_total')
    def field_count(self, obj):
        return obj.field_total

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(field_total=Count('fields'))


@admin.register(LoggerSchemaField)
class LoggerSchemaFieldAdmin(admin.ModelAdmin):
    list_display = ('name', 'logger_schema', 'kind', 'data_type', 'unit', 'address',
                    'scale', 'evaluation_order')
    list_filter = ('logger_schema', 'kind', 'data_type')
    search_fields = ('name', 'logger_schema__code', 'formula', 'description')
    list_per_page = 100


@admin.register(ViewerAsset)
class ViewerAssetAdmin(admin.ModelAdmin):
    list_display = ('label', 'slug', 'url', 'rating', 'active')
    list_filter = ('active',)
    search_fields = ('label', 'slug', 'url', 'rating')


@admin.register(ViewerRule)
class ViewerRuleAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'for_key', 'for_type', 'rating', 'asset',
                    'priority', 'active')
    list_filter = ('active', 'for_type', 'asset')
    search_fields = ('for_key', 'for_type', 'rating', 'asset__slug')
    autocomplete_fields = ('asset',)


@admin.register(ViewerSetting)
class ViewerSettingAdmin(admin.ModelAdmin):
    list_display = ('key',)
    search_fields = ('key',)


@admin.register(AssetType)
class EquipmentTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')


class TypeRtmInline(admin.TabularInline):
    model = RtmThreshold
    fields = _RTM_FIELDS
    extra = 0


@admin.register(PanelType)
class PanelTypeAdmin(EquipmentTypeAdmin):
    inlines = (TypeRtmInline,)


class EquipmentConfigInline(admin.StackedInline):
    model = EquipmentConfig
    extra = 0
    max_num = 1


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'key', 'type_label', 'distribution_panel', 'metered', 'group')
    search_fields = ('name', 'key')
    autocomplete_fields = ('panel_type', 'asset_type', 'group')
    list_filter = ('distribution_panel', 'metered', 'panel_type', 'asset_type')
    inlines = (EquipmentConfigInline,)

    @admin.display(description='type')
    def type_label(self, obj):
        return obj.panel_type or obj.asset_type


@admin.register(Nameplate)
class NameplateAdmin(admin.ModelAdmin):
    list_display = ('equipment', 'key', 'value', 'unit', 'normalized_value', 'source')
    search_fields = ('equipment__name', 'equipment__key', 'key', 'value', 'source')
    autocomplete_fields = ('equipment',)


class MfmThresholdPageFilter(admin.SimpleListFilter):
    title = 'Page'
    parameter_name = 'threshold_page'

    def lookups(self, request, model_admin):
        return (('overview', 'Overview'), ('power-quality', 'Power Quality'),
                ('voltage-current', 'Voltage & Current'))

    def queryset(self, request, queryset):
        # A page selects editable columns, not meters. Keep every MFM visible.
        return queryset


@admin.register(MfmThreshold)
class MfmThresholdAdmin(admin.ModelAdmin):
    change_list_template = 'admin/core/mfmthreshold/change_list.html'
    threshold_fields = tuple(f.name for f in MfmThreshold._meta.fields
                             if f.name not in {'id', 'mfm', 'updated_at'})
    list_display = ('mfm', *threshold_fields, 'updated_at')
    list_editable = threshold_fields
    list_per_page = 25
    search_fields = ('mfm__name', 'mfm__slug', 'mfm__table_name')
    autocomplete_fields = ('mfm',)
    list_filter = (MfmThresholdPageFilter,)
    list_select_related = ('mfm',)

    def page_fields(self, request):
        page = request.GET.get('threshold_page')
        if page == 'overview':
            return tuple(f for f in self.threshold_fields if f.startswith('overview_'))
        if page == 'power-quality':
            return tuple(f for f in self.threshold_fields if not f.startswith('overview_'))
        if page == 'voltage-current':
            return ('overview_phase_watch_pct', 'overview_phase_unbalanced_pct')
        return self.threshold_fields

    def get_list_display(self, request):
        return ('mfm', *self.page_fields(request), 'updated_at')

    def get_changelist_instance(self, request):
        changelist = super().get_changelist_instance(request)
        if changelist.list_editable:
            changelist.list_editable = self.page_fields(request)
        return changelist

    def get_changelist_formset(self, request, **kwargs):
        options = {'formfield_callback': partial(self.formfield_for_dbfield, request=request),
                   **kwargs, 'fields': self.page_fields(request)}
        return forms.modelformset_factory(
            self.model, self.get_changelist_form(request), extra=0, **options)


class DistributionThresholdPageFilter(admin.SimpleListFilter):
    title = 'Page'
    parameter_name = 'threshold_page'

    def lookups(self, request, model_admin):
        return (('voltage-current', 'Voltage & Current'),
                ('power-quality', 'Harmonics & PQ'))

    def queryset(self, request, queryset):
        return queryset


@admin.register(DistributionPanelThreshold)
class DistributionPanelThresholdAdmin(admin.ModelAdmin):
    change_list_template = 'admin/core/distributionpanelthreshold/change_list.html'
    fields = ('group', 'vc_vdev_warn_pct', 'vc_vdev_danger_pct',
              'vc_iunbal_warn_pct', 'vc_iunbal_danger_pct', 'thd_i_limit_pct',
              'thd_v_limit_pct', 'true_pf_min', 'neutral_current_limit_a')
    list_display = fields
    list_editable = fields[1:]
    autocomplete_fields = ('group',)
    search_fields = ('group__name', 'group__slug')
    list_filter = (DistributionThresholdPageFilter,)
    list_select_related = ('group',)
    ordering = ('group__name',)

    def page_fields(self, request):
        page = request.GET.get('threshold_page')
        if page == 'voltage-current':
            return tuple(f for f in self.fields[1:] if f.startswith('vc_'))
        if page == 'power-quality':
            return tuple(f for f in self.fields[1:] if not f.startswith('vc_'))
        return self.fields[1:]

    def get_list_display(self, request):
        return ('group', *self.page_fields(request))

    def get_changelist_instance(self, request):
        changelist = super().get_changelist_instance(request)
        if changelist.list_editable:
            changelist.list_editable = self.page_fields(request)
        return changelist

    def get_changelist_formset(self, request, **kwargs):
        options = {'formfield_callback': partial(self.formfield_for_dbfield, request=request),
                   **kwargs, 'fields': self.page_fields(request)}
        return forms.modelformset_factory(
            self.model, self.get_changelist_form(request), extra=0, **options)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(group__is_panel=True)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'group':
            kwargs['queryset'] = Group.objects.filter(is_panel=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Breaker)
class BreakerAdmin(admin.ModelAdmin):
    list_display = ('mfm', 'breaker_type', 'rating_a', 'source', 'panel_key')
    list_filter = ('breaker_type', 'source')
    search_fields = ('mfm__name', 'mfm__slug', 'mfm__table_name', 'panel_key', 'glb_node')
    autocomplete_fields = ('mfm',)


# --- the EMS Nav overview page ---------------------------------------------------
_ROLE_TITLES = {'incoming': 'Incomers', 'outgoing': 'Outgoers', 'spare': 'Spares'}


@admin.register(EmsNav)
class EmsNavAdmin(admin.ModelAdmin):
    """The eight categories exactly as the sidebar renders them (it IS
    ``build_tree()``), with an add-panel / add-MFM link on each. Read-only:
    every edit happens on the Distribution Panel / MFM pages it links to."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        data = build_tree()
        bound = set(MFM.objects.filter(active=True, data_source__isnull=False)
                    .exclude(table_name='').values_list('pk', flat=True))

        def meter(node):
            pk = node['mfm_id'] - FEEDER_ID_OFFSET
            return {'name': node['label'], 'bound': pk in bound,
                    'url': reverse('admin:core_mfm_change', args=[pk])}

        categories = []
        for cat in data['tree']:
            panels, meters = [], []
            for node in cat['children']:
                if node.get('distribution_panel'):
                    panels.append({
                        'name': node['label'],
                        'url': reverse('admin:core_distributionpanel_change',
                                       args=[node['mfm_id']]),
                        'roles': [(_ROLE_TITLES[r['slug']], [meter(x) for x in r['children']])
                                  for r in node['children'] if r.get('group')],
                    })
                else:
                    meters.append(meter(node))
            categories.append({'id': cat['category'], 'label': cat['label'],
                               'panels': panels, 'meters': meters})

        context = {
            **self.admin_site.each_context(request),
            'title': 'EMS Nav',
            'opts': self.model._meta,
            'data': data,
            'categories': categories,
            'panel_add_url': reverse('admin:core_distributionpanel_add'),
            'mfm_add_url': reverse('admin:core_mfm_add'),
            'panels_url': reverse('admin:core_distributionpanel_changelist'),
            'mfms_url': reverse('admin:core_mfm_changelist'),
            'import_url': reverse('admin:core_mfm_import_workbook'),
            **(extra_context or {}),
        }
        return TemplateResponse(request, 'admin/core/ems_nav.html', context)
