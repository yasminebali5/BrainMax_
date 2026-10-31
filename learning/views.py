import json
import random
from datetime import date, datetime, timedelta
from statistics import mean
from django.db import transaction
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from .coach import (
    CoachProviderError,
    _ai_line,
    ai_provider_configured,
    forecast_for,
    generate_learning_exercises,
    response_for,
)
from .cognitive_games import (
    COGNITIVE_GAMES,
    new_cognitive_challenge,
    new_domain_cognitive_challenge,
)
from django.utils import timezone
from django.views.decorators.http import require_POST
from .memory import (
    MemoryStoreUnavailable,
    forget_history,
    index_history,
    index_learner_context,
    index_learning_path,
    retrieve_history,
)
from .localization import localize_question, localize_subject, localize_topic
from .models import Learner, Assessment, LearningPathRun
from .questions import (
    BANK,
    HEBA_EXERCISES,
    HEBA_EXERCISES_BY_SUBJECT,
    SUBJECTS,
    define_choice,
    select_questions,
)


PATH_STEP_DETAILS = (
    ('memory', 'Sequence memory'),
    ('focus', 'Focus grid'),
    ('matching', 'Concept match'),
    ('balance', 'Balance tower'),
    ('strategy', 'Strategy board'),
    ('stroop', 'Stroop Color and Word'),
    ('n_back', 'N-Back Memory'),
    ('operation_span', 'Operation Span'),
    ('progressive_matrices', 'Pattern Matrix'),
    ('bayesian_updating', 'Evidence Updating'),
)
RETIRED_COGNITIVE_PATH_IDS = {
    'composite_stroop_nback',
    'flanker',
    'stop_signal',
    'task_switch',
    'wcst',
    'choice_reaction',
}
COGNITIVE_GAME_BY_ID = {game['id']: game for game in COGNITIVE_GAMES}
COGNITIVE_TRIAL_COUNT = 10
PATH_PASS_SCORE = 70
AI_COLLABORATION_WEIGHTS = {
    'clarification': 20,
    'critical_verification': 25,
    'iteration': 20,
    'autonomy': 20,
    'responsible_use': 15,
}


def _collaboration_payload(learner, assessment=None):
    reflection = learner.preferences.get('collaboration_reflection', {})
    ratings = reflection.get('ratings', {}) if isinstance(reflection, dict) else {}
    if not isinstance(ratings, dict) or set(ratings) != set(AI_COLLABORATION_WEIGHTS):
        ratings = {}
    if any(type(value) is not int or value not in range(101) for value in ratings.values()):
        ratings = {}

    independence = None
    if assessment and assessment.completed_at:
        independence = serialize(
            assessment,
            learner.preferences.get('language', 'en'),
        )['independence']
    components = dict(ratings)
    if ratings:
        components['autonomy'] = round(
            (ratings['autonomy'] + independence) / 2
        ) if independence is not None else ratings['autonomy']
        score = round(sum(
            components[key] * weight
            for key, weight in AI_COLLABORATION_WEIGHTS.items()
        ) / 100)
    else:
        score = None
    return {
        'score': score,
        'ratings': ratings,
        'components': components,
        'autonomy_evidence': independence,
        'weights': AI_COLLABORATION_WEIGHTS,
        'updated_at': reflection.get('updated_at') if isinstance(reflection, dict) else None,
    }


def _new_heba_exercise_state(subject, generated_exercises=None):
    exercises = []
    if generated_exercises:
        exercises = [
            {
                **exercise,
                'title': f'AI-generated exercise {index + 1}',
                'exercise_type': 'mcq',
                'expected_time_seconds': 30,
            }
            for index, exercise in enumerate(generated_exercises)
        ]
        source = 'ai'
    else:
        for exercise_id in HEBA_EXERCISES_BY_SUBJECT[subject]:
            exercise = dict(HEBA_EXERCISES[exercise_id])
            exercise['id'] = exercise_id
            if exercise['exercise_type'] == 'memory':
                exercise['choices'] = exercise['sequence'][:]
                random.shuffle(exercise['choices'])
            exercises.append(exercise)
        source = 'heba'
    return {
        'questions': exercises,
        'index': 0,
        'answers': [],
        'started_at': timezone.now().isoformat(),
        'completed': False,
        'passed': False,
        'score': None,
        'source': source,
    }


def _ensure_heba_exercise_state(run):
    if not run.steps:
        return None
    state = run.steps[0].get('heba_exercise')
    if state is None:
        state = _new_heba_exercise_state(run.assessment.subject)
        run.steps[0]['heba_exercise'] = state
        run.save(update_fields=['steps', 'updated_at'])
    return state


def _maybe_generate_heba_exercises(run, learner, language):
    if not learner.preferences.get('ai') or not ai_provider_configured():
        return
    state = run.steps[0].get('heba_exercise') if run.steps else None
    if state and (state.get('source') == 'ai' or state.get('answers')):
        return

    examples = [
        HEBA_EXERCISES[exercise_id]
        for exercise_id in HEBA_EXERCISES_BY_SUBJECT[run.assessment.subject]
    ]
    topics = list(dict.fromkeys(item['topic'] for item in examples))
    generated = generate_learning_exercises(
        run.assessment.subject,
        topics,
        topics,
        language,
        examples=examples,
    )
    new_state = _new_heba_exercise_state(
        run.assessment.subject,
        generated_exercises=generated,
    )
    with transaction.atomic():
        locked_run = LearningPathRun.objects.select_for_update().get(pk=run.pk)
        current_state = (
            locked_run.steps[0].get('heba_exercise')
            if locked_run.steps else None
        )
        if current_state is None or (
            current_state.get('source') != 'ai'
            and not current_state.get('answers')
        ):
            locked_run.steps[0]['heba_exercise'] = new_state
            locked_run.save(update_fields=['steps', 'updated_at'])
            run.steps = locked_run.steps


def _ensure_cognitive_games_state(run):
    if not run.steps:
        return None
    state = run.steps[0].get('cognitive_games')
    if not isinstance(state, dict):
        state = {'active': None, 'results': []}
        run.steps[0]['cognitive_games'] = state
        run.save(update_fields=['steps', 'updated_at'])
    return state


def _cognitive_games_payload(run):
    state = _ensure_cognitive_games_state(run)
    if state is None:
        return None
    active = state.get('active')
    results = state.get('results', [])
    result_by_game = {
        result['game_id']: result
        for result in results
    }
    return {
        'catalog': list(COGNITIVE_GAMES),
        'played': len(results),
        'results': [
            {
                'game_id': game['id'],
                'attempts': sum(
                    result['game_id'] == game['id'] for result in results
                ),
                'score': result_by_game[game['id']]['score'],
                'correct': result_by_game[game['id']]['correct'],
                'seconds': result_by_game[game['id']]['seconds'],
            }
            for game in COGNITIVE_GAMES
            if game['id'] in result_by_game
        ],
        'active': ({
            key: active.get(key)
            for key in (
                'game_id', 'prompt', 'stimulus', 'options', 'presentation',
                'domain_context', 'domain_question_id', 'started_at',
            )
        } if active else None),
    }


def _start_cognitive_game(run, data):
    game_id = data.get('game_id')
    seen_question_ids = _seen_cognitive_question_ids(
        run.learner,
        run.assessment.subject,
    )
    reserved_path_question_ids = {
        trial['domain_question_id']
        for step in run.steps
        for trial in step.get('cognitive_trials', [])
        if trial.get('domain_question_id')
    }
    available_questions = [
        question for question in BANK[run.assessment.subject]
        if question['id'] not in seen_question_ids
        and question['id'] not in reserved_path_question_ids
    ]
    if not available_questions:
        return JsonResponse(
            {
                'error': (
                    'Fresh reviewed questions are temporarily unavailable '
                    'for this subject. We will not repeat a question you '
                    'have already seen.'
                ),
            },
            status=409,
        )
    try:
        question = random.choice(available_questions)
        challenge = new_domain_cognitive_challenge(game_id, question)
    except (ValueError, TypeError):
        return JsonResponse(
            {'error': 'Choose a cognitive game from the available catalog.'},
            status=400,
        )
    state = _ensure_cognitive_games_state(run)
    if state is None:
        return JsonResponse({'error': 'Learning path is not available.'}, status=400)
    state['active'] = {
        **challenge,
        'started_at': timezone.now().isoformat(),
    }
    run.save(update_fields=['steps', 'updated_at'])
    return _path_payload(run, run.learner.preferences.get('language', 'en'))


def _score_cognitive_game(run, data, language='en'):
    state = _ensure_cognitive_games_state(run)
    active = state.get('active') if state else None
    if not active or data.get('game_id') != active['game_id']:
        return JsonResponse(
            {'error': 'Start a cognitive game before submitting an answer.'},
            status=409,
        )
    answer = data.get('answer')
    if type(answer) is not int or answer not in range(len(active['options'])):
        return JsonResponse(
            {'error': 'Choose one of the displayed answers.'},
            status=400,
        )
    correct = answer == active['expected_index']
    elapsed_seconds = max(
        0,
        round((
            timezone.now() - datetime.fromisoformat(active['started_at'])
        ).total_seconds()),
    )
    state['results'].append({
        'game_id': active['game_id'],
        'domain_question_id': active.get('domain_question_id'),
        'correct': correct,
        'score': 100 if correct else 0,
        'seconds': elapsed_seconds,
        'explanation': active['explanation'],
        'answer': active['options'][answer],
        'expected': active['expected'],
    })
    game_result = state['results'][-1]
    state['active'] = None
    run.save(update_fields=['steps', 'updated_at'])
    result = _path_payload(run, language)
    result['cognitive_game_result'] = {
        key: game_result[key]
        for key in ('game_id', 'correct', 'score', 'seconds', 'explanation')
    }
    return result


