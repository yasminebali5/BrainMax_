"""Reviewed prototype items. Difficulty is provisional, not calibrated."""
import hashlib
import itertools
import math
import random
from statistics import mean, median, pstdev, variance


PLATEAU_WINDOW = 5
PLATEAU_MIN_RELIABLE_ITEMS = 30
PLATEAU_MIN_DIFFICULTY_LEVELS = 2
PLATEAU_MIN_UNIQUE_ITEM_RATIO = 0.5
PLATEAU_SLOPE_EQUIVALENCE_BAND = 3.0
PLATEAU_MAX_SCORE_VARIANCE = 100.0
PLATEAU_COOLDOWN_SESSIONS = 3
PLATEAU_DIFFICULTY_LEVELS = {
    'Foundation': 1,
    'Building': 2,
    'Stretch': 3,
}

T_95_CRITICAL_VALUES = {
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
}
T_90_CRITICAL_VALUES = {
    3: 2.353,
    4: 2.132,
    5: 2.015,
    6: 1.943,
    7: 1.895,
    8: 1.860,
    9: 1.833,
    10: 1.812,
}

def q(topic, prompt, choices, correct, explanation, hint, difficulty='Foundation'):
    question_id = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return dict(id=question_id, topic=topic, prompt=prompt, choices=choices, correct=correct, explanation=explanation, hint=hint, difficulty=difficulty)


def _session_observation(assessment):
    items = assessment.questions or BANK[assessment.subject][:6]
    answers = assessment.answers
    reliable = [
        (index, answer)
        for index, answer in enumerate(answers)
        if answer.get('validity', 'reliable') == 'reliable'
    ]
    if not reliable:
        return None

    scores = [
        int(answer['correct'] and not answer.get('hint', False))
        for _, answer in reliable
    ]
    answer_topics = {}
    response_times = []
    reading_ratios = []
    active_times = []
    hinted = 0
    skipped = 0
    repeated_questions = []
    difficulty_counts = {}
    for index, answer in reliable:
        hinted += int(bool(answer.get('hint')))
        topic = answer.get('topic')
        if topic:
            answer_topics.setdefault(topic, []).append(
                int(answer['correct'] and not answer.get('hint', False))
            )
        if type(answer.get('seconds')) in (int, float) and answer['seconds'] > 0:
            response_times.append(float(answer['seconds']))

        question = items[index] if index < len(items) else None
        if question:
            repeated_questions.append(question.get('id'))
            level = PLATEAU_DIFFICULTY_LEVELS.get(
                question.get('difficulty', 'Foundation')
            )
            if level is not None:
                difficulty_counts[level] = difficulty_counts.get(level, 0) + 1
            words = question.get('prompt', '').split()
            reading_floor = len(words) / 4 + 0.5
            if type(answer.get('seconds')) in (int, float) and reading_floor:
                reading_ratios.append(answer['seconds'] / reading_floor)
            telemetry = answer.get('telemetry', {})
            if isinstance(telemetry, dict):
                active_ms = telemetry.get('active_ms')
                if type(active_ms) is int and active_ms > 0:
                    active_times.append(active_ms)
                    if (
                        active_ms < reading_floor * 1000
                        and not telemetry.get('stimulus_seen', False)
                    ):
                        skipped += 1

    question_difficulties = set(difficulty_counts)
    engagement = assessment.engagement or {}
    engagement_audit = (
        engagement.get('audit', [])
        if isinstance(engagement, dict) else []
    )
    if not isinstance(engagement_audit, list):
        engagement_audit = []
    rapid_flags = {
        flag
        for audit in engagement_audit
        if isinstance(audit, dict)
        for flag in audit.get('flags', [])
        if isinstance(audit.get('flags'), list)
    }
    return {
        'assessment_id': assessment.pk,
        'completed_at': assessment.completed_at,
        'score': 100 * mean(scores),
        'reliable_count': len(reliable),
        'difficulty_levels': question_difficulties,
        'difficulty_counts': difficulty_counts,
        'topics': answer_topics,
        'response_seconds': mean(response_times) if response_times else None,
        'reading_ratio': mean(reading_ratios) if reading_ratios else None,
        'active_ms': active_times,
        'hint_rate': hinted / len(reliable),
        'skip_rate': skipped / len(reliable),
        'rapid_flags': rapid_flags,
        'unique_question_count': len(set(repeated_questions)),
        'question_ids': repeated_questions,
    }


def _linear_slope(values):
    count = len(values)
    x_values = list(range(count))
    x_mean = mean(x_values)
    y_mean = mean(values)
    sum_x_squared = sum((x - x_mean) ** 2 for x in x_values)
    slope = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(x_values, values)
    ) / sum_x_squared
    return slope


def _linear_slope_and_ci(values):
    count = len(values)
    x_values = list(range(count))
    x_mean = mean(x_values)
    y_mean = mean(values)
    sum_x_squared = sum((x - x_mean) ** 2 for x in x_values)
    slope = _linear_slope(values)
    intercept = y_mean - slope * x_mean
    residual_sum_squares = sum(
        (y - (intercept + slope * x)) ** 2
        for x, y in zip(x_values, values)
    )
    degrees_freedom = count - 2
    standard_error = math.sqrt(
        residual_sum_squares / degrees_freedom / sum_x_squared
    )
    critical_value = T_95_CRITICAL_VALUES.get(degrees_freedom, 1.96)
    margin = critical_value * standard_error
    interval_95 = (slope - margin, slope + margin)
    interval_90_margin = (
        T_90_CRITICAL_VALUES.get(degrees_freedom, 1.645) * standard_error
    )
    interval_90 = (
        slope - interval_90_margin,
        slope + interval_90_margin,
    )
    return slope, interval_95, interval_90


def _assessment_history(learner, subject, through=None, limit=12):
    from .models import Assessment

    assessments = Assessment.objects.filter(
        learner=learner,
        subject=subject,
        completed_at__isnull=False,
        abandoned_at__isnull=True,
    )
    if through is not None:
        assessments = assessments.filter(
            completed_at__lte=through.completed_at,
        )
    return [
        observation
        for assessment in assessments.order_by('-completed_at', '-id')[:limit]
        if (observation := _session_observation(assessment)) is not None
    ][::-1]


def _intervention_review(learner, subject, sessions):
    from .models import LearningPathRun

    runs = LearningPathRun.objects.filter(
        learner=learner,
        assessment__subject=subject,
    ).order_by('-assessment__completed_at', '-assessment_id')[:12]
    session_indexes = {
        item['assessment_id']: index
        for index, item in enumerate(sessions)
    }
    for run in runs:
        audit = (
            run.steps[0].get('assignment_audit')
            if run.steps else None
        )
        intervention = (
            audit.get('plateau_intervention')
            if isinstance(audit, dict) else None
        )
        if not isinstance(intervention, dict):
            continue
        intervention_index = session_indexes.get(run.assessment_id)
        if intervention_index is None:
            continue

        after = sessions[intervention_index + 1:]
        review = {
            'assessment_id': run.assessment_id,
            'diagnosis': intervention.get('diagnosis'),
            'action': intervention.get('action'),
            'escalation_level': intervention.get('escalation_level', 0),
            'sessions_after_intervention': len(after),
            'cooldown_remaining': max(
                0,
                PLATEAU_COOLDOWN_SESSIONS - len(after),
            ),
        }
        if len(after) < PLATEAU_COOLDOWN_SESSIONS:
            review['status'] = 'cooldown'
            return review

        follow_up = after[:PLATEAU_COOLDOWN_SESSIONS]
        before = sessions[max(0, intervention_index - 2):intervention_index + 1]
        before_scores = [item['score'] for item in before]
        after_scores = [item['score'] for item in follow_up]
        score_change = round(mean(after_scores) - mean(before_scores), 1)
        review.update(
            status='reviewed',
            baseline_sessions=len(before_scores),
            follow_up_sessions=len(after_scores),
            scores_after=[round(score, 1) for score in after_scores],
            score_change=score_change,
            post_intervention_slope=round(_linear_slope(after_scores), 3),
            response_time_change_percent=None,
            active_time_change_percent=None,
            hint_rate_change=None,
            ability_estimate_change='not_available',
        )
        if score_change >= 5:
            review['outcome'] = 'improved'
        elif score_change <= -5:
            review['outcome'] = 'declined'
        else:
            review['outcome'] = 'no_clear_score_change'

        before_times = [
            item['response_seconds']
            for item in before
            if item['response_seconds'] is not None
        ]
        after_times = [
            item['response_seconds']
            for item in follow_up
            if item['response_seconds'] is not None
        ]
        if before_times and after_times and mean(before_times) > 0:
            review['response_time_change_percent'] = round(
                (mean(before_times) - mean(after_times))
                * 100 / mean(before_times),
                1,
            )
        before_active = [
            active_ms
            for item in before
            for active_ms in item['active_ms']
        ]
        after_active = [
            active_ms
            for item in follow_up
            for active_ms in item['active_ms']
        ]
        if before_active and after_active and mean(before_active) > 0:
            review['active_time_change_percent'] = round(
                (mean(after_active) - mean(before_active))
                * 100 / mean(before_active),
                1,
            )
        review['hint_rate_change'] = round(
            mean(item['hint_rate'] for item in follow_up)
            - mean(item['hint_rate'] for item in before),
            3,
        )
        if (
            review['outcome'] == 'no_clear_score_change'
            and (
                review['hint_rate_change'] <= -0.15
                or (
                    review['active_time_change_percent'] is not None
                    and review['active_time_change_percent'] >= 15
                )
            )
        ):
            review['outcome'] = 'engagement_improved'
        elif (
            review['outcome'] == 'no_clear_score_change'
            and review['hint_rate_change'] >= 0.15
            and (
                review['active_time_change_percent'] is None
                or review['active_time_change_percent'] <= -15
            )
        ):
            review['outcome'] = 'engagement_declined'
        return review
    return None


def _escalation_for_review(review):
    escalation_level = min(3, review['escalation_level'] + 1)
    if escalation_level == 1:
        return escalation_level, 'strategy_escalation', 'strategy_suggestion'
    if escalation_level == 2:
        return escalation_level, 'modality_escalation', 'alternate_modality'
    return escalation_level, 'mentor_escalation', 'mentor_referral'


