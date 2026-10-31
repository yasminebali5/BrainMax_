import random


COGNITIVE_GAMES = (
    {
        'id': 'stroop',
        'title': 'Stroop Color and Word',
        'category': 'Attention & inhibition',
        'description': 'Find the subject answer through a focused color-coded choice.',
    },
    {
        'id': 'n_back',
        'title': 'N-Back Memory',
        'category': 'Working memory',
        'description': 'Study and recall the subject answer from a set of hidden cards.',
    },
    {
        'id': 'operation_span',
        'title': 'Operation Span',
        'category': 'Working memory',
        'description': 'Follow a subject-specific question through a branching answer diagram.',
    },
    {
        'id': 'progressive_matrices',
        'title': 'Pattern Matrix',
        'category': 'Fluid reasoning',
        'description': 'Complete a subject-specific puzzle by selecting its missing answer piece.',
    },
    {
        'id': 'bayesian_updating',
        'title': 'Evidence Updating',
        'category': 'Quantitative logic',
        'description': 'Trace a subject-specific claim through an evidence review chart.',
    },
)

_GAME_BY_ID = {game['id']: game for game in COGNITIVE_GAMES}
_DOMAIN_INTERACTION_FORMATS = {
    'stroop': 'color_focus',
    'n_back': 'memory_cards',
    'operation_span': 'flow_diagram',
    'progressive_matrices': 'puzzle',
    'bayesian_updating': 'evidence_chart',
}
_COLORS = ('RED', 'BLUE', 'GREEN', 'YELLOW')
_SHAPES = ('circle', 'triangle', 'square', 'star')
_FALLBACK_TERMS = ('A', 'B', 'C', 'D', 'E', 'H', 'K', 'M')


def _challenge(
    game_id,
    prompt,
    stimulus,
    options,
    answer,
    explanation,
    presentation=None,
):
    challenge = {
        'game_id': game_id,
        'prompt': prompt,
        'stimulus': stimulus,
        'options': options,
        'expected_index': options.index(answer),
        'expected': answer,
        'explanation': explanation,
    }
    if presentation:
        challenge['presentation'] = presentation
    return challenge


def new_domain_cognitive_challenge(game_id, question):
    if game_id not in _GAME_BY_ID:
        raise ValueError('Choose a cognitive game from the available catalog.')
    if not isinstance(question, dict):
        raise ValueError('Choose a reviewed question from the selected subject.')
    choices = question.get('choices')
    correct = question.get('correct')
    if (
        not isinstance(choices, list)
        or len(choices) < 2
        or type(correct) is not int
        or correct not in range(len(choices))
    ):
        raise ValueError('The selected subject question is not valid.')

    option_order = list(range(len(choices)))
    random.shuffle(option_order)
    options = [choices[index] for index in option_order]
    answer_index = option_order.index(correct)
    challenge = _challenge(
        game_id,
        question['prompt'],
        question['topic'],
        options,
        options[answer_index],
        question['explanation'],
        {
            'type': 'domain_question',
            'mode': game_id,
            'interaction_format': _DOMAIN_INTERACTION_FORMATS[game_id],
            'topic': question['topic'],
        },
    )
    challenge['domain_question_id'] = question['id']
    challenge['domain_option_order'] = option_order
    return challenge


def _context_terms(context):
    if not isinstance(context, dict):
        return []
    terms = context.get('concepts', [])
    if not isinstance(terms, (list, tuple)):
        terms = []
    terms = [
        term.strip()
        for term in terms
        if isinstance(term, str) and term.strip()
    ]
    concept = context.get('concept')
    if isinstance(concept, str) and concept.strip():
        terms.insert(0, concept.strip())
    return list(dict.fromkeys(terms))


def new_cognitive_challenge(game_id, context=None):
    challenge = _new_cognitive_challenge(game_id, context)
    if context:
        domain_context = {
            key: context[key]
            for key in ('subject', 'topic', 'concept')
            if isinstance(context.get(key), str) and context[key].strip()
        }
        if domain_context:
            challenge['domain_context'] = domain_context
            context_label = ' · '.join(domain_context.values())
            challenge['prompt'] = f'{context_label}: {challenge["prompt"]}'
    return challenge


