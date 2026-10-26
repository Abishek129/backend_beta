# EMS structure

Generated from the backend database (cmd_equipment_beta) on 2026-10-05.

## Distribution panels

### 33KV HT Panel

- Category: **HT Panels**
- Panel id: 2 · slug: `33-kv` · kind: `ht_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | I/C_33KV_MAIN_VCB | `gic_01_n4_I_C_33KV_MAIN_VCB` | — |
| Outgoer | OG_33_415kV_TF_1 | `gic_01_n1_33_415V_TF_1` | — |
| Outgoer | OG_33_415kV_TF_2 | `gic_01_n2_33_415V_TF_2` | — |
| Outgoer | OG_33_415kV_TF_3 | `gic_01_n3_33_415V_TF_3` | — |

### AMF-1

- Category: **AMF Panels**
- Panel id: 16 · slug: `amf-1` · kind: `amf_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | TRANFORMER_01_INCOMER | `gic_04_n1_TRANFORMER_01_INCOMER` | — |
| Incomer | DIESEL_GENSET_1_INCOMER | `gic_04_n2_DIESEL_GENSET_1_INCOMER` | — |
| Outgoer | O/G_MODULE_PCC_PANEL_1 | `gic_04_n3_O_G_MODULE_PCC_PANEL_1` | — |
| Outgoer | O/G_APFC_PANEL_01 | `gic_04_n4_O_G_APFC_PANEL_01` | — |

### AMF-2

- Category: **AMF Panels**
- Panel id: 17 · slug: `amf-2` · kind: `amf_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | TRANSFORM_2_INCOMER | `gic_04_n5_TRANSFORM_2_INCOMER` | — |
| Incomer | DIESEL_GENSET_2_INCOMER | `gic_04_n6_DIESEL_GENSET_2_INCOMER` | — |
| Outgoer | O/G_APFC_PANEL_02 | `gic_04_n7_O_G_APFC_PANEL_02` | — |
| Outgoer | O/G_MODULE_PCC_PANEL_2 | `gic_04_n8_O_G_MODULE_PCC_PANEL_2` | — |

### AMF-3

- Category: **AMF Panels**
- Panel id: 18 · slug: `amf-3` · kind: `amf_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | TRANSFORMER_3_INCOMER | `gic_02_n1_TF_3_INCOMER` | — |
| Incomer | DIESEL_GENSET_3_INCOMER | `gic_02_n2_DG_3_INCOMER` | — |
| Outgoer | O/G_MODULE_PCC_PANEL_3 | `gic_02_n3_O_G_MODULE_PCC_PANEL_3` | — |
| Outgoer | O/G_APFCR_PANEL_3 | `gic_02_n4_O_G_APFCR_PANEL_3` | — |

### PCC-1