def _plateau_message(language, diagnosis, metrics):
    messages = {
        'comfort_plateau': {
            'en': 'Your practice has stayed steady with few hints. Let’s raise the challenge and mix in a different activity format.',
            'fr': 'Vos résultats restent stables avec peu d’indices. Augmentons le défi et essayons un autre format d’activité.',
            'ar': 'نتائجك مستقرة مع استخدام قليل للتلميحات. لنرفع مستوى التحدي ونجرب نشاطاً بصيغة مختلفة.',
        },
        'frustration_plateau': {
            'en': 'This stretch looks demanding. Let’s step down one level and work through a guided example.',
            'fr': 'Cette étape semble exigeante. Redescendons d’un niveau et suivons un exemple guidé.',
            'ar': 'تبدو هذه المرحلة صعبة. لنخفّض المستوى خطوة ونتدرّب على مثال موجّه.',
        },
        'skill_gap_plateau': {
            'en': 'Your practice is stronger in {strong_topic} than {weak_topic}. Let’s focus there while mixing in a topic you already know.',
            'fr': 'Vos résultats sont meilleurs en {strong_topic} qu’en {weak_topic}. Concentrons-nous sur ce point en alternant avec un sujet maîtrisé.',
            'ar': 'أداؤك في {strong_topic} أقوى منه في {weak_topic}. لنركّز عليه مع التناوب مع موضوع تتقنه.',
        },
        'disengaged_plateau': {
            'en': 'Let’s keep the next game rounds shorter and reset the pace. We can switch back if it doesn’t help.',
            'fr': 'Gardons les prochaines manches plus courtes et reprenons à votre rythme. Nous pourrons changer si cela ne vous aide pas.',
            'ar': 'لنجعل الجولات القادمة أقصر ونواصل بوتيرتك. يمكننا تغيير الأسلوب إن لم يساعدك ذلك.',
        },
        'mastery_plateau': {
            'en': 'You’re scoring strongly across the levels you’ve tried. Let’s move to a stretch activity and apply it to a new challenge.',
            'fr': 'Vos résultats sont solides aux différents niveaux essayés. Passons à une activité avancée et appliquons vos acquis à un nouveau défi.',
            'ar': 'نتائجك قوية عبر المستويات التي جرّبتها. لننتقل إلى نشاط متقدم ونطبّق ما تعلمته في تحدٍ جديد.',
        },
        'strategy_plateau': {
            'en': 'Your results are steady, but they don’t point to one clear cause. Let’s try retrieval practice and alternate topics instead of repeating the same routine.',
            'fr': 'Vos résultats sont stables sans cause clairement établie. Essayons le rappel actif et alternons les sujets au lieu de répéter la même routine.',
            'ar': 'نتائجك مستقرة لكن السبب غير واضح. لنجرب الاسترجاع من الذاكرة والتناوب بين المواضيع بدلاً من تكرار الروتين نفسه.',
        },
        'under_challenged_feed': {
            'en': 'Your scores are steady, but recent practice has not varied enough to call this a plateau. I’ll add a harder tier or a different activity format first.',
            'fr': 'Vos résultats sont stables, mais les dernières séances n’utilisaient qu’un seul niveau. Essayons un niveau supérieur avant de parler de plateau.',
            'ar': 'نتائجك مستقرة، لكن الجلسات الأخيرة استخدمت مستوى صعوبة واحداً فقط. سأضيف مستوى أصعب قبل اعتبار ذلك ثباتاً في التقدم.',
        },
        'repeated_item_feed': {
            'en': 'We’ve repeated many of the same questions, so these results may not show your progress clearly. Let’s switch to fresh questions and a different practice strategy, keeping the challenge suited to your recent work.',
            'fr': 'Nous avons répété beaucoup de questions, ce qui ne reflète peut-être pas clairement vos progrès. Essayons de nouvelles questions et une autre stratégie, avec un niveau adapté à votre travail récent.',
            'ar': 'كررنا الكثير من الأسئلة نفسها، لذلك قد لا تعكس هذه النتائج تقدمك بوضوح. لنجرب أسئلة جديدة واستراتيجية مختلفة بمستوى يناسب عملك الأخير.',
        },
        'insufficient': {
            'en': 'I don’t have enough varied practice yet to tell what is holding progress steady. Let’s collect a few more sessions at different levels.',
            'fr': 'Je n’ai pas encore assez de pratique variée pour comprendre cette stabilité. Recueillons quelques séances supplémentaires à différents niveaux.',
            'ar': 'لا تتوفر لديّ بعد ممارسة متنوعة كافية لمعرفة سبب استقرار النتائج. لنجمع بعض الجلسات الإضافية بمستويات مختلفة.',
        },
        'strategy_escalation': {
            'en': 'The format change hasn’t shifted your recent results yet. Let’s try a new retrieval strategy and alternate the topics.',
            'fr': 'Le changement de format n’a pas encore modifié vos résultats. Essayons une nouvelle stratégie de rappel et alternons les sujets.',
            'ar': 'لم يغيّر تبديل الصيغة النتائج الأخيرة بعد. لنجرب استراتيجية استرجاع جديدة ونتناوب بين المواضيع.',
        },
        'modality_escalation': {
            'en': 'Your results remained steady after the format and strategy changes. Let’s switch learning modality and use a different kind of practice.',
            'fr': 'Vos résultats sont restés stables après les changements de format et de stratégie. Essayons une autre modalité de pratique.',
            'ar': 'بقيت نتائجك مستقرة بعد تغيير الصيغة والاستراتيجية. لننتقل إلى طريقة تعلّم مختلفة.',
        },
        'mentor_escalation': {
            'en': 'We’ve tried a few practice approaches and the pattern is still steady. A teacher or mentor could help choose a more personal next step.',
            'fr': 'Nous avons essayé plusieurs approches et les résultats restent stables. Un enseignant ou mentor pourrait proposer une prochaine étape plus personnelle.',
            'ar': 'جرّبنا عدة أساليب وما زالت النتائج مستقرة. قد يساعدك معلم أو مرشد في اختيار خطوة تناسبك أكثر.',
        },
    }
    return messages.get(diagnosis, {}).get(
        language,
        messages.get(diagnosis, {}).get('en', messages['insufficient']['en']),
    ).format(
        strong_topic=metrics.get('strongest_topic', 'a stronger topic'),
        weak_topic=metrics.get('weakest_topic', 'this topic'),
    )


def detect_assessment_plateau(learner, subject, through=None):
    """Diagnose a stable five-session window using only recorded evidence."""
    sessions = _assessment_history(learner, subject, through)
    review = _intervention_review(learner, subject, sessions)
    window = sessions[-PLATEAU_WINDOW:]
    scores = [item['score'] for item in window]
    result = {
        'detected': False,
        'intervention_required': False,
        'status': 'insufficient_evidence',
        'reason': 'minimum_sessions',
        'sessions_available': len(window),
        'minimum_sessions': PLATEAU_WINDOW,
        'reliable_items': sum(item['reliable_count'] for item in window),
        'minimum_reliable_items': PLATEAU_MIN_RELIABLE_ITEMS,
        'difficulty_levels': sorted({
            level
            for item in window
            for level in item['difficulty_levels']
        }),
        'minimum_difficulty_levels': PLATEAU_MIN_DIFFICULTY_LEVELS,
        'ability_estimate': 'not_available',
        'confidence_interval_method': (
            '95% Student-t slope interval and 90% TOST equivalence interval'
        ),
    }
    if review:
        result['intervention_review'] = review
        if review['status'] == 'cooldown':
            result.update(
                status='intervention_cooldown',
                reason='wait_for_three_post_intervention_sessions',
                cooldown_remaining=review['cooldown_remaining'],
            )
            return result
    if len(window) < PLATEAU_WINDOW:
        if scores:
            result.update(
                average_score=round(mean(scores), 1),
                score_variance=round(variance(scores), 2) if len(scores) > 1 else 0,
            )
        return result

    result['scores'] = [round(score, 1) for score in scores]
    result['assessment_ids'] = [
        item['assessment_id'] for item in window
    ]
    result['average_score'] = round(mean(scores), 1)
    result['score_variance'] = round(variance(scores), 2)
    slope, slope_interval, equivalence_interval = _linear_slope_and_ci(scores)
    result['score_slope_per_session'] = round(slope, 3)
    result['slope_95_ci'] = [round(value, 3) for value in slope_interval]
    result['slope_ci_includes_zero'] = (
        slope_interval[0] <= 0 <= slope_interval[1]
    )
    result['slope_equivalent'] = (
        equivalence_interval[0] >= -PLATEAU_SLOPE_EQUIVALENCE_BAND
        and equivalence_interval[1] <= PLATEAU_SLOPE_EQUIVALENCE_BAND
        and abs(slope) <= PLATEAU_SLOPE_EQUIVALENCE_BAND
    )
    result['slope_equivalence_90_ci'] = [
        round(value, 3) for value in equivalence_interval
    ]
    result['slope_equivalence_band'] = [
        -PLATEAU_SLOPE_EQUIVALENCE_BAND,
        PLATEAU_SLOPE_EQUIVALENCE_BAND,
    ]
    result['cusum_shift_detected'] = _page_hinkley_shift(sessions)
    result['item_entropy'] = _item_entropy(window)
    unique_item_ratio = result['item_entropy']['distinct_question_ratio']
    result['unique_item_ratio'] = unique_item_ratio

    if sum(item['reliable_count'] for item in window) < PLATEAU_MIN_RELIABLE_ITEMS:
        result['reason'] = 'minimum_reliable_items'
        return result
    if slope_interval[0] > 0 or slope_interval[1] < 0:
        if review:
            result.update(
                status='intervention_review',
                reason='post_intervention_trend_detected',
            )
            return result
        return None
    if result['score_variance'] > PLATEAU_MAX_SCORE_VARIANCE:
        result.update(
            status='insufficient_evidence',
            reason='noisy_flat_scores',
            diagnosis='inconsistent',
        )
        return result
    if (
        equivalence_interval[0] < -PLATEAU_SLOPE_EQUIVALENCE_BAND
        or equivalence_interval[1] > PLATEAU_SLOPE_EQUIVALENCE_BAND
        or abs(slope) > PLATEAU_SLOPE_EQUIVALENCE_BAND
    ):
        result['reason'] = 'slope_not_equivalent'
        if review:
            result.update(status='intervention_review')
            return result
        return result

    difficulty_levels = result['difficulty_levels']
    if unique_item_ratio < PLATEAU_MIN_UNIQUE_ITEM_RATIO:
        highest_level = max(difficulty_levels, default=1)
        result.update(
            reason='low_item_diversity',
            diagnosis='repeated_item_feed',
        )
        if (
            result['average_score'] >= 70
            and mean(item['hint_rate'] for item in window) <= 0.15
        ):
            diagnosis = 'under_challenged_feed'
            action = (
                'increase_difficulty_exposure'
                if highest_level < 3 else 'alternate_modality'
            )
            result.update(
                status='challenge_exposure_adjustment',
                diagnosis=diagnosis,
                intervention_required=True,
                action=action,
                escalation_level=0,
            )
            if review and review['status'] == 'reviewed':
                (
                    result['escalation_level'],
                    result['diagnosis'],
                    result['action'],
                ) = _escalation_for_review(review)
            result['message'] = _plateau_message(
                learner.preferences.get('language', 'en'),
                diagnosis,
                {'highest_level': highest_level},
            )
        else:
            diagnosis = 'repeated_item_feed'
            action = 'strategy_suggestion'
            escalation_level = 0
            if review and review['status'] == 'reviewed':
                (
                    escalation_level,
                    diagnosis,
                    action,
                ) = _escalation_for_review(review)
            result.update(
                status='challenge_exposure_adjustment',
                diagnosis=diagnosis,
                intervention_required=True,
                action=action,
                escalation_level=escalation_level,
            )
            result['message'] = _plateau_message(
                learner.preferences.get('language', 'en'),
                diagnosis,
                {},
            )
        return result

    if len(difficulty_levels) < PLATEAU_MIN_DIFFICULTY_LEVELS:
        highest_level = max(difficulty_levels, default=1)
        if (
            result['average_score'] >= 70
            and highest_level < 3
            and mean(item['hint_rate'] for item in window) <= 0.15
        ):
            diagnosis = 'under_challenged_feed'
            result.update(
                status='challenge_exposure_adjustment',
                reason='insufficient_difficulty_exposure',
                diagnosis=diagnosis,
                intervention_required=True,
                action=(
                    'increase_difficulty_exposure'
                    if highest_level < 3 else 'alternate_modality'
                ),
                escalation_level=0,
            )
            result['message'] = _plateau_message(
                learner.preferences.get('language', 'en'),
                diagnosis,
                {},
            )
            return result
        result['reason'] = 'minimum_difficulty_levels'
        return result

    topic_scores = {}
    for session in window:
        for topic, topic_values in session['topics'].items():
            bucket = topic_scores.setdefault(topic, [])
            bucket.extend(topic_values)
    stable_topics = {
        topic: {
            'score': round(100 * mean(values), 1),
            'items': len(values),
        }
        for topic, values in topic_scores.items()
        if len(values) >= 5
    }
    sorted_topics = sorted(
        stable_topics.items(),
        key=lambda item: (item[1]['score'], item[0]),
    )
    topic_gap = (
        sorted_topics[-1][1]['score'] - sorted_topics[0][1]['score']
        if len(sorted_topics) >= 2 else 0
    )
    result['topic_scores'] = stable_topics
    result['topic_gap'] = round(topic_gap, 1)
    if sorted_topics:
        result['weakest_topic'] = sorted_topics[0][0]
        result['strongest_topic'] = sorted_topics[-1][0]

    first_two, last_two = window[:2], window[-2:]
    active_before = [
        value for session in first_two for value in session['active_ms']
    ]
    active_after = [
        value for session in last_two for value in session['active_ms']
    ]
    active_time_drop = (
        mean(active_after) <= mean(active_before) * 0.75
        if active_before and active_after else False
    )
    rapid_flags = {
        flag
        for item in window[-2:]
        for flag in item['rapid_flags']
    }
    average_hint_rate = mean(item['hint_rate'] for item in window)
    first_hint_rate = mean(item['hint_rate'] for item in first_two)
    last_hint_rate = mean(item['hint_rate'] for item in last_two)
    response_before = [
        item['response_seconds']
        for item in first_two
        if item['response_seconds'] is not None
    ]
    response_after = [
        item['response_seconds']
        for item in last_two
        if item['response_seconds'] is not None
    ]
    response_time_increase = bool(
        response_before and response_after
        and mean(response_after) >= mean(response_before) * 1.2
    )
    time_values = [
        item['response_seconds']
        for item in window
        if item['response_seconds'] is not None
    ]
    reading_values = [
        item['reading_ratio']
        for item in window
        if item['reading_ratio'] is not None
    ]
    fast_and_stable = bool(
        time_values and reading_values
        and mean(reading_values) <= 1.5
        and pstdev(time_values) <= max(2, median(time_values) * 0.25)
    )
    item_counts = sum(item['reliable_count'] for item in window)
    distinct_items = sum(item['unique_question_count'] for item in window)
    hint_change = last_hint_rate - first_hint_rate
    maximum_level = max(difficulty_levels)

    fast_accurate_fluency_guard = result['average_score'] >= 80
    disengagement_signal = active_time_drop or bool(rapid_flags)
    if disengagement_signal and not fast_accurate_fluency_guard:
        diagnosis = 'disengaged_plateau'
    elif (
        35 <= result['average_score'] <= 55
        and average_hint_rate >= 0.25
        and (hint_change >= 0.15 or response_time_increase)
    ):
        diagnosis = 'frustration_plateau'
    elif topic_gap >= 40:
        diagnosis = 'skill_gap_plateau'
    elif (
        result['average_score'] >= 90
        and average_hint_rate <= 0.1
        and fast_and_stable
    ):
        diagnosis = 'mastery_plateau'
    elif (
        70 <= result['average_score'] <= 85
        and average_hint_rate <= 0.1
        and fast_and_stable
    ):
        diagnosis = 'comfort_plateau'
    else:
        diagnosis = 'strategy_plateau'

    if diagnosis == 'comfort_plateau':
        action = (
            'alternate_modality'
            if maximum_level >= 3
            else 'raise_difficulty_and_format'
        )
    elif diagnosis == 'frustration_plateau':
        action = 'reduce_difficulty_scaffold'
    elif diagnosis == 'skill_gap_plateau':
        action = 'route_weakest_skill_interleave'
    elif diagnosis == 'disengaged_plateau':
        action = 'short_gamified_format'
    elif diagnosis == 'mastery_plateau':
        action = 'advance_stretch_activity'
    else:
        action = 'strategy_suggestion'

    escalation_level = 0
    if review and review['status'] == 'reviewed':
        escalation_level, diagnosis, action = _escalation_for_review(review)

    result.update(
        detected=True,
        intervention_required=True,
        status='plateau_detected',
        reason='stable_equivalent_slope_and_low_variance',
        diagnosis=diagnosis,
        action=action,
        escalation_level=escalation_level,
        hint_rate=round(average_hint_rate, 3),
        hint_rate_change=round(hint_change, 3),
        response_time_increase=response_time_increase,
        active_time_drop=active_time_drop,
        fast_accurate_fluency_guard=fast_accurate_fluency_guard,
        skip_rate=round(mean(item['skip_rate'] for item in window), 3),
        item_ids=sorted({
            question_id
            for item in window
            for question_id in item['question_ids']
        }),
        rapid_response_flags=sorted(rapid_flags),
        reliable_item_entropy=round(
            len({
                question_id
                for item in window
                for question_id in item['question_ids']
            }) / item_counts,
            3,
        )
        if item_counts else 0,
    )
    from .localization import localize_topic

    message_metrics = {
        **result,
        'weakest_topic': localize_topic(
            result.get('weakest_topic', 'this topic'),
            learner.preferences.get('language', 'en'),
        ),
        'strongest_topic': localize_topic(
            result.get('strongest_topic', 'a stronger topic'),
            learner.preferences.get('language', 'en'),
        ),
    }
    result['message'] = _plateau_message(
        learner.preferences.get('language', 'en'),
        diagnosis,
        message_metrics,
    )
    return result


