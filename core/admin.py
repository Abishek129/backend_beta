from django.contrib import admin

from .models import (DataSource, FeedEdge, Group, Membership, MFM, MfmConfig,
                     PanelConfig, RtmThreshold)
from .nav_categories import NAV_CATEGORY_CHOICES

_RTM_FIELDS = ('metric', 'low_max', 'normal_max', 'moderate_max', 'high_max')


class NavCategoryFilter(admin.SimpleListFilter):
    """The four nav categories plus an explicit '— uncategorised —' facet.

    A plain ``list_filter = ('nav_category',)`` uses ChoicesFieldListFilter,
    which offers only the DECLARED choices — so there would be no facet for
    '', i.e. no way to list the rows nobody has filed yet. That is the single
    most important query the operator runs after an import.
    """
    title = 'nav category'
    parameter_name = 'nav_category'

    def lookups(self, request, model_admin):
        return [('none', '— uncategorised —'), *NAV_CATEGORY_CHOICES]

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'none':
            return queryset.filter(nav_category='')
        return queryset.filter(nav_category=value) if value else queryset


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


class PanelConfigInline(admin.StackedInline):
    model = PanelConfig
    can_delete = True
    extra = 0


class GroupRtmInline(admin.TabularInline):      # per-panel band thresholds
    model = RtmThreshold
    fields = _RTM_FIELDS
    extra = 0


class MfmConfigInline(admin.StackedInline):
    model = MfmConfig
    can_delete = True
    extra = 0


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


@admin.register(MFM)
class MFMAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'data_source', 'table_name', 'table_binding',
                    'energy_direction', 'energy_scale', 'power_scale',
                    'nav_category', 'nav_category_source', 'active',
                    'logger_id')
    # grid-edit the binding straight from the list (bottom Save bulk-saves).
    list_editable = ('table_name', 'table_binding', 'energy_direction',
                     'energy_scale', 'power_scale', 'nav_category')
    list_filter = ('table_binding', 'energy_direction', NavCategoryFilter,
                   'nav_category_source', 'active', 'data_source')
    search_fields = ('name', 'slug', 'logger_id', 'table_name')
    autocomplete_fields = ('data_source',)
    list_per_page = 50
    actions = _NAV_ACTIONS
    inlines = (MfmConfigInline,)

    def save_model(self, request, obj, form, change):
        # PIN 2026-09-09: a hand-edited table_name pins the row to 'manual' so the
        # importer's auto-binding never overwrites it.
        if change and 'table_name' in form.changed_data and 'table_binding' not in form.changed_data:
            obj.table_binding = 'manual'
        # Same discipline for the nav category (see GroupAdmin.save_model).
        if ('nav_category' in form.changed_data
                and 'nav_category_source' not in form.changed_data):
            obj.nav_category_source = 'manual'
        super().save_model(request, obj, form, change)


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
    list_display = ('__str__', 'group', 'metric', *_RTM_FIELDS[1:])
    list_editable = _RTM_FIELDS[1:]
    list_filter = (('group', admin.RelatedOnlyFieldListFilter), 'metric')
    search_fields = ('group__name', 'group__slug')
    list_per_page = 50
    autocomplete_fields = ('group',)


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'dsn', 'logger_db_target_id')
    search_fields = ('name', 'dsn', 'logger_db_target_id')