def _heba_exercise_payload(run):
    state = _ensure_heba_exercise_state(run)
    if state is None:
        return None
    question = (
        state['questions'][state['index']]
        if not state['completed']
        else None
    )
    correct_count = sum(answer['correct'] for answer in state['answers'])
    return {
        'source': state['source'],
        'index': state['index'],
        'total': len(state['questions']),
        'answered': len(state['answers']),
        'score': state['score'] if state['completed'] else (
            round(100 * correct_count / len(state['answers']))
            if state['answers'] else None
        ),
        'status': (
            'passed' if state['completed'] and state['passed']
            else 'needs_review' if state['completed']
            else 'in_progress'
        ),
        'completed': state['completed'],
        'passed': state['passed'],
        'attempts': len(state['answers']),
        'question': ({
            'id': question['id'] if 'id' in question else None,
            'title': question['title'],
            'topic': question['topic'],
            'exercise_type': question['exercise_type'],
            'prompt': question['prompt'],
            'code': question.get('code'),
            'choices': question.get('choices', []),
            'sequence': question.get('sequence'),
            'expected_time_seconds': question['expected_time_seconds'],
        } if question else None),
        'review': [
            {
                key: answer[key]
                for key in ('topic', 'answer', 'expected', 'explanation', 'correct', 'seconds')
            }
            for answer in state['answers']
        ] if state['completed'] else [],
    }


def learner(request):
    return Learner.objects.get_or_create(user=request.user)[0]


def payload(request):
    try:
        data = json.loads(request.body)
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def serialize(a, language='en'):
    items = a.questions or BANK[a.subject][:6]
    localized_items = [localize_question(item, language) for item in items]
    out = {'id': a.id, 'subject': a.subject, 'subject_name': localize_subject(a.subject, language), 'total': len(items), 'answers_count': len(a.answers), 'completed': bool(a.completed_at)}
    if a.completed_at:
        rows = a.answers
        accuracy = round(100 * mean(r['correct'] for r in rows))
        independent = round(100 * mean(r['correct'] and not r['hint'] for r in rows))
        calibration = round(100 * (1 - mean((r['confidence'] / 100 - int(r['correct'])) ** 2 for r in rows)))
        topics = {
            localize_topic(topic, language): round(
                100 * mean(r['correct'] for r in rows if r['topic'] == topic)
            )
            for topic in dict.fromkeys(q['topic'] for q in items)
        }
        levels = {
            'fr': {
                'Strong starting point': 'Très bon point de départ',
                'Developing': 'En progression',
                'Foundation building': 'Consolidation des bases',
            },
            'ar': {
                'Strong starting point': 'نقطة انطلاق قوية',
                'Developing': 'في طور التطور',
                'Foundation building': 'بناء الأساسيات',
            },
        }
        level = 'Strong starting point' if independent >= 80 else 'Developing' if independent >= 50 else 'Foundation building'
        out.update(score=independent, accuracy=accuracy, calibration=calibration, independence=round(100 * mean(not r['hint'] for r in rows)), avg_time=round(mean(r['seconds'] for r in rows), 1), topics=topics, difficulty=_session_difficulty(items), level=levels.get(language, {}).get(level, level), date=a.completed_at.strftime('%d %b %Y'), completed_at=a.completed_at.isoformat(), review=[dict(question_id=q['id'], prompt=q['prompt'], choices=q['choices'], explanation=q['explanation'], difficulty=q.get('difficulty', 'Foundation'), chosen=q['choices'][r['choice']], expected=q['choices'][q['correct']], **r) for q, r in zip(localized_items, rows)])
    else:
        item = localized_items[len(a.answers)]
        out['question'] = {k: item[k] for k in ('topic', 'prompt', 'choices')}
        out['difficulty'] = item.get('difficulty', 'Foundation')
        out['hint_used'] = a.hint_used
        if a.hint_used:
            out['hint'] = item['hint']
    return out


def _session_difficulty(items):
    levels = {'Foundation': 1, 'Building': 2, 'Stretch': 3}
    score = round(mean(levels.get(item.get('difficulty'), 1) for item in items))
    return {1: 'Foundation', 2: 'Building', 3: 'Stretch'}[score]


@login_required
def state(request):
    l = learner(request)
    language = l.preferences.get('language', 'en')
    attempts = l.assessment_set.order_by('-id')
    latest = attempts.first()
    completed = attempts.filter(completed_at__isnull=False).order_by('-completed_at')
    history = []
    for item in completed.iterator():
        summary = serialize(item, language)
        history.append({
            key: summary[key]
            for key in (
                'id', 'subject', 'subject_name', 'total', 'score', 'accuracy',
                'calibration', 'independence', 'avg_time', 'topics', 'level',
                'difficulty', 'date', 'completed_at',
            )
        })
    latest_completed = completed.first()
    today = timezone.localdate()
    activity_start = today - timedelta(days=today.weekday() + 77)
    activity_counts = {
        activity_start + timedelta(days=offset): 0
        for offset in range((today - activity_start).days + 1)
    }
    for completed_at in completed.filter(
        completed_at__date__gte=activity_start
    ).values_list('completed_at', flat=True):
        activity_date = timezone.localtime(completed_at).date()
        if activity_date in activity_counts:
            activity_counts[activity_date] += 1
    goal_score = l.goal.get('target_score')
    goal_progress = min(
        100,
        round(serialize(latest_completed, language)['score'] * 100 / goal_score),
    ) if latest_completed and type(goal_score) is int and goal_score > 0 else 0
    week_start = today - timedelta(days=today.weekday())
    current_week_sessions = sum(
        count for activity_date, count in activity_counts.items()
        if activity_date >= week_start
    )
    return JsonResponse({
        'profile': l.profile,
        'goal': l.goal,
        'goal_progress': goal_progress,
        'current_week_sessions': current_week_sessions,
        'activity': [
            {'date': activity_date.isoformat(), 'count': count}
            for activity_date, count in activity_counts.items()
        ],
        'preferences': l.preferences,
        'ai_ready': ai_provider_configured(),
        'collaboration': _collaboration_payload(l, latest_completed),
        'assessment': serialize(latest, language) if latest else None,
        'latest_completed': serialize(latest_completed, language) if latest_completed else None,
        'history': history,
        'history_count': completed.count(),
        'forecast': forecast_for(l, serialize),
    })


@login_required
def memory_snapshot(request):
    l = learner(request)
    if not l.preferences.get('memory'):
        return JsonResponse({
            'enabled': False,
            'status': 'disabled',
            'documents': [],
        })
    profile = l.profile if isinstance(l.profile, dict) else {}
    goal = l.goal if isinstance(l.goal, dict) else {}
    query = 'learner name profile learning subject motivation goals history'
    query += ' ' + ' '.join(
        str(value)
        for value in (*profile.values(), *goal.values())
        if value
    )
    try:
        index_learner_context(l)
        documents = retrieve_history(l, query, limit=5)
    except MemoryStoreUnavailable as exc:
        return JsonResponse({
            'enabled': True,
            'status': 'unavailable',
            'error': str(exc),
            'documents': [],
        }, status=503)
    return JsonResponse({
        'enabled': True,
        'status': 'connected',
        'documents': documents,
    })


COGNITIVE_PATH_STEPS = ('memory', 'focus', *COGNITIVE_GAME_BY_ID)
PEDAGOGICAL_PATH_STEPS = ('matching', 'balance', 'strategy')


def _path_recommendation(learner, assessment):
    history = LearningPathRun.objects.filter(
        learner=learner,
        assessment__subject=assessment.subject,
    ).exclude(assessment=assessment).order_by('-updated_at')[:12]
    completed_runs = [
        run for run in history
        if run.steps
        and all(step.get('passed') for step in run.steps)
    ][:3]

    scores_by_step = {step_id: [] for step_id, _ in PATH_STEP_DETAILS}
    for run in completed_runs:
        for step in run.steps:
            scores = step.get('scores') or (
                [step['score']] if step.get('score') is not None else []
            )
            if step.get('id') in scores_by_step:
                scores_by_step[step['id']].extend(
                    score for score in scores if type(score) is int
                )

    cognitive_scores = [
        score
        for step_id in COGNITIVE_PATH_STEPS
        for score in scores_by_step[step_id]
    ]
    pedagogical_scores = [
        score
        for step_id in PEDAGOGICAL_PATH_STEPS
        for score in scores_by_step[step_id]
    ]
    cognitive_average = round(mean(cognitive_scores)) if cognitive_scores else None
    pedagogical_average = round(mean(pedagogical_scores)) if pedagogical_scores else None

    recommendation = {
        'activity_type': 'balanced',
        'recommended_step': None,
        'cognitive_score': cognitive_average,
        'cognitive_attempts': len(cognitive_scores),
        'pedagogical_score': pedagogical_average,
        'pedagogical_attempts': len(pedagogical_scores),
    }
    if len(cognitive_scores) >= 3 and pedagogical_scores:
        if cognitive_average <= pedagogical_average:
            recommendation['activity_type'] = 'cognitive'
            candidates = COGNITIVE_PATH_STEPS
        else:
            recommendation['activity_type'] = 'pedagogical'
            candidates = PEDAGOGICAL_PATH_STEPS
        recommendation['recommended_step'] = min(
            candidates,
            key=lambda step_id: (
                mean(scores_by_step[step_id])
                if scores_by_step[step_id]
                else 100
            ),
        )
    return recommendation