- Category: **PCC Panels**
- Panel id: 19 · slug: `pcc-1` · kind: `pcc_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | INCOMER_FROM_AMF_1 | `gic_05_n1_INCOMER_FROM_AMF_1` | — |
| Incomer | SOLAR_INCOMER_PCC_1 | `gic_05_n5_SOLAR_INCOMER_PCC_1` | — |
| Outgoer | AIR_COMPRESSOR_PDB | `gic_05_n2_PCC_1_AIR_COMPRESSOR_PDB` | — |
| Outgoer | UTILITY_PANEL_1 | `gic_05_n3_UTILITY_PANEL_1` | — |
| Outgoer | CT,CDWP,CWP_PANEL | `gic_05_n4_CT_CDWP_CWP_PANEL` | — |

### PCC-2

- Category: **PCC Panels**
- Panel id: 20 · slug: `pcc-2` · kind: `pcc_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | INCOMER_FROM_AMF_2 | `gic_05_n6_INCOMER_FROM_AMF_2` | — |
| Incomer | SOLAR_INCOMER_PCC_2 | `gic_05_n7_SOLAR_INCOMER_PCC_2` | — |
| Outgoer | LAMINATOR_PDB_01 | `gic_05_n8_LAMINATOR_PDB_01` | — |
| Outgoer | LAMINATOR_PDB_02 | `gic_05_n9_LAMINATOR_PDB_02` | — |
| Outgoer | OUT_GOING_UPS_1A | `gic_05_n10_OUT_GOING_UPS_1A` | — |
| Outgoer | OUT_GOING_UPS_1B | `gic_05_n11_OUT_GOING_UPS_1B` | — |
| Outgoer | OUT_GOING_UPS_1C | `gic_06_n1_OUT_GOING_UPS_1C` | — |
| Outgoer | OUT_GOING_UPS_1D | `gic_06_n2_OUT_GOING_UPS_1D` | — |
| Outgoer | OUT_GOING_UPS_1E | `gic_06_n3_OUT_GOING_UPS_1E` | — |
| Outgoer | HARMONIC_PANEL | `gic_06_n4_HARMONIC_PANEL` | — |
| Outgoer | O/G_CHILLER_FROM_PDB_PCC_3_LDB | `gic_06_n5_O_G_CHILLER_FROM_PDB_PCC_3_LDB` | — |
| Outgoer | O/G_PDB_2_FG_SHED | `gic_06_n6_O_G_PDB_2_FG_SHED` | — |
| Outgoer | O/G_LDB_LT_ROOM | `gic_06_n7_O_G_LDB_LT_ROOM_` | — |
| Outgoer | OUT_GOING_SPARE | `gic_06_n8_OUT_GOING_SPARE` | — |
| Outgoer | PCW_PANEL_CHILLER_ROOM | `gic_06_n9_PCW_PANEL_CHILLER_ROOM` | — |

### PCC-3

- Category: **PCC Panels**
- Panel id: 21 · slug: `pcc-3` · kind: `pcc_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | PCC_03_INCOMER | `gic_02_n6_PCC_03_INCOMER` | — |
| Incomer | SOLAR_INCOMER_PCC_3 | `gic_02_n7_SOLAR_INCOMER_PCC_3` | — |
| Outgoer | LAMINATOR_PDB_3 | `gic_02_n8_LAMINATOR_PDB_3` | — |
| Outgoer | AIR_COMPRESSOR_PDB | `gic_03_n1_AIR_COMPRESSOR_PDB` | — |
| Outgoer | UPS_2A | `gic_03_n2_UPS_2A` | — |
| Outgoer | PCW_PUMPS | `gic_03_n3_PCW_PUMPS` | — |
| Outgoer | ADMIN_BUILDING | `gic_03_n4_ADMIN_BUILDING` | — |
| Outgoer | STP,FHS&_PUMPS | `gic_03_n5_STP_FHS__PUMPS` | — |
| Outgoer | BOPT | `gic_03_n6_BOPT` | — |
| Spare | SPARE | `gic_03_n7_SPARE` | — |
| Spare | SPARE_2 | `gic_03_n8_SPARE_2` | — |

### UPS

- Category: **UPS Panels**
- Panel id: 24 · slug: `ups` · kind: `ups_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | UPS-A | `gic_09_n1_I_C_UPS_A` | — |
| Incomer | UPS-B | `gic_09_n2_I_C_UPS_B` | — |
| Incomer | UPS-C | `gic_09_n3_I_C_UPS_C` | — |
| Incomer | UPS-D | `gic_09_n4_I_C_UPS_D` | — |
| Outgoer | O/G_MANUAL_LINE | `gic_09_n5_O_G_MANUAL_LINE` | — |
| Outgoer | O/G_PRE_LAMINATOR_PANEL_1 | `gic_09_n6_O_G_PRE_LAMINATOR_PANEL_1` | — |
| Outgoer | O/G_PRE_LAMINATOR_PANEL_2 | `gic_09_n7_O_G_PRE_LAMINATOR_PANEL_2` | — |
| Outgoer | O/G_U_LAMINATOR_PANEL1 | `gic_09_n8_O_G_U_LAMINATOR_PANEL1` | — |
| Outgoer | O/G_POST_LAMINATOR_PDB_01 | `gic_09_n9_O_G_POST_LAMINATOR_PDB_01` | — |
| Outgoer | O/G_180_FLIPER_&_HITPOT | `gic_09_n10_O_G_180_FLIPER___HITPOT` | — |
| Outgoer | O/G_POST_LAMINATOR_PDB_02 | `gic_09_n11_O_G_POST_LAMINATOR_PDB_02` | — |
| Outgoer | O/G_U_LAMINATOR_PDB_02 | `gic_09_n12_O_G_U_LAMINATOR_PDB_02` | — |
| Outgoer | O/G_UPS_LIGHTING_DB | `gic_09_n13_O_G_UPS_LIGHTING_DB` | — |

