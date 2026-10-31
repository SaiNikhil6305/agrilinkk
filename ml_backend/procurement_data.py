"""Reference data loaded into SQLite at startup.

CENTERS: well-known regulated markets / procurement points. Only name, type, state,
district and principal crops are recorded — no phone numbers or hours are invented.
Verify contact details with the State Agricultural Marketing Board or the e-NAM portal.
Extend this list (or import the e-NAM mandi list CSV) to grow the directory.

MSP_RATES: Government of India MSP decisions (CCEA, published by PIB).
Re-verify each season at https://pib.gov.in or https://cacp.da.gov.in.
"""

_SRC = "Regulated market (APMC) — listed on Agmarknet/e-NAM; verify details with state marketing board"

CENTERS = [
    # (name, facility_type, state, district, crops, source)
    ("Khanna Grain Market", "APMC Mandi", "Punjab", "Ludhiana", "Wheat, Paddy, Maize", _SRC),
    ("Rajpura Grain Market", "APMC Mandi", "Punjab", "Patiala", "Wheat, Paddy", _SRC),
    ("Amritsar Grain Market", "APMC Mandi", "Punjab", "Amritsar", "Wheat, Paddy", _SRC),
    ("Sangrur Grain Market", "APMC Mandi", "Punjab", "Sangrur", "Wheat, Paddy", _SRC),
    ("Karnal Grain Market", "APMC Mandi", "Haryana", "Karnal", "Wheat, Paddy", _SRC),
    ("Kaithal Grain Market", "APMC Mandi", "Haryana", "Kaithal", "Wheat, Paddy", _SRC),
    ("Sirsa Grain Market", "APMC Mandi", "Haryana", "Sirsa", "Wheat, Cotton, Mustard", _SRC),
    ("Azadpur Mandi", "APMC Mandi", "Delhi", "North West Delhi", "Fruits, Vegetables", _SRC),
    ("Najafgarh Grain Market", "APMC Mandi", "Delhi", "South West Delhi", "Wheat, Paddy", _SRC),
    ("Hapur Mandi", "APMC Mandi", "Uttar Pradesh", "Hapur", "Wheat, Jaggery, Mustard", _SRC),
    ("Shahjahanpur Mandi", "APMC Mandi", "Uttar Pradesh", "Shahjahanpur", "Wheat, Paddy", _SRC),
    ("Indore (Laxmibai Nagar) Mandi", "APMC Mandi", "Madhya Pradesh", "Indore", "Soybean, Wheat, Gram", _SRC),
    ("Neemuch Mandi", "APMC Mandi", "Madhya Pradesh", "Neemuch", "Soybean, Garlic, Spices", _SRC),
    ("Kota Bhamashah Mandi", "APMC Mandi", "Rajasthan", "Kota", "Soybean, Wheat, Mustard, Coriander", _SRC),
    ("Lasalgaon APMC", "APMC Mandi", "Maharashtra", "Nashik", "Onion", _SRC),
    ("Vashi APMC (Navi Mumbai)", "APMC Mandi", "Maharashtra", "Thane", "Grains, Fruits, Vegetables", _SRC),
    ("Latur APMC", "APMC Mandi", "Maharashtra", "Latur", "Soybean, Tur, Gram", _SRC),
    ("Gondal APMC", "APMC Mandi", "Gujarat", "Rajkot", "Groundnut, Cotton, Wheat, Chilli", _SRC),
    ("Unjha APMC", "APMC Mandi", "Gujarat", "Mehsana", "Cumin, Fennel, Isabgol", _SRC),
    ("Yeshwanthpur APMC", "APMC Mandi", "Karnataka", "Bengaluru Urban", "Grains, Pulses, Onion", _SRC),
    ("Davangere APMC", "APMC Mandi", "Karnataka", "Davangere", "Maize, Paddy", _SRC),
    ("Hubballi APMC", "APMC Mandi", "Karnataka", "Dharwad", "Cotton, Chilli, Groundnut", _SRC),
    ("Guntur Mirchi Yard", "APMC Mandi", "Andhra Pradesh", "Guntur", "Chilli", _SRC),
    ("Kurnool Market Yard", "APMC Mandi", "Andhra Pradesh", "Kurnool", "Groundnut, Onion, Paddy", _SRC),
    ("Enumamula Market Yard (Warangal)", "APMC Mandi", "Telangana", "Warangal", "Cotton, Chilli, Maize", _SRC),
    ("Koyambedu Market Complex", "APMC Mandi", "Tamil Nadu", "Chennai", "Fruits, Vegetables", _SRC),
    ("Oddanchatram Market", "APMC Mandi", "Tamil Nadu", "Dindigul", "Vegetables", _SRC),
    ("Vellore Regulated Market", "APMC Mandi", "Tamil Nadu", "Vellore", "Paddy, Groundnut", _SRC),
    ("Gulabbagh Mandi", "APMC Mandi", "Bihar", "Purnia", "Maize, Jute", _SRC),
    ("Siliguri Regulated Market", "APMC Mandi", "West Bengal", "Darjeeling", "Vegetables, Jute, Paddy", _SRC),
]

_PIB = "CCEA decision published by PIB"
MSP_RATES = [
    # (commodity, variety, season, marketing_year, msp_per_qtl, source)
    ("Paddy", "Common", "Kharif", "2025-26", 2369, _PIB + ", May 2025"),
    ("Paddy", "Grade A", "Kharif", "2025-26", 2389, _PIB + ", May 2025"),
    ("Maize", "", "Kharif", "2025-26", 2400, _PIB + ", May 2025"),
    ("Jowar", "Hybrid", "Kharif", "2025-26", 3699, _PIB + ", May 2025"),
    ("Bajra", "", "Kharif", "2025-26", 2775, _PIB + ", May 2025"),
    ("Ragi", "", "Kharif", "2025-26", 4886, _PIB + ", May 2025"),
    ("Tur", "", "Kharif", "2025-26", 8000, _PIB + ", May 2025"),
    ("Moong", "", "Kharif", "2025-26", 8768, _PIB + ", May 2025"),
    ("Groundnut", "", "Kharif", "2025-26", 7263, _PIB + ", May 2025"),
    ("Soybean", "Yellow", "Kharif", "2025-26", 5328, _PIB + ", May 2025"),
    ("Cotton", "Medium Staple", "Kharif", "2025-26", 7710, _PIB + ", May 2025"),
    ("Wheat", "", "Rabi", "2026-27", 2585, _PIB + ", Oct 2025"),
    ("Jute", "Raw (TD-3)", "Annual", "2025-26", 5650, _PIB + ", Jan 2025"),
    ("Sugarcane", "FRP", "Annual", "2025-26", 355, _PIB + ", Apr 2025 (Fair & Remunerative Price)"),
]
