-- MC_Lib parts database schema.
-- Every table has the same columns so tools and agents can treat them uniformly.
-- Each table shows up in KiCad's symbol chooser as MC_Parts/<Table> (like Altium DbLib tables).
--
--   ID            internal part number, primary key, e.g. RES-0001 (never reuse)
--   Symbols       "Lib:Symbol"            e.g. MC_Generic:R
--   Footprints    "Lib:Fp[;Lib:Fp...]"   first is default, rest are alternates
--   Status        Active | Seed | Legacy | NRND | Obsolete
--   Source        where the part came from (Altium IntLib, Ultra Librarian, manual, ...)
--   Mouser_* / Lifecycle / Sourcing_Date   snapshot from the Mouser API (mclib sourcing), dated
--   Exclude_BOM / Exclude_Board   "1" to exclude (fiducials, stackup notes, ...)

CREATE TABLE IF NOT EXISTS _template (
    ID               TEXT PRIMARY KEY NOT NULL,
    MPN              TEXT NOT NULL DEFAULT '',
    Manufacturer     TEXT NOT NULL DEFAULT '',
    Value            TEXT NOT NULL DEFAULT '',
    Description      TEXT NOT NULL DEFAULT '',
    Package          TEXT NOT NULL DEFAULT '',
    Symbols          TEXT NOT NULL DEFAULT '',
    Footprints       TEXT NOT NULL DEFAULT '',
    Datasheet        TEXT NOT NULL DEFAULT '',
    Keywords         TEXT NOT NULL DEFAULT '',
    Tolerance        TEXT NOT NULL DEFAULT '',
    Voltage_Rating   TEXT NOT NULL DEFAULT '',
    Current_Rating   TEXT NOT NULL DEFAULT '',
    Power_Rating     TEXT NOT NULL DEFAULT '',
    Dielectric       TEXT NOT NULL DEFAULT '',
    Temp_Range       TEXT NOT NULL DEFAULT '',
    AEC_Q            TEXT NOT NULL DEFAULT '',
    DigiKey_PN       TEXT NOT NULL DEFAULT '',
    Mouser_PN        TEXT NOT NULL DEFAULT '',
    Mouser_Stock     TEXT NOT NULL DEFAULT '',
    Mouser_Price_1   TEXT NOT NULL DEFAULT '',
    Mouser_Price_100 TEXT NOT NULL DEFAULT '',
    Mouser_Lead_Time TEXT NOT NULL DEFAULT '',
    Lifecycle        TEXT NOT NULL DEFAULT '',
    Sourcing_Date    TEXT NOT NULL DEFAULT '',
    Alt_MPN          TEXT NOT NULL DEFAULT '',
    Alt_Manufacturer TEXT NOT NULL DEFAULT '',
    Status           TEXT NOT NULL DEFAULT 'Active',
    Source           TEXT NOT NULL DEFAULT '',
    Notes            TEXT NOT NULL DEFAULT '',
    Exclude_BOM      TEXT NOT NULL DEFAULT '0',
    Exclude_Board    TEXT NOT NULL DEFAULT '0'
);
