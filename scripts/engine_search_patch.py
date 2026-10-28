"""Instrument the pinned MCTS without changing its UCB or backup rules."""

from pathlib import Path


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f"Pinned native search source mismatch: {before[:90]}")
    return text.replace(before, after)


def patch_search(source: Path):
    path = source / "src/mcts.rs"
    text = path.read_text()
    text = replace_once(
        text,
        "    pub times_visited: u32,",
        "    pub times_visited: u32,\n    pub total_score: f32,",
    )
    text = replace_once(
        text,
        "            times_visited: 0,",
        "            times_visited: 0,\n            total_score: 0.0,",
    )
    text = replace_once(
        text,
        "        self.times_visited += 1;",
        "        self.times_visited += 1;\n        self.total_score += score;",
    )
    text = replace_once(
        text, "    limit: SearchLimit,\n)", "    limit: SearchLimit,\n    seed: u64,\n)"
    )
    text = replace_once(
        text,
        "    let mut rng = rng();",
        "    let mut rng = rand::rngs::StdRng::seed_from_u64(seed);",
    )
    # Exact iteration counts matter for reproducibility, unlike wall-clock limits.
    text = replace_once(
        text,
        "        for _ in 0..1000 {",
        """        let batch = match limit {
            SearchLimit::Iterations(n) => 1000.min(n.saturating_sub(root_node.times_visited)),
            _ => 32,
        };
        for _ in 0..batch {""",
    )
    start = text.index("pub fn perform_mcts(")
    signature_end = text.index("    let mut root_node", start)
    signature = text[start:signature_end]
    diagnostic_signature = (
        signature.replace("perform_mcts(", "perform_mcts_diagnostics(")
        .replace("    max_iterations: u32,", "    max_iterations: u32,\n    seed: u64,")
        .replace("-> MctsResult", "-> (MctsResult, Vec<Vec<u32>>, Vec<Vec<f32>>)")
    )
    wrapper = (
        signature
        + """    perform_mcts_diagnostics(state, side_one_options, side_two_options,
        max_time, max_iterations, rng().random()).0
}

"""
    )
    text = text[:start] + wrapper + diagnostic_signature + text[signature_end:]
    text = replace_once(
        text,
        "        search_limit,\n    );",
        "        search_limit,\n        seed,\n    );",
    )
    text = replace_once(
        text,
        "    result\n}",
        """    let rows = result.s1.len();
    let cols = result.s2.len();
    let root_ptr = &root_node as *const Node as usize;
    let mut joint_visits = vec![vec![0; cols]; rows];
    let mut joint_scores = vec![vec![0.0; cols]; rows];
    for ((parent, i, j), branches) in children.iter() {
        if *parent == root_ptr {
            for branch in branches.iter() {
                joint_visits[*i][*j] += branch.times_visited;
                joint_scores[*i][*j] += branch.total_score;
            }
        }
    }
    (result, joint_visits, joint_scores)
}""",
    )
    path.write_text(text)
    bindings = source / "poke-engine-py/src/lib.rs"
    text = bindings.read_text()
    text += (Path(__file__).parent / "native_search.rs").read_text()
    text = replace_once(
        text,
        "    m.add_function(wrap_pyfunction!(mcts, m)?)?;",
        """    m.add_function(wrap_pyfunction!(mcts, m)?)?;
    m.add_function(wrap_pyfunction!(seeded_search, m)?)?;
    m.add_function(wrap_pyfunction!(root_info, m)?)?;""",
    )
    bindings.write_text(text)
