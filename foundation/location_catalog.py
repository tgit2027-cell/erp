"""Tivoli's warehouse and showroom location list (owner-supplied, October 2026).

Each entry is (code, name, kind, area, street, sub-location, parent code,
business units). Showrooms belong to their own business unit; warehouses,
factory areas and the virtual locations are shared by all three
(provisional). The 0037 migration renames the earlier synthetic locations
to these codes, so existing stock and history keep their location.
"""

ALL_UNITS = ("TVL", "RBB", "NAT")
AL_RAYYAN = ("Al Rayyan Building", "Tivoli Group")
STREET_41 = ("Old Industrial Area", "Street 41")
BIRKAT = ("Birkat Al Awamer", "")

LOCATIONS = [
    ("SNAT", "Rayyan Showroom - Natuzzi Italia showroom", "showroom", *AL_RAYYAN, "Natuzzi Italia showroom", None, ("NAT",)),
    ("SRBB", "Rayyan Showroom - Roche Bobois showroom", "showroom", *AL_RAYYAN, "Roche Bobois showroom", None, ("RBB",)),
    ("STIV", "Rayyan Showroom - Tivoli showroom", "showroom", *AL_RAYYAN, "Tivoli Showroom", None, ("TVL",)),
    ("STIV-G", "Rayyan Showroom - Tivoli showroom - Ground Floor", "showroom", *AL_RAYYAN, "Tivoli Showroom - Ground Floor", "STIV", ("TVL",)),
    ("STIV-F", "Rayyan Showroom - Tivoli showroom - First Floor", "showroom", *AL_RAYYAN, "Tivoli Showroom - First Floor", "STIV", ("TVL",)),
    ("STIV-M", "Rayyan Showroom - Tivoli showroom - Mezzanine Floor", "showroom", *AL_RAYYAN, "Tivoli Showroom - Mezzanine Floor", "STIV", ("TVL",)),
    ("TFRW", "Items during transfer (virtual warehouse)", "virtual", "Virtual", "Virtual", "Virtual", None, ALL_UNITS),
    ("W41-01", "Industrial Area, Street 41 warehouse - Building 01", "warehouse", *STREET_41, "Building 01", None, ALL_UNITS),
    ("W41-02", "Industrial Area, Street 41 warehouse - Building 02 (Corridor)", "warehouse", *STREET_41, "Building 02", None, ALL_UNITS),
    ("W41-03.1", "Industrial Area, Street 41 warehouse - Building 03 - Section 01", "warehouse", *STREET_41, "Building 03", None, ALL_UNITS),
    ("W41-03.2", "Industrial Area, Street 41 warehouse - Building 03 - Section 02", "warehouse", *STREET_41, "Building 03", None, ALL_UNITS),
    ("W41-03.3", "Industrial Area, Street 41 warehouse - Building 03 - Section 03", "warehouse", *STREET_41, "Building 03", None, ALL_UNITS),
    ("W41-04.1", "Industrial Area, Street 41 warehouse - Building 04 - Section 01", "warehouse", *STREET_41, "Building 04", None, ALL_UNITS),
    ("W41-04.2", "Industrial Area, Street 41 warehouse - Building 04 - Section 02 (Factories raw materials store)", "warehouse", *STREET_41, "Building 04", None, ALL_UNITS),
    ("W41-FAC", "Industrial Area, Street 41 warehouse - Factory", "factory", *STREET_41, "Building 05 Factory", None, ALL_UNITS),
    ("W41-06.1", "Upholstery new workshop", "factory", *STREET_41, "Building 06", None, ALL_UNITS),
    ("W41-06.2", "Factory metal & CNC location", "factory", *STREET_41, "Building 06", None, ALL_UNITS),
    ("WBA-01", "Birkat Al Awamer Warehouse - Building 01", "warehouse", *BIRKAT, "Building 01", None, ALL_UNITS),
    ("WBA-02", "Birkat Al Awamer Warehouse - Building 02", "warehouse", *BIRKAT, "Building 02", None, ALL_UNITS),
    ("WBA-03", "Birkat Al Awamer Warehouse - Building 03", "warehouse", *BIRKAT, "Building 03", None, ALL_UNITS),
    ("VTIV", "Virtual location for projects purchases", "virtual", "Virtual Location", "Cloud", "Cloud", None, ALL_UNITS),
    ("XX", "Physically missing items but already existing in the ERP system", "virtual", "ERP", "ERP", "ERP", None, ALL_UNITS),
]

# Earlier synthetic location -> new code. Two earlier locations have no
# counterpart: the Street 41 grouping (the new list has no parent row) and the
# right half of the old mezzanine, whose stock belongs on STIV-M.
RENAMED = {
    "tivoli-showroom": "STIV",
    "tivoli-ground-floor": "STIV-G",
    "tivoli-mezzanine-left": "STIV-M",
    "roche-bobois-showroom": "SRBB",
    "natuzzi-showroom": "SNAT",
    "street-41-building-1": "W41-01",
    "street-41-building-2": "W41-02",
    "street-41-building-3": "W41-03.1",
    "street-41-building-4": "W41-04.1",
    "birkat-awamer-warehouse": "WBA-01",
}
RETIRED = {"street-41": None, "tivoli-mezzanine-right": "STIV-M"}