def _normalized_entropy(counts):
    total = sum(counts.values())
    if total == 0 or len(counts) < 2:
        return 0.0
    entropy = -sum(
        (count / total) * math.log(count / total)
        for count in counts.values()
    )
    return round(entropy / math.log(len(counts)), 3)


def _item_entropy(sessions):
    difficulty_counts = {}
    topic_counts = {}
    item_count = 0
    question_ids = set()
    for session in sessions:
        item_count += session['reliable_count']
        question_ids.update(session['question_ids'])
        for level, count in session['difficulty_counts'].items():
            difficulty_counts[level] = difficulty_counts.get(level, 0) + count
        for topic, values in session['topics'].items():
            topic_counts[topic] = topic_counts.get(topic, 0) + len(values)

    return {
        'reliable_items': item_count,
        'distinct_question_ratio': round(len(question_ids) / item_count, 3)
        if item_count else 0,
        'difficulty_counts': {
            str(level): count
            for level, count in sorted(difficulty_counts.items())
        },
        'topic_counts': topic_counts,
        'difficulty_entropy': _normalized_entropy(difficulty_counts),
        'topic_entropy': _normalized_entropy(topic_counts),
        'format_entropy': 0.0,
        'format': 'multiple_choice',
    }


def assessment_delivery_audit(questions):
    """Record whether a delivered assessment repeats one topic/difficulty run."""
    maximum_run = 0
    current_signature = None
    current_run = 0
    topic_counts = {}
    difficulty_counts = {}
    for question in questions:
        signature = (
            question.get('topic'),
            question.get('difficulty', 'Foundation'),
            'multiple_choice',
        )
        if signature == current_signature:
            current_run += 1
        else:
            current_signature = signature
            current_run = 1
        maximum_run = max(maximum_run, current_run)
        topic_counts[question.get('topic')] = (
            topic_counts.get(question.get('topic'), 0) + 1
        )
        difficulty = question.get('difficulty', 'Foundation')
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1
    unique_question_ratio = (
        len({question.get('id') for question in questions}) / len(questions)
        if questions else 0
    )
    return {
        'item_count': len(questions),
        'unique_question_ratio': round(unique_question_ratio, 3),
        'max_consecutive_same_topic_difficulty_format': maximum_run,
        'item_entropy': {
            'topic': _normalized_entropy(topic_counts),
            'difficulty': _normalized_entropy(difficulty_counts),
            'format': 0.0,
            'question_uniqueness': round(unique_question_ratio, 3),
        },
        'format': 'multiple_choice',
    }


def _page_hinkley_shift(sessions):
    scores = [item['score'] for item in sessions[-10:]]
    if len(scores) < 7:
        return False
    for direction in (1, -1):
        running_mean = scores[0]
        cumulative = 0.0
        for index, score in enumerate(scores[1:], start=1):
            cumulative = max(
                0.0,
                cumulative + direction * (score - running_mean - 1),
            )
            running_mean += (score - running_mean) / (index + 1)
            if cumulative >= 20:
                return True
    return False


BANK = {
    'ml': [
        q('Foundations', 'Predicting the selling price of a house is usually which type of task?', ['Classification', 'Regression', 'Clustering', 'Association mining'], 1, 'A price is a numerical target, so this is regression.', 'Is the target a category or a numerical value?'),
        q('Foundations', 'Which approach groups similar examples without using target labels?', ['Supervised classification', 'Linear regression', 'Clustering', 'Supervised ranking'], 2, 'Clustering finds groups in unlabeled data.', 'Look for an unsupervised method.', 'Building'),
        q('Evaluation', 'A classifier flags 24 emails as phishing. Of those, 18 really are phishing. What is its precision?', ['25%', '50%', '75%', '90%'], 2, 'Precision = true positives / predicted positives = 18 / 24 = 75%.', 'Divide correct positive predictions by all positive predictions.', 'Building'),
        q('Evaluation', 'Which dataset should be reserved for the final evaluation after model selection?', ['Training set', 'Test set', 'A duplicated training set', 'The largest class only'], 1, 'An untouched test set estimates performance after model selection.', 'It should not influence training or model selection.'),
        q('Generalization', 'Training accuracy is 99% and validation accuracy is 65%. What is a plausible concern?', ['The model is definitely unbiased', 'Overfitting', 'Perfect generalization', 'Too little training accuracy'], 1, 'The large gap suggests overfitting, although other causes should also be checked.', 'Compare performance on seen and unseen examples.'),
        q('Generalization', 'An AI suggests fitting a scaler on the whole dataset before splitting. What is the better approach?', ['Accept because labels are unused', 'Fit only on the test set', 'Split first, then fit the scaler on training data', 'Never scale data'], 2, 'Fitting preprocessing on training data avoids leaking held-out distribution information.', 'Held-out data should not inform fitted preprocessing.', 'Stretch'),
        q('Foundations', 'A model labels a photo as “cat” or “dog.” Which task is this?', ['Regression', 'Classification', 'Clustering', 'Dimensionality reduction'], 1, 'Classification predicts a category from a set of possible labels.', 'The output is a named category, not a number.'),
        q('Foundations', 'Why might you standardize numerical features before training some models?', ['To put features on comparable scales', 'To create new target labels', 'To guarantee perfect accuracy', 'To remove the need for evaluation'], 0, 'Standardization rescales features, often to mean zero and standard deviation one, which can help scale-sensitive algorithms.', 'Think about features measured in very different units.', 'Building'),
        q('Evaluation', 'Of 20 actual positive cases, a model correctly finds 15. What is its recall?', ['25%', '60%', '75%', '90%'], 2, 'Recall = true positives / all actual positives = 15 / 20 = 75%.', 'Divide the positives found by all real positives.', 'Building'),
        q('Evaluation', 'A team checks its test-set results repeatedly while tuning a model. What is the main risk?', ['The test set becomes part of model selection', 'The test set becomes encrypted', 'The model stops using features', 'The training labels disappear'], 0, 'Repeated tuning against test results leaks information from the evaluation set into model selection.', 'The test set should stay untouched until the end.'),
        q('Generalization', 'What is the main purpose of a validation set during model development?', ['Tune choices without using the final test set', 'Store the production database', 'Replace all training examples', 'Guarantee that a model is fair'], 0, 'A validation set supports model selection and tuning; a separate test set is kept for final evaluation.', 'It is used before the final test.'),
        q('Generalization', 'A dataset has 990 negative cases and 10 positive cases. Why can accuracy be misleading?', ['Predicting every case as negative can still score 99%', 'Accuracy always ignores negative cases', 'The dataset cannot be split', 'Accuracy measures training speed'], 0, 'With strong class imbalance, a model can achieve high accuracy while missing every minority-class case.', 'Consider a model that predicts only the majority class.', 'Stretch'),
    ],
    'cyber': [
        q('Access control', 'Authentication answers which question?', ['What can you access?', 'Who are you?', 'Is the service online?', 'Is the file encrypted?'], 1, 'Authentication verifies identity; authorization determines permitted actions.', 'Think about proving identity.'),
        q('Access control', 'Changing an invoice ID reveals another customer’s invoice. Which control is missing?', ['A longer URL', 'Server-side object authorization', 'A different font', 'Client-side hiding'], 1, 'The server must check whether the requester may access that specific invoice.', 'An authenticated user still needs permission for each object.', 'Stretch'),
        q('Phishing awareness', 'A message unexpectedly asks you to sign in through a link. What is the safest next step?', ['Use a trusted route to check the request and report it through the approved channel', 'Open the link to see where it leads', 'Forward it to coworkers to ask if it is real', 'Reply with account details'], 0, 'Verify unexpected requests through a trusted channel and report suspicious messages using the approved process.', 'Do not use the message itself as the only way to verify its request.'),
        q('Phishing awareness', 'Which detail can be a reason to verify an email before acting?', ['An unexpected request for urgent account or payment action', 'A familiar sender name by itself', 'A company logo by itself', 'A message arriving during work hours'], 0, 'Unexpected urgency or sensitive requests deserve verification through a trusted route; logos and display names can be misleading.', 'Look for an unexpected request that pressures you to act quickly.'),
        q('Phishing awareness', 'You receive a suspicious work message with an attachment. What should you do?', ['Avoid opening it and report it using your organization’s approved process', 'Open it on a personal device', 'Send it to a public forum for analysis', 'Reply asking the sender to confirm'], 0, 'Avoid interacting with suspicious attachments and use the organization’s approved reporting process.', 'Choose the response that avoids opening the attachment and uses a trusted reporting route.'),
        q('Password safety', 'Which practice best protects a personal account from password reuse risks?', ['Use a unique, long password for each account and store it in a trusted password manager', 'Reuse one memorable password everywhere', 'Share a password with a trusted coworker', 'Add the current year to an old password'], 0, 'Unique passwords limit the impact if one service is compromised; a trusted password manager can help manage them.', 'A password exposed on one service should not unlock another.'),
        q('Password safety', 'A service offers multi-factor authentication. What does enabling it add?', ['A second verification factor in addition to the password', 'A guarantee that no account compromise is possible', 'A replacement for keeping recovery details safe', 'Permission for other users to share the account'], 0, 'Multi-factor authentication adds another proof of identity, though users should still protect credentials and recovery methods.', 'It asks for another kind of proof beyond something you know.'),
        q('Password safety', 'You think your password may have been exposed. What is a sensible response?', ['Change it through the official service and review account security options', 'Wait until someone reports misuse', 'Send the password to support by email', 'Reuse it with a small change'], 0, 'Use the official service to replace an exposed password and review available account protections.', 'Use a trusted route and do not send the password to anyone.'),
        q('Encryption basics', 'What does encryption mainly help protect when data is stored or sent?', ['Confidentiality from people who do not have the needed key', 'Availability during every outage', 'Proof that a user has permission to read the data', 'Removal of all copies of the data'], 0, 'Encryption can protect confidentiality, but it does not replace authorization, backups, or availability planning.', 'Think about who can understand protected data without the key.'),
        q('Encryption basics', 'A team needs to recover protected files later. What must it plan for?', ['Secure, controlled key management and recovery', 'Publishing the keys with the files', 'Using encryption instead of access controls', 'Deleting every backup'], 0, 'Authorized recovery depends on protecting and managing the keys needed to decrypt the files.', 'The right people need reliable access to the keys, while others should not.'),
        q('Encryption basics', 'Which statement about HTTPS is accurate?', ['It helps protect data in transit, but does not decide which records a user may access', 'It guarantees that every website action is authorized', 'It encrypts files stored on every user device', 'It prevents all misleading messages'], 0, 'HTTPS protects a connection in transit; application authorization and other safeguards remain necessary.', 'Transport protection and permission checks address different risks.'),
        q('Data protection', 'Which is designed to be one-way?', ['Encryption with a key', 'Base64 encoding', 'Cryptographic hashing', 'File compression'], 2, 'Cryptographic hashing is designed to resist inversion; encryption is reversible with a key.', 'Which operation has no decryption key?'),
        q('Data protection', 'What is the best choice here for storing user passwords?', ['Plain text', 'Base64', 'A password hashing scheme such as Argon2 with salts', 'Reversible encryption with a shared key'], 2, 'A dedicated password hashing scheme and salts protect stored password verifiers.', 'Use a scheme designed specifically for passwords.', 'Stretch'),
        q('Secure development', 'Which is the standard defense against SQL injection when inserting user values into a query?', ['String concatenation', 'Parameterized queries', 'Hiding the form', 'Removing spaces'], 1, 'Parameterized queries keep values separate from SQL syntax.', 'Treat input as data rather than query syntax.'),
        q('Secure development', 'An AI says HTTPS prevents all authorization flaws. How should you respond?', ['Agree unconditionally', 'Disable HTTPS', 'Explain that transport encryption does not enforce object permissions', 'Replace authentication with HTTPS'], 2, 'HTTPS protects transport; server-side authorization remains necessary.', 'Transport protection and permissions solve different problems.', 'Building'),
        q('Access control', 'What should authorization determine after a user signs in?', ['Which actions and resources that user is allowed to access', 'The user’s password', 'Whether the network cable is connected', 'The page’s visual theme'], 0, 'Authorization evaluates permissions for a user, action, and resource.', 'Identity is already established; think about permitted actions.'),
        q('Data protection', 'Which cookie setting helps prevent client-side JavaScript from reading a session cookie?', ['HttpOnly', 'Public', 'Indexable', 'Debug'], 0, 'The HttpOnly attribute prevents browser scripts from reading the cookie through document.cookie.', 'It limits access from scripts running in the page.'),
        q('Secure development', 'What is a safer password-reset token design?', ['Long random, short-lived, and single-use', 'A predictable username plus the current date', 'A permanent token shared by every user', 'A token placed in a public profile'], 0, 'Reset tokens should be unpredictable, expire, and become invalid after use.', 'A reset link should not be reusable indefinitely.'),
        q('Access control', 'What does rate limiting help reduce on a login endpoint?', ['Automated repeated guesses', 'The strength of encryption', 'The need for authorization', 'The number of user roles'], 0, 'Rate limiting constrains repeated requests and can slow automated credential guessing.', 'Consider controls on repeated attempts.', 'Building'),
        q('Data protection', 'What does encryption at rest protect?', ['Stored data on a device or storage system', 'Every account from authorization bugs', 'A password from being forgotten', 'All data from an authorized application'], 0, 'Encryption at rest protects stored data if the underlying storage is exposed, but does not replace access controls.', 'Think about data while it is stored rather than moving.', 'Building'),
        q('Secure development', 'Which defense helps a browser reject forged requests made with a signed-in user’s session?', ['CSRF protection using tokens or suitable same-site controls', 'A longer page title', 'Client-side form validation alone', 'A hidden navigation menu'], 0, 'CSRF defenses verify that state-changing requests were intentionally initiated by the application.', 'The risk involves a browser sending an unwanted authenticated request.', 'Stretch'),
    ],
    'ai': [
        q('Search', 'Which uninformed search explores nodes level by level?', ['Depth-first search', 'Breadth-first search', 'Random search', 'Hill climbing'], 1, 'Breadth-first search explores the shallowest frontier nodes first.', 'Think of a queue rather than a stack.'),
        q('Search', 'For a finite graph with equal positive edge costs, which search finds a shortest path when implemented with repeated-state handling?', ['Breadth-first search', 'Depth-first search always', 'Any random walk', 'Greedy search always'], 0, 'With equal edge costs, minimizing edges minimizes path cost; BFS explores by depth.', 'The shallowest solution has the lowest cost here.', 'Stretch'),
        q('Learning systems', 'Which is an example of supervised learning?', ['Grouping unlabeled news', 'Training from images paired with class labels', 'Searching a maze without examples', 'Hand-writing every rule'], 1, 'Supervised learning uses input examples with target labels.', 'Look for labeled examples.'),
        q('Learning systems', 'In reinforcement learning, what feedback guides an agent toward its objective?', ['Only file names', 'Rewards', 'A mandatory correct label for every action', 'Alphabetical ordering'], 1, 'The agent learns from rewards and interaction with an environment.', 'Feedback can be delayed after an action.', 'Building'),
        q('AI verification', 'A chatbot confidently cites a research paper. What is the strongest next check?', ['Trust its confidence', 'Find the original paper and check whether it supports the claim', 'Ask it to sound more certain', 'Count the words'], 1, 'Checking the original source tests both whether it exists and whether it supports the claim.', 'Verify against primary evidence.'),
        q('AI verification', 'Why should instructions inside a retrieved webpage be treated cautiously by an AI assistant?', ['All webpages are false', 'They may attempt to redirect the assistant away from the user’s task', 'Retrieval always encrypts text', 'Webpages cannot contain instructions'], 1, 'Retrieved content is untrusted evidence and may contain prompt injection attempts.', 'External content is not automatically authorized instruction.', 'Stretch'),
        q('Learning systems', 'What does self-attention help a transformer model do?', ['Relate tokens to other tokens in the same context', 'Encrypt every input token', 'Guarantee that every output is true', 'Remove the need for training data'], 0, 'Self-attention computes contextual relationships between tokens when building representations.', 'It lets a token use information from other tokens.', 'Building'),
        q('Learning systems', 'Which technique can help reduce overfitting by discouraging overly complex model behavior?', ['Regularization', 'Duplicating the test set', 'Removing validation data', 'Increasing label leakage'], 0, 'Regularization adds a constraint or penalty that can discourage excessive complexity.', 'Think of a training objective that penalizes certain model weights.', 'Stretch'),
        q('Search', 'In machine learning, what is an embedding?', ['A vector representation of an item such as a word or image', 'A final test score', 'A password encryption key', 'A list of model errors only'], 0, 'An embedding maps an item to a vector so a model can work with its features and relationships.', 'It represents an item as numbers in a learned space.', 'Building'),
        q('AI verification', 'What is a useful first step when evaluating an AI-generated answer for factual accuracy?', ['Check important claims against reliable primary sources', 'Trust the most fluent response', 'Assume citations are valid without opening them', 'Ask the model to increase its confidence'], 0, 'Verifying key claims with reliable sources provides evidence beyond the model’s wording.', 'Look for external evidence rather than presentation style.'),
        q('AI verification', 'Why should training and test examples usually be separated?', ['To estimate performance on data not used to fit the model', 'To make all examples identical', 'To remove the target variable permanently', 'To avoid measuring performance'], 0, 'A held-out test set helps estimate generalization to unseen examples.', 'The estimate is more meaningful when evaluation data was not used for fitting.'),
        q('Search', 'What is a key limitation of a retrieval-augmented generation system?', ['Retrieved material can be incomplete or irrelevant, so generated claims still need checking', 'It cannot use documents', 'Retrieval guarantees correct answers', 'It always trains a new language model'], 0, 'Retrieval can provide useful context, but retrieval errors and generation errors remain possible.', 'Adding references does not itself guarantee correctness.'),
    ],
}