def _new_path_cognitive_challenges(
    game_id,
    assessment,
    excluded_question_ids=(),
    trial_count=COGNITIVE_TRIAL_COUNT,
):
    question_bank = [
        question for question in BANK[assessment.subject]
        if question['id'] not in excluded_question_ids
    ]
    if len(question_bank) < trial_count:
        raise ValueError(
            'Fresh reviewed questions are temporarily unavailable for this '
            'subject. We will not repeat a question you have already seen.'
        )
    questions = random.sample(question_bank, k=trial_count)
    return [
        new_domain_cognitive_challenge(game_id, question)
        for question in questions
    ]


def _seen_cognitive_question_ids(learner, subject):
    seen = set()
    runs = LearningPathRun.objects.filter(
        learner=learner,
        assessment__subject=subject,
    ).order_by('created_at')
    for run in runs:
        for step in run.steps:
            trials = step.get('cognitive_trials', [])
            has_started = bool(
                step.get('started_at')
                or step.get('attempts')
                or step.get('passed')
            )
            if has_started and step.get('practice_question', {}).get('id'):
                seen.add(step['practice_question']['id'])
            if has_started:
                seen.update(step.get('match_question_ids', []))
            seen.update(step.get('previous_cognitive_question_ids', []))
            trial_answers = step.get('trial_answers', [])
            if step.get('passed'):
                exposed_trials = trials
            elif (
                step.get('started_at')
                or step.get('trial_started_at')
                or trial_answers
            ):
                exposed_trials = trials[:min(len(trial_answers) + 1, len(trials))]
            else:
                exposed_trials = []
            seen.update(
                trial['domain_question_id']
                for trial in exposed_trials
                if trial.get('domain_question_id')
            )
        first_step = run.steps[0] if run.steps else {}
        for attempt in first_step.get('revised_cognitive_attempts', []):
            trials = attempt.get('trials', [])
            if attempt.get('attempts') or attempt.get('trial_answers'):
                seen.update(
                    trial['domain_question_id']
                    for trial in trials
                    if trial.get('domain_question_id')
                )
        game_state = first_step.get('cognitive_games') or {}
        active_game = game_state.get('active')
        if active_game and active_game.get('domain_question_id'):
            seen.add(active_game['domain_question_id'])
        seen.update(
            result['domain_question_id']
            for result in game_state.get('results', [])
            if result.get('domain_question_id')
        )
    return seen


def _create_path_steps(
    assessment,
    learner=None,
    previously_seen_question_ids=(),
):
    questions = assessment.questions or BANK[assessment.subject][:6]
    assessment_ids = {question.get('id') for question in questions}
    assessment_topics = {question['topic'] for question in questions}
    available_questions = [
        question for question in BANK[assessment.subject]
        if question.get('id') not in assessment_ids
        and question.get('id') not in previously_seen_question_ids
        and question['topic'] in assessment_topics
    ]
    topic_scores = {
        topic: mean(
            int(answer['correct'] and not answer['hint'])
            for answer in assessment.answers
            if answer['topic'] == topic
        )
        for topic in assessment_topics
        if any(answer['topic'] == topic for answer in assessment.answers)
    }
    prioritized_topics = sorted(
        assessment_topics,
        key=lambda topic: (topic_scores.get(topic, 1), random.random()),
    )
    practice_questions = []
    for topic in prioritized_topics:
        topic_questions = [
            question
            for question in available_questions
            if question['topic'] == topic
        ]
        random.shuffle(topic_questions)
        practice_questions.extend(topic_questions[:4 - len(practice_questions)])
        if len(practice_questions) >= 4:
            break
    if len(practice_questions) < 4:
        remaining = [
            question for question in available_questions
            if question not in practice_questions
        ]
        random.shuffle(remaining)
        practice_questions.extend(remaining[:4 - len(practice_questions)])
    if len(practice_questions) < 4:
        raise ValueError(
            'Fresh reviewed questions are temporarily unavailable for this '
            'subject. We will not repeat a question you have already seen.'
        )

    matching_pool = [
        question for question in available_questions
        if question['id'] not in {
            item['id'] for item in practice_questions
        }
    ]
    if len(matching_pool) < 6:
        raise ValueError(
            'Fresh reviewed questions are temporarily unavailable for this '
            'subject. We will not repeat a question you have already seen.'
        )
    matching_questions = random.sample(matching_pool, k=6)
    reserved_question_ids = (
        set(previously_seen_question_ids)
        | assessment_ids
        | {question['id'] for question in practice_questions}
        | {question['id'] for question in matching_questions}
    )
    steps = []
    now = timezone.now().isoformat()
    question_for_step = {
        'memory': 0,
        'focus': 1,
        'matching': 0,
        'balance': 2,
        'strategy': 3,
    }
    path_order = list(PATH_STEP_DETAILS)
    if learner is not None:
        recommendation = _path_recommendation(learner, assessment)
        if recommendation['recommended_step']:
            recommended_type = (
                COGNITIVE_PATH_STEPS
                if recommendation['activity_type'] == 'cognitive'
                else PEDAGOGICAL_PATH_STEPS
            )
            original_ids = [step_id for step_id, _ in PATH_STEP_DETAILS]
            remaining_ids = [
                step_id for step_id in original_ids
                if step_id not in recommended_type
            ]
            path_ids = [recommendation['recommended_step']]
            path_ids.extend(
                step_id for step_id in recommended_type
                if step_id != recommendation['recommended_step']
            )
            path_ids.extend(remaining_ids)
            labels = dict(PATH_STEP_DETAILS)
            path_order = [(step_id, labels[step_id]) for step_id in path_ids]
    cognitive_game_ids = [
        step_id for step_id, _ in path_order
        if step_id in COGNITIVE_GAME_BY_ID
    ]
    for index, (step_id, _) in enumerate(path_order):
        step = {
            'id': step_id,
            'passed': False,
            'score': None,
            'attempts': 0,
            'scores': [],
            'seconds': [],
            'feedback': [],
            'started_at': now if index == 0 else None,
        }
        if step_id in COGNITIVE_GAME_BY_ID:
            step['topic'] = COGNITIVE_GAME_BY_ID[step_id]['category']
            remaining_games = len(cognitive_game_ids) - cognitive_game_ids.index(step_id)
            remaining_questions = sum(
                question['id'] not in reserved_question_ids
                for question in BANK[assessment.subject]
            )
            trial_count = min(
                COGNITIVE_TRIAL_COUNT,
                remaining_questions // remaining_games,
            )
            if trial_count < 1:
                raise ValueError(
                    'Fresh reviewed questions are temporarily unavailable '
                    'for this subject. We will not repeat a question you '
                    'have already seen.'
                )
            step['cognitive_trials'] = _new_path_cognitive_challenges(
                step_id,
                assessment,
                reserved_question_ids,
                trial_count=trial_count,
            )
            reserved_question_ids.update(
                trial['domain_question_id']
                for trial in step['cognitive_trials']
            )
            step['trial_answers'] = []
            step['trial_seconds'] = []
            step['trial_started_at'] = now if index == 0 else None
        else:
            practice_question = (
                matching_questions[0]
                if step_id == 'matching'
                else practice_questions[question_for_step[step_id]]
            )
            matching_indexes = [
                question_index for question_index, question in enumerate(questions)
                if question['topic'] == practice_question['topic']
            ]
            question_index = random.choice(
                matching_indexes or list(range(len(questions)))
            )
            step['question_index'] = question_index
            step['practice_question'] = practice_question
        if step_id == 'memory':
            step['memory_order'] = random.sample(range(4), k=4)
        elif step_id == 'focus':
            choices = list(range(4))
            correct = practice_question['correct']
            cells = [correct] * 3 + [
                choice for choice in choices if choice != correct
                for _ in range(2)
            ]
            random.shuffle(cells)
            step['focus_cells'] = cells
        elif step_id == 'matching':
            step['match_question_ids'] = [
                question['id'] for question in matching_questions
            ]
            step['match_order'] = random.sample(range(6), k=6)
        steps.append(step)
    steps[0]['heba_exercise'] = _new_heba_exercise_state(assessment.subject)
    return steps


