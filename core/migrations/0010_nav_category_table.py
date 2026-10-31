from django.db import migrations, models


# The eight categories as core.nav_categories shipped them, copied rather than
# imported (see 0002): live code would drift under a data migration. APFC and
# HVAC keep the "omitted while empty" rule they already had.
SEED = [
    ('ht', 'HT Panels', 'ht-panels', 'ht_panel', False),
    ('amf', 'AMF Panels', 'amf-panels', 'amf_panel', False),
    ('pcc', 'PCC Panels', 'pcc-panels', 'pcc_panel', False),
    ('ups', 'UPS Panels', 'ups-panels', 'ups_panel', False),
    ('apfc', 'APFC Panels', 'apfc-panels', 'distribution_panel', True),
    ('production', 'Production Panels', 'production-panels', 'distribution_panel', False),
    ('utility', 'Utility Panels', 'utility-panels', 'distribution_panel', False),
    ('hvac', 'HVAC Panels', 'hvac-panels', 'distribution_panel', True),
]
STRUCTURAL = {kind: code for code, _label, _slug, kind, _hide in SEED
              if kind != 'distribution_panel'}


def seed(apps, schema_editor):
    NavCategory = apps.get_model('core', 'NavCategory')
    for i, (code, label, slug, kind, hide) in enumerate(SEED):
        NavCategory.objects.get_or_create(code=code, defaults={
            'label': label, 'slug': slug, 'sort_order': i,
            'panel_kind': kind, 'hide_when_empty': hide})

    # Until now a structural panel_kind beat any nav_category. A hand-set
    # category now wins, so a panel carrying both gets its category rewritten
    # to the one it already renders under — nothing moves in the sidebar.
    Group = apps.get_model('core', 'Group')
    for group in (Group.objects.filter(is_panel=True, nav_category_source='manual',
                                       config__panel_kind__in=list(STRUCTURAL))
                  .exclude(nav_category='').select_related('config')):
        code = STRUCTURAL[group.config.panel_kind]
        if group.nav_category != code:
            group.nav_category = code
            group.save(update_fields=['nav_category'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_mfm_threshold_kpi_labels'),
    ]

    operations = [
        migrations.CreateModel(
            name='NavCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(help_text='Stable id stored on panels and meters; the FE "category" value.', max_length=32, unique=True)),
                ('label', models.CharField(max_length=100)),
                ('slug', models.SlugField(help_text='Route segment: /electrical/<slug>.', max_length=100, unique=True)),
                ('sort_order', models.IntegerField(default=0)),
                ('panel_kind', models.CharField(choices=[('distribution_panel', 'Distribution Panel'), ('pcc_panel', 'PCC Panel'), ('ht_panel', 'HT Panel'), ('amf_panel', 'AMF Panel'), ('ups_panel', 'UPS Panel')], default='distribution_panel', help_text='Kind a panel filed here gets by default. A non-default kind also places panels nobody has filed by hand (the importer seeds kinds).', max_length=24)),
                ('hide_when_empty', models.BooleanField(default=False, help_text='Leave the category out of /api/ems/ while it is empty.')),
                ('active', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'EMS Nav Category',
                'verbose_name_plural': 'EMS Nav Categories',
                'db_table': 'nav_category',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.AlterField(
            model_name='group',
            name='nav_category',
            field=models.CharField(blank=True, db_index=True, default='', help_text='NavCategory code. Set by hand it always places the panel; an importer guess yields to PanelConfig.panel_kind. Blank = placed by panel_kind, else the nearest ancestor. On a plain group it is also the fallback for the loose meters beneath it.', max_length=32),
        ),
        migrations.AlterField(
            model_name='mfm',
            name='nav_category',
            field=models.CharField(blank=True, db_index=True, default='', help_text='NavCategory code. Only for a meter that is NOT in a distribution panel: it then shows directly under this category. Ignored for a panel incomer/outgoer/spare (the panel places it).', max_length=32),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