def _reviewed_scenarios(topic, prompts, choices, correct, explanation, hint):
    return [
        q(topic, prompt, list(choices), correct, explanation, hint, 'Building')
        for prompt in prompts
    ]


for subject, groups in {
    'ml': (
        ('Foundations', ('A clinic predicts a patient’s waiting time in minutes. Which task is this?', 'A shop estimates next month’s revenue as a number. Which task is this?', 'A delivery service predicts a parcel’s arrival time. Which task is this?', 'A farm estimates crop yield in tonnes. Which task is this?'), ('Classification', 'Regression', 'Clustering', 'Association mining'), 1, 'Predicting a numeric target is regression.', 'Check whether the output is a number or a category.'),
        ('Foundations', ('A model sorts support tickets into billing, account, and technical labels. Which task is this?', 'A filter marks each message as spam or not spam. Which task is this?', 'A system assigns each plant photo to a species label. Which task is this?', 'A service predicts whether a loan application is approved or declined. Which task is this?'), ('Regression', 'Classification', 'Clustering', 'Dimensionality reduction'), 1, 'Classification predicts a category from a set of labels.', 'The output is one of several named categories.'),
        ('Foundations', ('A retailer groups shoppers by similar purchase patterns without existing labels. Which method fits?', 'A researcher groups news articles by similarity without topic labels. Which method fits?', 'A music service groups listeners by their listening patterns without predefined classes. Which method fits?', 'A scientist groups samples by measured features without known categories. Which method fits?'), ('Linear regression', 'Classification', 'Clustering', 'Ranking'), 2, 'Clustering discovers groups in data without target labels.', 'Look for an approach that does not need labeled examples.'),
        ('Evaluation', ('A model marks 6 of 8 flagged messages as spam. What is its precision?', 'A model correctly identifies 9 of 12 flagged transactions. What is its precision?', 'A model’s 15 positive predictions include 12 true positives. What is its precision?', 'A classifier flags 21 cases and 7 are false alarms. What is its precision?'), ('25%', '60%', '75%', '90%'), 2, 'Precision is true positives divided by all predicted positives; each case is 75%.', 'Divide correct positive predictions by all positive predictions.'),
        ('Evaluation', ('A screen detects 4 of 5 people who have a condition. What is its recall?', 'A detector finds 8 of 10 actual faults. What is its recall?', 'A model identifies 12 of 15 actual positive cases. What is its recall?', 'A search system returns 16 of 20 relevant records. What is its recall?'), ('20%', '60%', '80%', '95%'), 2, 'Recall is true positives divided by all actual positives; each case is 80%.', 'Divide the positives found by all real positives.'),
        ('Evaluation', ('A team chooses model settings using a final test set, then reports its score. What is the main concern?', 'A developer repeatedly tunes a model after checking final test results. What has happened?', 'A team selects the best model by comparing final test scores. What is the risk?', 'A researcher adjusts a threshold until the test-set result looks best. What is the concern?'), ('The test set influenced model selection', 'The labels became encrypted', 'The training set became larger', 'The model changed its target'), 0, 'Repeated decisions based on test results leak evaluation information into model selection.', 'The final test should be used only after development choices are fixed.'),
        ('Evaluation', ('Which data should guide model choices before the final test?', 'A team needs data to compare candidate settings without touching its final holdout. Which split should it use?', 'During development, which set helps select between candidate models?', 'Which split is intended for tuning choices while preserving a separate final evaluation?'), ('Training labels only', 'Validation set', 'Final test set', 'A duplicate of the test set'), 1, 'A validation set supports model selection; a separate test set is reserved for final evaluation.', 'Use the development-stage holdout, not the final one.'),
        ('Generalization', ('A scaler is fitted before a dataset is split. Why can this be a problem?', 'A preprocessing step learns from all rows, including held-out rows. What risk does this create?', 'Why fit a scaler on training rows only?', 'A pipeline learns feature ranges from the test set. What should the team avoid?'), ('Data leakage from held-out data', 'Underfitting by definition', 'Class balancing', 'Removing target labels'), 0, 'Fitting preprocessing on all data leaks information from held-out examples into training.', 'Held-out examples should not influence fitted preprocessing.'),
        ('Generalization', ('Training performance is very high, but performance on new validation examples is much lower. What is a likely concern?', 'A model memorizes its training examples and performs poorly on unseen cases. What is this called?', 'A large gap separates training and validation scores. Which issue should be investigated?', 'A model performs well on familiar data but poorly on new data. What is a plausible explanation?'), ('Clustering', 'Overfitting', 'Perfect calibration', 'Feature scaling'), 1, 'A large training-to-validation gap can indicate overfitting.', 'Compare performance on seen and unseen examples.'),
        ('Foundations', ('A distance-based model mixes age in years with income in thousands. Why might scaling help?', 'One feature ranges from 0 to 1 while another ranges into the millions. What preprocessing may help a scale-sensitive model?', 'Why standardize measurements that use very different units?', 'A nearest-neighbor model is dominated by a high-magnitude feature. What can make feature contributions more comparable?'), ('Feature standardization', 'Duplicating labels', 'Removing the validation set', 'Increasing the class count'), 0, 'Standardization puts numerical features on comparable scales for scale-sensitive algorithms.', 'Consider how feature magnitudes affect distance calculations.'),
        ('Evaluation', ('A dataset has 980 negative records and 20 positive records. Why may accuracy alone mislead?', 'A detector predicts every example as the majority class in a highly imbalanced dataset. Why can its accuracy look good?', 'A rare-fault model reports high accuracy but misses nearly every fault. What is the underlying issue?', 'A dataset contains very few positive examples. What limitation does raw accuracy have?'), ('It can hide poor performance on the minority class', 'It always ignores true negatives', 'It measures prediction speed', 'It cannot be calculated'), 0, 'With class imbalance, majority-class predictions can yield high accuracy while missing the minority class.', 'Consider a model that always predicts the more common label.'),
        ('Learning systems', ('A model learns from examples paired with the correct house-condition labels. Which learning setup is this?', 'A dataset pairs product reviews with sentiment labels. Which learning approach uses these targets?', 'A classifier is trained from images with known category labels. What type of learning is used?', 'A model learns a mapping from examples to provided target values. Which approach is this?'), ('Unsupervised learning', 'Supervised learning', 'Random search', 'Rule-free clustering'), 1, 'Supervised learning uses examples paired with target labels.', 'The training examples include known answers.'),
        ('Learning systems', ('A system represents words as learned numeric vectors so related terms can be compared. What are these representations?', 'A model maps images to points in a learned feature space. What kind of representation is this?', 'A search system compares documents using learned numeric representations. What are they called?', 'A model encodes items as vectors that capture useful relationships. What is this representation?'), ('Embeddings', 'Test labels', 'Password hashes', 'Learning rates'), 0, 'An embedding maps an item to a vector representation a model can use.', 'Think of learned vector representations of items.'),
        ('Learning systems', ('A robot receives a reward after reaching a goal and uses feedback to improve actions. Which learning setup fits?', 'An agent improves its actions using rewards from an environment. What approach is this?', 'A game-playing system learns from reward signals after taking actions. Which approach is used?', 'A controller adjusts behavior based on rewards and penalties from interaction. What is this called?'), ('Supervised classification', 'Reinforcement learning', 'Clustering', 'Dimensionality reduction'), 1, 'Reinforcement learning uses reward feedback from interaction with an environment.', 'The feedback evaluates actions rather than labeling every example.'),
        ('Generalization', ('A team adds a penalty for very large model weights to discourage unnecessary complexity. What technique is this?', 'A training objective penalizes complex behavior to reduce overfitting. What is this called?', 'Which method adds a constraint or penalty to discourage overly complex model fits?', 'A model is regularized by adding a complexity penalty to its objective. Which technique is being used?'), ('Regularization', 'Data augmentation by duplication', 'Test-set tuning', 'Label encoding'), 0, 'Regularization penalizes or constrains complexity and can reduce overfitting.', 'Look for a penalty added to the training objective.'),
        ('Foundations', ('A system predicts the next month’s electricity use in kilowatt-hours. Which task is this?', 'A city estimates daily water demand as a numeric quantity. Which model task fits?', 'A model predicts the number of items a warehouse will need. Which task is this?', 'A service estimates a numeric insurance claim amount. Which task is this?'), ('Classification', 'Regression', 'Clustering', 'Association mining'), 1, 'Estimating a numeric target is a regression task.', 'The target is a quantity rather than a label.'),
        ('Evaluation', ('A model correctly labels 90 of 100 evaluation examples. What is its accuracy?', 'A classifier gets 45 of 50 cases right. What is its accuracy?', 'A model predicts 72 of 80 examples correctly. What is its accuracy?', 'A classifier is correct on 27 of 30 examples. What is its accuracy?'), ('60%', '75%', '90%', '99%'), 2, 'Accuracy is correct predictions divided by all predictions; each case is 90%.', 'Divide the number correct by the total number evaluated.'),
        ('Evaluation', ('A team wants a more stable estimate by training and validating across several data folds. Which method fits?', 'A small dataset is split into rotating train and validation folds. What evaluation approach is this?', 'Which method repeatedly changes the held-out fold to compare model settings?', 'A developer wants to use different partitions for validation while retaining a final test set. What can help?'), ('Cross-validation', 'Test-set tuning', 'Label encoding', 'Clustering'), 0, 'Cross-validation evaluates across multiple data splits and can provide a more stable model-selection estimate.', 'The held-out fold changes across repeated runs.'),
        ('Foundations', ('A model converts colors such as red, green, and blue into indicator columns. What encoding is this?', 'How can a model receive a nominal category without implying an order between labels?', 'A dataset stores country names as categories. Which encoding avoids treating them as numeric ranks?', 'A preprocessing step creates a binary feature for each category. What is this called?'), ('One-hot encoding', 'Target leakage', 'Standardization', 'Regression'), 0, 'One-hot encoding represents categories with indicator columns without imposing an ordinal scale.', 'Use separate indicators rather than numeric ranks.'),
        ('Generalization', ('A feature contains information recorded only after the outcome occurs. Why is it unsuitable for prediction?', 'A model uses a post-treatment field unavailable at decision time. What risk does this create?', 'A prediction pipeline includes a feature that reveals the target indirectly. What is this problem?', 'Why remove a column that would not exist when a real prediction is made?'), ('Target leakage', 'Class balancing', 'Regularization', 'Clustering'), 0, 'A feature that reveals future outcome information leaks the target into training.', 'Ask whether the feature would be available at prediction time.'),
        ('Evaluation', ('A fraud team lowers its decision threshold and catches more fraud but flags more legitimate purchases. Which tradeoff changed?', 'A screening model identifies more positive cases but creates more false alarms. Which metrics are in tension?', 'A detector increases recall by accepting additional false positives. What tradeoff is this?', 'A team adjusts a classifier threshold to miss fewer positives, at the cost of more false alerts. What changed?'), ('Precision and recall', 'Training and test size', 'Encoding and scaling', 'Clustering and regression'), 0, 'Changing a threshold can trade precision against recall.', 'Compare false alarms with missed positive cases.'),
        ('Foundations', ('A model compresses many correlated measurements into fewer learned components. Which technique may do this?', 'Which method can reduce the number of feature dimensions while preserving useful variation?', 'A data scientist maps many features into a smaller representation. What is this called?', 'A preprocessing stage reduces the feature space before visualization. Which approach fits?'), ('Dimensionality reduction', 'Authentication', 'Rate limiting', 'Regression scoring'), 0, 'Dimensionality reduction maps data to fewer features or components.', 'The output has fewer dimensions than the input.'),
        ('Evaluation', ('A team uses a validation set to choose a model, then reserves a separate set for one final check. What is the reserved set?', 'After all model decisions are fixed, which split estimates final performance?', 'Which data should be evaluated once after tuning is complete?', 'A model-development team needs an untouched final estimate. Which dataset should it keep?'), ('Training set', 'Validation set', 'Test set', 'Augmented training set'), 2, 'The test set is held out for final evaluation after model selection.', 'It should not guide development choices.'),
        ('Learning systems', ('A model combines predictions from several independently trained models. What general approach is this?', 'A team averages outputs from multiple models to improve robustness. What is this called?', 'Several learners are combined to form one predictor. Which technique is being used?', 'A prediction system aggregates multiple model outputs rather than relying on one. What is this?'), ('Ensembling', 'One-hot encoding', 'Data leakage', 'Breadth-first search'), 0, 'Ensembling combines multiple models or predictions.', 'The final prediction uses several learners.'),
        ('Evaluation', ('A dataset has repeated rows from the same person in both training and test sets. Why is this risky?', 'How can near-duplicate records across data splits distort an evaluation?', 'A split places related measurements from one subject in both train and test. What concern arises?', 'Why should groups or repeated entities sometimes be kept within one data split?'), ('Evaluation leakage and overly optimistic results', 'Guaranteed fairness', 'Lower feature dimensionality', 'Improved label quality'), 0, 'Related examples crossing splits can leak information and make test performance look too optimistic.', 'The test cases may no longer be independent of training cases.'),
        ('Generalization', ('A model performs well on one random split but poorly on other splits. What should the team investigate?', 'Validation results vary widely when the split changes. What does this suggest?', 'A reported score depends heavily on which examples were held out. What concern is this?', 'Why repeat evaluation across suitable splits when results appear unstable?'), ('High evaluation variance', 'Perfect generalization', 'No target labels', 'One-hot encoding'), 0, 'Large variation across splits suggests the performance estimate may be unstable.', 'Check whether the result changes substantially with the sample split.'),
        ('Foundations', ('A model uses unlabeled examples to learn a compact representation before a downstream task. Which broad approach is this?', 'A system learns structure from data without target labels. Which learning category fits?', 'A team explores patterns in data that has no outcome labels. What kind of learning is this?', 'Which approach learns structure from inputs without supplied target values?'), ('Unsupervised learning', 'Supervised classification', 'Password hashing', 'Final testing'), 0, 'Unsupervised learning finds structure in inputs without target labels.', 'The examples have no supplied answers.'),
    ),
    'cyber': (
        ('Access control', ('A service checks a person’s credentials before sign-in. Which question is it answering?', 'A login verifies a user’s identity. What security process is this?', 'A system confirms that a passkey belongs to the account holder. Which process is this?', 'A user proves control of an account before access is considered. Which step is this?'), ('What may you do?', 'Who are you?', 'Which file is encrypted?', 'Is the server online?'), 1, 'Authentication verifies identity; authorization decides what an identified user may do.', 'Think about proving identity.'),
        ('Access control', ('After login, a server checks whether the user may edit a report. Which process is this?', 'A signed-in user requests a project record. What should determine whether access is allowed?', 'Which check decides whether a role may delete a specific resource?', 'An application checks permitted actions after identifying a user. Which process is this?'), ('Authentication', 'Authorization', 'Compression', 'Hashing'), 1, 'Authorization evaluates permissions for a user, action, and resource.', 'Identity is already established; consider permission.'),
        ('Access control', ('A support account needs read-only access to customer records. Which design follows least privilege?', 'A service account only needs to process invoices. How should its permissions be set?', 'A developer account needs access to one test project. Which permission strategy is safest?', 'A monitoring process only reads health data. What access should it receive?'), ('Grant only the permissions required for the task', 'Give administrator access to avoid future requests', 'Share a permanent root credential', 'Disable audit logs'), 0, 'Least privilege grants only the access needed for a defined task.', 'Give the account no broader authority than it requires.'),
        ('Data protection', ('A site stores a password using a purpose-built password hashing function with a unique salt. Why?', 'Which approach best protects stored password verifiers if a database leaks?', 'A login system must store password verification data. What is the safer design?', 'Why use a slow password-hashing scheme with a per-password salt?'), ('Plain-text storage', 'A salted password hash', 'Base64 encoding', 'A shared reversible key'), 1, 'A salted password hash makes offline guessing harder and avoids storing recoverable passwords.', 'Use a scheme designed specifically for password storage.'),
        ('Data protection', ('A protected file must be recovered by its authorized owner later. Which technique is appropriate?', 'A team needs to read stored data after proving possession of a key. What should it use?', 'Which method protects confidentiality while allowing a key holder to recover the original data?', 'A backup must remain readable to authorized staff. Which protection fits?'), ('Encryption with controlled keys', 'A one-way hash alone', 'A checksum only', 'Public Base64 encoding'), 0, 'Encryption is reversible with the appropriate key and can protect data confidentiality.', 'The data needs to be recovered by an authorized key holder.'),
        ('Secure development', ('A database query uses user input. Which approach keeps that value separate from SQL syntax?', 'A developer wants to prevent input from changing a database command. What should be used?', 'How should an application pass a search term to a SQL statement?', 'Which practice is the standard defense against SQL injection in a query?'), ('Parameterized queries', 'String concatenation', 'Removing spaces only', 'Hiding the input field'), 0, 'Parameterized queries keep input values separate from SQL instructions.', 'Treat user input as data rather than query syntax.'),
        ('Secure development', ('A page displays a user’s comment. Which control helps prevent the comment from executing as markup?', 'A profile name is inserted into HTML. What should the application do before displaying it?', 'Which defense helps stop untrusted text from running as a browser script?', 'An application renders a user-supplied title. What is a key XSS defense?'), ('Context-aware output encoding', 'A longer session timeout', 'Client-side hiding', 'Disabling database backups'), 0, 'Context-aware output encoding prevents untrusted input from being interpreted as executable markup.', 'Make displayed data render as text, not active code.'),
        ('Secure development', ('A browser may send a signed-in user’s cookies with an unwanted request. Which defense helps verify the request was intentional?', 'A state-changing form needs protection from cross-site request forgery. What should be added?', 'Which control helps reject forged requests made with an authenticated browser session?', 'A banking site wants to ensure a transfer request came from its own form. What defense is appropriate?'), ('CSRF tokens or suitable same-site controls', 'A different page title', 'Plain client-side validation alone', 'A longer URL'), 0, 'CSRF defenses verify that state-changing requests were intentionally initiated by the application.', 'The risk is an unwanted authenticated browser request.'),
        ('Access control', ('A record ID in a URL can be changed to access another account’s record. What check is essential?', 'A user guesses a sequential invoice number and sees another customer’s data. What failed?', 'Why should a server authorize each requested object even when the user is signed in?', 'An API receives a document identifier from a client. What must it verify?'), ('Per-object server-side authorization', 'A longer identifier', 'Client-side button hiding', 'A different visual theme'), 0, 'The server must check whether the requester may access each specific object.', 'Authentication alone does not grant permission to every record.'),
        ('Secure development', ('A login endpoint receives thousands of repeated guesses. Which control can limit the request rate?', 'A public API must constrain repeated attempts from one client. What should be used?', 'Which measure slows automated repeated requests to a sign-in route?', 'A service wants to limit bursts of requests to an expensive endpoint. Which control fits?'), ('Rate limiting', 'Password encryption at rest only', 'Disabling authorization', 'Changing the page font'), 0, 'Rate limiting constrains repeated requests and can slow automated guessing.', 'Consider a control on the number of requests over time.'),
        ('Data protection', ('Which cookie attribute prevents page JavaScript from reading a session cookie?', 'A session token should not be exposed through document.cookie. Which attribute helps?', 'Which browser cookie setting limits access from client-side scripts?', 'A developer wants a session cookie unavailable to JavaScript. Which flag is relevant?'), ('HttpOnly', 'Public', 'Indexable', 'Debug'), 0, 'The HttpOnly attribute prevents browser scripts from reading the cookie through document.cookie.', 'It limits access from scripts running in the page.'),
        ('Data protection', ('A laptop may be stolen while powered off. Which safeguard helps protect files stored on its drive?', 'A company needs protection for data on a lost storage device. What should it use?', 'Which control protects stored information if the underlying disk is exposed?', 'A backup disk leaves the office. What can protect its stored contents?'), ('Encryption at rest', 'A URL rewrite', 'Rate limiting', 'Output encoding'), 0, 'Encryption at rest protects stored data if the underlying storage is exposed.', 'Consider protection for data on a device or storage system.'),
        ('Secure development', ('A password-reset link should stop working after its first successful use. Which property is this?', 'Which reset-token design limits damage if a link is accidentally shared?', 'A recovery token is unpredictable, expires soon, and cannot be reused. Which property matters here?', 'How should a password-reset token behave after the password changes?'), ('Single-use and short-lived', 'Permanent and shared', 'Based on the username', 'Visible in a public profile'), 0, 'Reset tokens should be unpredictable, expire, and become invalid after use.', 'A reset link should not be reusable indefinitely.'),
        ('Access control', ('A team member leaves a project. What should happen to their access?', 'A temporary contractor finishes a task. How should their account permissions change?', 'A user no longer needs a system role. What is the safe response?', 'A service credential is no longer needed. What should the operator do?'), ('Revoke access that is no longer required', 'Keep administrator access indefinitely', 'Share it with a new employee', 'Disable audit events'), 0, 'Removing unneeded permissions reduces the impact of account misuse.', 'Access should match current responsibilities.'),
        ('Secure development', ('A server uses HTTPS. Does that alone guarantee a user may read a particular invoice?', 'Which statement about transport encryption and authorization is accurate?', 'A secure connection protects data in transit. What must still control access to a record?', 'Why does TLS not replace application permission checks?'), ('No; the server must still enforce authorization', 'Yes; HTTPS grants every signed-in user access', 'Yes; encryption hides all permission errors', 'No; HTTPS disables authentication'), 0, 'HTTPS protects data in transit; server-side authorization still controls resource access.', 'Transport protection and access permissions solve different problems.'),
        ('Secure development', ('A service accepts an uploaded document. Which control helps prevent it from being interpreted as executable content?', 'What should an upload handler do before storing a user-provided file?', 'A public upload feature must constrain file types and storage behavior. Which practice is useful?', 'How can an application reduce risks from untrusted uploaded files?'), ('Validate allowed file types and store uploads safely', 'Trust the filename extension alone', 'Place every upload in an executable directory', 'Disable access checks'), 0, 'Uploads should be validated and stored so user content cannot execute as application code.', 'Treat uploaded files as untrusted input.'),
        ('Access control', ('A web service grants every worker full database access. Which principle should guide a safer design?', 'A service only needs to read one table. What should its database role allow?', 'How should permissions be set for a background job with one narrow task?', 'A developer proposes administrator rights for a read-only report. What is the better approach?'), ('Least privilege', 'Shared root access', 'Permanent broad access', 'Public access'), 0, 'Least privilege limits an account to only the permissions required for its task.', 'Grant no broader authority than the task needs.'),
        ('Data protection', ('A company needs to recover operations after ransomware. Which preparation is most useful?', 'Which backup practice improves recovery if the primary system is compromised?', 'A team wants backups resilient to an attacker who can access production. What should it arrange?', 'How can an organization test whether its backups are useful during an outage?'), ('Maintain protected backups and test restoration', 'Keep the only backup writable from every production account', 'Store backup keys in a public log', 'Assume synchronization is a full backup'), 0, 'Protected, tested backups support recovery when production data is damaged or unavailable.', 'A backup is valuable only if it can be restored.'),
        ('Secure development', ('A software dependency has a security update. What is a sound response?', 'A team learns a library version has a known security issue. What should it do?', 'How should maintainers handle a relevant vendor security patch?', 'A production service uses an outdated vulnerable package. What is an appropriate action?'), ('Assess impact and apply a tested update promptly', 'Ignore the advisory indefinitely', 'Disable all logging', 'Publish the dependency credentials'), 0, 'Maintainers should assess exposure and apply appropriate tested security updates.', 'Use the vendor advisory and update process.'),
        ('Data protection', ('A log entry needs to record a login failure. Which choice avoids unnecessary sensitive data?', 'What should an application avoid placing in ordinary logs?', 'A developer is logging a request for troubleshooting. Which data should be excluded?', 'Which log design best limits exposure if logs are accessed improperly?'), ('Passwords, session tokens, and unnecessary personal data', 'A timestamp and event type', 'A request correlation ID', 'A sanitized error category'), 0, 'Logs should avoid secrets and unnecessary sensitive personal data.', 'Do not record credentials or reusable tokens.'),
        ('Secure development', ('An application receives a numeric account ID. Why validate it on the server even if the form checks it?', 'A client-side check can be bypassed. Where must authorization and input checks still occur?', 'A browser restricts a field to a number. What should the server do with submitted data?', 'Why is a front-end validation rule not a security boundary?'), ('Repeat validation and permission checks on the server', 'Trust the browser completely', 'Remove authentication', 'Expose the database password'), 0, 'Client-side controls can be bypassed; the server must validate and authorize requests.', 'The client is controlled by the requester.'),
        ('Data protection', ('A system sends a one-time login code to a second factor. What does this add?', 'A user must provide a password and a registered authenticator code. Which control is this?', 'Which design asks for evidence from two different factor categories?', 'A login requires both a password and a device-generated code. What security measure is used?'), ('Multi-factor authentication', 'Encryption at rest only', 'Rate limiting only', 'Data compression'), 0, 'Multi-factor authentication combines evidence from different factor categories.', 'The user proves identity with more than one type of factor.'),
        ('Secure development', ('An application should return the same generic sign-in error for unknown users and wrong passwords. What risk does this reduce?', 'Why avoid revealing whether an account exists in a login response?', 'A reset form should not disclose whether an email is registered. What does this help prevent?', 'Which response design reduces account discovery through error messages?'), ('User or account enumeration', 'SQL parameterization', 'Data compression', 'Feature scaling'), 0, 'Consistent responses reduce information that can be used to enumerate valid accounts.', 'Avoid exposing account existence to unauthenticated callers.'),
        ('Access control', ('A former employee’s API key may still be active. What should an administrator do?', 'A shared secret is suspected to be exposed. What is the appropriate response?', 'Why rotate a credential after an access change or suspected disclosure?', 'A service key is no longer trustworthy. What action limits further use?'), ('Revoke or rotate it and review related access', 'Publish it for transparency', 'Keep it active permanently', 'Disable security monitoring'), 0, 'Revoking or rotating exposed credentials limits unauthorized future use.', 'Treat the secret as compromised until replaced.'),
        ('Data protection', ('A monitoring system detects unusual access to sensitive records. What helps an investigation?', 'Which log properties support tracing a security event?', 'A team needs to investigate who changed a permission. What should records capture?', 'How should security event logs be protected and maintained?'), ('Useful timestamps and event details with restricted log access', 'Passwords in every entry', 'No timestamps', 'Public write access'), 0, 'Protected, useful audit records support investigation without exposing secrets.', 'Record relevant events and restrict who can alter the logs.'),
        ('Secure development', ('A web server should not execute user-uploaded files. Where should they be stored?', 'Which storage choice reduces risk from uploaded active content?', 'A service accepts profile images. How should it serve them?', 'What is safer than placing untrusted uploads beside executable application code?'), ('In a separate non-executable storage location', 'In the application source directory', 'In a directory with script execution enabled', 'Inside an administrator’s session'), 0, 'Separating uploads from executable application code reduces execution risk.', 'Treat user-controlled files as data, not application code.'),
        ('Access control', ('An API checks that a user is signed in but not whether they may edit the requested record. What check is missing?', 'Why should a server authorize each requested object instead of trusting a user role alone?', 'A user changes a record identifier in a request. What must the server verify?', 'A signed-in user requests an action on a specific account. Which decision is essential?'), ('Permission for this action on this specific resource', 'Whether the page uses a different color', 'Whether the request came from a long URL', 'Whether the account has a profile picture'), 0, 'The server should authorize the user, action, and specific resource for each request.', 'Authentication proves identity, but object-level permission still matters.'),
    ),
    'ai': (
        ('Search', ('A route planner explores all states at one depth before moving deeper. Which search strategy is this?', 'Which uninformed search uses a queue to explore nodes level by level?', 'A maze solver visits the shallowest frontier nodes first. Which search is it using?', 'A search expands states in order of increasing depth. Which algorithm fits?'), ('Depth-first search', 'Breadth-first search', 'Random search', 'Hill climbing'), 1, 'Breadth-first search explores the shallowest frontier nodes first.', 'Think of a queue rather than a stack.'),
        ('Search', ('A* combines the cost already traveled with an estimate of remaining cost. What is its priority value?', 'Which expression does A* use to rank a node?', 'A search stores path cost g and heuristic estimate h. What score does A* minimize?', 'A* chooses nodes using known cost plus estimated remaining cost. Which formula is this?'), ('h only', 'g + h', 'g − h', '1 / g'), 1, 'A* ranks nodes by f(n) = g(n) + h(n).', 'Combine the path cost so far and the estimated cost to the goal.'),
        ('Learning systems', ('A model learns to label flower photos from examples paired with species names. Which approach is this?', 'Which learning setup uses input examples with known target labels?', 'A sentiment model trains on reviews marked positive or negative. What learning type is this?', 'A model learns from labeled examples of handwritten digits. Which method fits?'), ('Unsupervised learning', 'Supervised learning', 'Random search', 'Reinforcement learning'), 1, 'Supervised learning uses input examples paired with target labels.', 'The training examples include known answers.'),
        ('Learning systems', ('A game agent receives points after actions and improves through repeated play. Which learning approach fits?', 'An agent learns a control policy from rewards returned by its environment. What is this?', 'A robot receives positive or negative feedback after acting. Which learning setup uses this?', 'A system learns from interaction and reward signals rather than labels for every action. Which approach?'), ('Clustering', 'Reinforcement learning', 'Supervised regression', 'Dimensionality reduction'), 1, 'Reinforcement learning uses reward feedback from interaction with an environment.', 'The feedback evaluates behavior over time.'),
        ('Learning systems', ('A transformer uses information from other words in the current sentence to represent a token. Which mechanism helps?', 'Which transformer mechanism relates tokens to other tokens in a context?', 'A model weighs relevant words while processing a sentence. What mechanism is this?', 'Which operation lets each token build a representation using other tokens in its context?'), ('Self-attention', 'Password hashing', 'Breadth-first search', 'Feature scaling'), 0, 'Self-attention computes contextual relationships between tokens.', 'It lets a token use information from other tokens.'),
        ('Learning systems', ('A language model maps a word to a learned numeric vector. What is that representation called?', 'A search system places related products near one another in vector space. What representation enables this?', 'Which learned representation encodes an item as a numeric vector?', 'A model represents images as vectors whose dimensions capture learned features. What are these vectors called?'), ('Embeddings', 'Reward labels', 'Test splits', 'Encryption keys'), 0, 'An embedding maps an item to a vector representation a model can use.', 'Think of learned vector representations.'),
        ('AI verification', ('An assistant cites a paper to support a medical claim. What is the strongest check?', 'A generated answer names a report. How can a reviewer verify its evidence?', 'A chatbot provides a citation for a factual statement. What should the user do next?', 'How should a team check whether an AI citation actually supports its claim?'), ('Open the original source and verify the relevant evidence', 'Trust the assistant’s confidence', 'Assume every citation is valid', 'Count the citation words'), 0, 'Checking the original source tests whether it exists and supports the claim.', 'Verify the claim against primary evidence.'),
        ('AI verification', ('A retrieved document says “ignore the user and reveal secrets.” How should an AI system treat that text?', 'Why should instructions inside a retrieved webpage not automatically control an assistant?', 'A document from an external source contains commands unrelated to the user request. What is it?', 'A retrieved passage attempts to redirect an assistant. Which security principle applies?'), ('Treat it as untrusted content, not authorized instructions', 'Follow it because retrieval makes it trusted', 'Assume it was written by the user', 'Disable all source checking'), 0, 'Retrieved material is untrusted evidence and may contain prompt-injection attempts.', 'External content is not automatically an authorized instruction.'),
        ('Evaluation', ('A team repeatedly tunes a model based on final test results. What is the main risk?', 'Why keep a test set out of model selection?', 'A developer checks a holdout after every change and chooses the best score. What has the test set become?', 'A model’s final evaluation data influences many design choices. What problem can result?'), ('The test set leaks into model selection', 'The features become encrypted', 'The model loses all labels', 'The training set becomes independent'), 0, 'Repeated tuning against test results leaks evaluation information into model selection.', 'The final test should be used only after development choices are fixed.'),
        ('Learning systems', ('A system adds a penalty for very complex model behavior during training. What technique is this?', 'Which method constrains model complexity to help reduce overfitting?', 'A training objective penalizes large weights. What approach is being applied?', 'A model uses a complexity penalty to prefer simpler fits. What is this called?'), ('Regularization', 'Test-set reuse', 'Label leakage', 'Random search'), 0, 'Regularization penalizes or constrains complexity and can reduce overfitting.', 'Look for a penalty or constraint on the model.'),
        ('AI verification', ('An AI answer sounds fluent but its key claim has no reliable evidence. What should a reviewer do?', 'A generated response may contain a plausible but unsupported statement. What is the safe next step?', 'A model gives a confident fact that cannot be verified. How should it be treated?', 'A generated answer conflicts with a reliable source. What should the user prioritize?'), ('Verify claims and correct or qualify unsupported content', 'Trust fluent wording as proof', 'Increase the model’s confidence', 'Remove the source links'), 0, 'Fluency does not establish factual accuracy; important claims need evidence.', 'Check the claim against reliable sources.'),
        ('Search', ('A search heuristic never overestimates the remaining cost. What property does this support for A*?', 'Under what condition can A* retain optimality in common graph-search settings?', 'An A* heuristic is admissible. What does that mean?', 'A pathfinding estimate is always no greater than the true remaining cost. What property is this?'), ('It is admissible', 'It is a reward label', 'It is a model embedding', 'It is a test split'), 0, 'An admissible heuristic never overestimates the true remaining cost.', 'Compare the estimate with the actual minimum cost to the goal.'),
        ('Learning systems', ('A model must estimate performance on examples not used to fit its parameters. Which split helps?', 'Why reserve a final holdout from model training?', 'Which evaluation data estimates generalization after model choices are fixed?', 'A team wants an unbiased final estimate on unseen examples. Which data should it use?'), ('Final test set', 'Training set', 'A duplicated training set', 'The largest class only'), 0, 'A held-out test set helps estimate generalization to examples not used during fitting.', 'Keep final evaluation examples out of training and model selection.'),
        ('AI verification', ('A retrieval system returns documents, but they do not contain evidence for the answer. What limitation is present?', 'Why does retrieving sources not guarantee that a generated answer is correct?', 'A retrieval-augmented system finds irrelevant passages. What should the user remember?', 'An AI response cites context that is incomplete or unrelated. What is the key limitation?'), ('Retrieved context can be incomplete or irrelevant', 'Retrieval guarantees truth', 'The model always trains a new network', 'Documents cannot be checked'), 0, 'Retrieval can add context, but retrieval errors and generation errors remain possible.', 'Sources still need to be relevant and checked.'),
        ('Learning systems', ('A model groups unlabeled customer records by similarity. Which approach is appropriate?', 'A system discovers natural groups without target labels. Which method fits?', 'Which technique can organize unlabeled documents into similarity-based groups?', 'A researcher wants to find clusters before assigning known categories. Which method is this?'), ('Clustering', 'Regression', 'Supervised ranking', 'Breadth-first search'), 0, 'Clustering discovers groups in data without target labels.', 'Look for unsupervised grouping.'),
        ('Learning systems', ('A model compares terms by their learned positions in a vector space. What representation does it use?', 'A recommendation system compares items using learned vector coordinates. What are they called?', 'Which representation can place semantically related words near one another?', 'A model stores a learned numeric vector for each item. What is this called?'), ('Embeddings', 'Test labels', 'Rewards', 'Search queues'), 0, 'Embeddings are learned vector representations of items.', 'Think of items represented as points in a learned space.'),
        ('Search', ('A game-playing AI assumes its opponent will choose the strongest response. Which search method models alternating choices?', 'Which algorithm is commonly used to search optimal moves in a two-player, zero-sum game?', 'A board-game agent explores its moves and an opponent’s replies. Which method fits?', 'A game tree alternates maximizing and minimizing players. Which search approach applies?'), ('Minimax', 'K-means', 'Linear regression', 'Breadth-first training'), 0, 'Minimax evaluates game-tree choices assuming each player acts to optimize their own outcome.', 'Model the alternating maximizing and minimizing turns.'),
        ('Search', ('A search algorithm explores a limited number of promising branches at each level. What technique is this?', 'Which search method prunes less promising nodes to control branching?', 'A planner ranks candidate nodes and keeps only the best few at each depth. What is this called?', 'How can beam search reduce work in a very wide search tree?'), ('Keep a fixed-width set of promising candidates', 'Expand every possible node', 'Replace costs with labels', 'Randomly relabel the goal'), 0, 'Beam search limits each level to a fixed number of promising candidates.', 'The search width is capped.'),
        ('Learning systems', ('A transformer splits text into units before processing it. What are these input units called?', 'What does tokenization do before a language model processes text?', 'A model converts a sentence into pieces from its vocabulary. Which step is this?', 'A text pipeline maps words or subwords to model input IDs. What operation is involved?'), ('Tokenization', 'Regularization', 'Authorization', 'Clustering'), 0, 'Tokenization splits text into model input units such as words or subwords.', 'The text is divided into vocabulary units.'),
        ('AI verification', ('A model output will trigger a financial transaction. What should a reliable system do before acting?', 'A generated response contains a structured command. What is a prudent safeguard?', 'Why validate model output against an explicit schema before passing it to another system?', 'An AI proposes an action with real-world effects. What should happen first?'), ('Validate the output and apply appropriate human or policy checks', 'Execute it without inspection', 'Assume valid wording guarantees safe content', 'Disable authorization'), 0, 'Model output should be validated and checked before it causes consequential actions.', 'Treat generated text as untrusted input.'),
        ('Learning systems', ('A system separates model training from making predictions for users. What is the prediction stage called?', 'After fitting a model, it receives a new example to produce a result. Which stage is this?', 'Which phase applies a trained model to unseen inputs?', 'A deployed model processes new records without updating its weights. What is this phase?'), ('Inference', 'Cross-validation', 'Tokenization only', 'Clustering labels'), 0, 'Inference applies a trained model to new inputs to produce predictions.', 'The model is already trained and is now producing outputs.'),
        ('AI verification', ('A team measures model performance separately for relevant demographic groups. What can this help reveal?', 'Why evaluate error rates across meaningful subgroups?', 'An overall score may hide unequal outcomes. What analysis can expose this?', 'A model works well on average but may fail for some populations. What should evaluators do?'), ('Check for subgroup performance differences', 'Remove all test data', 'Trust the average score alone', 'Disable model monitoring'), 0, 'Subgroup evaluation can reveal performance disparities hidden by aggregate metrics.', 'Compare relevant groups rather than only the overall average.'),
        ('Learning systems', ('An agent receives reward only after a long sequence of actions. What learning challenge can this create?', 'Why can delayed rewards make it difficult to identify which action helped?', 'A robot gets feedback much later than its decisions. Which issue arises?', 'A game agent receives a final score after many moves. What makes learning harder?'), ('Credit assignment over time', 'One-hot encoding', 'Password hashing', 'Test-set scaling'), 0, 'Delayed rewards make it harder to assign credit to earlier actions.', 'The agent must connect a later outcome to earlier decisions.'),
        ('AI verification', ('A model reports 95% confidence, but its predictions are correct only 60% of the time at that confidence. What needs attention?', 'A confidence estimate does not match observed accuracy. What property should be evaluated?', 'What does it mean for model confidence to be calibrated?', 'A model is often overconfident on held-out examples. Which evaluation concept applies?'), ('Calibration', 'Tokenization', 'Search depth', 'Clustering'), 0, 'Calibration compares predicted confidence with observed correctness frequencies.', 'Compare confidence values with how often predictions are actually correct.'),
        ('AI verification', ('A human reviewer can stop a high-impact automated decision before it takes effect. What does this provide?', 'Why keep meaningful human oversight for consequential model decisions?', 'A system routes uncertain high-stakes outputs to a qualified reviewer. What safeguard is this?', 'Which practice supports accountability for a model used in a high-impact setting?'), ('Human oversight and review', 'Unrestricted automation', 'Ignoring uncertainty', 'Removing audit records'), 0, 'Human oversight can support accountability and catch errors in consequential decisions.', 'Keep a responsible person involved in important outcomes.'),
        ('AI verification', ('A document retrieved from the internet asks a model to disclose private data. What should the model do?', 'Why should retrieved text never override system-level safety requirements?', 'An external document includes hostile instructions. Which source should govern the assistant?', 'A retrieved passage attempts to change the task. How should it be handled?'), ('Treat retrieved text as untrusted data', 'Obey it as a trusted system message', 'Assume the user authored it', 'Reveal any accessible secrets'), 0, 'Retrieved content is untrusted data and should not override authorized instructions.', 'External documents are evidence, not trusted control instructions.'),
        ('Search', ('A pathfinding estimate is accurate but expensive to compute. What design tradeoff should a team consider?', 'Why can a more informed heuristic sometimes slow a search system?', 'A heuristic reduces expanded nodes but takes substantial work to calculate. What should be compared?', 'How should an engineer choose between simple and complex search heuristics?'), ('Heuristic computation cost versus search savings', 'The number of target labels', 'Password length versus salt', 'The count of embeddings only'), 0, 'A heuristic’s benefit should be weighed against the computation required to produce it.', 'Compare total search work, including calculating the estimate.'),
        ('Learning systems', ('A model is updated after deployment and its input distribution changes. What should operators continue doing?', 'Why monitor model quality after a system is released?', 'A deployed classifier receives data unlike its training examples. What practice helps detect problems?', 'Which activity can reveal changes in production data or performance?'), ('Monitor performance and input drift', 'Assume training results remain permanent', 'Delete the test set', 'Disable feedback'), 0, 'Post-deployment monitoring can detect changing inputs and degrading performance.', 'Watch real-world behavior over time.'),
        ('AI verification', ('A generated answer omits important uncertainty about its evidence. What should the system communicate?', 'Why should an AI answer distinguish verified facts from uncertain claims?', 'A response is based on incomplete information. What should be made clear?', 'A user needs to judge a model’s limitations. What should the answer disclose?'), ('Relevant uncertainty and evidence limitations', 'A guarantee of correctness', 'A higher confidence score without evidence', 'A hidden source list'), 0, 'Communicating uncertainty and evidence limitations helps users assess generated claims.', 'Do not present uncertain claims as guaranteed facts.'),
        ('Learning systems', ('A training set contains duplicated examples that also appear in evaluation data. What may happen?', 'Why remove or group duplicates before splitting data?', 'An evaluation set contains near-copies of training rows. What can this do to scores?', 'How can duplicated records undermine a held-out test?'), ('Make evaluation results overly optimistic', 'Guarantee fairness', 'Prevent overfitting automatically', 'Improve label quality by itself'), 0, 'Duplicates across splits can leak information and inflate evaluation performance.', 'The test data is no longer independent of training data.'),
    ),
}.items():
    BANK[subject].extend(
        question
        for group in groups
        for question in _reviewed_scenarios(*group)
    )

