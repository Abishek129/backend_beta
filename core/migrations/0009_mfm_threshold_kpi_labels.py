from django.db import migrations, models


LABELS = {
    'i_thd_limit_pct': 'Power Quality — Current THD limit (%)',
    'v_thd_limit_pct': 'Power Quality — Voltage THD limit (%)',
    'individual_harmonic_limit_pct': 'Power Quality — Individual harmonic limit (%)',
    'flicker_pst_limit': 'Power Quality — Flicker Pst limit',
    'crest_factor_ideal': 'Power Quality — Ideal crest factor',
    'overview_busbar_temp_warn_c': 'Busbar Temperature — Warning above (°C)',
    'overview_busbar_temp_critical_c': 'Busbar Temperature — Critical above (°C)',
    'overview_load_elevated_pct': 'Rated Capacity Utilization — Elevated above (%)',
    'overview_load_critical_pct': 'Rated Capacity Utilization — Critical above (%)',
    'overview_pf_good': 'Power Factor — Good at or above',
    'overview_pf_fair': 'Power Factor — Fair at or above',
    'overview_freq_nominal_hz': 'Grid Frequency — Nominal (Hz)',
    'overview_freq_fair_dev_hz': 'Grid Frequency — Fair deviation above (Hz)',
    'overview_freq_unstable_dev_hz': 'Grid Frequency — Unstable deviation above (Hz)',
    'overview_phase_watch_pct': 'Current Unbalance — Watch above (%)',
    'overview_phase_unbalanced_pct': 'Current Unbalance — Unbalanced above (%)',
    'overview_voltage_dev_elevated_pct': 'Voltage Deviation — Elevated above (%)',
    'overview_voltage_dev_critical_pct': 'Voltage Deviation — Critical above (%)',
    'overview_energy_elevated_pct': 'Energy Consumption Today — Elevated above target usage (%)',
    'overview_energy_critical_pct': 'Energy Consumption Today — Critical above target usage (%)',
}


class Migration(migrations.Migration):
    dependencies = [('core', '0008_rtm_type_thresholds')]
    operations = [migrations.AlterField(
        model_name='mfmthreshold', name=name,
        field=models.FloatField(verbose_name=label, null=True, blank=True))
        for name, label in LABELS.items()]
