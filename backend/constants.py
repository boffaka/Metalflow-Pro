"""
Physical constants — immutable scientific facts.

These are universal constants that never change between projects, scenarios,
or study phases. All other modules import from here instead of redefining.
"""
from __future__ import annotations

# Electrochemistry
FARADAY_C_MOL = 96_485          # Faraday constant (C/mol)

# Gold
M_AU_G_MOL = 196.97            # Gold molar mass (g/mol)
AU_ELECTRONS = 3                # Electrons for Au3+ -> Au
TROY_OZ_PER_GRAM = 1 / 31.1035 # Troy ounce conversion

# Water
WATER_SG = 1.0                  # Water specific gravity at STP

# Geochemistry (Sobek 1978)
AP_FACTOR_PYRITE = 31.25        # kg CaCO3/t per %S (pyrite stoichiometry)