### Utility Panel

- Category: **Utility Panels**
- Panel id: 25 · slug: `utility-panel` · kind: `distribution_panel`

| Role | MFM | Table name | MFM nav category |
|---|---|---|---|
| Incomer | I/C_PCC_1 | `gic_10_n3_I_C_PCC_1` | — |
| Incomer | SOLAR_INCOMING_UTILITY_PANEL | `gic_10_n4_SOLAR_INCOMING_UTILITY_PANEL` | — |
| Outgoer | O/G_AHU_09 | `gic_10_n5_O_G_AHU_09` | — |
| Outgoer | O/G_AHU_PDB_01 | `gic_10_n6_O_G_AHU_PDB_01` | — |
| Outgoer | O/G_AHU_PDB_02 | `gic_10_n7_O_G_AHU_PDB_02` | — |
| Outgoer | O/G_AHU_PDB_03 | `gic_10_n8_O_G_AHU_PDB_03` | — |
| Outgoer | O/G_AHU_PDB_05 | `gic_10_n9_O_G_AHU_PDB_05` | — |
| Outgoer | O/G_AIR_WASHER_PDB_3 | `gic_10_n10_O_G_AIR_WASHER_PDB_3` | — |
| Outgoer | O/G_AHU_PDB_04 | `gic_10_n11_O_G_AHU_PDB_04` | — |
| Outgoer | O/G_AHU_PDB_06 | `gic_10_n12_O_G_AHU_PDB_06` | — |
| Outgoer | O/G_AHU_PDB_07 | `gic_10_n13_O_G_AHU_PDB_07` | — |
| Outgoer | O/G_AHU_PDB_08 | `gic_10_n14_O_G_AHU_PDB_08` | — |
| Outgoer | O/G_AIR_WASHER_PDB_2 | `gic_11_n1_O_G_AIR_WASHER_PDB_2` | — |
| Outgoer | O/G_AIR_WASHER_01 | `gic_11_n2_O_G_AIR_WASHER_01` | — |
| Outgoer | O/G_AIR_EXUAST_3 | `gic_11_n3_O_G_AIR_EXUAST_3` | — |
| Outgoer | O/G_EXHAUST_UNIT_PDB_01 | `gic_11_n4_O_G_EXHAUST_UNIT_PDB_01` | — |
| Outgoer | O/G_EXHAUST_UNIT_PDB_02 | `gic_11_n5_0_G_EXHAUST_UNIT_PDB_02` | — |
| Outgoer | O/G_AIR_WASHER_03 | `gic_11_n6_O_G_AIR_WASHER_03` | — |
| Outgoer | O/G_AXIAL_FLOW_FAN_PDB_03 | `gic_11_n7_O_G_AXIAL_FLOW_FAN_PDB_03` | — |
| Outgoer | O/G_AXIAL_FLOW_FAN_PDB_01 | `gic_11_n8_O_G_AXIAL_FLOW_FAN_PDB_01` | — |
| Outgoer | O/G_AXIAL_FLOW_FAN_PDB_02 | `gic_11_n9_O_G_AXIAL_FLOW_FAN_PDB_02` | — |
| Outgoer | O/G_PRE_LAMINATOR_PDB_AND_LDB | `gic_11_n10_O_G_PRE_LAINATOR_PDB_AND_LDB` | — |
| Outgoer | O/G_ALL_CSU | `gic_11_n11_O_G_ALL_CSU` | — |
| Outgoer | O/G_LINE_3_AIR_WASHER_PDB | `gic_11_n12_O_G_LINE_3_AIR_WASHER_PDB` | — |
| Outgoer | LIGHTNING_DB_3 | `gic_05_n12_LIGHTNING_DB_3` | — |

