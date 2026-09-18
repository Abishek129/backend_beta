"""Shared constants.

FEEDER_ID_OFFSET namespaces the single WS id space (``ws/mfm/{id}/``) that
CMD_V2 uses for everything. backend2 split panels (``Equipment.id``) from
feeders (``MFM.id``) into two id spaces that overlap, so the EMS tree adds this
offset to every feeder/lt_panel leaf id. The WS dispatcher then routes:

    id <  FEEDER_ID_OFFSET  -> distribution panel  (Equipment.id, aggregate)
    id >= FEEDER_ID_OFFSET  -> single feeder/lt_panel (MFM.id, column-row)

Picked far above any real Equipment.id (hundreds) so the ranges never collide.
"""

FEEDER_ID_OFFSET = 1_000_000