def _append_cognitive_path_steps(run):
    retired = [
        step for step in run.steps
        if step.get('id') in RETIRED_COGNITIVE_PATH_IDS
    ]
    changed = bool(retired)
    if retired:
        run.steps = [
            step for step in run.steps
            if step.get('id') not in RETIRED_COGNITIVE_PATH_IDS
        ]
        if run.steps:
            run.steps[0].setdefault('retired_cognitive_steps', []).extend(retired)
    revised = []
    for step in run.steps:
        if step.get('id') not in COGNITIVE_GAME_BY_ID:
            continue
        trials = step.get('cognitive_trials', [])
        if trials and all(trial.get('domain_question_id') for trial in trials):
            continue
        revised.append({
            'id': step['id'],
            'attempts': step.get('attempts', 0),
            'scores': step.get('scores', []),
            'score': step.get('score'),
            'passed': step.get('passed', False),
            'trials': trials,
            'trial_answers': step.get('trial_answers', []),
            'trial_seconds': step.get('trial_seconds', []),
            'seconds': step.get('seconds', []),
        })
        step.update({
            'topic': COGNITIVE_GAME_BY_ID[step['id']]['category'],
            'cognitive_trials': _new_path_cognitive_challenges(
                step['id'],
                run.assessment,
                _seen_cognitive_question_ids(run.learner, run.assessment.subject)
                | {
                    trial['domain_question_id']
                    for other_step in run.steps
                    if other_step is not step
                    for trial in other_step.get('cognitive_trials', [])
                    if trial.get('domain_question_id')
                }
                | {
                    trial['domain_question_id']
                    for trial in trials
                    if trial.get('domain_question_id')
                },
            ),
            'trial_answers': [],
            'trial_seconds': [],
            'trial_started_at': None,
            'passed': False,
            'score': None,
            'attempts': 0,
            'scores': [],
            'seconds': [],
            'feedback': [],
            'started_at': None,
        })
        changed = True
    if revised and run.steps:
        run.steps[0].setdefault('revised_cognitive_attempts', []).extend(revised)
    existing = {step['id'] for step in run.steps}
    missing = [
        game_id for game_id in COGNITIVE_GAME_BY_ID
        if game_id not in existing
    ]
    if not missing and not changed:
        return
    now = timezone.now().isoformat()
    for game_id in missing:
        run.steps.append({
            'id': game_id,
            'topic': COGNITIVE_GAME_BY_ID[game_id]['category'],
            'cognitive_trials': _new_path_cognitive_challenges(
                game_id,
                run.assessment,
                _seen_cognitive_question_ids(run.learner, run.assessment.subject)
                | {
                    trial['domain_question_id']
                    for step in run.steps
                    for trial in step.get('cognitive_trials', [])
                    if trial.get('domain_question_id')
                },
            ),
            'trial_answers': [],
            'trial_seconds': [],
            'trial_started_at': None,
            'passed': False,
            'score': None,
            'attempts': 0,
            'scores': [],
            'seconds': [],
            'feedback': [],
            'started_at': now if not any(
                not step.get('passed') for step in run.steps
            ) else None,
        })
    run.save(update_fields=['steps', 'updated_at'])


def _path_final_result(steps):
    if not steps or not all(step.get('passed') for step in steps):
        return None
    scores = [step['score'] for step in steps]
    attempt_times = [
        seconds
        for step in steps
        for seconds in step.get('seconds', [])
    ]
    return {
        'score': round(mean(scores)),
        'total_seconds': (
            sum(attempt_times)
            if all(type(seconds) is int for seconds in attempt_times)
            else None
        ),
        'attempts': sum(step.get('attempts', 0) for step in steps),
    }


def _path_payload(run, language='en'):
    _append_cognitive_path_steps(run)
    questions = run.assessment.questions or BANK[run.assessment.subject][:6]
    changed = False
    for step in run.steps:
        if 'scores' not in step:
            step['scores'] = [step['score']] if step['score'] is not None else []
            changed = True
        if 'seconds' not in step:
            step['seconds'] = [None] * step['attempts']
            changed = True
        if 'feedback' not in step:
            step['feedback'] = []
            changed = True
    active = next((step for step in run.steps if not step['passed']), None)
    if (
        active
        and active.get('question_exhausted')
        and active['id'] in COGNITIVE_GAME_BY_ID
    ):
        excluded_question_ids = (
            _seen_cognitive_question_ids(run.learner, run.assessment.subject)
            | {
                question['id']
                for question in (
                    run.assessment.questions
                    or BANK[run.assessment.subject][:6]
                )
            }
            | set(active.get('previous_cognitive_question_ids', []))
            | {
                trial['domain_question_id']
                for trial in active.get('cognitive_trials', [])
                if trial.get('domain_question_id')
            }
            | {
                question['id']
                for step in run.steps
                if step is not active
                for question in [step.get('practice_question', {})]
                if question.get('id')
            }
            | {
                question_id
                for step in run.steps
                if step is not active
                for question_id in step.get('match_question_ids', [])
            }
            | {
                trial['domain_question_id']
                for step in run.steps
                if step is not active
                for trial in step.get('cognitive_trials', [])
                if trial.get('domain_question_id')
            }
        )
        try:
            active['cognitive_trials'] = _new_path_cognitive_challenges(
                active['id'],
                run.assessment,
                excluded_question_ids,
            )
        except ValueError:
            pass
        else:
            active['trial_answers'] = []
            active['trial_seconds'] = []
            active['trial_started_at'] = timezone.now().isoformat()
            active['started_at'] = active['trial_started_at']
            active.pop('question_exhausted', None)
            changed = True
    if active and not active.get('started_at'):
        active['started_at'] = timezone.now().isoformat()
        changed = True
    if changed:
        run.save(update_fields=['steps', 'updated_at'])
    progress = []
    challenge = None
    labels = dict(PATH_STEP_DETAILS)
    for step in run.steps:
        label = labels[step['id']]
        if step['id'] in COGNITIVE_GAME_BY_ID:
            topic = step.get('topic', COGNITIVE_GAME_BY_ID[step['id']]['category'])
            current_trial_index = len(step.get('trial_answers', []))
            if (
                step.get('cognitive_trials')
                and current_trial_index < len(step['cognitive_trials'])
                and step['cognitive_trials'][current_trial_index].get(
                    'domain_question_id'
                )
            ):
                domain_question = next(
                    question
                    for question in BANK[run.assessment.subject]
                    if question['id']
                    == step['cognitive_trials'][current_trial_index][
                        'domain_question_id'
                    ]
                )
                topic = localize_topic(domain_question['topic'], language)
        else:
            question = localize_question(
                step.get('practice_question', questions[step['question_index']]),
                language,
            )
            topic = question['topic']
        progress.append({
            'id': step['id'],
            'label': label,
            'topic': topic,
            'score': step['score'],
            'attempts': step['attempts'],
            'scores': step.get('scores', []),
            'seconds': step.get('seconds', []),
            'feedback': step.get('feedback', []),
            'passed': step['passed'],
            'unlocked': active is not None and step['id'] == active['id'],
        })
    if active:
        started_at = datetime.fromisoformat(active['started_at'])
        if active['id'] in COGNITIVE_GAME_BY_ID:
            if active.get('question_exhausted'):
                challenge = {
                    'id': active['id'],
                    'game_id': active['id'],
                    'topic': COGNITIVE_GAME_BY_ID[active['id']]['category'],
                    'title': COGNITIVE_GAME_BY_ID[active['id']]['title'],
                    'content_exhausted': True,
                }
            else:
                trial_index = len(active.get('trial_answers', []))
                trial = active['cognitive_trials'][trial_index]
                domain_question = None
                if trial.get('domain_question_id'):
                    domain_question = next(
                        question
                        for question in BANK[run.assessment.subject]
                        if question['id'] == trial['domain_question_id']
                    )
                    domain_question = localize_question(domain_question, language)
                presentation = trial.get('presentation')
                if (
                    presentation
                    and presentation.get('type') == 'domain_question'
                ):
                    presentation = {
                        **presentation,
                        'topic': localize_topic(presentation['topic'], language),
                    }
                challenge = {
                    'id': active['id'],
                    'game_id': active['id'],
                    'topic': (
                        domain_question['topic']
                        if domain_question
                        else COGNITIVE_GAME_BY_ID[active['id']]['category']
                    ),
                    'title': COGNITIVE_GAME_BY_ID[active['id']]['title'],
                    'prompt': (
                        domain_question['prompt']
                        if domain_question
                        else trial['prompt']
                    ),
                    'stimulus': trial['stimulus'],
                    'presentation': presentation,
                    'domain_context': trial.get('domain_context'),
                    'options': (
                        trial['presentation']['letter_options']
                        if (
                            active['id'] == 'operation_span'
                            and trial.get('presentation', {}).get('type')
                            == 'operation_span'
                        )
                        else [
                            domain_question['choices'][index]
                            for index in trial['domain_option_order']
                        ]
                        if domain_question
                        else trial['options']
                    ),
                    'trial_index': trial_index,
                    'trial_total': len(active['cognitive_trials']),
                    'elapsed_seconds': max(
                        0,
                        round((timezone.now() - started_at).total_seconds()),
                    ),
                }
        else:
            question = active.get(
                'practice_question',
                questions[active['question_index']],
            )
            question = localize_question(question, language)
            challenge = {
                'id': active['id'],
                'topic': question['topic'],
                'prompt': (
                    {
                        'en': 'Study the cards in order, then rebuild the sequence from memory.',
                        'fr': 'Étudiez les cartes dans l’ordre, puis reconstituez la séquence de mémoire.',
                        'ar': 'ادرس البطاقات بالترتيب، ثم أعد بناء التسلسل من الذاكرة.',
                    }.get(language, 'Study the cards in order, then rebuild the sequence from memory.')
                    if active['id'] == 'memory'
                    else question['prompt']
                ),
                'elapsed_seconds': max(
                    0,
                    round((timezone.now() - started_at).total_seconds()),
                ),
            }
        if active['id'] == 'memory':
            challenge['cards'] = [
                question['choices'][choice]
                for choice in active['memory_order']
            ]
            challenge['options'] = question['choices']
        elif active['id'] == 'focus':
            challenge['cells'] = [
                question['choices'][choice]
                for choice in active['focus_cells']
            ]
        elif active['id'] == 'matching':
            match_questions = [
                localize_question(
                    next(
                        question
                        for question in BANK[run.assessment.subject]
                        if question['id'] == question_id
                    ),
                    language,
                )
                for question_id in active['match_question_ids']
            ]
            challenge['left_cards'] = [
                item['choices'][item['correct']]
                for item in match_questions
            ]
            challenge['right_cards'] = [
                match_questions[index]['explanation']
                for index in active['match_order']
            ]
        elif active['id'] == 'balance':
            challenge['blocks'] = question['choices']
        else:
            challenge['moves'] = question['choices']
    assessment_summary = serialize(run.assessment, language)
    baseline = {
        key: assessment_summary[key]
        for key in (
            'score', 'accuracy', 'calibration', 'independence', 'avg_time',
            'topics', 'level', 'date',
        )
    } if run.assessment.completed_at else None
    return {
        'assessment_id': run.assessment_id,
        'pass_score': PATH_PASS_SCORE,
        'steps': progress,
        'active_step': active['id'] if active else None,
        'complete': active is None,
        'final_result': _path_final_result(run.steps),
        'challenge': challenge,
        'cognitive_catalog': list(COGNITIVE_GAMES),
        'heba_exercise': _heba_exercise_payload(run),
        'cognitive_games': _cognitive_games_payload(run),
        'baseline': baseline,
        'collaboration': _collaboration_payload(run.learner, run.assessment),
        'recommendation': _path_recommendation(run.learner, run.assessment),
    }