## Single MFMs (no panel)

| MFM | Nav category | Table name | In plain group |
|---|---|---|---|
| I/C_PCC_2_LAMINATOR_PDB | Production Panels | `gic_08_n3_I_C_PCC_2_LAMINATOR_PDB` | — |
| I/C_PCC_2_LAMINATOR_PDB_01 | Production Panels | `gic_07_n1_I_C_PCC_2_LAMINATOR_PDB_01` | — |
| I/C_PCC_2_LAMINATOR_PDB_2 | Production Panels | `gic_08_n2_I_C_PCC_2_LAMINATOR_PDB_2` | — |
| I/C_PCC_3_LAMINATOR_PDB_3 | Production Panels | `gic_08_n7_I_C_PCC_3_LAMINATOR_PDB_3` | — |
| I/C_POST_LAMINATOR_PANEL_2 | Production Panels | `gic_08_n11_I_C_POST_LAMINATOR_PANEL_2` | — |
| I/C_PRE_LAMINATOR_PANEL_2 | Production Panels | `gic_08_n10_I_C_PRE_LAMINATOR_PANEL_2` | — |
| I/C_SOLAR_AIR_COMPRESSOR_PDB-1 | Production Panels | `gic_07_n2_I_C_SOLAR_AIR_COMPRESSOR_PDB_1` | — |
| I/C_SOLAR_AIR_COMPRESSOR_PDB-2 | Production Panels | `gic_07_n4_I_C_SOLAR_AIR_COMPRESSOR_PDB_2` | — |
| I/C_UPS_PANEL_PRE_LAMINATOR_1 | Production Panels | `gic_10_n1_I_C_UPS_PANEL_PRE_LAMINATOR_1` | — |
| I/C_UPS_PANEL_PRE_LAMINATOR_2 | Production Panels | `gic_08_n9_I_C_UPS_PANEL_PRE_LAMINATOR_2` | — |
| I/C_UPS_PANEL_U_LAMINATOR | Production Panels | `gic_08_n1_I_C_UPS_PANEL_U_LAMINATOR` | — |
| I/C_UPS_PANEL_U_LAMINATOR_PDB_2 | Production Panels | `gic_08_n5_I_C_UPS_PANEL_U_LAMINATOR_PDB_2` | — |
| INCOMER_LAMINATOR_PDB_02 | Production Panels | `gic_07_n3_INCOMER_LAMINATOR_PDB_02` | — |
| INCOMER_PRE LAMINATOR_PANEL_1 | Production Panels | `gic_10_n2_INCOMER_PRE_LAMINATOR_PANEL_1` | — |
| SOLAR_LAMINATOR_PDB_01 | Production Panels | `gic_08_n4_SOLAR_LAMINATOR_PDB_01` | — |
| SOLAR_LAMINATOR_PDB_3 | Production Panels | `gic_08_n8_SOLAR_LAMINATOR_PDB_3` | — |
| SOLAR_U_LAMINATOR_PDB_2 | Production Panels | `gic_08_n6_SOLAR_U_LAMINATOR_PDB_2` | — |

Totals: 9 panels, 85 MFMs in panels, 17 single MFMs, 102 active MFMs.
