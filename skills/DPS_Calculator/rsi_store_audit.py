"""RSI Pledge Store vs DPS Calculator comparison audit."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Our 208 DPS Calculator ships (from Erkul cache)
erkul = [
    '100i','125a','135c','300i','315p','325a','350r','400i',
    '600i','600i Executive Edition','600i Touring','85X Limited','890 Jump',
    'A1 Spirit','A2 Hercules Starlifter','Apollo Medivac','Apollo Triage',
    'Ares Star Fighter Inferno','Ares Star Fighter Ion','Arrow','Asgard',
    'Aurora Mk I  LX','Aurora Mk I CL','Aurora Mk I ES','Aurora Mk I LN',
    'Aurora Mk I MR','Aurora Mk I SE','Aurora Mk II',
    'Avenger Stalker','Avenger Titan','Avenger Titan Renegade','Avenger Warlock',
    'Ballista','Ballista Dunestalker','Ballista Snowblind','Blade','Buccaneer',
    'C1 Spirit','C2 Hercules Starlifter','C8 Pisces','C8R Pisces Rescue','C8X Pisces Expedition',
    'CSV-SM','Carrack','Carrack Expedition','Caterpillar','Caterpillar Pirate',
    'Centurion','Clipper',
    'Constellation Andromeda','Constellation Aquila','Constellation Phoenix',
    'Constellation Phoenix Emerald','Constellation Taurus','Corsair',
    'Cutlass Black','Cutlass Blue','Cutlass Red','Cutlass Steel',
    'Cutter','Cutter Rambler','Cutter Scout',
    'Cyclone','Cyclone AA','Cyclone MT','Cyclone RC','Cyclone RN','Cyclone TR',
    'Defender','Dragonfly','Dragonfly Star Kitten','Dragonfly Yellowjacket',
    'Eclipse','F7A Hornet Mk I','F7A Hornet Mk II',
    'F7C Hornet Mk I','F7C Hornet Mk II','F7C Hornet Wildfire Mk I',
    'F7C-M Hornet Heartseeker Mk I','F7C-M Hornet Heartseeker Mk II',
    'F7C-M Super Hornet Mk I','F7C-M Super Hornet Mk II',
    'F7C-R Hornet Tracker Mk I','F7C-R Hornet Tracker Mk II',
    'F7C-S Hornet Ghost Mk I','F7C-S Hornet Ghost Mk II',
    'F8A Lightning','F8C Lightning','Fortune',
    'Freelancer','Freelancer DUR','Freelancer MAX','Freelancer MIS',
    'Fury','Fury LX','Fury MX',
    'Gladiator','Gladius','Gladius Pirate','Gladius Valiant','Glaive',
    'Golem','Golem OX','Guardian','Guardian MX','Guardian QI',
    'Hammerhead','Hawk','Herald','Hermes','HoverQuad',
    'Hull A','Hull C','Hurricane',
    'Idris M','Idris P','Intrepid','Khartu-al',
    'L-21 Wolf','L-22 Alpha Wolf','Lynx',
    'M2 Hercules Starlifter','M50 Interceptor','MDC','MOLE','MOTH',
    'MPUV Cargo','MPUV Personnel','MPUV Tractor','MTC',
    'Mantis','Mercury Star Runner','Meteor','Mule',
    'Mustang Alpha','Mustang Beta','Mustang Delta','Mustang Gamma','Mustang Omega',
    'Nomad','Nova','Nox','Nox Kue',
    'P-52 Merlin','P-72 Archimedes','P-72 Archimedes Emerald','PTV',
    'Paladin','Perseus','Polaris','Prospector',
    'Prowler','Prowler Utility','Pulse','Pulse LX',
    'RAFT','ROC','ROC-DS',
    'Razor','Razor EX','Razor LX',
    'Reclaimer','Redeemer',
    'Reliant Kore','Reliant Mako','Reliant Sen','Reliant Tana',
    'Retaliator','SRV','STV','Sabre','Sabre Comet','Sabre Firebird',
    'Sabre Peregrine','Sabre Raven','Salvation',
    "San\u2019tok.y\u0101i",'Scorpius','Scorpius Antares','Scythe','Shiv',
    'Spartan','Starfarer','Starfarer Gemini','Starlancer MAX','Starlancer TAC',
    'Stinger','Storm','Storm AA','Syulen',
    'Talon','Talon Shrike','Terrapin','Terrapin Medic',
    'Ursa','Ursa Fortuna','Ursa Medivac','Valkyrie',
    'Vanguard Harbinger','Vanguard Hoplite','Vanguard Sentinel','Vanguard Warden',
    'Vulture','X1','X1 Force','X1 Velocity',
    'Zeus Mk II CL','Zeus Mk II ES',
]

# RSI pledge store ships (from UEX Corp + RSI page DOM snapshot)
# Includes both currently-for-sale and temporarily-unavailable
rsi = [
    # Confirmed from RSI page DOM (alphabetical order, sale=true filter)
    '100i','125a','135c','300i','315p','325a','350r','400i',
    'A1 Spirit','Apollo Medivac','Apollo Triage',
    'Ares Star Fighter Inferno','Ares Star Fighter Ion','Arrow',
    'ATLS','ATLS GEO',
    'Aurora Mk I CL','Aurora Mk I ES','Aurora Mk I LN','Aurora Mk I LX',
    'Aurora Mk I MR','Aurora Mk I SE','Aurora Mk II',
    'Avenger Stalker','Avenger Titan','Avenger Titan Renegade','Avenger Warlock',
    # From UEX Corp pledge store list
    '600i Executive Edition','600i Touring','890 Jump',
    'A2 Hercules Starlifter','Asgard',
    'Banu Merchantman',
    'Blade','Buccaneer',
    'C1 Spirit','C2 Hercules Starlifter','C8 Pisces','C8R Pisces Rescue','C8X Pisces Expedition',
    'Carrack','Carrack Expedition','Caterpillar','Caterpillar Pirate',
    'Clipper',
    'Constellation Andromeda','Constellation Aquila','Constellation Phoenix',
    'Constellation Phoenix Emerald','Constellation Taurus','Corsair',
    'Crucible',
    'Cutlass Black','Cutlass Blue','Cutlass Red','Cutlass Steel',
    'Cutter','Cutter Rambler','Cutter Scout',
    'Cyclone','Cyclone AA','Cyclone MT','Cyclone RC','Cyclone RN','Cyclone TR',
    'Defender',
    'Drake Ironclad','Drake Ironclad Assault','Drake Kraken','Drake Kraken Privateer',
    'Dragonfly','Dragonfly Star Kitten','Dragonfly Yellowjacket',
    'Eclipse','Endeavor',
    'F7A Hornet Mk I','F7A Hornet Mk II',
    'F7C Hornet Mk I','F7C Hornet Mk II','F7C Hornet Wildfire Mk I',
    'F7C-M Hornet Heartseeker Mk I','F7C-M Hornet Heartseeker Mk II',
    'F7C-M Super Hornet Mk I','F7C-M Super Hornet Mk II',
    'F7C-R Hornet Tracker Mk I','F7C-R Hornet Tracker Mk II',
    'F7C-S Hornet Ghost Mk I','F7C-S Hornet Ghost Mk II',
    'F8A Lightning','F8C Lightning','Fortune',
    'Freelancer','Freelancer DUR','Freelancer MAX','Freelancer MIS',
    'Fury','Fury LX','Fury MX',
    'Gatac Railen','Genesis Starliner',
    'Gladiator','Gladius','Gladius Valiant','Glaive',
    'Guardian','Guardian MX','Guardian QI',
    'Hammerhead','Hawk','Herald','Hermes','HoverQuad',
    'Hull A','Hull C','Hull D','Hull E','Hurricane',
    'Idris M','Idris P','Intrepid',
    'Javelin','Khartu-al',
    'Liberator',
    'M2 Hercules Starlifter','M50 Interceptor','MOLE','MOTH',
    'MPUV Cargo','MPUV Personnel','MPUV Tractor',
    'Mantis','Mercury Star Runner','Meteor',
    'Mustang Alpha','Mustang Beta','Mustang Delta','Mustang Gamma','Mustang Omega',
    'Nautilus','Nautilus Solstice Edition',
    'Nomad','Nox','Nox Kue',
    'MISC Odyssey','RSI Orion','RSI Arrastra','RSI Galaxy',
    'C.O. Pioneer',
    'P-52 Merlin','P-72 Archimedes','P-72 Archimedes Emerald',
    'Paladin','Perseus','Polaris','Prospector',
    'Prowler','Prowler Utility','Pulse','Pulse LX',
    'RAFT','ROC','ROC-DS',
    'Razor','Razor EX','Razor LX',
    'Reclaimer','Redeemer','Retaliator','Retaliator Bomber',
    'Reliant Kore','Reliant Mako','Reliant Sen','Reliant Tana',
    'SRV','Sabre','Sabre Comet','Sabre Firebird','Sabre Peregrine','Sabre Raven',
    'Salvation',"San tok.yai",'Scorpius','Scorpius Antares','Scythe',
    'Starfarer','Starfarer Gemini','Starlancer MAX','Starlancer TAC','Stinger',
    'Syulen','Talon','Talon Shrike','Terrapin','Terrapin Medic',
    'Ursa','Ursa Fortuna','Ursa Medivac','Valkyrie',
    'Vanguard Harbinger','Vanguard Hoplite','Vanguard Sentinel','Vanguard Warden',
    'Vulcan','Vulture',
    'X1','X1 Force','X1 Velocity',
    '85X Limited',
    'Zeus Mk II CL','Zeus Mk II ES','Zeus Mk II MR',
]

def norm(s):
    return s.lower().strip().replace('  ', ' ')

e_norm = {norm(s): s for s in erkul}
r_norm = {norm(s): s for s in rsi}

on_rsi_not_erkul = sorted([r_norm[k] for k in r_norm if k not in e_norm], key=str.lower)
in_erkul_not_rsi = sorted([e_norm[k] for k in e_norm if k not in r_norm], key=str.lower)

print('=' * 60)
print('RSI PLEDGE STORE vs DPS CALCULATOR AUDIT REPORT')
print('=' * 60)
print()
print(f'Our DPS Calculator:  {len(erkul)} ships')
print(f'RSI Pledge Store:    {len(rsi)} ships (all categories)')
print()

# Categorise the RSI-only ships
concept_known = {
    'banu merchantman','crucible','endeavor','gatac railen','genesis starliner',
    'hull d','hull e','javelin','liberator','misc odyssey','rsi orion',
    'rsi arrastra','rsi galaxy','c.o. pioneer','nautilus','nautilus solstice edition',
    'drake ironclad','drake ironclad assault','drake kraken','drake kraken privateer',
    'retaliator bomber','vulcan',
}

flyable_missing = [s for s in on_rsi_not_erkul if norm(s) not in concept_known]
concept_only   = [s for s in on_rsi_not_erkul if norm(s) in concept_known]

print(f'ON RSI STORE, MISSING FROM DPS CALCULATOR: {len(on_rsi_not_erkul)} total')
print()
print(f'  FLYABLE (action required - {len(flyable_missing)}):')
for s in flyable_missing:
    print(f'    * {s}')
print()
print(f'  CONCEPT/SPECIAL (not yet in-game, no DPS data expected - {len(concept_only)}):')
for s in concept_only:
    print(f'    ~ {s}')
print()
print(f'IN DPS CALCULATOR, NOT IN RSI STORE DATA: {len(in_erkul_not_rsi)} total')
print('  (These are game-only or NPC/special ships not sold on pledge store)')
for s in in_erkul_not_rsi:
    print(f'    - {s}')