@login_required
@require_POST
def learning_path(request):
    l = learner(request)
    data = payload(request)
    latest = l.assessment_set.filter(
        completed_at__isnull=False,
    ).order_by('-completed_at', '-id').first()
    if not latest:
        return JsonResponse(
            {'error': 'Complete an assessment before starting your learning path.'},
            status=400,
        )
    try:
        with transaction.atomic():
            Learner.objects.select_for_update().get(pk=l.pk)
            run = LearningPathRun.objects.select_for_update().filter(
                learner=l,
                assessment=latest,
            ).first()
            if run is None:
                run = LearningPathRun.objects.create(
                    learner=l,
                    assessment=latest,
                    steps=_create_path_steps(
                        latest,
                        learner=l,
                        previously_seen_question_ids=(
                            _seen_cognitive_question_ids(l, latest.subject)
                        ),
                    ),
                )
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    if not data:
        try:
            _maybe_generate_heba_exercises(
                run,
                l,
                l.preferences.get('language', 'en'),
            )
        except CoachProviderError as exc:
            return JsonResponse({'error': str(exc)}, status=503)
        with transaction.atomic():
            run = LearningPathRun.objects.select_for_update().get(pk=run.pk)
            result = _path_payload(run, l.preferences.get('language', 'en'))
    else:
        with transaction.atomic():
            run = LearningPathRun.objects.select_for_update().get(pk=run.pk)
            _append_cognitive_path_steps(run)
            if data.get('step') == 'heba_exercise':
                result = _score_heba_exercise_attempt(
                    run,
                    data,
                    l.preferences.get('language', 'en'),
                )
            elif data.get('step') == 'cognitive_game':
                result = (
                    _start_cognitive_game(run, data)
                    if data.get('action') == 'start'
                    else _score_cognitive_game(
                        run,
                        data,
                        l.preferences.get('language', 'en'),
                    )
                )
            else:
                result = _score_learning_path_attempt(
                    l,
                    latest,
                    run,
                    data,
                    l.preferences.get('language', 'en'),
                )
            if isinstance(result, JsonResponse):
                return result
    if result['complete']:
        if l.preferences.get('memory'):
            try:
                index_learning_path(l, run, result['final_result'])
            except MemoryStoreUnavailable as exc:
                result['memory_status'] = 'unavailable'
                result['memory_error'] = str(exc)
            else:
                result['memory_status'] = 'saved'
        else:
            result['memory_status'] = 'disabled'
    return JsonResponse(result)


def _score_heba_exercise_attempt(run, data, language='en'):
    state = _ensure_heba_exercise_state(run)
    if state is None or state['completed']:
        return JsonResponse(
            {'error': 'This exercise set is already complete. Start a new challenge for another set.'},
            status=409,
        )
    index = data.get('index')
    if type(index) is not int or index != state['index']:
        return JsonResponse(
            {'error': 'This exercise was already submitted. Refresh to continue.'},
            status=409,
        )
    question = state['questions'][index]
    answer = data.get('answer')
    if question['exercise_type'] == 'memory':
        if (
            not isinstance(answer, list)
            or len(answer) != len(question['choices'])
            or any(
                type(option) is not int or option not in range(len(question['choices']))
                for option in answer
            )
            or len(set(answer)) != len(answer)
        ):
            return JsonResponse(
                {'error': 'Rebuild the sequence using each card exactly once.'},
                status=400,
            )
        submitted = [question['choices'][option] for option in answer]
        correct_answer = question['sequence']
        correct = submitted == correct_answer
    else:
        if (
            type(answer) is not int
            or answer not in range(len(question['choices']))
        ):
            return JsonResponse(
                {'error': 'Choose one answer to submit this exercise.'},
                status=400,
            )
        submitted = question['choices'][answer]
        correct_answer = question['choices'][question['correct']]
        correct = answer == question['correct']

    elapsed_seconds = max(
        0,
        round((
            timezone.now() - datetime.fromisoformat(state['started_at'])
        ).total_seconds()),
    )
    state['answers'].append({
        'correct': correct,
        'seconds': elapsed_seconds,
        'topic': question['topic'],
        'answer': submitted,
        'expected': correct_answer,
        'explanation': question['explanation'],
    })
    state['index'] += 1
    state['started_at'] = timezone.now().isoformat()
    if state['index'] == len(state['questions']):
        state['completed'] = True
        state['score'] = round(
            100 * sum(item['correct'] for item in state['answers'])
            / len(state['questions'])
        )
        state['passed'] = state['score'] >= PATH_PASS_SCORE
    run.save(update_fields=['steps', 'updated_at'])

    result = _path_payload(run, language)
    result['exercise_result'] = {
        'correct': correct,
        'correct_answer': correct_answer,
        'explanation': question['explanation'],
        'seconds': elapsed_seconds,
        'score': state['score'] if state['completed'] else round(
            100 * sum(item['correct'] for item in state['answers'])
            / len(state['answers'])
        ),
        'completed': state['completed'],
        'passed': state['passed'] if state['completed'] else None,
    }
    return result


def _cognitive_trial_feedback(trial, subject, correct, language):
    feedback = {'correct': correct}
    if correct:
        return feedback
    feedback.update({
        'question_prompt': trial.get('prompt', ''),
        'question_id': trial.get('domain_question_id'),
    })
    if trial.get('domain_question_id'):
        question = next(
            question
            for question in BANK[subject]
            if question['id'] == trial['domain_question_id']
        )
        question = localize_question(question, language)
        answer_index = trial['domain_option_order'][trial['expected_index']]
        feedback.update({
            'correct_answer': question['choices'][answer_index],
            'explanation': question['explanation'],
        })
    else:
        feedback.update({
            'correct_answer': trial.get('expected', ''),
            'explanation': trial.get('explanation', ''),
        })
    return feedback


