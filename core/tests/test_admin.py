"""Admin: the nav-category pin, and the filter that can find blank rows.

The pin is the whole no-clobber contract seen from the operator's side —
assigning a category must mark the row 'manual' so the next import leaves it
alone. Django calls save_model() per changed row for list_editable, so this is
also what makes a bulk changelist edit pin every row it touches.
"""
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from core.admin import (GroupAdmin, MFMAdmin, NavCategoryFilter,
                        clear_nav_category, pin_nav_category, unpin_nav_category)
from core.models import Group, MFM


class _FakeForm:
    def __init__(self, *changed):
        self.changed_data = list(changed)


class SaveModelPinTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.request = RequestFactory().post('/')

    def test_editing_a_groups_nav_category_pins_it(self):
        admin = GroupAdmin(Group, self.site)
        group = Group.objects.create(logger_id='g1', name='AHU', slug='ahu',
                                     nav_category='hvac')
        admin.save_model(self.request, group, _FakeForm('nav_category'), change=True)
        self.assertEqual(Group.objects.get(pk=group.pk).nav_category_source, 'manual')

    def test_an_unrelated_edit_leaves_the_pin_alone(self):
        admin = GroupAdmin(Group, self.site)
        group = Group.objects.create(logger_id='g1', name='AHU', slug='ahu')
        admin.save_model(self.request, group, _FakeForm('name'), change=True)
        self.assertEqual(Group.objects.get(pk=group.pk).nav_category_source, 'auto')

    def test_setting_the_source_explicitly_is_not_overridden(self):
        admin = GroupAdmin(Group, self.site)
        group = Group.objects.create(logger_id='g1', name='AHU', slug='ahu',
                                     nav_category='hvac', nav_category_source='auto')
        admin.save_model(self.request, group,
                         _FakeForm('nav_category', 'nav_category_source'), change=True)
        self.assertEqual(Group.objects.get(pk=group.pk).nav_category_source, 'auto')

    def test_editing_a_meters_nav_category_pins_it(self):
        admin = MFMAdmin(MFM, self.site)
        mfm = MFM.objects.create(logger_id='d1', name='A', slug='a', nav_category='hvac')
        admin.save_model(self.request, mfm, _FakeForm('nav_category'), change=True)
        self.assertEqual(MFM.objects.get(pk=mfm.pk).nav_category_source, 'manual')

    def test_the_table_name_pin_still_works_alongside_it(self):
        """Regression guard on the pre-existing binding pin, which now shares
        MFMAdmin.save_model with the category pin."""
        admin = MFMAdmin(MFM, self.site)
        mfm = MFM.objects.create(logger_id='d1', name='A', slug='a', table_name='t')
        admin.save_model(self.request, mfm, _FakeForm('table_name'), change=True)
        row = MFM.objects.get(pk=mfm.pk)
        self.assertEqual(row.table_binding, 'manual')
        self.assertEqual(row.nav_category_source, 'auto')


class NavCategoryActionTests(TestCase):
    def setUp(self):
        self.admin = GroupAdmin(Group, AdminSite())
        self.request = RequestFactory().post('/')
        self.request._messages = _SilentMessages()
        Group.objects.create(logger_id='g1', name='AHU', slug='ahu',
                             nav_category='hvac', nav_category_source='auto')

    def test_pin_marks_the_rows_manual(self):
        pin_nav_category(self.admin, self.request, Group.objects.all())
        self.assertEqual(Group.objects.get(slug='ahu').nav_category_source, 'manual')

    def test_unpin_hands_the_rows_back_to_the_importer(self):
        Group.objects.update(nav_category_source='manual')
        unpin_nav_category(self.admin, self.request, Group.objects.all())
        self.assertEqual(Group.objects.get(slug='ahu').nav_category_source, 'auto')

    def test_clear_blanks_the_value_and_unpins(self):
        Group.objects.update(nav_category_source='manual')
        clear_nav_category(self.admin, self.request, Group.objects.all())
        row = Group.objects.get(slug='ahu')
        self.assertEqual(row.nav_category, '')
        self.assertEqual(row.nav_category_source, 'auto')


class NavCategoryFilterTests(TestCase):
    def setUp(self):
        Group.objects.create(logger_id='g1', name='AHU', slug='ahu', nav_category='hvac')
        Group.objects.create(logger_id='g2', name='Mystery', slug='mystery')

    def _filter(self, value):
        # Django builds `params` from request.GET.lists(), so every value is a
        # LIST; a bare string makes SimpleListFilter.value() return its last
        # character. Mirror the real calling convention.
        params = {'nav_category': [value]} if value else {}
        request = RequestFactory().get('/', params)
        return NavCategoryFilter(request, params, Group, GroupAdmin)

    def test_the_uncategorised_facet_exists_and_finds_blank_rows(self):
        lookups = dict(self._filter(None).lookups(None, None))
        self.assertIn('none', lookups)
        rows = self._filter('none').queryset(None, Group.objects.all())
        self.assertEqual([g.slug for g in rows], ['mystery'])

    def test_a_category_facet_filters_to_it(self):
        rows = self._filter('hvac').queryset(None, Group.objects.all())
        self.assertEqual([g.slug for g in rows], ['ahu'])

    def test_no_selection_returns_everything(self):
        rows = self._filter(None).queryset(None, Group.objects.all())
        self.assertEqual(rows.count(), 2)


class _SilentMessages:
    """django.contrib.messages needs a backend; the actions only ever add."""

    def add(self, level, message, extra_tags):
        pass