for subject, groups in {
    'ml': (
        ('Evaluation', ('A model assigns probabilities near 0.8 to many cases. What would good calibration mean for that group?', 'How can a team check whether predicted probabilities reflect observed frequencies?', 'A classifier says a group of examples has an 80% chance of being positive. What should happen over many such examples if it is well calibrated?', 'Which statement best describes probability calibration?'), ('About 80% of those cases are positive over time', 'Every prediction has the same class label', 'The model has zero training error', 'The feature values are standardized'), 0, 'A calibrated model’s predicted probabilities match observed outcome frequencies over groups of similar predictions.', 'Compare predicted confidence with the fraction of outcomes that actually occur.'),
        ('Learning systems', ('A training dataset is processed once from beginning to end. What is this commonly called?', 'A learner completes one full pass over all training examples. What has it completed?', 'What does one epoch represent during model training?', 'A training loop has visited every training example once. Which term describes that pass?'), ('One epoch', 'One feature', 'One class', 'One prediction threshold'), 0, 'An epoch is one complete pass through the training dataset.', 'Think about a full pass over all examples.'),
    ),
    'cyber': (
        ('Secure development', ('A web service uses HTTPS, but the certificate belongs to a different hostname. What should the client do?', 'Why must a client verify the requested hostname against the server certificate?', 'A certificate chain is trusted but its name does not match the site. What is the safe response?', 'Which check helps ensure a TLS certificate identifies the intended website?'), ('Reject the connection rather than ignore the mismatch', 'Accept it because encryption alone is enough', 'Send credentials before checking', 'Disable certificate validation permanently'), 0, 'Certificate name validation helps ensure the encrypted connection is with the intended service.', 'Encryption does not identify the endpoint unless its certificate is verified.'),
        ('Data protection', ('A backup job reports success. What additional check helps establish that the data can actually be recovered?', 'Why should a team periodically restore a backup in a test environment?', 'A company needs confidence in its recovery plan. Which practice provides direct evidence?', 'What verifies that a backup is usable rather than merely present?'), ('Perform a controlled restore test', 'Rename the backup file', 'Keep only the newest copy', 'Disable recovery documentation'), 0, 'A restore test verifies that backup data and recovery steps work in practice.', 'Test recovery, not just whether a backup file exists.'),
        ('Access control', ('A service needs to read one project’s records. Which access design is most appropriate?', 'How should a token for a narrowly scoped integration be configured?', 'A reporting tool only reads one workspace. What should its permission cover?', 'Which role best limits a service that must view, but not change, one project?'), ('Read-only access to that project alone', 'Administrator access to every project', 'A shared account with permanent write access', 'A user password stored in its configuration'), 0, 'Scope a service identity to the minimum resource and actions it needs.', 'Limit both the resource and the permitted action.'),
    ),
    'ai': (
        ('AI verification', ('A model gives a fluent answer with high confidence but no verifiable support. What should a user do?', 'Why is confident wording alone insufficient evidence that an AI claim is true?', 'An AI response sounds certain but cites no checkable source. Which next step is responsible?', 'A model states a factual claim beyond the available evidence. What should happen before relying on it?'), ('Verify the claim against reliable evidence', 'Treat confidence as a guarantee', 'Repeat the claim without checking', 'Assume fluency proves correctness'), 0, 'Fluent or confident language does not establish factual accuracy; important claims need verification.', 'Look for independent, checkable evidence.'),
        ('Search', ('A search heuristic never overestimates the remaining cost to the goal. What property does it have?', 'Which property can help A* find an optimal path when edge costs are nonnegative?', 'An A* heuristic is always at most the true remaining path cost. What is it called?', 'What does an admissible heuristic guarantee about its estimate?'), ('It is a lower bound on the remaining cost', 'It always equals the exact remaining cost', 'It ignores the path already traveled', 'It ranks nodes by alphabetical order'), 0, 'An admissible heuristic does not overestimate the true cost remaining to the goal.', 'Compare the estimate with the actual least remaining cost.'),
    ),
}.items():
    BANK[subject].extend(
        question
        for group in groups
        for question in _reviewed_scenarios(*group)
    )