def _score_cognitive_path_attempt(l, assessment, run, active, data, language):
    answer = data.get('answer')
    trial_index = len(active.get('trial_answers', []))
    trials = active.get('cognitive_trials', [])
    if not trials or trial_index >= len(trials):
        return JsonResponse({'error': 'This game trial is no longer available.'}, status=409)

    trial = trials[trial_index]
    trial_score = 0
    if (
        active['id'] == 'operation_span'
        and trial.get('presentation', {}).get('type') == 'operation_span'
    ):
        operation_data = trial.get('operation_span_data', {})
        if (
            not isinstance(answer, dict)
            or set(answer) != {'math_answers', 'recalled_letters'}
            or not isinstance(answer['math_answers'], list)
            or not isinstance(answer['recalled_letters'], list)
            or not operation_data.get('equation_checks')
            or not operation_data.get('letters')
            or len(answer['math_answers']) != len(operation_data.get('equation_checks', []))
            or len(answer['recalled_letters']) != len(operation_data.get('letters', []))
            or any(type(value) is not bool for value in answer['math_answers'])
            or any(
                not isinstance(letter, str)
                or letter not in trial['presentation']['letter_options']
                for letter in answer['recalled_letters']
            )
        ):
            return JsonResponse(
                {'error': 'Complete the equation checks and recall the letter sequence.'},
                status=400,
            )
        math_accuracy = sum(
            actual == expected
            for actual, expected in zip(
                answer['math_answers'],
                operation_data['equation_checks'],
            )
        ) / len(operation_data['equation_checks'])
        recall_accuracy = sum(
            actual == expected
            for actual, expected in zip(
                answer['recalled_letters'],
                operation_data['letters'],
            )
        ) / len(operation_data['letters'])
        trial_score = round(100 * (math_accuracy + recall_accuracy) / 2)
        correct = trial_score >= 70
    elif active['id'] == 'stop_signal' and trial['presentation']['is_stop']:
        if answer is not None and (
            type(answer) is not int
            or answer not in range(len(trial['options']))
        ):
            return JsonResponse({'error': 'Choose a displayed direction or withhold.'}, status=400)
        correct = answer is None
        trial_score = 100 if correct else 0
    elif active['id'] == 'stop_signal':
        if answer is None:
            correct = False
        elif (
            type(answer) is not int
            or answer not in range(len(trial['options']))
        ):
            return JsonResponse({'error': 'Choose one of the displayed directions.'}, status=400)
        else:
            correct = answer == trial['expected_index']
        trial_score = 100 if correct else 0
    else:
        if (
            type(answer) is not int
            or answer not in range(len(trial['options']))
        ):
            return JsonResponse(
                {'error': 'Choose one of the displayed answers.'},
                status=400,
            )
        correct = answer == trial['expected_index']
        trial_score = 100 if correct else 0

    now = timezone.now()
    trial_started_at = datetime.fromisoformat(
        active.get('trial_started_at') or active['started_at']
    )
    elapsed = max(0, round((now - trial_started_at).total_seconds()))
    active.setdefault('trial_answers', []).append({
        'correct': correct,
        'score': trial_score,
    })
    active.setdefault('trial_seconds', []).append(elapsed)
    trial_result = {
        **_cognitive_trial_feedback(
            trial,
            assessment.subject,
            correct,
            language,
        ),
        'index': trial_index + 1,
        'total': len(trials),
    }
    if trial_index + 1 < len(trials):
        active['trial_started_at'] = now.isoformat()
        run.save(update_fields=['steps', 'updated_at'])
        result = _path_payload(run, language)
        result['trial_result'] = trial_result
        return result

    correct_trials = sum(item['correct'] for item in active['trial_answers'])
    score = round(mean(item['score'] for item in active['trial_answers']))
    elapsed_seconds = sum(active['trial_seconds'])
    active['attempts'] += 1
    active['score'] = score
    active.setdefault('scores', []).append(score)
    active.setdefault('seconds', []).append(elapsed_seconds)
    active['passed'] = score >= PATH_PASS_SCORE
    if active['passed']:
        next_step = next(
            (step for step in run.steps if not step['passed'] and step is not active),
            None,
        )
        if next_step:
            next_step['started_at'] = now.isoformat()
            if next_step['id'] in COGNITIVE_GAME_BY_ID:
                next_step['trial_started_at'] = now.isoformat()
    else:
        excluded_question_ids = (
            _seen_cognitive_question_ids(l, assessment.subject)
            | {
                question['id']
                for question in (
                    assessment.questions
                    or BANK[assessment.subject][:6]
                )
            }
            | {
                step['practice_question']['id']
                for step in run.steps
                if step.get('practice_question')
            }
            | {
                trial['domain_question_id']
                for step in run.steps
                for trial in step.get('cognitive_trials', [])
                if trial.get('domain_question_id')
            }
            | {
                trial['domain_question_id']
                for trial in active.get('cognitive_trials', [])
                if trial.get('domain_question_id')
            }
            | set(active.get('previous_cognitive_question_ids', []))
        )
        try:
            active['cognitive_trials'] = _new_path_cognitive_challenges(
                active['id'],
                assessment,
                excluded_question_ids,
            )
        except ValueError:
            active['question_exhausted'] = True
        else:
            active.setdefault(
                'previous_cognitive_question_ids',
                [],
            ).extend(
                trial['domain_question_id']
                for trial in trials
                if trial.get('domain_question_id')
            )
            active['trial_answers'] = []
            active['trial_seconds'] = []
            active['trial_started_at'] = now.isoformat()
            active['started_at'] = now.isoformat()

    game = COGNITIVE_GAME_BY_ID[active['id']]
    guidance = {
        'en': f"Review the selected {assessment.subject} concept and its explanation, then apply it to a new question.",
        'fr': f"Révisez le concept de {assessment.subject} et son explication, puis appliquez-le à une nouvelle question.",
        'ar': f"راجع المفهوم في {assessment.subject} واقرأ شرحه، ثم طبّقه على سؤال جديد.",
    }.get(language, f"Review the selected {assessment.subject} concept and its explanation, then apply it to a new question.")
    prompt = (
        f"Give brief, encouraging coaching feedback after a set of "
        f"{assessment.subject} knowledge questions in the {game['title']} activity. "
        f"The learner scored {score}% "
        f"across {len(trials)} trials in {elapsed_seconds} seconds. "
        f"They {'passed' if active['passed'] else 'did not pass'} the "
        f"{PATH_PASS_SCORE}% threshold on attempt {active['attempts']}. "
        f"Offer one concrete next step: {guidance} Do not judge ability."
    )
    result = _path_payload(run, language)
    result['result'] = {
        'step': active['id'],
        'score': score,
        'seconds': elapsed_seconds,
        'passed': active['passed'],
        'attempts': active['attempts'],
        'correct_trials': correct_trials,
        'total_trials': len(trials),
    }
    result['trial_result'] = trial_result
    if l.preferences.get('ai') and ai_provider_configured():
        try:
            result['coach'] = _ai_line(prompt, {
                'response_language': language,
                'subject': localize_subject(assessment.subject, language),
                'experience': l.profile.get('experience'),
                'challenge': active['id'],
                'topic': game['category'],
                'score': score,
                'elapsed_seconds': elapsed_seconds,
                'passed': active['passed'],
                'attempt': active['attempts'],
            })
            result['coach_source'] = 'AI coach'
        except CoachProviderError as exc:
            result['coach_error'] = str(exc)
    if not result.get('coach'):
        if language == 'fr':
            outcome = (
                'Défi réussi : le suivant est débloqué.'
                if active['passed']
                else f'Il vous faut {PATH_PASS_SCORE} % pour débloquer le défi suivant ; vous pouvez réessayer.'
            )
            result['coach'] = f'Vous avez obtenu {score} % en {elapsed_seconds} secondes. {outcome} {guidance}'
        elif language == 'ar':
            outcome = (
                'اجتزت التحدي وفتحت التحدي التالي.'
                if active['passed']
                else f'تحتاج إلى {PATH_PASS_SCORE}٪ لفتح التحدي التالي؛ ويمكنك المحاولة مجدداً.'
            )
            result['coach'] = f'حققت {score}٪ خلال {elapsed_seconds} ثانية. {outcome} {guidance}'
        else:
            outcome = (
                'You passed and unlocked the next challenge.'
                if active['passed']
                else f'You need {PATH_PASS_SCORE}% to unlock the next challenge; you can retry.'
            )
            result['coach'] = (
                f'You scored {score}% in {elapsed_seconds} seconds. {outcome} {guidance}'
            )
        result['coach_source'] = 'Local coach'
    active.setdefault('feedback', []).append({
        'source': result['coach_source'],
        'text': result['coach'],
        'error': result.get('coach_error'),
    })
    run.save(update_fields=['steps', 'updated_at'])
    return result


