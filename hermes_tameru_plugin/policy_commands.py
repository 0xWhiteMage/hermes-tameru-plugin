"""/tameru policy command parsing and the compact policy presentation."""

from __future__ import annotations

from .session_policy import PolicyError

_LABELS = {
 'mode': {'adaptive':'Adaptive','fixed':'Fixed','legacy':'Legacy'},
 'preference': {'save_space':'Save space','balanced':'Balanced','preserve_detail':'Preserve detail'},
 'activation': {'auto':'Auto','earlier':'Earlier','standard':'Standard','later':'Later'},
 'recent': {'auto':'Auto','short':'Short','standard':'Standard','long':'Long'},
}
_SHORTHAND = {'save-space':'save_space','balanced':'balanced','preserve-detail':'preserve_detail'}
HELP = ('Use /tameru, /tameru changes, /tameru show REF [character offset], /tameru ask REF [START-END | term], /tameru bias save|balanced|detail, '
        '/tameru pause, /tameru resume, /tameru pin TEXT, /tameru pins, /tameru stats, /tameru reset, /tameru help, or the older /tameru policy [key=value ...].')


def _parse(parts):
    updates, reset, life = {}, False, None
    index = 0
    while index < len(parts):
        item = parts[index]
        if item == '--lifecycle':
            if life is not None or index + 1 >= len(parts) or not parts[index + 1]:
                raise PolicyError(HELP)
            life = parts[index + 1]
            index += 2
            continue
        if item == 'reset':
            if reset or updates:
                raise PolicyError(HELP)
            reset = True
        else:
            if reset:
                raise PolicyError(HELP)
            if item in _SHORTHAND:
                key, value = 'preference', _SHORTHAND[item]
            elif '=' in item:
                key, value = item.split('=', 1)
            else:
                raise PolicyError(HELP)
            if key not in _LABELS or key in updates or value not in _LABELS[key]:
                raise PolicyError(HELP)
            updates[key] = value
        index += 1
    return updates, reset, life


def policy_command(engine, parts, *, session_id, lifecycle_id, json_mode):
    updates, reset, requested_life = _parse(parts)
    if requested_life is not None and requested_life != lifecycle_id:
        raise PolicyError('The active session changed. Open context details again.')
    if updates or reset:
        if json_mode and requested_life is None:
            raise PolicyError('Refresh context details before changing preferences.')
        engine.set_policy_controls(updates, session_id=session_id,
                                   lifecycle_id=requested_life or lifecycle_id, reset=reset)
    return {'ok': True, 'kind': 'policy', **engine.activity_snapshot()}


def policy_lines(policy):
    controls = policy.get('controls', {})
    def label(key):
        return _LABELS[key].get(controls.get(key), 'Unavailable')
    lines = [f"Policy: {label('mode')} · {label('preference')}"]
    if controls.get('mode') == 'legacy':
        lines.append('Legacy mode uses the existing cleanup settings; adaptive preferences are inactive.')
    else:
        lines.append(f"Timing: {label('activation')} · Recent protection: {label('recent')}")
        if policy.get('proactive_recent_basis') == 'messages':
            lines.append('Routine cleanup follows the configured recent-message count.')
    presentation = policy_presentation(policy)
    lines.append(presentation['storage'])
    lines += presentation['budget'] + presentation['check']
    if presentation['last_action']:
        lines.append(presentation['last_action'])
    if policy.get('controls_issue'):
        lines.append(policy['controls_issue'])
    lines.append(presentation['footnote'])
    return lines


def _budget_lines(policy):
    budget = policy.get('budget', {})
    usable, window = budget.get('usable_input_tokens'), budget.get('window_tokens')
    if type(usable) is int:
        source = {'configured_input':'configured input limit', 'configured_reserve':'configured response reserve',
                  'host':'host reservation', 'previous_request':'previous request'}.get(budget.get('source'), 'unavailable source')
        title = 'Estimated input limit' if budget.get('estimated') else 'Input limit'
        lines = [f'{title}: {usable:,} tokens ({source}).']
    elif type(window) is int:
        lines = [f'Context window: {window:,} tokens; response headroom unavailable.']
    else:
        lines = ['Input budget: unavailable.']
    if budget.get('estimated'):
        lines.append('The next request may use a different response allowance.')
    invalid = {'inconsistent_input_limit':'Declared input and response limits conflict.',
               'no_input_space':'No input space remains in the declared budget.'}.get(budget.get('invalid_reason'))
    if invalid:
        lines.append(invalid)
    return lines


def _check_lines(policy):
    check = policy.get('last_check')
    if not check:
        return []
    lines = []
    amount = check.get('input_tokens')
    if type(amount) is int:
        complete = (check.get('plan') or {}).get('input_complete')
        basis = {True: 'whole request estimate', False: 'message bodies only'}.get(complete, 'accounting unavailable')
        lines.append(f'Last check: ~{amount:,} tokens ({basis}).')
    reasons = {'ample_room':'Enough room; detail retained.', 'pressure':'Context pressure allowed cleanup.',
               'no_commit':'No cleanup met the reclaim and cache requirements.',
               'host_compaction':'Hermes requested full compaction.',
               'activation_disabled':'Routine cleanup is disabled in settings.'}
    if check.get('reason') in reasons:
        lines.append(reasons[check['reason']])
    return lines


def _commit_line(policy):
    commit = policy.get('last_commit')
    if not commit:
        return None
    method = {'local_full':'Local full cleanup','full':'Hermes full compaction','proactive':'Routine cleanup'}.get(commit.get('method'),'Cleanup')
    mode = commit.get('plan', {}).get('controls', {}).get('mode')
    stage = {'light':'Light','standard':'Standard','strong':'Strong'}.get(commit.get('stage'),'Unspecified')
    count = commit.get('estimated_reclaim_tokens')
    if type(count) is not int:
        return None
    effect = 'reclaimed' if count >= 0 else 'added'
    return (f'Last {_LABELS["mode"].get(mode, "policy").lower()} action: {method} · {stage}; '
            f'~{abs(count):,} message tokens {effect}.')


_SOURCES = {'default':'Using defaults for this agent.', 'session':'Preferences saved for this chat.',
            'memory':'Preferences apply until this agent restarts.'}
_FOOTNOTE = 'Changes apply to future cleanup; they do not restore removed content.'


def policy_presentation(policy):
    sources = _SOURCES
    return {'storage': sources.get(policy.get('controls_source'), 'Preference storage is unavailable.'),
            'budget': _budget_lines(policy), 'check': _check_lines(policy),
            'last_action': _commit_line(policy),
            'footnote': _FOOTNOTE}
