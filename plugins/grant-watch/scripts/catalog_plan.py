"""Pure dry-run planner for compact catalog queues; NOT a crawler or runtime limiter."""
DEFAULT_BUDGETS = dict(sources=20, calls=30, requests=100, runtime_minutes=45,
                       request_timeout_seconds=30, max_retries=2)


def plan_run(sources, calls, budgets=None):
    """Inputs are compact active/due rows, never raw archive observations.

    Queue order is supplied by the persistent runner (due time / resume cursor).
    Duplicate identities are counted once. Conflicting duplicates require review.
    """
    limits = dict(DEFAULT_BUDGETS)
    if budgets:
        if set(budgets) - set(limits):
            raise ValueError("Unknown budget")
        limits.update(budgets)
    if any(type(v) is not int or v < (0 if k == "max_retries" else 1)
           for k, v in limits.items()):
        raise ValueError("Budgets must be positive integers; retries may be zero")

    def unique(rows, fields):
        found, conflicts = {}, set()
        for row in rows:
            key = tuple(row.get(f) for f in fields)
            if any(not isinstance(v, str) or not v.strip() for v in key):
                raise ValueError("Missing stable catalog identity")
            if row.get("active") is not True or row.get("due") is not True:
                continue
            if key in found and found[key] != row:
                conflicts.add(key)
            else:
                found[key] = row
        return [r for k, r in found.items() if k not in conflicts], len(conflicts)

    src, src_conflicts = unique(sources, ("source_key",))
    known, call_conflicts = unique(calls, ("program_key", "cycle"))
    skipped, eligible, ambiguous = [], [], []
    # The runner must obtain this decision from should_recheck and verified evidence,
    # not infer it from raw workbook labels. Personal-only restrictions never qualify.
    for row in known:
        if row.get("identity_ambiguous") is True:
            ambiguous.append(row)
        elif row.get("confirmed_excluded_same_cycle") is True:
            skipped.append(row)
        else:
            eligible.append(row)
    selected_sources = src[:limits["sources"]]
    selected_calls = eligible[:limits["calls"]]
    review = [r for r in selected_calls if r.get("eligibility_review_due") is not False]
    return dict(
        budgets=limits,
        discovery_sources_due=len(src), discovery_sources_selected=len(selected_sources),
        known_unique_calls_due=len(known), calls_selected=len(selected_calls),
        lightweight_status_checks=len(selected_calls), eligibility_reviews=len(review),
        confirmed_excluded_skips=len(skipped), ambiguity_reviews=len(ambiguous),
        conflicting_source_keys=src_conflicts, conflicting_call_keys=call_conflicts,
        deferred_sources=len(src)-len(selected_sources), deferred_calls=len(eligible)-len(selected_calls),
        selected_sources=selected_sources, selected_calls=selected_calls,
        new_discoveries_count=None, planned_successes=0,
        note="Planning only. Request count and discoveries are unknown until execution; runtime must enforce budgets.")