def _score_learning_path_attempt(l, assessment, run, data, language='en'):
    step_id = data.get('step')
    active = next((step for step in run.steps if not step['passed']), None)
    if not active or step_id != active['id']:
        return JsonResponse(
            {'error': 'Finish the current activity with a passing score first.'},
            status=409,
        )
    if step_id in COGNITIVE_GAME_BY_ID:
        return _score_cognitive_path_attempt(
            l,
            assessment,
            run,
            active,
            data,
            language,
        )

    answers = data.get('answers')
    if not isinstance(answers, list):
        return JsonResponse({'error': 'Submit your completed activity answers.'}, status=400)
    matching_total = len(active.get('match_question_ids', []))
    assessment_questions = assessment.questions or BANK[assessment.subject][:6]
    question = active.get(
        'practice_question',
        assessment_questions[active['question_index']],
    )
    if step_id == 'memory':
        if (
            len(answers) != 4
            or any(type(choice) is not int or choice not in range(4) for choice in answers)
            or len(set(answers)) != 4
        ):
            return JsonResponse({'error': 'Put each memory card in sequence.'}, status=400)
        expected_positions = {
            choice: position
            for position, choice in enumerate(active['memory_order'])
        }
        correctly_ordered_pairs = sum(
            expected_positions[first] < expected_positions[second]
            for position, first in enumerate(answers)
            for second in answers[position + 1:]
        )
        total_pairs = len(answers) * (len(answers) - 1) // 2
        score = round(100 * correctly_ordered_pairs / total_pairs)
        match_count = None
        focus_found = focus_distractors = None
    elif step_id == 'focus':
        if (
            any(type(cell) is not int or cell not in range(9) for cell in answers)
            or len(set(answers)) != len(answers)
        ):
            return JsonResponse({'error': 'Select each focus-grid square only once.'}, status=400)
        targets = set(
            index for index, choice in enumerate(active['focus_cells'])
            if choice == question['correct']
        )
        selected = set(answers)
        focus_found = len(selected & targets)
        focus_distractors = len(selected - targets)
        precision = len(selected & targets) / len(selected) if selected else 0
        recall = len(selected & targets) / len(targets)
        score = round(100 * (
            2 * precision * recall / (precision + recall)
            if precision + recall else 0
        ))
        match_count = None
    elif step_id == 'matching':
        pair_count = len(active['match_question_ids'])
        required_pairs = (pair_count * PATH_PASS_SCORE + 99) // 100
        if (
            not required_pairs <= len(answers) <= pair_count
            or any(
                not isinstance(pair, list)
                or len(pair) != 2
                or any(type(index) is not int or index not in range(pair_count) for index in pair)
                for pair in answers
            )
            or len({pair[0] for pair in answers}) != len(answers)
            or len({pair[1] for pair in answers}) != len(answers)
        ):
            return JsonResponse(
                {'error': f'Match at least {required_pairs} concepts to distinct explanations before submitting.'},
                status=400,
            )
        expected_matches = {
            left_index: active['match_order'].index(left_index)
            for left_index in range(pair_count)
        }
        match_count = sum(
            expected_matches[left_index] == right_index
            for left_index, right_index in answers
        )
        score = round(100 * match_count / pair_count)
        focus_found = focus_distractors = None
    else:
        if (
            len(answers) != 1
            or type(answers[0]) is not int
            or answers[0] not in range(4)
        ):
            return JsonResponse({'error': 'Choose one answer to finish this activity.'}, status=400)
        score = 100 if answers[0] == question['correct'] else 0
        match_count = focus_found = focus_distractors = None

    now = timezone.now()
    started_at = datetime.fromisoformat(active['started_at'])
    elapsed_seconds = max(0, round((now - started_at).total_seconds()))
    active['attempts'] += 1
    active['score'] = score
    active.setdefault('scores', []).append(score)
    active.setdefault('seconds', []).append(elapsed_seconds)
    active['passed'] = score >= PATH_PASS_SCORE
    if active['passed']:
        next_step = next(
            (step for step in run.steps if not step['passed'] and step is not active),
            None,
        )
        if next_step:
            next_step['started_at'] = now.isoformat()
    else:
        active['started_at'] = now.isoformat()
    run.save(update_fields=['steps', 'updated_at'])
    result = _path_payload(run, language)
    result['result'] = {
        'step': step_id,
        'score': score,
        'seconds': elapsed_seconds,
        'passed': active['passed'],
        'attempts': active['attempts'],
    }
    if step_id == 'matching':
        result['result']['correct_matches'] = match_count
        result['result']['total_matches'] = len(active['match_question_ids'])
    decision_count = {
        'memory': 6,
        'focus': 9,
        'matching': 6,
        'balance': 1,
        'strategy': 1,
    }[step_id]
    guidance = {
        'memory': (
            "Say each card name quietly and group the sequence into two pairs before ordering it.",
            "Nommez doucement chaque carte et regroupez la séquence en deux paires avant de la remettre en ordre.",
            "ردّد أسماء البطاقات بهدوء واجمعها في زوجين قبل ترتيبها.",
        ),
        'focus': (
            f"You found {focus_found} of 3 matching squares and selected {focus_distractors} distractors. Scan one row at a time and compare every square to the target.",
            f"Vous avez trouvé {focus_found} des 3 cases correspondantes et sélectionné {focus_distractors} distracteur(s). Parcourez une ligne à la fois et comparez chaque case à la cible.",
            f"وجدت {focus_found} من 3 مربعات مطابقة واخترت {focus_distractors} مربعات مشتتة. افحص صفاً واحداً كل مرة وقارن كل مربع بالهدف.",
        ),
        'matching': (
            f"You matched {match_count} of {matching_total} concepts. Look for the key idea in each explanation, then connect it to the concept in your own words.",
            f"Vous avez associé {match_count} concepts sur {matching_total}. Repérez l’idée principale de chaque explication, puis reliez-la au concept avec vos propres mots.",
            f"طابقت {match_count} من {matching_total} مفاهيم. ابحث عن الفكرة الأساسية في كل شرح ثم اربطها بالمفهوم بكلماتك.",
        ),
        'balance': (
            "Identify the main constraint in the question, then eliminate choices that do not address it.",
            "Repérez la contrainte principale de la question, puis éliminez les choix qui n’y répondent pas.",
            "حدّد القيد الأساسي في السؤال، ثم استبعد الخيارات التي لا تعالجه.",
        ),
        'strategy': (
            "Compare each move with the goal in the question. Eliminate moves that do not advance that goal.",
            "Comparez chaque action à l’objectif de la question. Éliminez celles qui ne vous en rapprochent pas.",
            "قارن كل خطوة بالهدف في السؤال، واستبعد الخطوات التي لا تقرّبك منه.",
        ),
    }[step_id]
    language_index = {'en': 0, 'fr': 1, 'ar': 2}.get(language, 0)
    next_guidance = guidance[language_index]
    prompt = (
        f"Give brief, encouraging coaching feedback after a {step_id} "
        f"practice challenge in {assessment.subject}. The learner scored "
        f"{score}% in {elapsed_seconds} seconds on {question['topic']}. "
        f"They {'passed' if active['passed'] else 'did not pass'} the "
        f"{PATH_PASS_SCORE}% threshold on attempt {active['attempts']}. "
        f"The challenge involved {decision_count} scored decisions. "
        f"Specific observation and practice guidance: {next_guidance} "
        "Comment on this one practice attempt without judging ability. "
        "Suggest one concrete next step. Do not reveal or infer the answer if "
        "the learner did not pass."
    )
    if l.preferences.get('ai') and ai_provider_configured():
        try:
            result['coach'] = _ai_line(prompt, {
                'response_language': language,
                'subject': localize_subject(assessment.subject, language),
                'experience': l.profile.get('experience'),
                'challenge': step_id,
                'topic': localize_topic(question['topic'], language),
                'score': score,
                'elapsed_seconds': elapsed_seconds,
                'passed': active['passed'],
                'attempt': active['attempts'],
            })
            result['coach_source'] = 'AI coach'
        except CoachProviderError as exc:
            result['coach_error'] = str(exc)
    if not result.get('coach'):
        topic = localize_topic(question['topic'], language)
        if language == 'fr':
            pace = (
                "Prenez votre temps ; la vitesse ne compte pas dans le score."
                if elapsed_seconds >= 60
                else "Votre durée est notée à titre indicatif, sans juger vos capacités."
            )
            outcome = (
                "Défi réussi : le suivant est débloqué."
                if active['passed']
                else f"Il vous faut {PATH_PASS_SCORE} % pour débloquer le défi suivant ; vous pouvez réessayer."
            )
            result['coach'] = (
                f"Vous avez obtenu {score} % en {elapsed_seconds} secondes sur {topic}. "
                f"{outcome} {pace} {next_guidance}"
            )
        elif language == 'ar':
            pace = (
                "خذ وقتك؛ فالسرعة لا تدخل في النتيجة."
                if elapsed_seconds >= 60
                else "سُجّل وقتك للسياق فقط، ولا يُستخدم للحكم على قدراتك."
            )
            outcome = (
                "اجتزت التحدي وفتحت التحدي التالي."
                if active['passed']
                else f"تحتاج إلى {PATH_PASS_SCORE}٪ لفتح التحدي التالي؛ ويمكنك المحاولة مجدداً."
            )
            result['coach'] = (
                f"حققت {score}٪ خلال {elapsed_seconds} ثانية في {topic}. "
                f"{outcome} {pace} {next_guidance}"
            )
        else:
            pace = (
                "Take your time; speed is not part of the score."
                if elapsed_seconds >= 60
                else "Your completion time is recorded for context, not used to judge ability."
            )
            outcome = (
                "You passed and unlocked the next challenge."
                if active['passed']
                else f"You need {PATH_PASS_SCORE}% to unlock the next challenge; you can retry."
            )
            result['coach'] = (
                f"You scored {score}% in {elapsed_seconds} seconds on {topic}. "
                f"{outcome} {pace} {next_guidance}"
            )
        result.setdefault('coach_source', 'Local coach')
    active.setdefault('feedback', []).append({
        'source': result['coach_source'],
        'text': result['coach'],
        'error': result.get('coach_error'),
    })
    run.save(update_fields=['steps', 'updated_at'])
    if active['passed'] and step_id != 'matching':
        localized_question = localize_question(question, language)
        result['result'].update(
            expected=localized_question['choices'][question['correct']],
            explanation=localized_question['explanation'],
        )
    return result