def _new_cognitive_challenge(game_id, context=None):
    if game_id not in _GAME_BY_ID:
        raise ValueError('Choose a cognitive game from the available catalog.')
    domain_terms = _context_terms(context) or list(_FALLBACK_TERMS)

    if game_id == 'stroop':
        word = random.choice(domain_terms) if context else random.choice(_COLORS)
        ink = random.choice([color for color in _COLORS if color != word])
        if ink == word:
            ink = random.choice(_COLORS)
        return _challenge(
            game_id,
            'Select the ink color. Ignore the word.',
            f'{word} (shown in {ink} ink)',
            list(_COLORS),
            ink,
            f'The letters were displayed in {ink} ink; the word itself was a distractor.',
            {'type': 'stroop', 'word': word, 'ink': ink.lower()},
        )

    if game_id == 'flanker':
        target = random.choice(('LEFT', 'RIGHT'))
        distractor = 'RIGHT' if target == 'LEFT' else 'LEFT'
        arrows = ('←', '→')
        target_arrow = arrows[0 if target == 'LEFT' else 1]
        distractor_arrow = arrows[0 if distractor == 'LEFT' else 1]
        return _challenge(
            game_id,
            'Choose the direction of the center arrow.',
            f'{distractor_arrow} {distractor_arrow}  {target_arrow}  {distractor_arrow} {distractor_arrow}',
            ['LEFT', 'RIGHT'],
            target,
            f'Focus on the center arrow, which points {target.lower()}.',
            {
                'type': 'flanker',
                'target': target.lower(),
                'arrows': [
                    distractor_arrow,
                    distractor_arrow,
                    target_arrow,
                    distractor_arrow,
                    distractor_arrow,
                ],
            },
        )

    if game_id == 'stop_signal':
        is_stop = random.random() < 0.28
        direction = random.choice(('LEFT', 'RIGHT'))
        cue = 'STOP' if is_stop else f'GO {direction}'
        answer = direction
        return _challenge(
            game_id,
            'Follow the signal: respond to GO, withhold your response for STOP.',
            cue,
            ['LEFT', 'RIGHT'],
            answer,
            'A STOP cue means do not respond.' if is_stop else f'The GO direction is {direction.lower()}.',
            {
                'type': 'stop_signal',
                'is_stop': is_stop,
                'direction': direction.lower(),
                'arrow': '←' if direction == 'LEFT' else '→',
                'ssd_ms': random.randint(150, 350),
            },
        )

    if game_id == 'n_back':
        first, second = random.sample(domain_terms, 2)
        matches = random.choice((True, False))
        last = first if matches else random.choice(
            [symbol for symbol in domain_terms if symbol != first]
        )
        return _challenge(
            game_id,
            'Does the final symbol match the one two positions earlier?',
            f'{first}  →  {second}  →  {last}',
            ['MATCH', 'NO MATCH'],
            'MATCH' if matches else 'NO MATCH',
            f'The final symbol {last} does '
            f'{"match" if matches else "not match"} the first symbol {first}.',
            {
                'type': 'sequence',
                'symbols': [first, second, last],
            },
        )

    if game_id == 'operation_span':
        letters = random.sample(domain_terms, 3)
        equations = []
        equation_checks = []
        steps = []
        for concept in letters:
            left = random.randint(2, 6)
            right = random.randint(2, 5)
            adjustment = random.randint(1, 4)
            sign = random.choice(('+', '-'))
            correct_value = left * right + adjustment if sign == '+' else left * right - adjustment
            displayed_value = correct_value + random.choice((-1, 1)) if random.choice((True, False)) else correct_value
            equation_checks.append(displayed_value == correct_value)
            equation = f'({left} × {right}) {sign} {adjustment} = {displayed_value}'
            equations.append(equation)
            steps.append({
                'equation': equation,
                'letter': concept,
            })
        sequence = ' → '.join(letters)
        check_summary = ', '.join(
            'true' if check else 'false' for check in equation_checks
        )
        correct = f'{sequence} | Equations: {check_summary}'
        options = [correct]
        while len(options) < 4:
            alternative_letters = ' → '.join(
                random.sample(letters, len(letters))
            )
            alternative_checks = ', '.join(
                random.choice(('true', 'false')) for _ in equation_checks
            )
            alternative = (
                f'{alternative_letters} | Equations: {alternative_checks}'
            )
            if alternative not in options:
                options.append(alternative)
        random.shuffle(options)
        challenge = _challenge(
            game_id,
            'Check each equation, remember its subject concept, then recall the sequence.',
            '\n'.join(equations),
            options,
            correct,
            f'The letter order was {sequence}; the equation checks were {check_summary}.',
            {
                'type': 'operation_span',
                'steps': steps,
                'letter_options': domain_terms,
            },
        )
        challenge['operation_span_data'] = {
            'equation_checks': equation_checks,
            'letters': letters,
        }
        return challenge

    if game_id == 'task_switch':
        number = random.choice((1, 2, 3, 4, 6, 7, 8, 9))
        rule = random.choice(('PARITY', 'MAGNITUDE'))
        answer = (
            'EVEN' if number % 2 == 0 else 'ODD'
        ) if rule == 'PARITY' else ('HIGH' if number > 5 else 'LOW')
        options = ['ODD', 'EVEN'] if rule == 'PARITY' else ['LOW', 'HIGH']
        return _challenge(
            game_id,
            'Apply the rule shown to the number.',
            f'{rule}: {number}',
            options,
            answer,
            f'{number} is {answer.lower()} under the {rule.lower()} rule.',
            {'type': 'task_switch', 'rule': rule, 'number': number},
        )

    if game_id == 'wcst':
        references = [
            {'color': 'red', 'shape': 'triangle', 'count': 1},
            {'color': 'green', 'shape': 'star', 'count': 2},
            {'color': 'blue', 'shape': 'circle', 'count': 3},
            {'color': 'yellow', 'shape': 'square', 'count': 4},
        ]
        dimension = random.choice(('color', 'shape', 'count'))
        matching = random.choice(references)
        target = {
            'color': matching['color'] if dimension == 'color' else random.choice(
                [color for color in ('red', 'green', 'blue', 'yellow') if color != matching['color']]
            ),
            'shape': matching['shape'] if dimension == 'shape' else random.choice(
                [shape for shape in _SHAPES if shape != matching['shape']]
            ),
            'count': matching['count'] if dimension == 'count' else random.choice(
                [count for count in (1, 2, 3, 4) if count != matching['count']]
            ),
        }
        rule = f'match by {dimension}'
        options = [
            f"Card {index + 1}: {card['color']} · {card['shape']} · {card['count']}"
            for index, card in enumerate(references)
        ]
        target_text = (
            f"Rule: {rule}\n"
            f"Target: {target['color']} · {target['shape']} · {target['count']}"
        )
        answer = options[references.index(matching)]
        return _challenge(
            game_id,
            'Choose the reference card that matches the target under the announced rule.',
            target_text + '\n' + '\n'.join(options),
            options,
            answer,
            f'The rule is to match by {dimension}; {answer} has the same {dimension} as the target.',
            {
                'type': 'card_sorting',
                'rule': dimension,
                'target': target,
                'cards': references,
            },
        )

    if game_id == 'progressive_matrices':
        concepts = random.sample(domain_terms, 3)
        rows = [concepts[:], concepts[:], [concepts[0], concepts[1], None]]
        options = concepts[:]
        random.shuffle(options)
        return _challenge(
            game_id,
            'Choose the missing subject concept. Each row follows the same left-to-right pattern.',
            '\n'.join('  '.join(str(cell) if cell is not None else '?' for cell in row) for row in rows),
            options,
            concepts[2],
            f'The missing concept is {concepts[2]}, matching the repeated row pattern.',
            {'type': 'matrix', 'rows': rows},
        )

    if game_id == 'bayesian_updating':
        concept = random.choice(domain_terms) if context else 'the system'
        prior = random.choice((1, 2, 5, 10, 15, 20))
        sensitivity = random.choice((80, 85, 90, 95))
        false_positive = random.choice((5, 8, 10, 15))
        posterior = round(
            (sensitivity * prior)
            / (sensitivity * prior + false_positive * (100 - prior))
            * 100
        )
        choices = {posterior, sensitivity, max(1, round(posterior * 0.45)), min(99, round(posterior * 1.65))}
        while len(choices) < 4:
            choices.add(random.randint(1, 99))
        options = [f'{value}%' for value in choices]
        random.shuffle(options)
        correct = f'{posterior}%'
        return _challenge(
            game_id,
            f'A positive test for {concept} was observed. What is the approximate probability it is truly present?',
            f'Base rate for {concept}: {prior}%\nSensitivity: {sensitivity}%\nFalse-positive rate: {false_positive}%',
            options,
            correct,
            f'Using Bayes’ rule, the posterior probability is approximately {posterior}%.',
            {
                'type': 'evidence',
                'prior': prior,
                'sensitivity': sensitivity,
                'false_positive': false_positive,
            },
        )

    if game_id == 'choice_reaction':
        target = random.choice(('●', '▲', '■', '★'))
        options = ['●', '▲', '■', '★']
        return _challenge(
            game_id,
            'Select the symbol that matches the target.',
            target,
            options,
            target,
            f'The target symbol was {target}.',
            {'type': 'symbol', 'symbol': target},
        )

    raise ValueError('This cognitive game does not have a challenge generator.')
