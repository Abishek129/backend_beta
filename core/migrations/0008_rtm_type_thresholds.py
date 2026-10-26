import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0007_viewer_catalog')]
    operations = [
        migrations.CreateModel(
            name='DistributionPanelThreshold', fields=[],
            options={'proxy': True, 'indexes': [], 'constraints': [],
                     'verbose_name': 'Distribution Panel Threshold',
                     'verbose_name_plural': 'Distribution Panel Thresholds'},
            bases=('core.panelconfig',)),
        migrations.AlterField(
            model_name='rtmthreshold', name='group',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='rtm_thresholds', to='core.group')),
        migrations.AddField(
            model_name='rtmthreshold', name='panel_type',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='rtm_thresholds', to='core.paneltype')),
        migrations.AddConstraint(model_name='rtmthreshold', constraint=models.UniqueConstraint(
            fields=('panel_type', 'metric'), name='uniq_rtm_type_metric')),
        migrations.AddConstraint(model_name='rtmthreshold', constraint=models.CheckConstraint(
            condition=(models.Q(group__isnull=False, panel_type__isnull=True) |
                       models.Q(group__isnull=True, panel_type__isnull=False)),
            name='rtm_exactly_one_scope')),
    ]