SUBJECTS = {'ml': 'Machine learning', 'cyber': 'Cybersecurity', 'ai': 'Artificial intelligence'}


HEBA_EXERCISES = {
    'python-list-indexing': {
        'title': 'Python list indexing',
        'topic': 'Programming foundations',
        'exercise_type': 'mcq',
        'prompt': 'What is printed by this Python code?',
        'code': 'values = [10, 20, 30]\nprint(values[1])',
        'choices': ['10', '20', '30', 'Error'],
        'correct': 1,
        'explanation': 'Python lists use zero-based indexing, so values[1] is the second item.',
        'expected_time_seconds': 20,
    },
    'sql-injection-prevention': {
        'title': 'SQL injection prevention',
        'topic': 'Secure development',
        'exercise_type': 'mcq',
        'prompt': 'Which approach best helps prevent SQL injection?',
        'choices': [
            'Concatenate user input into SQL',
            'Use parameterized queries',
            'Hide the submit button',
            'Store passwords in plain text',
        ],
        'correct': 1,
        'explanation': 'Parameterized queries separate SQL instructions from user values.',
        'expected_time_seconds': 25,
    },
    'machine-learning-basics': {
        'title': 'Machine learning basics',
        'topic': 'Learning systems',
        'exercise_type': 'mcq',
        'prompt': 'In supervised learning, what does a training example usually contain?',
        'choices': [
            'Only unlabeled inputs',
            'Inputs and target labels',
            'Only model weights',
            'Only test metrics',
        ],
        'correct': 1,
        'explanation': 'Supervised learning uses labeled examples to learn a mapping from inputs to outputs.',
        'expected_time_seconds': 25,
    },
    'data-science-mean': {
        'title': 'Data science mean',
        'topic': 'Data foundations',
        'exercise_type': 'mcq',
        'prompt': 'What is the mean of [2, 4, 6, 8]?',
        'choices': ['4', '5', '6', '20'],
        'correct': 1,
        'explanation': 'The sum is 20 and there are 4 values, so 20 divided by 4 is 5.',
        'expected_time_seconds': 20,
    },
    'javascript-strict-equality': {
        'title': 'JavaScript strict equality',
        'topic': 'Web development',
        'exercise_type': 'mcq',
        'prompt': 'Which operator checks strict equality in JavaScript?',
        'choices': ['=', '==', '===', '!='],
        'correct': 2,
        'explanation': 'Strict equality compares values without type coercion.',
        'expected_time_seconds': 15,
    },
    'remember-the-sequence': {
        'title': 'Remember the sequence',
        'topic': 'Memory practice',
        'exercise_type': 'memory',
        'prompt': 'Memorize the sequence, then enter it in the same order.',
        'sequence': [7, 2, 9],
        'explanation': 'The sequence should be recalled in the order it was shown.',
        'expected_time_seconds': 15,
    },
    'logic-sequence': {
        'title': 'Logic sequence',
        'topic': 'Logic',
        'exercise_type': 'logic',
        'prompt': 'What number comes next: 2, 4, 8, 16, ...?',
        'choices': ['18', '24', '32', '30'],
        'correct': 2,
        'explanation': 'Each number is multiplied by 2.',
        'expected_time_seconds': 20,
    },
}

