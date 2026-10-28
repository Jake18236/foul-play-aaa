"""Opt-in native offline transitions and bulk successor exchange for PyTorch."""

from pathlib import Path
from engine_search_patch import replace_once


def patch_neural(source):
    path = source / "src/state.rs"
    text = path.read_text()
    text = replace_once(
        text,
        "    pub use_damage_dealt: bool,",
        "    pub use_damage_dealt: bool,\n    pub track_all_pp: bool,",
    )
    # This flag belongs to a transition invocation, not serialized battle state.
    text = text.replace(
        "            use_damage_dealt: false,",
        "            use_damage_dealt: false,\n            track_all_pp: false,",
    )
    path.write_text(text)
    path = source / "src/genx/generate_instructions.rs"
    text = path.read_text()
    text = replace_once(
        text,
        "    let (attacker_side, defender_side) = state.get_both_sides(&attacking_side);\n    let active = attacker_side.get_active();\n    if active.moves[&choice.move_index].pp < 10 {",
        "    let track_all_pp = state.track_all_pp;\n    let (attacker_side, defender_side) = state.get_both_sides(&attacking_side);\n    let active = attacker_side.get_active();\n    if track_all_pp || active.moves[&choice.move_index].pp < 10 {",
    )
    text = replace_once(text, "fn get_effective_speed(", "pub fn get_effective_speed(")
    path.write_text(text)
    path = source / "poke-engine-py/src/lib.rs"
    text = path.read_text()
    text = replace_once(
        text,
        "            use_damage_dealt: false,",
        "            use_damage_dealt: false,\n            track_all_pp: false,",
    )
    text += (Path(__file__).parent / "native_neural.rs").read_text()
    text = replace_once(
        text,
        "    m.add_function(wrap_pyfunction!(root_info, m)?)?;",
        "    m.add_function(wrap_pyfunction!(root_info, m)?)?;\n    m.add_function(wrap_pyfunction!(neural_successors, m)?)?;",
    )
    path.write_text(text)
