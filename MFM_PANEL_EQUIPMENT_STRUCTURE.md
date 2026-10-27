# MFM, reference panel, role, and mapped equipment

## The four distinct concepts

An MFM is a meter. Its reference identifies where it sits, its role describes its relationship to that panel, and its mapped equipment identifies the asset it measures. These must not be treated as the same object.

| Concept | Meaning | Current backend representation |
|---|---|---|
| MFM | Meter identity and telemetry binding | `MFM` |
| Reference panel | Primary physical panel where the meter sits | `MFM.reference` → `Group` |
| Role relative to a panel | Incomings, Outgoings, or Spare | `Membership.role`, linked to both `Membership.mfm` and `Membership.group` |
| Mapped equipment | Equipment or asset measured by the meter | `MFM.equipment` → `Equipment` |

The reference is currently a **Group foreign key**, not an Equipment foreign key. A distribution-panel Group has its own canonical Equipment record through `Equipment.group`. That panel equipment is distinct from the equipment mapped to an individual meter.

## Relationship structure

```text
Reference distribution panel: Group (is_panel=True)
  ├── Panel's own Equipment record (Equipment.group)
  └── Membership
        ├── role: incoming / outgoing / spare
        └── MFM
              ├── reference → primary panel Group
              ├── equipment → measured Equipment
              ├── data_source + table_name → telemetry table
              └── logger_schema → field definitions
```

Role belongs to the **meter–panel relationship**, not to the measured equipment globally. Incoming/outgoing does not identify whether the source is grid, DG, or solar.

## Example

Illustrative mapping, not a confirmation of the currently saved equipment assignment:

| Field | Example |
|---|---|
| MFM | `OG_33_415kV_TF_1` |
| Reference panel | `33KV HT Panel` |
| Role to reference | Outgoings (`outgoing`) |
| Mapped equipment | Transformer 1 |
| Logger table | `gic_01_n1_33_415V_TF_1` |

Read this as: “The meter sits in the 33KV HT Panel, is an outgoing of that panel, and measures Transformer 1.” The logger table supplies measurements; it is not the reference panel or the equipment identity.

## Single-MFM and distribution-panel screens

- A standalone MFM appears directly under its navigation category, without a distribution-panel membership or panel role.
- A distribution panel groups MFMs into Incomings, Outgoings, and Spares.
- Each member MFM still has its own single-MFM screens.
- Distribution-panel navigation/API identity uses `Group.id`. Meter screen identity uses `MFM.eid`. Neither should be substituted with `Equipment.id`.

## Multiple panel memberships

One MFM can belong to multiple panels, with a different role in each. For example, it can be outgoing from Panel A and incoming to Panel B.

- `Membership` records are authoritative for panel membership and role.
- `MFM.reference` stores only one primary/legacy panel; it cannot express every membership.
- The current helper fills an empty reference from the first panel membership ordered by position and membership ID; it preserves an existing reference.
- There can be only one membership per `(group, mfm)` pair.
- Several MFMs can map to the same Equipment; each MFM has at most one mapped Equipment through this field.

## Current XLSX import mapping

On the **Meters** sheet:

| Spreadsheet column | Purpose |
|---|---|
| MFM name | Meter label; the plant-specific admin importer uses the resolved device/table label for naming |
| Nav category | Category containing the panel, or the standalone meter |
| Distribution panel | Panel name used to resolve/create the Group and attach the meter |
| Role | Incomings, Outgoings, or Spare; saved as `incoming`, `outgoing`, or `spare` |
| Table name | Logger table/device binding |
| Schema | Logger field-layout assignment |

Rows with the same distribution-panel name reuse that panel. A named panel requires a role; a standalone row leaves both blank. The plant-specific admin importer additionally applies the agreed UPS, Utility, Production, and HT normalization rules.

**Important limitation:** the current workbook has no explicit **Mapped equipment** column. It therefore does not capture all four concepts independently from engineer input.

The current equipment helper preserves an existing `MFM.equipment` assignment. If no assignment exists, it creates a measured Equipment record using the MFM's name and configured/default device type. This automatic record is not proof that a real transformer, DG, or other asset has been correctly identified.

To support explicit equipment mapping in a future workbook revision, add a stable equipment identifier/name and implement its resolution and validation in the importer. Merely adding a column to Excel will not establish that relationship today.

## Implementation references

- `core/models.py`: `Group`, `MFM`, `Membership`, `Equipment`.
- `core/equipment.py`: `ensure_panel_equipment`, `ensure_mfm_equipment`, `ensure_primary_reference`.
- `core/sheet_import.py`: workbook columns, panel creation, and membership assignment.
- `core/audited_upload.py`: plant-specific admin upload normalization and validation.

This document describes the current structure and explicitly distinguishes the desired real-world equipment mapping from automatically created metadata. It does not change database assignments or the import contract.
