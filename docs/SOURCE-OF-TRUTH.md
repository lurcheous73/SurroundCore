# SurroundCore / ControlMac source of truth

This project deliberately has one design lineage.

1. **ControlMac v2 current source is the primary truth** for workflow, feature behaviour and visual language.
2. If v2 has no equivalent feature, **ControlMac v1.1a001meridian** is the behavioural/style reference.
3. Where v1.1 demonstrates a feature but its exact source is unavailable, **ControlMac v1.02b1 source** is the implementation donor.
4. SurroundCore web mirrors the applicable ControlMac behaviour and presentation; it must not invent a conflicting third interface.

The archived v1.1 build records Git commit `3681a61`, but that object is not currently present in the fetched donor repository. Do not falsely claim reproducible v1.1 source until that commit is recovered.

The goth-black/aubergine/purple ControlMac-style shell is the canonical SurroundCore web presentation from build v0.001 onward.