@login_required
def export_data(request):
    l = learner(request)
    completed = l.assessment_set.filter(completed_at__isnull=False).order_by('completed_at')
    document = {
        'profile': l.profile,
        'preferences': l.preferences,
        'goal': l.goal,
        'history': [
            serialize(item, l.preferences.get('language', 'en'))
            for item in completed
        ],
        'learning_path': [
            {
                'assessment_id': run.assessment_id,
                'final_result': _path_final_result(run.steps),
                'steps': [
                    {
                        key: step[key]
                        for key in (
                            'id', 'passed', 'score', 'attempts', 'scores',
                            'seconds', 'feedback',
                        )
                    }
                    for step in _path_payload(
                        run,
                        l.preferences.get('language', 'en'),
                    )['steps']
                ],
            }
            for run in l.learningpathrun_set.order_by('assessment__completed_at')
        ],
    }
    response = HttpResponse(
        json.dumps(document, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8',
    )
    response['Content-Disposition'] = 'attachment; filename="brainmax-my-data.json"'
    return response


@login_required
@require_POST
def profile(request):
    data = payload(request)
    limits = {'name': 80, 'subject': 20, 'occupation': 60, 'motivation': 500, 'experience': 30, 'minutes': 10}
    cleaned = {k: str(data.get(k, '')).strip()[:v] for k, v in limits.items()}
    if not all(cleaned.values()) or cleaned['subject'] not in BANK or cleaned['occupation'] not in ['Student', 'Professional', 'Exploring a new field'] or cleaned['experience'] not in ['New to this', 'Some foundations', 'Comfortable', 'Advanced'] or cleaned['minutes'] not in ['15', '30', '60']:
        return JsonResponse({'error': 'Please complete all profile fields with valid choices.'}, status=400)
    l = learner(request)
    l.profile = cleaned
    l.save()
    if l.preferences.get('memory'):
        try:
            index_learner_context(l)
        except MemoryStoreUnavailable as exc:
            return JsonResponse({'error': str(exc)}, status=503)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def assessment(request):
    l = learner(request)
    if not l.profile:
        return JsonResponse({'error': 'Complete your profile first.'}, status=400)
    active = l.assessment_set.filter(completed_at__isnull=True).first()
    if active:
        return JsonResponse(serialize(active, l.preferences.get('language', 'en')))
    selected = select_questions(l, l.profile['subject'])
    a = Assessment.objects.create(
        learner=l,
        subject=l.profile['subject'],
        questions=selected,
        question_started_at=timezone.now(),
    )
    return JsonResponse(serialize(a, l.preferences.get('language', 'en')))


@login_required
@require_POST
def hint(request):
    l = learner(request)
    a = l.assessment_set.filter(completed_at__isnull=True).first()
    if not a:
        return JsonResponse({'error': 'No active assessment.'}, status=400)
    a.hint_used = True
    a.save(update_fields=['hint_used'])
    return JsonResponse(serialize(a, l.preferences.get('language', 'en')))


@login_required
@require_POST
def answer(request):
    data = payload(request)
    choice, confidence = data.get('choice'), data.get('confidence')
    if type(choice) is not int or choice not in range(4) or type(confidence) is not int or confidence not in [25, 50, 75, 100]:
        return JsonResponse({'error': 'Select an answer and confidence level.'}, status=400)
    l = learner(request)
    assessment_id = data.get('assessment_id')
    with transaction.atomic():
        assessments = l.assessment_set.select_for_update()
        if type(assessment_id) is int:
            a = assessments.filter(pk=assessment_id).first()
        else:
            a = assessments.filter(completed_at__isnull=True).first()
        index = data.get('index')
        if not a or type(index) is not int or index < 0:
            return JsonResponse({'error': 'This assessment is no longer available. Refresh to resume.'}, status=409)
        if index < len(a.answers):
            return JsonResponse(serialize(a, l.preferences.get('language', 'en')))
        if a.completed_at or index != len(a.answers):
            return JsonResponse({'error': 'Your saved progress has changed. Refresh to resume.'}, status=409)
        items = a.questions or BANK[a.subject][:6]
        if index >= len(items):
            return JsonResponse({'error': 'This assessment is complete. Refresh to review your results.'}, status=409)
        q = items[index]
        now = timezone.now()
        a.answers = a.answers + [dict(choice=choice, confidence=confidence, correct=choice == q['correct'], hint=a.hint_used, seconds=round((now - a.question_started_at).total_seconds(), 2), topic=q['topic'])]
        a.hint_used = False
        a.question_started_at = now
        if len(a.answers) == len(items):
            a.completed_at = now
        a.save()
        return JsonResponse(serialize(a, l.preferences.get('language', 'en')))


@login_required
@require_POST
def definition(request):
    data = payload(request)
    assessment_id, index = data.get('assessment_id'), data.get('index')
    if type(assessment_id) is not int or type(index) is not int or index < 0:
        return JsonResponse({'error': 'Choose a completed answer to explain.'}, status=400)
    l = learner(request)
    a = l.assessment_set.filter(
        id=assessment_id, completed_at__isnull=False
    ).first()
    if not a:
        return JsonResponse({'error': 'Complete the assessment before requesting an explanation.'}, status=400)
    items = a.questions or BANK[a.subject][:6]
    if index >= len(a.answers) or index >= len(items):
        return JsonResponse({'error': 'That answer is not available.'}, status=400)
    original_question = items[index]
    question = localize_question(
        original_question,
        l.preferences.get('language', 'en'),
    )
    answer = a.answers[index]
    concept = question['choices'][answer['choice']]
    level = learner(request).profile.get('experience', 'New to this')
    definition_text = define_choice(
        original_question['choices'][answer['choice']],
        original_question['topic'],
        SUBJECTS[a.subject],
        level,
    )
    return JsonResponse({
        'concept': concept,
        'definition': definition_text,
        'level': level,
    })


@login_required
@require_POST
def reset(request):
    l = learner(request)
    if l.preferences.get('memory'):
        try:
            forget_history(l)
        except MemoryStoreUnavailable as exc:
            return JsonResponse({'error': str(exc)}, status=503)
    l.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def preferences(request):
    l = learner(request)
    data = payload(request)
    language = data.get('language', l.preferences.get('language', 'en'))
    if language not in ('en', 'fr', 'ar'):
        return JsonResponse({'error': 'Choose English, French, or Arabic.'}, status=400)
    memory_enabled = data.get('memory') is True
    if memory_enabled and not l.preferences.get('memory'):
        try:
            index_history(l, lambda item: serialize(item, language))
        except MemoryStoreUnavailable as exc:
            return JsonResponse({'error': str(exc)}, status=503)
    if l.preferences.get('memory') and not memory_enabled:
        try:
            forget_history(l)
        except MemoryStoreUnavailable as exc:
            return JsonResponse({'error': str(exc)}, status=503)
    collaboration_reflection = l.preferences.get('collaboration_reflection')
    allowed = ('ai', 'voice', 'growth', 'mood', 'memory')
    l.preferences = {key: data.get(key) is True for key in allowed}
    l.preferences['language'] = language
    if collaboration_reflection is not None:
        l.preferences['collaboration_reflection'] = collaboration_reflection
    l.save(update_fields=['preferences'])
    return JsonResponse({'preferences': l.preferences})


@login_required
@require_POST
def collaboration(request):
    l = learner(request)
    ratings = payload(request)
    if (
        set(ratings) != set(AI_COLLABORATION_WEIGHTS)
        or any(
            type(value) is not int or value not in range(101)
            for value in ratings.values()
        )
    ):
        return JsonResponse(
            {'error': 'Rate each collaboration dimension from 0 to 100.'},
            status=400,
        )
    l.preferences['collaboration_reflection'] = {
        'ratings': ratings,
        'updated_at': timezone.now().isoformat(),
    }
    l.save(update_fields=['preferences'])
    latest = l.assessment_set.filter(
        completed_at__isnull=False,
    ).order_by('-completed_at', '-id').first()
    return JsonResponse({
        'collaboration': _collaboration_payload(l, latest),
    })


@login_required
@require_POST
def goal(request):
    data = payload(request)
    target_score = data.get('target_score')
    weekly_sessions = data.get('weekly_sessions')
    target_date = data.get('target_date', '')
    if (
        type(target_score) is not int or not 50 <= target_score <= 100
        or type(weekly_sessions) is not int or not 1 <= weekly_sessions <= 7
        or not isinstance(target_date, str) or len(target_date) not in (0, 10)
    ):
        return JsonResponse({'error': 'Choose a score goal from 50–100 and 1–7 practice days.'}, status=400)
    if target_date:
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError:
            return JsonResponse({'error': 'Choose a valid target date.'}, status=400)
        if parsed_date < timezone.localdate():
            return JsonResponse({'error': 'Choose today or a future target date.'}, status=400)
    l = learner(request)
    l.goal = {
        'target_score': target_score,
        'weekly_sessions': weekly_sessions,
        'target_date': target_date,
    }
    l.save(update_fields=['goal'])
    if l.preferences.get('memory'):
        try:
            index_learner_context(l)
        except MemoryStoreUnavailable as exc:
            return JsonResponse({'error': str(exc)}, status=503)
    return JsonResponse({'goal': l.goal})


@login_required
@require_POST
def coach(request):
    data = payload(request)
    mode = data.get('mode')
    if mode not in ('learning', 'assessment', 'debrief'):
        return JsonResponse({'error': 'Choose a valid coaching mode.'}, status=400)
    message = data.get('message', '')
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500:
        return JsonResponse({'error': 'Enter a message up to 500 characters.'}, status=400)
    response_language = data.get('language')
    if response_language not in (None, '', 'en', 'tn'):
        return JsonResponse({'error': 'Choose English or Tunisian Arabic for coach responses.'}, status=400)
    mood = data.get('mood')
    if mood not in (None, '', 'confused', 'stressed', 'frustrated', 'tired', 'disengaged', 'confident'):
        return JsonResponse({'error': 'Choose a valid optional mood.'}, status=400)
    l = learner(request)
    if mood and not l.preferences.get('mood'):
        return JsonResponse({'error': 'Enable optional mood check-in in Settings first.'}, status=403)
    latest = l.assessment_set.order_by('-id').first()
    conversation = data.get('conversation', [])
    valid_conversation = isinstance(conversation, list) and len(conversation) <= 20
    if valid_conversation:
        total_characters = 0
        for item in conversation:
            if (
                not isinstance(item, dict)
                or item.get('role') not in ('user', 'assistant')
                or not isinstance(item.get('content'), str)
            ):
                valid_conversation = False
                break
            content_length = len(item['content'].strip())
            message_limit = 500 if item['role'] == 'user' else 4000
            if not 1 <= content_length <= message_limit:
                valid_conversation = False
                break
            total_characters += content_length
        if total_characters > 12000:
            valid_conversation = False
    if not valid_conversation:
        return JsonResponse({'error': 'The chat conversation is invalid. Clear it and try again.'}, status=400)
    try:
        result = response_for(
            l,
            latest,
            mode,
            message.strip(),
            mood or None,
            conversation=conversation,
            response_language=response_language or None,
        )
    except CoachProviderError as exc:
        return JsonResponse({'error': str(exc)}, status=502)
    except MemoryStoreUnavailable as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    return JsonResponse(result)
