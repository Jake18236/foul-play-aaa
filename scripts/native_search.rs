
// Diagnostic API: both selectors consume the same seeded tree, root payoff
// priors, and computation. No RNG outcomes or hidden sets are supplied by replay.
#[pyfunction]
fn seeded_search(
    py_state: PyState, duration_ms: u64, iterations: u32, seed: u64,
) -> PyResult<(PyMctsResult, Vec<Vec<f32>>, Vec<Vec<u32>>, Vec<Vec<f32>>)> {
    let start = std::time::Instant::now();
    let mut state: State = py_state.into();
    let (rows, cols) = state.root_get_all_options();
    let root_eval = poke_engine::engine::evaluate::evaluate(&state);
    let mut priors = vec![vec![0.0; cols.len()]; rows.len()];
    for (i, a) in rows.iter().enumerate() {
        for (j, b) in cols.iter().enumerate() {
            let branches = generate_instructions_from_move_pair(&mut state, a, b, true);
            let mut mass = 0.0;
            for branch in branches {
                state.apply_instructions(&branch.instruction_list);
                let terminal = state.battle_is_over();
                let score = if terminal < 0.0 { 0.0 } else if terminal > 0.0 {
                    terminal
                } else {
                    let delta = poke_engine::engine::evaluate::evaluate(&state) - root_eval;
                    1.0 / (1.0 + (-0.0125 * delta).exp())
                };
                state.reverse_instructions(&branch.instruction_list);
                priors[i][j] += score * branch.percentage;
                mass += branch.percentage;
            }
            if mass <= 0.0 {
                return Err(pyo3::exceptions::PyValueError::new_err("Empty root chance distribution"));
            }
            priors[i][j] /= mass;
        }
    }
    let remaining = duration_ms.saturating_sub(start.elapsed().as_millis() as u64);
    let (result, visits, scores) = poke_engine::mcts::perform_mcts_diagnostics(
        &mut state, rows, cols, Duration::from_millis(remaining), iterations, seed,
    );
    Ok((PyMctsResult::from_mcts_result(result, &state), priors, visits, scores))
}

#[pyfunction]
fn root_info(py_state: PyState) -> PyResult<(Vec<String>, Vec<String>, f32, f32)> {
    let mut state: State = py_state.into();
    let (a, b) = state.root_get_all_options();
    Ok((a.iter().map(|m| movechoice_to_string(&state.side_one, m)).collect(),
        b.iter().map(|m| movechoice_to_string(&state.side_two, m)).collect(),
        poke_engine::engine::evaluate::evaluate(&state), state.battle_is_over()))
}