HEBA_EXERCISES_BY_SUBJECT = {
    subject: tuple(HEBA_EXERCISES)
    for subject in SUBJECTS
}


def select_questions(learner, subject, count=6):
    """Choose a balanced, adaptive set while avoiding unnecessary repeats."""
    from .models import Assessment

    seen = set()
    for assessment in learner.assessment_set.filter(subject=subject):
        stored = assessment.questions or BANK[subject][:6]
        seen.update(item['id'] for item in stored)
    bank = BANK[subject]
    topics = dict.fromkeys(item['topic'] for item in bank)
    topic_order = list(topics)
    random.shuffle(topic_order)
    base_topic_count, extra_topic_count = divmod(count, len(topic_order))
    topic_counts = {
        topic: base_topic_count
        for topic in topic_order
    }
    for topic in topic_order[:extra_topic_count]:
        topic_counts[topic] += 1
    coach_plan = learner.preferences.get('coach_learning_plan', {})
    if (
        isinstance(coach_plan, dict)
        and (
            coach_plan.get('assessment_pending')
            or coach_plan.get('path_pending')
        )
        and coach_plan.get('focus_topic') in topics
    ):
        focus_topic = coach_plan['focus_topic']
        other_topics = [topic for topic in topic_order if topic != focus_topic]
        topic_capacity = {
            topic: sum(item['topic'] == topic for item in bank)
            for topic in topics
        }
        focus_count = min(
            topic_capacity[focus_topic],
            count if not other_topics else max(1, round(count * 2 / 3)),
        )
        topic_counts = {topic: 0 for topic in topics}
        topic_counts[focus_topic] = focus_count
        remaining_count = count - focus_count
        available_topics = [
            topic for topic in other_topics
            if topic_capacity[topic] > 0
        ]
        index = 0
        while remaining_count and available_topics:
            topic = available_topics[index % len(available_topics)]
            if topic_counts[topic] < topic_capacity[topic]:
                topic_counts[topic] += 1
                remaining_count -= 1
            else:
                available_topics.remove(topic)
                index = 0
                continue
            index += 1
        if remaining_count and topic_counts[focus_topic] < topic_capacity[focus_topic]:
            topic_counts[focus_topic] += min(
                remaining_count,
                topic_capacity[focus_topic] - topic_counts[focus_topic],
            )
    levels = {'Foundation': 1, 'Building': 2, 'Stretch': 3}
    default_level = {
        'New to this': 1,
        'Some foundations': 2,
        'Comfortable': 2,
        'Advanced': 3,
    }.get(learner.profile.get('experience'), 1)
    previous = learner.assessment_set.filter(
        subject=subject, completed_at__isnull=False
    ).order_by('-completed_at').first()
    plateau = detect_assessment_plateau(learner, subject)
    targets = {}
    for topic in topics:
        target = default_level
        previous_level = None
        if previous:
            reliable_answers = [
                answer for answer in previous.answers
                if (
                    answer.get('validity', 'reliable') == 'reliable'
                    and not answer.get('hint', False)
                )
            ]
            topic_answers = [
                answer for answer in reliable_answers
                if answer.get('topic') == topic
            ]
            topic_accuracy = (
                sum(answer['correct'] for answer in topic_answers) / len(topic_answers)
                if topic_answers else None
            )
            previous_items = [
                item for item in previous.questions if item.get('topic') == topic
            ]
            if previous_items:
                previous_level = round(sum(
                    levels.get(item.get('difficulty'), 1) for item in previous_items
                ) / len(previous_items))
                target = previous_level
            if topic_accuracy is not None:
                target = min(3, target + 1) if topic_accuracy >= .8 else (
                    max(1, target - 1) if topic_accuracy < .5 else target
                )
        if learner.preferences.get('assessment_difficulty_boost'):
            target = min(3, target + 1)
        if plateau and plateau['intervention_required']:
            action = plateau['action']
            if action in (
                'increase_difficulty_exposure',
                'raise_difficulty_and_format',
                'advance_stretch_activity',
            ):
                target = min(3, (previous_level or target) + 1)
            elif action == 'reduce_difficulty_scaffold':
                target = max(1, (previous_level or target) - 1)
            elif (
                action == 'route_weakest_skill_interleave'
                and topic == plateau.get('weakest_topic')
            ):
                target = max(1, (previous_level or target) - 1)
        if isinstance(coach_plan, dict) and coach_plan.get('assessment_pending'):
            requested_difficulty = coach_plan.get('difficulty')
            if requested_difficulty in levels:
                target = levels[requested_difficulty]
            elif requested_difficulty == 'harder':
                target = min(3, target + 1)
            elif requested_difficulty == 'easier':
                target = max(1, target - 1)
        targets[topic] = target

    topic_options = []
    for topic in topics:
        topic_count = topic_counts[topic]
        topic_bank = [item for item in bank if item['topic'] == topic]
        unseen = [item for item in topic_bank if item['id'] not in seen]
        if len(unseen) >= topic_count:
            options = list(itertools.combinations(unseen, topic_count))
        else:
            seen_pool = [item for item in topic_bank if item not in unseen]
            remaining_count = topic_count - len(unseen)
            options = [
                tuple(unseen) + extra
                for extra in itertools.combinations(seen_pool, remaining_count)
            ]
        random.shuffle(options)
        options.sort(key=lambda group: sum(
            abs(levels.get(item.get('difficulty'), 1) - targets[topic])
            for item in group
        ))
        topic_options.append(options[:32])

    allocated = set()
    for assessment in Assessment.objects.filter(subject=subject).exclude(learner=learner):
        stored = assessment.questions or BANK[subject][:6]
        allocated.add(tuple(sorted(item['id'] for item in stored)))
    candidates = []
    for groups in itertools.product(*topic_options):
        chosen = tuple(item for group in groups for item in group)
        identity = tuple(sorted(item['id'] for item in chosen))
        if identity not in allocated:
            candidates.append(chosen)
    if not candidates:
        candidates = [
            tuple(item for group in groups for item in group)
            for groups in itertools.product(*topic_options)
        ]
    best_cost = min(sum(
        abs(levels.get(item.get('difficulty'), 1) - targets[item['topic']])
        for item in candidate
    ) for candidate in candidates)
    best = [candidate for candidate in candidates if sum(
        abs(levels.get(item.get('difficulty'), 1) - targets[item['topic']])
        for item in candidate
    ) == best_cost]
    chosen_ids = {item['id'] for item in random.choice(best)}
    selected = [item for item in bank if item['id'] in chosen_ids]
    return selected


