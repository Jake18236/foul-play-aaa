
// Bulk exchange interface. Rust owns chance expansion, Python owns a batch of
// observation-only evaluations. No Python callback occurs inside MCTS.
#[pyfunction]
fn neural_successors(jobs: Vec<(PyState, String, String)>) -> PyResult<Vec<Vec<(PyState, f32)>>> {
    use poke_engine::state::SideReference;
    use poke_engine::engine::generate_instructions::get_effective_speed;
    use poke_engine::engine::abilities::ability_on_switch_in;
    use poke_engine::engine::items::item_on_switch_in;
    let mut results = Vec::with_capacity(jobs.len());
    for (py_state, a, b) in jobs {
        let mut state: State = py_state.into();
        state.track_all_pp = true;
        state.use_last_used_move = true;
        let (legal_a, legal_b) = state.root_get_all_options();
        let a = legal_a.iter().find(|m| m.to_string(&state.side_one).to_lowercase() == a.to_lowercase()
            || (matches!(m, MoveChoice::None) && a == "none")).cloned()
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("Invalid side one action"))?;
        let b = legal_b.iter().find(|m| m.to_string(&state.side_two).to_lowercase() == b.to_lowercase()
            || (matches!(m, MoveChoice::None) && b == "none")).cloned()
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("Invalid side two action"))?;
        if !legal_a.contains(&a) || !legal_b.contains(&b) {
            return Err(pyo3::exceptions::PyValueError::new_err("Illegal joint action"));
        }
        if state.team_preview {
            let (MoveChoice::Switch(i), MoveChoice::Switch(j)) = (a, b) else {
                return Err(pyo3::exceptions::PyValueError::new_err("Expected lead switches"));
            };
            state.side_one.active_index = i;
            state.side_two.active_index = j;
            state.side_one.last_used_move = LastUsedMove::Switch(i);
            state.side_two.last_used_move = LastUsedMove::Switch(j);
            state.team_preview = false;
            let s1 = get_effective_speed(&state, &SideReference::SideOne);
            let s2 = get_effective_speed(&state, &SideReference::SideTwo);
            let orders = if s1 == s2 { vec![(true, 50.0), (false, 50.0)] }
                else { vec![(s1 > s2, 100.0)] };
            let mut successors = Vec::new();
            for (first, percentage) in orders {
                let mut next = state.clone();
                let mut instructions = StateInstructions::default();
                let order = if first { [SideReference::SideOne, SideReference::SideTwo] }
                    else { [SideReference::SideTwo, SideReference::SideOne] };
                for side in order {
                    item_on_switch_in(&mut next, &side, &mut instructions);
                    ability_on_switch_in(&mut next, &side, &mut instructions);
                }
                successors.push((PyState::from(next), percentage));
            }
            results.push(successors);
        } else {
            let branches = generate_instructions_from_move_pair(&mut state, &a, &b, true);
            let mut successors = Vec::with_capacity(branches.len());
            for branch in branches {
                state.apply_instructions(&branch.instruction_list);
                successors.push((PyState::from(state.clone()), branch.percentage));
                state.reverse_instructions(&branch.instruction_list);
            }
            results.push(successors);
        }
    }
    Ok(results)
}
