"""Add a serialized, format-wide Tera gate to the pinned native engine."""

from engine_search_patch import replace_once


def patch_format_rules(source):
    path = source / "src/state.rs"
    text = path.read_text()
    text = replace_once(text, "    pub team_preview: bool,", "    pub team_preview: bool,\n    pub terastallization_allowed: bool,")
    text = replace_once(text, "            team_preview: false,", "            team_preview: false,\n            terastallization_allowed: true,")
    text = replace_once(text, '"{}/{}/{}/{}/{}/{}",', '"{}/{}/{}/{}/{}/{}/{}",')
    text = replace_once(text, "            self.team_preview\n", "            self.team_preview,\n            self.terastallization_allowed\n")
    text = replace_once(text, "            team_preview: split[5].parse::<bool>().unwrap(),", "            team_preview: split[5].parse::<bool>().unwrap(),\n            terastallization_allowed: split.get(6).map_or(true, |v| v.parse::<bool>().unwrap()),")
    path.write_text(text)

    path = source / "src/genx/state.rs"
    text = path.read_text()
    for side in ("side_one", "side_two"):
        before = f"self.{side}.can_use_tera()"
        if text.count(before) != 2:
            raise ValueError("Unexpected native Tera option generation")
        text = text.replace(before, f"self.terastallization_allowed && {before}")
    path.write_text(text)

    path = source / "poke-engine-py/src/lib.rs"
    text = path.read_text()
    for before, after in (
        ("    pub team_preview: bool,", "    pub team_preview: bool,\n    pub terastallization_allowed: bool,"),
        ("            team_preview: other.team_preview,", "            team_preview: other.team_preview,\n            terastallization_allowed: other.terastallization_allowed,"),
        ("            team_preview: self.team_preview,", "            team_preview: self.team_preview,\n            terastallization_allowed: self.terastallization_allowed,"),
        ("        team_preview=false,", "        team_preview=false,\n        terastallization_allowed=true,"),
        ("        team_preview: bool,", "        team_preview: bool,\n        terastallization_allowed: bool,"),
        ("            team_preview,", "            team_preview,\n            terastallization_allowed,"),
    ):
        text = replace_once(text, before, after)
    path.write_text(text)