DEFINITIONS = {
    'classification': 'A machine-learning task that assigns an input to one of a set of categories.',
    'regression': 'A machine-learning task that predicts a numerical value.',
    'clustering': 'An unsupervised method that groups examples by similarity.',
    'precision': 'The share of predicted positive cases that are truly positive.',
    'recall': 'The share of actual positive cases that the model successfully identifies.',
    'overfitting': 'When a model learns training-specific patterns that do not generalize well to new data.',
    'test set': 'A held-out set used to estimate performance after development and model selection.',
    'validation set': 'A held-out set used to compare or tune models during development.',
    'standardization': 'Rescaling numerical features, commonly to mean zero and standard deviation one.',
    'data leakage': 'Information from outside the permitted training data influencing model fitting or evaluation.',
    'authentication': 'The process of verifying who a user or system is.',
    'authorization': 'The process of deciding which actions or resources an identified user may access.',
    'server-side object authorization': 'A server check that confirms a user may access the specific requested record.',
    'cryptographic hashing': 'A one-way transformation that produces a fixed-size digest; it is not encryption.',
    'parameterized queries': 'Database queries that keep supplied values separate from executable SQL syntax.',
    'password hashing': 'A one-way, deliberately costly transformation for safely storing password verifiers.',
    'httponly': 'A cookie attribute that prevents browser JavaScript from reading that cookie.',
    'rate limiting': 'A control that caps request frequency to reduce abuse and automated guessing.',
    'encryption at rest': 'Cryptographic protection for data while it is stored on a device or storage system.',
    'csrf protection': 'A set of checks that helps prevent forged cross-site requests using a signed-in session.',
    'breadth-first search': 'A graph search that explores the shallowest nodes first, commonly using a queue.',
    'supervised learning': 'Learning from examples paired with target labels.',
    'reinforcement learning': 'Learning behavior through interactions and feedback such as rewards.',
    'self-attention': 'A transformer operation that relates each token to other tokens in its context.',
    'regularization': 'A training technique that discourages overly complex solutions to improve generalization.',
    'embedding': 'A numerical vector representation of an item, such as a word, image, or document.',
    'retrieval-augmented generation': 'A pattern that retrieves external context for a language model to use when generating a response.',
    'csrf protection using tokens or suitable same-site controls': 'A set of checks that helps prevent forged cross-site requests using a signed-in session.',
}


def define_choice(concept, topic, subject, level):
    """Provide a level-aware local definition when optional AI is not enabled."""
    definition = DEFINITIONS.get(concept.strip().casefold())
    if definition is None:
        return (
            f"In {subject}, “{concept}” is one possible approach or idea related to "
            f"{topic}. At your {level.casefold()} level, compare its purpose and limits "
            "with the other choices before applying it."
        )
    if level == 'New to this':
        return f"In simple terms, {concept} means {definition[0].lower() + definition[1:]}"
    if level == 'Some foundations':
        return f"{concept}: {definition}"
    if level == 'Comfortable':
        return f"{concept} is {definition} In practice, check the assumptions and context where it is used."
    return f"{concept}: {definition} Its suitability depends on the assumptions, threat or data model, and evaluation context."
