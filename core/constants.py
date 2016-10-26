"""
Unmerged conflicts in status, which are one of:
    DD    unmerged, both deleted
    AU    unmerged, added by us
    UD    unmerged, deleted by them
    UA    unmerged, added by them
    DU    unmerged, deleted by us
    AA    unmerged, both added
    UU    unmerged, both modified
 """
MERGE_CONFLICT_PORCELAIN_STATUSES = (
    ("D", "D"),  # unmerged, both deleted
    ("A", "U"),  # unmerged, added by us
    ("U", "D"),  # unmerged, deleted by them
    ("U", "A"),  # unmerged, added by them
    ("D", "U"),  # unmerged, deleted by us
    ("A", "A"),  # unmerged, both added
    ("U", "U")  # unmerged, both modified
)
