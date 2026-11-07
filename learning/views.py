import json
import math
import re
import random
from copy import deepcopy
from datetime import date, datetime, timedelta
from statistics import mean, median, pstdev
from django.db import transaction
from django.core import signing
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from .coach import (
    CoachProviderError,
    _ai_line,
    _forecast,
    _is_forecast_request,
    ai_provider_configured,
    forecast_for,
    generate_learning_exercises,
    pinky_engagement_response,
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
    assessment_delivery_audit,
    detect_assessment_plateau,
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
UNSAFE_CYBER_TOPIC_PATTERN = re.compile(
    r'\b(?:exploit|payload|bypass|evasion|evade|malware|ransomware|'
    r'credential theft|steal credentials|crack passwords|brute force|'
    r'weaponize|persistence|privilege escalation)\b',
    re.IGNORECASE,
)
AI_COLLABORATION_WEIGHTS = {
    'clarification': 20,
    'critical_verification': 25,
    'iteration': 20,
    'autonomy': 20,
    'responsible_use': 15,
}


def _dispute_records(learner):
    records = []
    assessments = learner.assessment_set.filter(
        completed_at__isnull=False,
    ).order_by('completed_at', 'id')
    for assessment in assessments.iterator():
        dispute = (assessment.engagement or {}).get('dispute')
        if isinstance(dispute, dict):
            records.append((assessment, dispute))
    return records


def _challenge_rewards(learner):
    critical_credit = 0.0
    gaming_risk = 0.0
    inconsistent_count = 0
    iteration_credit = 0.0
    corroborated_count = 0
    open_disputes = []
    records = _dispute_records(learner)
    for index, (assessment, dispute) in enumerate(records):
        decay = 0.5 ** index
        critical_credit += min(
            6,
            6 * dispute.get('quality', 0),
        ) * decay
        if dispute.get('status') == 'retest_assigned':
            iteration_credit += 5 * decay
        elif dispute.get('status') == 'resolved_by_retest':
            iteration_credit += 10 * decay
        if dispute.get('evidence_status') == 'corroborated':
            corroborated_count += 1
        elif dispute.get('evidence_status') == 'inconsistent':
            inconsistent_count += 1
        if dispute.get('status') in ('under_review', 'retest_assigned'):
            open_disputes.append((assessment, dispute))
    gaming_risk = min(1.0, max(0, inconsistent_count - 1) / 4)
    gaming_penalty = 3 * gaming_risk
    return {
        'critical_verification': round(max(
            0,
            critical_credit - gaming_penalty,
        )),
        'gaming_risk': round(gaming_risk, 3),
        'iteration': round(iteration_credit),
        'challenge_count': len(records),
        'corroborated_count': corroborated_count,
        'open_disputes': open_disputes,
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
    rewards = _challenge_rewards(learner)
    components = dict(ratings)
    if ratings:
        components['critical_verification'] = min(
            100,
            ratings['critical_verification']
            + rewards['critical_verification'],
        )
        components['iteration'] = min(
            100,
            ratings['iteration'] + rewards['iteration'],
        )
        components['autonomy'] = round(
            (ratings['autonomy'] + independence) / 2
        ) if independence is not None else ratings['autonomy']
        score = round(sum(
            components[key] * weight
            for key, weight in AI_COLLABORATION_WEIGHTS.items()
        ) / 100)
    else:
        score = None
    open_disputes = rewards['open_disputes']
    latest_open = open_disputes[-1] if open_disputes else None
    latest_dispute = latest_open[1] if latest_open else {}
    return {
        'score': score,
        'ratings': ratings,
        'components': components,
        'autonomy_evidence': independence,
        'weights': AI_COLLABORATION_WEIGHTS,
        'updated_at': reflection.get('updated_at') if isinstance(reflection, dict) else None,
        'challenge_credit': {
            'critical_verification': rewards['critical_verification'],
            'iteration': rewards['iteration'],
            'challenge_count': rewards['challenge_count'],
        },
        'profile_review': {
            'status': 'under_review' if open_disputes else 'clear',
            'open_disputes': len(open_disputes),
            'latest_assessment_id': (
                open_disputes[-1][0].pk if open_disputes else None
            ),
            'uncertainty_multiplier': round(max(
                (
                    dispute.get('uncertainty_multiplier', 1.0)
                    for _, dispute in open_disputes
                ),
                default=1.0,
            ), 3),
            'affected_items': latest_dispute.get('affected_items', []),
            'affected_questions': [
                index + 1
                for index in latest_dispute.get('affected_indexes', [])
                if type(index) is int
            ],
            'challenge_quality': latest_dispute.get('quality'),
            'credibility': latest_dispute.get('credibility'),
            'item_weight': latest_dispute.get('item_weight'),
            'collaboration_credit': round(
                min(12, 12 * latest_dispute.get('quality', 0))
            ) if latest_open else 0,
            'claim_reliability_prior': learner.preferences.get(
                'claim_reliability_prior',
                0.5,
            ),
            'gaming_risk': rewards['gaming_risk'],
            'recurrent_corroborated_disruptions': (
                rewards['corroborated_count'] >= 3
            ),
            'ai_stance_integrity': 100,
        },
    }


def _pushback_item_indexes(message, answer_count):
    match = re.search(
        r'(?:\b(?:items?|questions?)|الأسئلة|سؤال)\s*#?\s*(\d+)'
        r'(?:\s*(?:-|to|through|à|au|إلى|حتى)\s*#?\s*(\d+))?',
        message,
        re.IGNORECASE,
    )
    if not match:
        text = message.casefold()
        if re.search(r'\bsecond half\b|second part|latter half|النصف الثاني', text):
            return list(range((answer_count + 1) // 2, answer_count))
        if re.search(r'\bfirst half\b|first part|النصف الأول', text):
            return list(range(0, max(1, answer_count // 2)))
        return None
    first = int(match.group(1))
    last = int(match.group(2) or first)
    if last < first:
        first, last = last, first
    if first < 1 or first > answer_count:
        return None
    return [
        index - 1
        for index in range(first, min(last, answer_count) + 1)
        if index > 0
    ]


def _is_assessment_pushback(message):
    text = message.casefold()
    has_dispute = any(term in text for term in (
        'wrong', 'unfair', 'poor', 'doesn’t reflect', "doesn't reflect",
        'not reflect', 'disagree', 'dispute', 'contest', 'challenge',
        'distraction', 'distract',
        'interruption', 'interrupted', 'noisy', 'mistake',
        'internet kept dropping', 'internet dropped', 'connection dropped',
        'wifi dropped', 'disconnect',
        'loud', 'noise', 'cafe', 'café', 'phone rang', 'phone ringing',
        'phone kept ringing',
        'injuste', 'faux', 'mauvais résultat', 'pas juste', 'pas d’accord',
        'contester', 'erreur', 'distrait', 'distraction', 'interruption',
        'bruit', 'غير عادل', 'خطأ', 'نتيجتي', 'درجتي', 'مشتت', 'انقطاع',
        'مقاطعة',
    ))
    has_assessment_context = any(term in text for term in (
        'score', 'result', 'assessment', 'performance', 'question', 'answer',
        'session', 'distraction', 'distract', 'interruption', 'interrupted',
        'noisy', 'loud', 'noise', 'cafe', 'café', 'phone rang',
        'phone ringing', 'phone kept ringing', 'internet', 'connection',
        'wifi', 'disconnect',
        'résultat', 'évaluation', 'performance', 'question', 'réponse',
        'séance', 'score', 'نتيجة', 'النتيجة', 'درجة', 'الدرجة', 'التقييم',
        'الأسئلة',
    ))
    return has_dispute and has_assessment_context


def _is_verification_request(message):
    text = message.casefold()
    return any(term in text for term in (
        'retest', 're-test', 'retake', 'verify', 'verification',
        'fresh check', 'start the check',
        'retester', 'réévaluer', 'vérifier', 'nouvelle vérification',
        'إعادة الاختبار', 'أعد الاختبار', 'تحقق جديد', 'التحقق',
    ))


def _dispute_evidence(assessment, indexes):
    answers = assessment.answers or []
    selected_indexes = (
        list(range(len(answers))) if indexes is None else indexes
    )
    selected = [
        (index, answers[index])
        for index in selected_indexes
        if 0 <= index < len(answers)
    ]
    direct_indexes = []
    signals = []
    evidence_summary = {
        'selected_indexes': selected_indexes,
        'tab_blurred_indexes': [],
        'inactive_gap_indexes': [],
        'minimal_exposure_indexes': [],
        'long_pause_then_burst_indexes': [],
        'response_time_spike_indexes': [],
        'selected_response_times': [
            {
                'index': index,
                'seconds': round(answer['seconds'], 2),
            }
            for index, answer in selected
            if type(answer.get('seconds')) in (int, float)
        ],
        'steady_timing_cv': None,
        'dip_then_recovery': False,
    }
    complete_telemetry = bool(selected)
    for index, answer in selected:
        telemetry = answer.get('telemetry')
        if not isinstance(telemetry, dict):
            complete_telemetry = False
            continue
        if telemetry.get('tab_blurred') or telemetry.get('inactive_gap'):
            direct_indexes.append(index)
            if telemetry.get('tab_blurred'):
                evidence_summary['tab_blurred_indexes'].append(index)
                signals.append('tab_blur')
            if telemetry.get('inactive_gap'):
                evidence_summary['inactive_gap_indexes'].append(index)
                signals.append('inactive_gap')
        if (
            type(telemetry.get('active_ms')) is int
            and telemetry['active_ms'] < 1500
            and telemetry.get('stimulus_seen') is False
        ):
            direct_indexes.append(index)
            evidence_summary['minimal_exposure_indexes'].append(index)
            signals.append('minimal_stimulus_exposure')
        if not all(
            key in telemetry
            for key in ('tab_blurred', 'inactive_gap', 'active_ms')
        ):
            complete_telemetry = False
    evidence_summary['telemetry_available'] = complete_telemetry

    times = _personal_response_times(assessment, [])
    selected_times = [
        answer.get('seconds')
        for _, answer in selected
        if type(answer.get('seconds')) in (int, float)
        and answer['seconds'] > 0
    ]
    if len(times) >= 3:
        time_mean = mean(times)
        time_deviation = pstdev(times)
        for index, answer in selected:
            seconds = answer.get('seconds')
            if (
                type(seconds) in (int, float)
                and time_deviation > 0
                and (seconds - time_mean) / time_deviation > 2
            ):
                direct_indexes.append(index)
                evidence_summary['response_time_spike_indexes'].append(index)
                signals.append('personal_baseline_response_time_spike')
    current_times = [
        answer['seconds']
        for answer in answers
        if type(answer.get('seconds')) in (int, float)
        and answer['seconds'] > 0
    ]
    if len(current_times) >= 4:
        time_baseline = median([
            answer['seconds']
            for answer in answers
            if type(answer.get('seconds')) in (int, float)
            and answer['seconds'] > 0
        ])
        for index, answer in selected:
            seconds = answer.get('seconds')
            next_seconds = (
                answers[index + 1].get('seconds')
                if index + 1 < len(answers)
                else None
            )
            if (
                type(seconds) in (int, float)
                and type(next_seconds) in (int, float)
                and seconds >= max(20, time_baseline * 3)
                and 0 < next_seconds <= time_baseline * 0.5
            ):
                direct_indexes.append(index)
                evidence_summary['long_pause_then_burst_indexes'].append(index)
                signals.append('long_pause_then_burst')
    if direct_indexes:
        return (
            'corroborated',
            sorted(set(direct_indexes)),
            sorted(set(signals)),
            evidence_summary,
        )

    timing_cv = (
        pstdev(selected_times) / mean(selected_times)
        if selected_times and mean(selected_times) > 0
        else None
    )
    if timing_cv is not None:
        evidence_summary['steady_timing_cv'] = round(timing_cv, 3)
    steady_timing = (
        complete_telemetry
        and len(selected_times) >= 4
        and mean(selected_times) > 0
        and timing_cv is not None
        and timing_cv <= 0.15
    )
    if steady_timing:
        return 'inconsistent', [], ['steady_timing'], evidence_summary

    if indexes:
        start, end = min(indexes), max(indexes)
        before = answers[:start]
        window = answers[start:end + 1]
        after = answers[end + 1:]
        if before and window and after:
            before_accuracy = mean(answer['correct'] for answer in before)
            window_accuracy = mean(answer['correct'] for answer in window)
            after_accuracy = mean(answer['correct'] for answer in after)
            if (
                before_accuracy >= 0.7
                and after_accuracy >= 0.7
                and before_accuracy - window_accuracy >= 0.4
            ):
                evidence_summary['dip_then_recovery'] = True
                evidence_summary['accuracy_before'] = round(
                    before_accuracy, 3,
                )
                evidence_summary['accuracy_in_window'] = round(
                    window_accuracy, 3,
                )
                evidence_summary['accuracy_after'] = round(
                    after_accuracy, 3,
                )
                signals.append('dip_then_recovery')
    return 'neutral', [], signals, evidence_summary


def _record_assessment_pushback(assessment, message):
    engagement = assessment.engagement or {}
    existing = engagement.get('dispute')
    if isinstance(existing, dict):
        existing['repeat_count'] = existing.get('repeat_count', 1) + 1
        engagement['dispute'] = existing
        assessment.engagement = engagement
        assessment.save(update_fields=['engagement'])
        return existing

    reported_indexes = _pushback_item_indexes(
        message,
        len(assessment.answers),
    )
    indexes = reported_indexes or []
    (
        evidence_status,
        corroborated_indexes,
        evidence_signals,
        evidence_summary,
    ) = _dispute_evidence(assessment, reported_indexes)
    normalized_message = message.casefold()
    has_specific_reason = any(term in normalized_message for term in (
        'distraction', 'distract', 'interruption', 'interrupted', 'noisy',
        'noise', 'phone rang', 'phone ringing', 'phone kept ringing',
        'notification', 'distrait',
        'bruit', 'distraction', 'interruptions', 'téléphone',
        'internet', 'wifi', 'connection', 'disconnect',
        'مشتت', 'مقاطعة', 'انقطاع', 'ضوضاء', 'هاتف', 'إشعار',
    ))
    cause = (
        'noise' if any(term in normalized_message for term in (
            'noise', 'noisy', 'loud', 'bruit', 'ضوضاء',
        )) else 'phone_notification' if any(term in normalized_message for term in (
            'phone', 'ringing', 'notification', 'téléphone', 'هاتف', 'إشعار',
        )) else 'interruption' if any(term in normalized_message for term in (
            'interruption', 'interrupted', 'interruptions', 'مقاطعة', 'انقطاع',
        )) else 'environmental_distraction'
        if has_specific_reason else 'assessment_validity'
    )
    reason = (
        'environmental_distraction'
        if has_specific_reason
        else 'assessment_validity'
    )
    specificity = 1.0 if indexes and has_specific_reason else (
        0.75 if indexes else 0.0
    )
    constructive = 1.0 if (
        _is_verification_request(message)
        or any(term in message.casefold() for term in (
            'quiet', 'focus mode', 'shorter session', 'different time',
            'less noise', 'quiet place', 'mode concentration',
            'séance plus courte', 'endroit calme', 'moins de bruit',
            'وضع التركيز', 'جلسة أقصر', 'مكان هادئ',
        ))
    ) else 0.0
    calibration = 1.0 if any(term in message.casefold() for term in (
        'partly', 'part of', 'may have', 'might have', 'not entirely',
        'en partie', 'peut-être', 'j’ai aussi', 'قد أكون', 'جزئيًا',
    )) else 0.0
    concedes_responsibility = any(term in normalized_message for term in (
        'partly my fault', 'partly responsible', 'i may have contributed',
        'i might have contributed', 'partly contributed',
        'i was also responsible',
        'en partie ma faute', 'j’ai aussi contribué', 'قد أكون ساهمت',
    ))
    recent = (
        timezone.now() - assessment.completed_at
    ).total_seconds() <= 24 * 60 * 60
    timing = 1.0 if recent else 0.5
    quality = round(
        0.35 * specificity
        + 0.25 * constructive
        + 0.25 * calibration
        + 0.15 * timing,
        3,
    )
    if evidence_status == 'corroborated':
        behavioral_consistency = min(
            1.0,
            0.5 + 0.4 * (
                len(corroborated_indexes) / max(1, len(indexes))
            ) + 0.1 * bool(evidence_summary.get('dip_then_recovery')),
        )
    elif evidence_status == 'inconsistent':
        behavioral_consistency = 0.1
    else:
        behavioral_consistency = min(
            1.0,
            0.5 + 0.1 * bool(evidence_summary.get('dip_then_recovery')),
        )
    claim_prior = assessment.learner.preferences.get(
        'claim_reliability_prior',
        0.5,
    )
    if type(claim_prior) not in (int, float) or not 0 <= claim_prior <= 1:
        claim_prior = 0.5
    credibility = round(
        0.6 * behavioral_consistency + 0.4 * quality,
        3,
    )
    base_uncertainty = round(1 + 0.7 * credibility, 3)
    item_weight = round(1 - 0.5 * credibility, 3)

    for index in indexes:
        if 0 <= index < len(assessment.answers):
            answer = assessment.answers[index]
            answer['update_weight'] = min(
                answer.get('update_weight', 1.0),
                item_weight,
            )
            answer['dispute_reason'] = reason
    engagement.setdefault(
        'engagement_sigma_multiplier',
        engagement.get('sigma_multiplier', 1.0),
    )
    engagement['dispute_sigma_multiplier'] = base_uncertainty
    engagement['sigma_multiplier'] = round(
        engagement['engagement_sigma_multiplier'] * base_uncertainty,
        3,
    )
    dispute = {
        'reason': reason,
        'claim': {
            'cause': cause,
            'window': {
                'first_question': indexes[0] + 1,
                'last_question': indexes[-1] + 1,
            },
            'scope': (
                'total' if len(indexes) == len(assessment.answers)
                else 'partial'
            ),
            'concedes_responsibility': concedes_responsibility,
            'proposes_fix': constructive > 0,
        },
        'affected_indexes': indexes,
        'affected_items': [
            assessment.questions[index].get('id')
            for index in indexes
            if index < len(assessment.questions)
        ],
        'corroborated_indexes': corroborated_indexes,
        'evidence_signals': evidence_signals,
        'evidence': evidence_summary,
        'evidence_status': evidence_status,
        'quality': quality,
        'behavioral_consistency': round(behavioral_consistency, 3),
        'distraction_consistency': round(behavioral_consistency, 3),
        'claim_reliability_prior': claim_prior,
        'credibility': credibility,
        'item_weight': item_weight,
        'status': 'under_review',
        'repeat_count': 1,
        'created_at': timezone.now().isoformat(),
        'base_uncertainty_multiplier': base_uncertainty,
        'uncertainty_multiplier': base_uncertainty,
        'confidence_multiplier': engagement['sigma_multiplier'],
        'clean_sessions_after': 0,
        'point_estimate_unchanged': True,
        'score_penalty': False,
        'ai_stance_integrity': 100,
    }
    engagement['dispute'] = dispute
    assessment.engagement = engagement
    assessment.save(update_fields=['answers', 'engagement'])
    return dispute


def _assessment_pushback_response(assessment, dispute, language='en'):
    messages = {
        'corroborated': {
            'en': 'Some session signals around those questions support your report. I’ve marked this result under review without changing the score; a fresh three-question check is ready when you are.',
            'fr': 'Certains signaux de la séance autour de ces questions vont dans le sens de votre remarque. J’ai placé ce résultat en révision sans modifier le score ; une nouvelle vérification est prête quand vous le souhaitez.',
            'ar': 'تدعم بعض إشارات الجلسة حول هذه الأسئلة ملاحظتك. وضعت النتيجة قيد المراجعة دون تغيير الدرجة؛ ويمكنك بدء تحقق جديد متى أردت.',
        },
        'inconsistent': {
            'en': 'I hear you. The timing record is steady, so I can’t confirm a disruption; I’ve marked the result for review without changing your score. A fresh check can help.',
            'fr': 'Je comprends. Le rythme enregistré est régulier, donc je ne peux pas confirmer une interruption ; j’ai marqué le résultat pour révision sans changer le score. Une nouvelle vérification peut aider.',
            'ar': 'أتفهمك. يبدو إيقاع الإجابة المسجل منتظمًا، لذلك لا أستطيع تأكيد انقطاع؛ وضعت النتيجة قيد المراجعة دون تغيير الدرجة. قد يساعد تحقق جديد.',
        },
        'neutral': {
            'en': 'Thanks for flagging this. I can’t verify the disruption from the available signals, so I’ve marked the result for review without changing your score. A fresh check can help.',
            'fr': 'Merci de l’avoir signalé. Les données disponibles ne permettent pas de vérifier l’interruption ; j’ai marqué le résultat pour révision sans changer le score. Une nouvelle vérification peut aider.',
            'ar': 'شكرًا لتنبيهي. لا تكفي البيانات المتاحة للتحقق من الانقطاع؛ وضعت النتيجة قيد المراجعة دون تغيير الدرجة. قد يساعد تحقق جديد.',
        },
    }
    evidence_status = dispute.get('evidence_status', 'neutral')
    localized = messages.get(evidence_status, messages['neutral'])
    message = localized.get(language, localized['en'])
    if dispute.get('reason') != 'environmental_distraction':
        general_messages = {
            'inconsistent': {
                'en': 'I hear you. I couldn’t confirm the concern in the available session record, so I’ve marked this for review without changing your score. A fresh check can help.',
                'fr': 'Je comprends. Les données de la séance ne permettent pas de confirmer votre remarque ; j’ai marqué le résultat pour révision sans changer le score. Une nouvelle vérification peut aider.',
                'ar': 'أتفهمك. لا يمكن تأكيد ملاحظتك من سجل الجلسة المتاح؛ وضعت النتيجة قيد المراجعة دون تغيير الدرجة. قد يساعد تحقق جديد.',
            },
            'neutral': {
                'en': 'Thanks for challenging this result. The available evidence is not enough to confirm a problem, so I’ve marked it for review without changing your score. A fresh check can help.',
                'fr': 'Merci d’avoir contesté ce résultat. Les éléments disponibles ne suffisent pas à confirmer un problème ; j’ai marqué le résultat pour révision sans changer le score. Une nouvelle vérification peut aider.',
                'ar': 'شكرًا لمراجعة هذه النتيجة. لا تكفي الأدلة المتاحة لتأكيد وجود مشكلة؛ وضعتها قيد المراجعة دون تغيير الدرجة. قد يساعد تحقق جديد.',
            },
            'corroborated': {
                'en': 'Some session signals around those questions support your concern. I’ve marked this result under review without changing your score; a fresh three-question check is ready when you are.',
                'fr': 'Certains signaux de la séance autour de ces questions vont dans le sens de votre remarque. J’ai placé le résultat en révision sans modifier le score ; une nouvelle vérification est prête quand vous le souhaitez.',
                'ar': 'تدعم بعض إشارات الجلسة حول هذه الأسئلة ملاحظتك. وضعت النتيجة قيد المراجعة دون تغيير الدرجة؛ ويمكنك بدء تحقق جديد متى أردت.',
            },
        }
        localized = general_messages.get(evidence_status, general_messages['neutral'])
        message = localized.get(language, localized['en'])
    rewards = _challenge_rewards(assessment.learner)
    if rewards['challenge_count'] >= 3:
        if rewards['corroborated_count'] >= 3:
            message = {
                'en': 'Several sessions show patterns around your reported concerns. A shorter session or a do-not-disturb break may help; your score remains unchanged.',
                'fr': 'Plusieurs séances montrent des tendances autour de vos remarques. Une séance plus courte ou une pause sans notifications peut aider ; votre score ne change pas.',
                'ar': 'تُظهر عدة جلسات أنماطًا حول ملاحظاتك. قد تساعد جلسة أقصر أو استراحة بلا إشعارات؛ درجتك لم تتغير.',
            }.get(language, 'Several sessions show patterns around your reported concerns. A shorter session or a do-not-disturb break may help; your score remains unchanged.')
        elif rewards['corroborated_count'] == 0:
            message = {
                'en': 'We’ve reviewed a few session concerns. I’ve kept each score unchanged; a fresh check can help us compare the evidence together.',
                'fr': 'Nous avons examiné plusieurs préoccupations concernant les séances. Je n’ai modifié aucun score ; une nouvelle vérification peut nous aider à comparer les données.',
                'ar': 'راجعنا عدة ملاحظات حول الجلسات. لم أغيّر أي درجة؛ ويمكن لتحقق جديد أن يساعدنا على مقارنة البيانات معًا.',
            }.get(language, 'We’ve reviewed a few session concerns. I’ve kept each score unchanged; a fresh check can help us compare the evidence together.')
    if (
        evidence_status == 'neutral'
        and not dispute.get('evidence', {}).get('telemetry_available', False)
    ):
        message = {
            'en': (
                'I don’t have session signals to check what happened, so I '
                'won’t treat missing data as evidence against you. I’ve marked '
                'only those questions for review without changing the score; '
                'a short re-check can resolve it.'
            ),
            'fr': (
                'Je n’ai pas de signaux de séance pour vérifier ce qui s’est '
                'passé ; leur absence ne constitue pas une preuve contre vous. '
                'J’ai marqué uniquement ces questions pour révision sans '
                'changer le score ; une courte vérification peut clarifier la situation.'
            ),
            'ar': (
                'لا تتوفر لدي إشارات من الجلسة للتحقق مما حدث، ولن أعدّ غياب '
                'البيانات دليلاً ضدك. وضعت هذه الأسئلة فقط قيد المراجعة دون '
                'تغيير الدرجة؛ ويمكن لتحقق قصير أن يوضح الأمر.'
            ),
        }.get(language, messages['neutral']['en'])
    affected = dispute.get('affected_indexes', [])
    if affected:
        question_range = (
            str(affected[0] + 1)
            if len(affected) == 1
            else f'{affected[0] + 1}–{affected[-1] + 1}'
        )
        transparency = {
            'en': (
                f'I widened uncertainty only for questions {question_range}; '
                'your score and judgment are unchanged.'
            ),
            'fr': (
                f'J’ai élargi l’incertitude uniquement pour les questions '
                f'{question_range} ; votre score et votre évaluation restent inchangés.'
            ),
            'ar': (
                f'وسّعت نطاق عدم اليقين للأسئلة {question_range} فقط؛ '
                'درجتك وتقييمك لم يتغيرا.'
            ),
        }.get(language)
        if transparency:
            message = f'{message} {transparency}'
    return {
        'mode': 'debrief',
        'speaker': 'pinky',
        'source': 'Assessment review',
        'message': message,
        'actions': [{
            'type': 'verification_retest',
            'assessment_id': assessment.pk,
            'label': {
                'en': 'Start a fresh three-question check',
                'fr': 'Commencer une nouvelle vérification',
                'ar': 'ابدأ تحققًا جديدًا من ثلاثة أسئلة',
            }.get(language, 'Start a fresh three-question check'),
        }] if dispute.get('status') in ('under_review', 'retest_assigned') else [],
        'pushback': {
            'event_type': 'pushback',
            'evidence_status': evidence_status,
            'status': dispute.get('status'),
            'D': dispute.get('distraction_consistency', 0.5),
            'Q': dispute.get('quality', 0.0),
            'C': dispute.get('credibility', 0.0),
            'telemetry_available': dispute.get(
                'evidence',
                {},
            ).get('telemetry_available', False),
            'signals': dispute.get('evidence_signals', []),
            'quality': dispute.get('quality'),
            'credibility': dispute.get('credibility'),
            'affected_items': dispute.get('affected_items', []),
            'uncertainty_multiplier': dispute.get(
                'uncertainty_multiplier',
                1.0,
            ),
            'item_weight': (
                min(
                    (
                        answer.get('update_weight', 1.0)
                        for answer in assessment.answers
                        if answer.get('dispute_reason') == dispute.get('reason')
                    ),
                    default=1.0,
                )
            ),
            'point_estimate_unchanged': True,
            'score_penalty': False,
            'ai_stance_integrity': 100,
        },
    }


def _pushback_clarification_response(language='en', closed=False):
    messages = {
        'en': (
            'Which part of the session was affected? A quick re-check can help us verify it.'
            if not closed else
            'I’ll leave the score as it is and won’t adjust uncertainty without a specific question range. You can share the affected questions later.'
        ),
        'fr': (
            'Quelle partie de la séance a été affectée ? Une courte vérification peut nous aider à le confirmer.'
            if not closed else
            'Je laisse le score inchangé et ne modifierai pas l’incertitude sans numéro de question précis. Vous pourrez indiquer les questions concernées plus tard.'
        ),
        'ar': (
            'أي جزء من الجلسة تأثر؟ يمكن لتحقق قصير أن يساعدنا على التأكد.'
            if not closed else
            'سأبقي الدرجة كما هي ولن أعدّل مستوى عدم اليقين دون تحديد الأسئلة. يمكنك ذكر الأسئلة المتأثرة لاحقًا.'
        ),
    }
    return {
        'mode': 'debrief',
        'speaker': 'pinky',
        'source': 'Assessment review',
        'message': messages.get(language, messages['en']),
        'actions': [],
        'pushback': {
            'event_type': 'pushback',
            'status': 'awaiting_window' if not closed else 'not_recorded',
            'point_estimate_unchanged': True,
            'score_penalty': False,
        },
    }


def _latest_open_dispute(learner):
    return next(
        (
            (assessment, dispute)
            for assessment, dispute in reversed(_dispute_records(learner))
            if dispute.get('status') in ('under_review', 'retest_assigned')
        ),
        None,
    )


def _create_verification_retest(learner, assessment_id):
    with transaction.atomic():
        source = learner.assessment_set.select_for_update().filter(
            pk=assessment_id,
            completed_at__isnull=False,
        ).first()
        if not source:
            raise ValueError('This assessment is not available for verification.')
        engagement = source.engagement or {}
        dispute = engagement.get('dispute')
        if not isinstance(dispute, dict) or dispute.get('status') not in (
            'under_review', 'retest_assigned',
        ):
            raise ValueError('This assessment does not have an open review.')
        existing_id = dispute.get('retest_assessment_id')
        if existing_id:
            existing = learner.assessment_set.filter(pk=existing_id).first()
            if existing and not existing.completed_at and not existing.abandoned_at:
                return existing
            if existing and existing.completed_at:
                raise ValueError('This assessment review has already been verified.')
        active = learner.assessment_set.select_for_update().filter(
            completed_at__isnull=True,
            abandoned_at__isnull=True,
        ).first()
        if active:
            raise ValueError('Finish or leave your current assessment before starting a verification check.')
        dispute_indexes = dispute.get('affected_indexes', [])
        target_topics = list(dict.fromkeys(
            source.questions[index].get('topic')
            for index in dispute_indexes
            if (
                type(index) is int
                and 0 <= index < len(source.questions)
                and source.questions[index].get('topic')
            )
        ))
        questions = []
        if target_topics:
            seen = {
                item.get('id')
                for assessment in learner.assessment_set.filter(
                    subject=source.subject,
                )
                for item in assessment.questions
            }
            for topic in target_topics:
                options = [
                    item for item in BANK[source.subject]
                    if item.get('topic') == topic
                    and item.get('id') not in seen
                ]
                if options and len(questions) < 3:
                    question = random.choice(options)
                    questions.append(question)
                    seen.add(question.get('id'))
        if len(questions) < 3:
            fallback = select_questions(
                learner,
                source.subject,
                count=6,
            )
            for question in fallback:
                if (
                    question.get('id') not in {
                        item.get('id') for item in questions
                    }
                    and len(questions) < 3
                ):
                    questions.append(question)
        verification = Assessment.objects.create(
            learner=learner,
            subject=source.subject,
            questions=questions,
            engagement={
                'delivery_audit': assessment_delivery_audit(questions),
                'verification_for': source.pk,
            },
            question_started_at=timezone.now(),
        )
        dispute['status'] = 'retest_assigned'
        dispute['retest_assessment_id'] = verification.pk
        engagement['dispute'] = dispute
        source.engagement = engagement
        source.save(update_fields=['engagement'])
        return verification


def _decay_dispute_uncertainty(learner, completed_assessment):
    clean = (
        all(
            answer.get('validity', 'reliable') == 'reliable'
            for answer in completed_assessment.answers
        )
        and not (completed_assessment.engagement or {}).get('audit')
        and not (completed_assessment.engagement or {}).get('verification_for')
    )
    if not clean:
        return
    previous = learner.assessment_set.filter(
        completed_at__lt=completed_assessment.completed_at,
    ).order_by('-completed_at', '-id')
    for assessment in previous.iterator():
        engagement = assessment.engagement or {}
        dispute = engagement.get('dispute')
        if (
            not isinstance(dispute, dict)
            or dispute.get('status') not in ('under_review', 'retest_assigned')
        ):
            continue
        dispute['clean_sessions_after'] = dispute.get(
            'clean_sessions_after', 0,
        ) + 1
        base = dispute.get('base_uncertainty_multiplier', 1.0)
        dispute['uncertainty_multiplier'] = round(
                1 + (base - 1) * (0.7 ** dispute['clean_sessions_after']),
            3,
        )
        engagement['dispute'] = dispute
        engagement.setdefault(
            'engagement_sigma_multiplier',
            engagement.get('sigma_multiplier', 1.0),
        )
        engagement['sigma_multiplier'] = round(
            engagement['engagement_sigma_multiplier']
            * dispute['uncertainty_multiplier'],
            3,
        )
        assessment.engagement = engagement
        assessment.save(update_fields=['engagement'])


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
    custom_topic = (
        run.steps[0].get('custom_topic')
        if run.steps else None
    )
    focus_topic = (
        run.steps[0].get('heba_topic')
        if run.steps else None
    )
    topic_request = custom_topic or focus_topic
    if custom_topic and (
        not learner.preferences.get('ai') or not ai_provider_configured()
    ):
        raise CoachProviderError(
            'Your requested topic needs AI-generated exercises. Enable AI coaching '
            'in Settings and configure an AI provider, then reopen the learning path.'
        )
    if not learner.preferences.get('ai') or not ai_provider_configured():
        return
    state = run.steps[0].get('heba_exercise') if run.steps else None
    if state and (
        state.get('topic_retargeted')
        or state.get('source') == 'ai'
        or state.get('answers')
    ):
        return

    examples = [
        HEBA_EXERCISES[exercise_id]
        for exercise_id in HEBA_EXERCISES_BY_SUBJECT[run.assessment.subject]
    ]
    topics = (
        [topic_request]
        if topic_request
        else list(dict.fromkeys(item['topic'] for item in examples))
    )
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
        reliable_rows = [
            row for row in rows
            if row.get('validity', 'reliable') == 'reliable'
        ]
        score_rows = reliable_rows or rows
        accuracy = round(100 * mean(r['correct'] for r in score_rows))
        independent = round(100 * mean(r['correct'] and not r['hint'] for r in score_rows))
        calibration = round(100 * (1 - mean((r['confidence'] / 100 - int(r['correct'])) ** 2 for r in score_rows)))
        topics = {
            localize_topic(topic, language): round(
                100 * mean(
                    r['correct'] for r in score_rows if r['topic'] == topic
                )
            )
            for topic in dict.fromkeys(
                q['topic'] for q in items
                if any(r['topic'] == q['topic'] for r in score_rows)
            )
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
        out.update(score=independent, accuracy=accuracy, calibration=calibration, independence=round(100 * mean(not r['hint'] for r in score_rows)), avg_time=round(mean(r['seconds'] for r in score_rows), 1), topics=topics, difficulty=_session_difficulty(items), level=levels.get(language, {}).get(level, level), date=a.completed_at.strftime('%d %b %Y'), completed_at=a.completed_at.isoformat(), reliable_answer_count=len(reliable_rows), validity_total=len(rows), confidence_multiplier=a.engagement.get('sigma_multiplier', 1.0), engagement_audit=a.engagement.get('audit', []), delivery_audit=a.engagement.get('delivery_audit', {}), review=[dict(question_id=q['id'], prompt=q['prompt'], choices=q['choices'], explanation=q['explanation'], difficulty=q.get('difficulty', 'Foundation'), chosen=q['choices'][r['choice']], expected=q['choices'][q['correct']], **r) for q, r in zip(localized_items, rows)])
        dispute = a.engagement.get('dispute')
        if isinstance(dispute, dict):
            out['dispute'] = {
                'status': dispute.get('status'),
                'uncertainty_multiplier': dispute.get(
                    'uncertainty_multiplier', 1.0,
                ),
                'point_estimate_unchanged': True,
                'score_penalty': False,
                'verification_assessment_id': dispute.get(
                    'retest_assessment_id',
                ),
            }
        verification_for = a.engagement.get('verification_for')
        if type(verification_for) is int:
            out['verification_for'] = verification_for
    else:
        item = localized_items[len(a.answers)]
        out['question'] = {
            **{k: item[k] for k in ('topic', 'prompt', 'choices')},
            'requeued': item.get('requeued_from') is not None,
        }
        out['difficulty'] = item.get('difficulty', 'Foundation')
        out['reliable_answer_count'] = sum(
            answer.get('validity', 'reliable') == 'reliable'
            for answer in a.answers
        )
        out['validity_total'] = len(a.answers)
        out['engagement_sigma_multiplier'] = a.engagement.get(
            'sigma_multiplier', 1.0,
        )
        out['delivery_audit'] = a.engagement.get('delivery_audit', {})
        out['engagement_paused'] = a.engagement.get('paused', False)
        if a.engagement.get('current_response'):
            out['engagement_intervention'] = a.engagement['current_response']
        out['hint_used'] = a.hint_used
        if a.hint_used:
            out['hint'] = item['hint']
    return out


def _personal_response_times(assessment, answers):
    baseline = [
        answer['seconds']
        for answer in answers
        if answer.get('validity', 'reliable') == 'reliable'
        and not answer.get('hint')
        and type(answer.get('seconds')) in (int, float)
        and answer['seconds'] > 0
    ]
    previous_assessments = assessment.learner.assessment_set.filter(
        subject=assessment.subject,
        completed_at__isnull=False,
    ).exclude(pk=assessment.pk).order_by('-completed_at')[:3]
    for previous in previous_assessments:
        baseline.extend(
            answer['seconds']
            for answer in previous.answers
            if answer.get('validity', 'reliable') == 'reliable'
            and not answer.get('hint')
            and type(answer.get('seconds')) in (int, float)
            and answer['seconds'] > 0
        )
    return baseline[-30:]


def _engagement_analysis(assessment):
    answers = assessment.answers
    items = assessment.questions or BANK[assessment.subject][:6]
    if len(answers) < 5 or len(answers) > len(items):
        return None

    start = len(answers) - 5
    window = answers[start:]
    questions = items[start:len(answers)]
    if any(answer.get('validity', 'reliable') != 'reliable' for answer in window):
        return None

    baseline = _personal_response_times(assessment, answers[:start])
    personal_median = median(baseline) if len(baseline) >= 3 else None
    fast = []
    skipped = []
    choices = []
    for answer, question in zip(window, questions):
        quick_recall = question.get('quick_recall') is True
        words = re.findall(r"\w+", question.get('prompt', ''))
        reading_floor = len(words) / 4 + 0.5
        seconds = answer['seconds']
        speed_limit = max(2.0, personal_median * 0.1) if personal_median else 2.0
        fast.append(
            not quick_recall
            and not answer.get('hint')
            and (seconds < speed_limit or seconds < reading_floor)
        )
        telemetry = answer.get('telemetry', {})
        skipped.append(
            isinstance(telemetry, dict)
            and type(telemetry.get('active_ms')) is int
            and telemetry['active_ms'] < reading_floor * 1000
            and not telemetry.get('stimulus_seen', False)
        )
        choices.append(answer['choice'])

    if sum(fast) < 3:
        return None
    accuracy = sum(answer['correct'] for answer in window) / len(window)
    if accuracy >= 0.8:
        return None

    options = len(questions[-1].get('choices', [])) or 4
    chance_level = (1 / options) + 0.05
    low_accuracy = accuracy <= chance_level
    same_choice = max(choices.count(choice) for choice in set(choices)) >= 4
    cyclic = len(choices) >= 5 and all(
        (choices[index] + 1) % options == choices[index + 1]
        for index in range(len(choices) - 1)
    )
    low_variance = pstdev(answer['seconds'] for answer in window) <= 0.2
    skipped_engagement = sum(skipped) >= 3
    distraction_context = sum(
        answer.get('telemetry', {}).get('tab_blurred', False)
        or answer.get('telemetry', {}).get('inactive_gap', False)
        for answer in window
    ) >= 2
    fired_signals = [
        name for name, fired in (
            ('chance_level_accuracy', low_accuracy),
            ('repeated_or_cyclic_answers', same_choice or cyclic),
            ('low_variance_timing', low_variance),
            ('skipped_engagement', skipped_engagement),
            ('distraction_context', distraction_context),
        )
        if fired
    ]
    if not fired_signals:
        return None

    pattern = '-'.join('ABCD'[choice] for choice in choices)
    if personal_median:
        rt_ratio = round(mean(answer['seconds'] for answer in window) / personal_median, 3)
    else:
        typical_reading_time = mean(
            len(re.findall(r"\w+", question.get('prompt', ''))) / 4 + 0.5
            for question in questions
        )
        rt_ratio = round(
            mean(answer['seconds'] for answer in window) / typical_reading_time,
            3,
        )
    engagement = assessment.engagement or {}
    interventions = engagement.get('interventions', 0)
    last_intervention_at = engagement.get('last_intervention_at', 0)
    items_since = len(answers) - last_intervention_at
    if interventions >= 3 or items_since < 5:
        return None
    severity = (
        'persistent'
        if engagement.get('windows_flagged', 0) > 0
        and len(answers) - engagement.get('last_window_at', 0) >= 3
        else 'soft'
    )
    affected_indexes = list(range(start, len(answers)))
    event = {
        'event': 'ENGAGEMENT_ANOMALY',
        'severity': severity,
        'flags': fired_signals,
        'evidence': {
            'rt_ratio': rt_ratio,
            'acc': round(accuracy, 3),
            'pattern': pattern,
        },
        'affected_items': [questions[index - start]['id'] for index in affected_indexes],
        'interventions_so_far': interventions,
        'items_since_last_intervention': items_since,
        'user_reply': None,
        'locale': 'en',
    }
    return {
        'event': event,
        'start': start,
        'end': len(answers),
        'fired_signals': fired_signals,
        'accuracy': accuracy,
        'rt_ratio': rt_ratio,
        'pattern': pattern,
    }


def _normally_engaged(answer, question, previous_answers, assessment):
    if question.get('quick_recall') is True:
        return True
    words = re.findall(r"\w+", question.get('prompt', ''))
    reading_floor = len(words) / 4 + 0.5
    baseline = _personal_response_times(assessment, previous_answers)
    speed_limit = max(2.0, median(baseline) * 0.1) if len(baseline) >= 3 else 2.0
    telemetry = answer.get('telemetry', {})
    if (
        answer['seconds'] < reading_floor
        or answer['seconds'] < speed_limit
        or telemetry.get('tab_blurred', False)
        or telemetry.get('inactive_gap', False)
    ):
        return False
    return not (
        type(telemetry.get('active_ms')) is int
        and telemetry['active_ms'] < reading_floor * 1000
        and not telemetry.get('stimulus_seen', False)
    )


def _session_difficulty(items):
    levels = {'Foundation': 1, 'Building': 2, 'Stretch': 3}
    score = round(mean(levels.get(item.get('difficulty'), 1) for item in items))
    return {1: 'Foundation', 2: 'Building', 3: 'Stretch'}[score]


@login_required
def state(request):
    l = learner(request)
    language = l.preferences.get('language', 'en')
    attempts = l.assessment_set.filter(
        abandoned_at__isnull=True,
    ).order_by('-id')
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
    latest_summary = serialize(latest, language) if latest else None
    plateau = (
        detect_assessment_plateau(l, latest.subject, through=latest)
        if latest and latest.completed_at
        else None
    )
    if latest_summary and plateau:
        latest_summary['plateau'] = plateau
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
        'assessment': latest_summary,
        'plateau': plateau,
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
    history = list(LearningPathRun.objects.filter(
        learner=learner,
        assessment__subject=assessment.subject,
    ).exclude(assessment=assessment).order_by('-updated_at')[:12])
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
        'decision_basis': (
            'balanced_sample_insufficient'
            if history
            else 'balanced_baseline'
        ),
        'cognitive_score': cognitive_average,
        'cognitive_attempts': len(cognitive_scores),
        'pedagogical_score': pedagogical_average,
        'pedagogical_attempts': len(pedagogical_scores),
    }
    plateau = detect_assessment_plateau(
        learner,
        assessment.subject,
        through=assessment,
    )
    if plateau and plateau.get('intervention_review'):
        recommendation['intervention_review'] = plateau['intervention_review']
    if plateau and plateau.get('intervention_required'):
        action = plateau['action']
        previous_run = LearningPathRun.objects.filter(
            learner=learner,
            assessment__subject=assessment.subject,
        ).exclude(assessment=assessment).order_by('-updated_at').first()
        previous_first_step = (
            previous_run.steps[0].get('id')
            if previous_run and previous_run.steps
            else None
        )
        previous_type = (
            'cognitive'
            if previous_first_step in COGNITIVE_PATH_STEPS
            else 'pedagogical'
            if previous_first_step in PEDAGOGICAL_PATH_STEPS
            else 'cognitive'
        )
        if action in ('strategy_suggestion', 'reduce_difficulty_scaffold', 'mentor_referral'):
            activity_type = 'pedagogical'
        elif action in ('short_gamified_format', 'advance_stretch_activity', 'increase_difficulty_exposure'):
            activity_type = 'cognitive'
        elif action == 'route_weakest_skill_interleave':
            activity_type = 'pedagogical'
        else:
            activity_type = (
                'pedagogical' if previous_type == 'cognitive' else 'cognitive'
            )
        candidates = (
            COGNITIVE_PATH_STEPS
            if activity_type == 'cognitive'
            else PEDAGOGICAL_PATH_STEPS
        )
        preferred_steps = {
            'strategy_suggestion': 'strategy',
            'reduce_difficulty_scaffold': 'strategy',
            'route_weakest_skill_interleave': 'balance',
            'advance_stretch_activity': 'progressive_matrices',
            'increase_difficulty_exposure': 'progressive_matrices',
            'mentor_referral': 'strategy',
            'short_gamified_format': 'stroop',
        }
        preferred_step = preferred_steps.get(action)
        if action == 'alternate_modality':
            previous_type = (
                'cognitive'
                if previous_first_step in COGNITIVE_PATH_STEPS
                else 'pedagogical'
                if previous_first_step in PEDAGOGICAL_PATH_STEPS
                else 'cognitive'
            )
            activity_type = (
                'pedagogical' if previous_type == 'cognitive' else 'cognitive'
            )
            candidates = (
                COGNITIVE_PATH_STEPS
                if activity_type == 'cognitive'
                else PEDAGOGICAL_PATH_STEPS
            )
        if preferred_step not in candidates:
            preferred_step = None
        if preferred_step:
            recommended_step = preferred_step
        else:
            recommended_step = min(
                candidates,
                key=lambda step_id: (
                    mean(scores_by_step[step_id])
                    if scores_by_step[step_id]
                    else 100
                ),
            )
        recommendation['activity_type'] = activity_type
        recommendation['recommended_step'] = recommended_step
        recommendation['decision_basis'] = (
            'stable_assessment_plateau'
            if plateau['detected']
            else 'challenge_exposure_adjustment'
        )
        recommendation['plateau'] = plateau
        recommendation['format_changed'] = activity_type != previous_type
    elif len(cognitive_scores) >= 3 and pedagogical_scores:
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
        recommendation['decision_basis'] = 'lower_learning_path_average'
    if plateau:
        recommendation['plateau'] = plateau
    coach_plan = learner.preferences.get('coach_learning_plan', {})
    if (
        isinstance(coach_plan, dict)
        and coach_plan.get('path_pending')
        and not coach_plan.get('assessment_pending')
    ):
        activity_type = coach_plan.get('activity_type')
        candidates = (
            COGNITIVE_PATH_STEPS
            if activity_type == 'cognitive'
            else PEDAGOGICAL_PATH_STEPS
            if activity_type == 'pedagogical'
            else ()
        )
        coach_adjustments = {'requested_by_learner': True}
        if candidates:
            recommendation['activity_type'] = activity_type
            recommendation['recommended_step'] = min(
                candidates,
                key=lambda step_id: (
                    mean(scores_by_step[step_id])
                    if scores_by_step[step_id]
                    else 100
                ),
            )
            coach_adjustments['activity_type'] = activity_type
        focus_topic = coach_plan.get('focus_topic')
        if focus_topic in {
            item['topic'] for item in BANK.get(assessment.subject, ())
        }:
            coach_adjustments['focus_topic'] = focus_topic
        if coach_plan.get('difficulty') in (
            'harder',
            'easier',
            'Foundation',
            'Building',
            'Stretch',
        ):
            coach_adjustments['difficulty'] = coach_plan['difficulty']
        recommendation['decision_basis'] = 'learner_requested_adaptation'
        recommendation['coach_adjustments'] = coach_adjustments
    if recommendation['recommended_step']:
        step_scores = scores_by_step[recommendation['recommended_step']]
        recommendation['recommended_step_score'] = (
            round(mean(step_scores)) if step_scores else None
        )
        recommendation['recommended_step_attempts'] = len(step_scores)
    return recommendation


def _coach_assignment_evidence(learner, include_history):
    subject = learner.profile.get('subject')
    if subject not in BANK:
        return None

    assessment = learner.assessment_set.filter(
        subject=subject,
        completed_at__isnull=False,
        abandoned_at__isnull=True,
    ).order_by('-completed_at', '-id').first()
    if assessment is None:
        return None

    run = LearningPathRun.objects.filter(
        learner=learner,
        assessment=assessment,
    ).first()
    if run is None or not run.steps:
        return {
            'assessment_date': assessment.completed_at.strftime('%d %b %Y'),
            'assigned_activity': None,
            'assignment_audit': None,
            'progress_sharing_enabled': include_history,
            'progress_curve': [],
            'note': 'No saved learning-path exercise assignment exists yet.',
        }

    steps = run.steps
    active_step = next(
        (step for step in steps if not step.get('passed')),
        steps[-1],
    )
    first_step = steps[0]
    activity_titles = dict(PATH_STEP_DETAILS)

    def exercise_topic(step):
        practice_question = step.get('practice_question')
        if practice_question:
            return practice_question.get('topic')
        trials = step.get('cognitive_trials') or []
        if trials:
            question_id = trials[0].get('domain_question_id')
            question = next(
                (
                    item for item in BANK[subject]
                    if item.get('id') == question_id
                ),
                None,
            )
            if question:
                return question['topic']
        return None

    assigned_activity = {
        'title': activity_titles.get(active_step['id'], active_step['id']),
        'id': active_step['id'],
        'topic': exercise_topic(active_step),
        'is_first_recommended_activity': active_step is first_step,
        'sequence_number': steps.index(active_step) + 1,
        'sequence_total': len(steps),
    }
    saved_audit = first_step.get('assignment_audit')
    audit = (
        dict(saved_audit)
        if isinstance(saved_audit, dict)
        else {
            'decision_basis': 'not_recorded',
            'first_activity': first_step.get('id'),
            'audit_available': False,
        }
    )

    evidence = {
        'assessment_date': assessment.completed_at.strftime('%d %b %Y'),
        'assigned_activity': assigned_activity,
        'assignment_audit': audit,
        'progress_sharing_enabled': include_history,
        'progress_curve': [],
    }
    if include_history:
        previous_sessions = list(
            learner.assessment_set.filter(
                subject=subject,
                completed_at__isnull=False,
                abandoned_at__isnull=True,
            ).order_by('-completed_at', '-id')[:5]
        )
        curve = []
        for item in reversed(previous_sessions):
            summary = serialize(item, learner.preferences.get('language', 'en'))
            curve.append({
                'date': summary['date'],
                'score': summary['score'],
                'average_response_seconds': summary['avg_time'],
                'topics': summary['topics'],
            })
        evidence['progress_curve'] = curve
        if len(curve) >= 2:
            previous_time = curve[-2]['average_response_seconds']
            latest_time = curve[-1]['average_response_seconds']
            if previous_time > 0:
                evidence['response_time_change_percent'] = round(
                    (previous_time - latest_time) * 100 / previous_time,
                    1,
                )
    else:
        if audit:
            for key in (
                'cognitive_score',
                'cognitive_attempts',
                'pedagogical_score',
                'pedagogical_attempts',
                'recommended_step_score',
                'recommended_step_attempts',
                'plateau',
                'intervention_review',
                'plateau_intervention',
                'item_entropy',
                'assessment_topic_scores',
            ):
                audit.pop(key, None)
            if audit.get('decision_basis') in (
                'lower_learning_path_average',
                'stable_assessment_plateau',
                'challenge_exposure_adjustment',
            ):
                audit['decision_basis'] = 'historical_metrics_not_shared'
        evidence['note'] = (
            'Progress-history sharing is off. Do not cite historical scores, '
            'trends, response times, or learning-path attempt scores.'
        )
    return evidence


def _new_path_cognitive_challenges(
    game_id,
    assessment,
    excluded_question_ids=(),
    trial_count=COGNITIVE_TRIAL_COUNT,
    topic=None,
):
    if topic:
        context = {
            'subject': SUBJECTS[assessment.subject],
            'topic': topic,
            'concept': topic,
            'concepts': list(dict.fromkeys([
                topic,
                *topic.split(),
                SUBJECTS[assessment.subject],
                'evidence',
            ])),
        }
        return [
            new_cognitive_challenge(game_id, context)
            for _ in range(trial_count)
        ]
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


def _generate_topic_path_questions(assessment, topic, language):
    examples = [
        HEBA_EXERCISES[exercise_id]
        for exercise_id in HEBA_EXERCISES_BY_SUBJECT[assessment.subject]
    ]
    questions = []
    for _ in range(2):
        batch = generate_learning_exercises(
            assessment.subject,
            [topic],
            [topic],
            language,
            examples=examples,
        )
        questions.extend(batch)
        examples = [
            {
                **question,
                'title': f'Fresh topic question {index + 1}',
            }
            for index, question in enumerate(batch)
        ]
    unique_prompts = {
        question['prompt'].strip().casefold()
        for question in questions
    }
    if len(questions) < 10 or len(unique_prompts) != len(questions):
        raise CoachProviderError(
            'The AI service did not return enough distinct questions for this '
            'topic. Please try again shortly.'
        )
    return questions[:10]


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


def _dimension_analysis(learner, assessment, language='en'):
    history = list(learner.assessment_set.filter(
        subject=assessment.subject,
        completed_at__isnull=False,
        completed_at__lte=assessment.completed_at or timezone.now(),
    ).order_by('completed_at', 'id'))
    history = history[-5:]
    snapshots = []
    dimension_rows = {}
    ordered_topics = list(dict.fromkeys([
        *(item['topic'] for item in BANK[assessment.subject]),
        *(
            question.get('topic')
            for session in history
            for question in (
                session.questions or BANK[session.subject][:6]
            )
            if question.get('topic')
        ),
    ]))

    for session in history:
        items = session.questions or BANK[session.subject][:6]
        topic_rows = {}
        for index, answer in enumerate(session.answers):
            if (
                answer.get('validity', 'reliable') != 'reliable'
                or index >= len(items)
            ):
                continue
            topic = answer.get('topic') or items[index].get('topic')
            if topic not in ordered_topics:
                continue
            row = {
                'score': int(answer['correct'] and not answer.get('hint', False)),
                'difficulty': items[index].get('difficulty', 'Foundation'),
                'prompt': items[index].get('prompt', ''),
                'telemetry': answer.get('telemetry'),
                'seconds': answer.get('seconds'),
            }
            topic_rows.setdefault(topic, []).append(row)
            dimension_rows.setdefault(topic, []).append(row)
        snapshots.append(topic_rows)

    def estimate(rows):
        if not rows:
            return None
        theta = mean(row['score'] for row in rows)
        count = len(rows)
        sigma2 = max(theta * (1 - theta) / count, 1 / (4 * count * count))
        return theta, sigma2

    available_topics = [
        topic for topic in ordered_topics if dimension_rows.get(topic)
    ]
    estimates = {
        topic: estimate(dimension_rows[topic])
        for topic in available_topics
    }
    dimensions_ranked = sorted(
        available_topics,
        key=lambda topic: estimates[topic][0],
    )
    weakest = dimensions_ranked[0] if dimensions_ranked else None
    strongest = dimensions_ranked[-1] if dimensions_ranked else None
    gap_z = 0.0
    if strongest and weakest and strongest != weakest:
        strong_theta, strong_sigma2 = estimates[strongest]
        weak_theta, weak_sigma2 = estimates[weakest]
        gap_z = (strong_theta - weak_theta) / math.sqrt(
            strong_sigma2 + weak_sigma2
        )

    persistence_sessions = 0
    persistent_pair = None
    for snapshot in reversed(snapshots):
        observed = [
            topic for topic in ordered_topics if snapshot.get(topic)
        ]
        if len(observed) < 2:
            break
        session_estimates = {
            topic: estimate(snapshot[topic])
            for topic in observed
        }
        ranked = sorted(observed, key=lambda topic: session_estimates[topic][0])
        pair = (ranked[-1], ranked[0])
        high, high_variance = session_estimates[pair[0]]
        low, low_variance = session_estimates[pair[1]]
        session_gap = (high - low) / math.sqrt(high_variance + low_variance)
        if abs(session_gap) <= 2 or (
            persistent_pair is not None and pair != persistent_pair
        ):
            break
        persistent_pair = pair
        persistence_sessions += 1
    asymmetry_detected = persistence_sessions >= 2
    has_gap = bool(strongest and weakest and strongest != weakest and gap_z > 2)

    checks = {'ceiling': 'not_checked', 'floor': 'not_checked', 'context': 'not_checked'}
    checks_pass = {'ceiling': False, 'floor': False, 'context': False}
    if has_gap:
        latest = snapshots[-1] if snapshots else {}
        strong_items = latest.get(strongest, [])
        weak_items = latest.get(weakest, [])
        checks_pass['ceiling'] = any(
            row['difficulty'] == 'Stretch' for row in strong_items
        )
        checks['ceiling'] = (
            'stretch_item_present'
            if checks_pass['ceiling']
            else 'probe_upward_before_confirming'
        )
        checks_pass['floor'] = bool(weak_items) and all(
            isinstance(row['telemetry'], dict)
            and type(row['telemetry'].get('active_ms')) is int
            and row['telemetry']['active_ms'] >= (
                len(row['prompt'].split()) / 4 + 0.5
            ) * 1000
            for row in weak_items
        )
        checks['floor'] = (
            'response_format_signals_available'
            if checks_pass['floor']
            else 'try_an_easier_or_clearer_variant'
        )
        attention_sensitive = bool(
            re.search(r'\b(?:attention|focus|alertness)\b', weakest, re.I)
        )
        if attention_sensitive:
            weak_telemetry = [
                row['telemetry'] for row in weak_items
                if isinstance(row['telemetry'], dict)
            ]
            checks_pass['context'] = bool(weak_telemetry) and not any(
                item.get('tab_blurred') or item.get('inactive_gap')
                for item in weak_telemetry
            )
            checks['context'] = (
                'context_signals_reviewed'
                if checks_pass['context']
                else 'context_or_fatigue_signals_uncertain'
            )
        else:
            checks['context'] = 'no_attention_sensitive_dimension'
            checks_pass['context'] = True

    verified = (
        asymmetry_detected
        and has_gap
        and all(checks_pass.values())
    )
    topic_shares = {}
    if len(dimensions_ranked) == 1:
        topic_shares[dimensions_ranked[0]] = 1.0
    elif dimensions_ranked and not has_gap:
        for topic in dimensions_ranked:
            topic_shares[topic] = 1 / len(dimensions_ranked)
    elif dimensions_ranked:
        topic_shares[weakest] = 0.65
        remaining_share = 0.35 / (len(dimensions_ranked) - 1)
        for topic in dimensions_ranked:
            if topic != weakest:
                topic_shares[topic] = remaining_share

    dimensions = []
    difficulty_levels = {
        'Foundation': 1,
        'Building': 2,
        'Stretch': 3,
    }
    for topic in available_topics:
        theta, sigma2 = estimates[topic]
        rows_by_difficulty = {}
        for row in dimension_rows[topic]:
            tier = difficulty_levels.get(row['difficulty'])
            if tier is not None:
                rows_by_difficulty.setdefault(tier, []).append(row['score'])
        success_by_difficulty = {
            tier: mean(scores)
            for tier, scores in rows_by_difficulty.items()
        }
        on_target_tiers = [
            tier for tier, success in success_by_difficulty.items()
            if 0.65 <= success <= 0.75
        ]
        if on_target_tiers:
            target = max(on_target_tiers)
        elif success_by_difficulty and all(
            success > 0.75 for success in success_by_difficulty.values()
        ):
            target = min(3, max(success_by_difficulty) + 1)
        elif success_by_difficulty and all(
            success < 0.65 for success in success_by_difficulty.values()
        ):
            target = max(1, min(success_by_difficulty) - 1)
        else:
            target = min(
                success_by_difficulty,
                key=lambda tier: abs(success_by_difficulty[tier] - 0.7),
            ) if success_by_difficulty else 1
        topic_session_scores = [
            100 * mean(row['score'] for row in snapshot[topic])
            for snapshot in snapshots
            if snapshot.get(topic)
        ]
        topic_plateau = (
            len(topic_session_scores) >= 5
            and max(topic_session_scores) - min(topic_session_scores) <= 5
        )
        level = (
            'advanced' if theta >= 0.8
            else 'intermediate' if theta >= 0.5
            else 'foundational'
        )
        skipped_exposure = any(
            isinstance(row['telemetry'], dict)
            and type(row['telemetry'].get('active_ms')) is int
            and row['telemetry']['active_ms'] < (
                len(row['prompt'].split()) / 4 + 0.5
            ) * 1000
            and not row['telemetry'].get('stimulus_seen', False)
            for row in dimension_rows[topic]
        )
        if topic == strongest and has_gap:
            plan = ['Probe harder items', 'Keep a short maintenance task']
        elif topic == weakest and has_gap:
            plan = ['Use short foundational practice', 'Increase difficulty gradually']
        else:
            plan = ['Continue practice at this topic’s own pace']
        if topic == weakest and skipped_exposure:
            plan = ['Shorten tasks', 'Try a clearer activity format']
        if topic_plateau:
            plan.append('Change activity format or practice strategy')
        topic_forecast = _forecast(topic_session_scores)
        if topic_forecast and topic_forecast['data_sufficiency'] == 'insufficient':
            topic_forecast = {
                'data_sufficiency': 'insufficient',
                'sessions': topic_forecast['sessions'],
                'drivers': topic_forecast['drivers'],
            }
        dimensions.append({
            'name': localize_topic(topic, language),
            'topic_key': topic,
            'theta': round(theta, 3),
            'sigma2': round(sigma2, 5),
            'level': level,
            'difficulty_target': target,
            'target_success_range': [0.65, 0.75],
            'time_share': round(topic_shares.get(topic, 0), 3),
            'plan': plan,
            'provisional': not verified,
            'engagement_signal': (
                'skipped_exposure' if skipped_exposure else 'not_detected'
            ),
            'plateau': {
                'detected': topic_plateau,
                'sessions': len(topic_session_scores),
                'values': [round(value, 1) for value in topic_session_scores],
                'intervention': (
                    'change_format_or_strategy' if topic_plateau else None
                ),
            },
            'forecast': topic_forecast,
        })

    previous_snapshot = snapshots[-2] if len(snapshots) > 1 else {}
    values = []
    uncertainty = []
    previous = []
    for topic in available_topics:
        theta, sigma2 = estimates[topic]
        radius = math.sqrt(sigma2)
        values.append(round(theta * 100, 1))
        uncertainty.append({
            'lower': round(max(0, theta - radius) * 100, 1),
            'upper': round(min(1, theta + radius) * 100, 1),
        })
        prior = estimate(previous_snapshot.get(topic, []))
        previous.append(round(prior[0] * 100, 1) if prior else None)
    target = [70.0] * len(available_topics)
    alt_text = '; '.join(
        f"{item['name']}: {item['theta'] * 100:.1f}%"
        for item in dimensions
    )
    annotation = (
        f"Largest observed gap: {localize_topic(strongest, language)} and "
        f"{localize_topic(weakest, language)}."
        if has_gap else ''
    )
    return {
        'dimensions': dimensions,
        'asymmetry': {
            'detected': asymmetry_detected,
            'gap_z': round(gap_z, 3),
            'persistence_sessions': persistence_sessions,
            'verified': verified,
            'checks': checks,
        },
        'radar': {
            'axes': [localize_topic(topic, language) for topic in available_topics],
            'values': values,
            'uncertainty': uncertainty,
            'previous': previous,
            'target': target,
            'annotation': annotation,
            'alt_text': alt_text,
        },
        'averaged_difficulty_used': False,
        'candidate_gap': has_gap,
        'difficulty_by_topic': {
            topic: item['difficulty_target']
            for topic, item in zip(available_topics, dimensions)
        },
    }


def _create_path_steps(
    assessment,
    learner=None,
    previously_seen_question_ids=(),
    topic_request=None,
    topic_questions=(),
):
    questions = assessment.questions or BANK[assessment.subject][:6]
    dimension_analysis = (
        _dimension_analysis(
            learner,
            assessment,
            learner.preferences.get('language', 'en'),
        )
        if learner is not None
        else {'difficulty_by_topic': {}}
    )
    recommendation = (
        _path_recommendation(learner, assessment)
        if learner is not None
        else {
            'activity_type': 'balanced',
            'recommended_step': None,
            'decision_basis': 'balanced_baseline',
            'cognitive_score': None,
            'cognitive_attempts': 0,
            'pedagogical_score': None,
            'pedagogical_attempts': 0,
        }
    )
    plateau = recommendation.get('plateau')
    assessment_ids = {question.get('id') for question in questions}
    assessment_topics = {question['topic'] for question in questions}
    coach_plan = (
        learner.preferences.get('coach_learning_plan', {})
        if learner is not None else {}
    )
    requested_focus_topic = (
        coach_plan.get('focus_topic')
        if (
            isinstance(coach_plan, dict)
            and coach_plan.get('path_pending')
            and not coach_plan.get('assessment_pending')
        )
        else None
    )
    path_topic = topic_request or requested_focus_topic
    if requested_focus_topic in {
        question['topic'] for question in BANK[assessment.subject]
    }:
        assessment_topics.add(requested_focus_topic)
    available_questions = [
        question for question in BANK[assessment.subject]
        if question.get('id') not in assessment_ids
        and question.get('id') not in previously_seen_question_ids
        and question['topic'] in assessment_topics
    ]
    available_questions.extend(
        question for question in topic_questions
        if question.get('id') not in assessment_ids
        and question.get('id') not in previously_seen_question_ids
    )
    topic_specific_questions = [
        question for question in available_questions
        if question['topic'] == path_topic
    ]
    if path_topic and len(topic_specific_questions) >= 10:
        available_questions = topic_specific_questions
    topic_scores = {
        topic: mean(
            int(answer['correct'] and not answer.get('hint', False))
            for answer in assessment.answers
            if (
                answer['topic'] == topic
                and answer.get('validity', 'reliable') == 'reliable'
            )
        )
        for topic in assessment_topics
        if any(
            answer['topic'] == topic
            and answer.get('validity', 'reliable') == 'reliable'
            for answer in assessment.answers
        )
    }
    prioritized_topics = sorted(
        assessment_topics,
        key=lambda topic: (topic_scores.get(topic, 1), random.random()),
    )
    if plateau and plateau.get('diagnosis') == 'skill_gap_plateau':
        weak_topic = plateau.get('weakest_topic')
        strong_topic = plateau.get('strongest_topic')
        if weak_topic in assessment_topics:
            prioritized_topics = [
                weak_topic,
                *(
                    [strong_topic]
                    if strong_topic in assessment_topics
                    and strong_topic != weak_topic
                    else []
                ),
                *(
                    topic for topic in prioritized_topics
                    if topic not in (weak_topic, strong_topic)
                ),
            ]
    if path_topic in assessment_topics:
        prioritized_topics = [
            path_topic,
            *(
                topic for topic in prioritized_topics
                if topic != path_topic
            ),
        ]
    elif path_topic and any(
        question['topic'] == path_topic for question in available_questions
    ):
        prioritized_topics = [
            path_topic,
            *(topic for topic in prioritized_topics if topic != path_topic),
        ]
    practice_questions = []
    coach_plan = (
        learner.preferences.get('coach_learning_plan', {})
        if learner is not None else {}
    )
    path_difficulty = (
        coach_plan.get('difficulty')
        if (
            isinstance(coach_plan, dict)
            and coach_plan.get('path_pending')
            and not coach_plan.get('assessment_pending')
        )
        else None
    )
    difficulty_levels = {
        'Foundation': 1,
        'Building': 2,
        'Stretch': 3,
    }
    dimension_targets = dict(dimension_analysis['difficulty_by_topic'])
    for topic in assessment_topics:
        if topic not in dimension_targets:
            topic_levels = [
                difficulty_levels.get(question.get('difficulty'), 1)
                for question in questions
                if question.get('topic') == topic
            ]
            dimension_targets[topic] = (
                round(mean(topic_levels)) if topic_levels else 1
            )
        if path_difficulty == 'harder':
            dimension_targets[topic] = min(3, dimension_targets[topic] + 1)
        elif path_difficulty == 'easier':
            dimension_targets[topic] = max(1, dimension_targets[topic] - 1)
        elif path_difficulty in difficulty_levels:
            dimension_targets[topic] = difficulty_levels[path_difficulty]

    def prioritize_path_difficulty(items, topic):
        random.shuffle(items)
        target_level = dimension_targets.get(topic)
        if target_level is not None:
            items.sort(
                key=lambda question: abs(
                    difficulty_levels.get(question.get('difficulty'), 1)
                    - target_level
                )
            )
        return items

    topic_question_pools = {}
    for topic in prioritized_topics:
        topic_question_pools[topic] = prioritize_path_difficulty(
            [
                question
                for question in available_questions
                if question['topic'] == topic
            ],
            topic,
        )
    while len(practice_questions) < 4 and any(topic_question_pools.values()):
        for topic in prioritized_topics:
            if topic_question_pools[topic]:
                practice_questions.append(topic_question_pools[topic].pop())
                if len(practice_questions) == 4:
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
    if dimension_targets:
        random.shuffle(matching_pool)
        matching_pool.sort(
            key=lambda question: abs(
                difficulty_levels.get(question.get('difficulty'), 1)
                - dimension_targets.get(question['topic'], 1)
            )
        )
    if len(matching_pool) < 6:
        raise ValueError(
            'Fresh reviewed questions are temporarily unavailable for this '
            'subject. We will not repeat a question you have already seen.'
        )
    matching_questions = (
        matching_pool[:6]
        if dimension_targets
        else random.sample(matching_pool, k=6)
    )
    reserved_question_ids = (
        set(previously_seen_question_ids)
        | assessment_ids
        | {question['id'] for question in practice_questions}
        | {question['id'] for question in matching_questions}
    )
    steps = []
    now = timezone.now().isoformat()
    path_order = list(PATH_STEP_DETAILS)
    question_for_step = {
        step_id: index
        for index, step_id in enumerate(
            step_id
            for step_id, _ in path_order
            if step_id in ('memory', 'focus', 'balance', 'strategy')
        )
    }
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
            trial_limit = (
                min(COGNITIVE_TRIAL_COUNT, 4)
                if plateau and plateau.get('action') == 'short_gamified_format'
                else COGNITIVE_TRIAL_COUNT
            )
            trial_count = min(
                trial_limit,
                remaining_questions // remaining_games,
            ) if not path_topic else trial_limit
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
                topic=path_topic,
            )
            reserved_question_ids.update(
                trial['domain_question_id']
                for trial in step['cognitive_trials']
                if trial.get('domain_question_id')
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
        if path_topic:
            step['path_topic'] = path_topic
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
            step['match_questions'] = matching_questions
            step['match_order'] = random.sample(range(6), k=6)
        steps.append(step)
    steps[0]['heba_exercise'] = _new_heba_exercise_state(assessment.subject)
    steps[0]['assignment_audit'] = {
        'decision_basis': recommendation['decision_basis'],
        'activity_type': recommendation['activity_type'],
        'recommended_step': recommendation['recommended_step'],
        'coach_adjustments': recommendation.get('coach_adjustments'),
        'cognitive_score': recommendation['cognitive_score'],
        'cognitive_attempts': recommendation['cognitive_attempts'],
        'pedagogical_score': recommendation['pedagogical_score'],
        'pedagogical_attempts': recommendation['pedagogical_attempts'],
        'recommended_step_score': recommendation.get('recommended_step_score'),
        'recommended_step_attempts': recommendation.get(
            'recommended_step_attempts',
            0,
        ),
        'plateau': recommendation.get('plateau'),
        'intervention_review': recommendation.get('intervention_review'),
        'item_entropy': (assessment.engagement or {}).get('delivery_audit'),
        'first_activity': steps[0]['id'],
        'assessment_topic_scores': {
            localize_topic(topic): round(score * 100)
            for topic, score in topic_scores.items()
        },
        'practice_question_topics': {
            step['id']: step['practice_question']['topic']
            for step in steps
            if step.get('practice_question')
        },
    }
    if plateau and plateau.get('intervention_required'):
        steps[0]['assignment_audit']['plateau_intervention'] = {
            'diagnosis': plateau['diagnosis'],
            'action': plateau['action'],
            'escalation_level': plateau.get('escalation_level', 0),
            'scores_before': plateau.get('scores', []),
            'average_score_before': plateau.get('average_score'),
            'difficulty_levels_before': plateau.get('difficulty_levels', []),
            'activity_type': recommendation['activity_type'],
            'recommended_step': recommendation['recommended_step'],
        }
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
        if trials and all(
            trial.get('domain_question_id') or trial.get('domain_context')
            for trial in trials
        ):
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
                topic=step.get('path_topic'),
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
    step_order = {
        step_id: index
        for index, (step_id, _) in enumerate(PATH_STEP_DETAILS)
    }
    ordered_steps = sorted(
        run.steps,
        key=lambda step: step_order.get(step.get('id'), len(step_order)),
    )
    if ordered_steps != run.steps:
        old_first = run.steps[0] if run.steps else {}
        run.steps = ordered_steps
        if run.steps:
            new_first = run.steps[0]
            for key in (
                'assignment_audit',
                'custom_topic',
                'heba_exercise',
                'heba_topic',
                'topic_change_history',
                'retired_cognitive_steps',
                'revised_cognitive_attempts',
            ):
                if key in old_first:
                    new_first.setdefault(key, old_first.pop(key))
            audit = new_first.get('assignment_audit')
            if isinstance(audit, dict):
                audit['first_activity'] = new_first['id']
        changed = True
    if not missing and not changed:
        return
    now = timezone.now().isoformat()
    for game_id in missing:
        path_topic = run.steps[0].get('path_topic') if run.steps else None
        run.steps.append({
            'id': game_id,
            'topic': COGNITIVE_GAME_BY_ID[game_id]['category'],
            'path_topic': path_topic,
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
                topic=path_topic,
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
    run.steps.sort(
        key=lambda step: step_order.get(step.get('id'), len(step_order)),
    )
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
                topic=active.get('path_topic'),
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
            if step.get('path_topic'):
                topic = localize_topic(step['path_topic'], language)
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
                        else localize_topic(
                            active.get('path_topic'),
                            language,
                        )
                        if active.get('path_topic')
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
            match_question_items = active.get('match_questions')
            if match_question_items is None:
                match_question_items = [
                    next(
                        question
                        for question in BANK[run.assessment.subject]
                        if question['id'] == question_id
                    )
                    for question_id in active['match_question_ids']
                ]
            match_questions = [
                localize_question(question, language)
                for question in match_question_items
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
    recommendation = _path_recommendation(run.learner, run.assessment)
    dimension_analysis = _dimension_analysis(
        run.learner,
        run.assessment,
        language,
    )
    if dimension_analysis['candidate_gap']:
        ordered_dimensions = sorted(
            dimension_analysis['dimensions'],
            key=lambda item: item['theta'],
        )
        weak_name = ordered_dimensions[0]['name']
        strong_name = ordered_dimensions[-1]['name']
        if dimension_analysis['asymmetry']['verified']:
            pinky_message = (
                f"Your current profile shows {strong_name} as a relative "
                f"strength, so I’ll keep it stretching. We’ll build {weak_name} "
                "with shorter, foundational steps; each track follows its own "
                "evidence and pace."
            )
        elif dimension_analysis['asymmetry']['detected']:
            pinky_message = (
                f"I see a repeated difference between {strong_name} and "
                f"{weak_name}, but the challenge or context checks are not "
                "complete. I’ll make light, provisional adjustments while we "
                "gather stronger evidence."
            )
        else:
            pinky_message = (
                f"There is an early difference between {strong_name} and "
                f"{weak_name}. I’ll keep the plan provisional, probe the "
                "challenge level, and use short practice to learn more."
            )
    elif dimension_analysis['dimensions']:
        pinky_message = (
            'Your learning profile is still taking shape. I’ll adapt each topic '
            'on its own evidence and keep the plan provisional as we gather more.'
        )
    else:
        pinky_message = (
            'There is not enough reliable topic-level evidence to split your '
            'practice yet, so I’ll keep the current path balanced.'
        )
    assignment_audit = (
        run.steps[0].get('assignment_audit', {})
        if run.steps else {}
    )
    if assignment_audit.get('decision_basis') == 'learner_requested_adaptation':
        recommendation.update(
            decision_basis=assignment_audit['decision_basis'],
            activity_type=assignment_audit['activity_type'],
            recommended_step=assignment_audit['recommended_step'],
            coach_adjustments=assignment_audit.get('coach_adjustments'),
        )
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
        'recommendation': recommendation,
        'dimensions': dimension_analysis['dimensions'],
        'asymmetry': dimension_analysis['asymmetry'],
        'radar': dimension_analysis['radar'],
        'averaged_difficulty_used': False,
        'pinky_message': pinky_message,
    }


def _retarget_learning_path(run, learner, topic, topic_questions=()):
    unfinished = [step for step in run.steps if not step.get('passed')]
    if not unfinished:
        raise ValueError(
            'This learning path is complete. Start a new assessment to create '
            'a new topic-specific path; completed results remain unchanged.'
        )
    if all(step.get('path_topic') == topic for step in unfinished):
        return
    used_ids = _seen_cognitive_question_ids(learner, run.assessment.subject)
    for step in run.steps:
        question = step.get('practice_question', {})
        if question.get('id'):
            used_ids.add(question['id'])
        used_ids.update(step.get('match_question_ids', []))
        used_ids.update(
            trial['domain_question_id']
            for trial in step.get('cognitive_trials', [])
            if trial.get('domain_question_id')
        )

    question_pool = [
        question for question in topic_questions
        if question.get('id') not in used_ids
    ]
    if not question_pool:
        question_pool = [
            question for question in BANK[run.assessment.subject]
            if question['topic'] == topic and question['id'] not in used_ids
        ]
    unique_questions = list({
        question['id']: question for question in question_pool
    }.values())
    if len(unique_questions) < 10:
        raise ValueError(
            'Fresh reviewed questions are temporarily unavailable for this '
            'topic. We will not repeat a question you have already seen.'
        )
    random.shuffle(unique_questions)
    practice_questions = unique_questions[:4]
    matching_questions = unique_questions[4:10]
    now = timezone.now().isoformat()
    topic_history = []
    updated = deepcopy(run.steps)
    first_unfinished_index = next(
        index for index, step in enumerate(updated) if not step.get('passed')
    )
    practice_index = 0
    for index, step in enumerate(updated):
        if step.get('passed'):
            continue
        topic_history.append({
            'step_id': step['id'],
            'topic': step.get('path_topic', step.get('topic')),
            'attempts': step.get('attempts', 0),
            'scores': step.get('scores', []),
        })
        if step['id'] in ('memory', 'focus', 'balance', 'strategy'):
            question = practice_questions[practice_index]
            practice_index += 1
            step['practice_question'] = question
            matching_indexes = [
                question_index
                for question_index, item in enumerate(run.assessment.questions)
                if item['topic'] == topic
            ]
            if matching_indexes:
                step['question_index'] = random.choice(matching_indexes)
            if step['id'] == 'focus':
                choices = list(range(4))
                correct = question['correct']
                cells = [correct] * 3 + [
                    choice for choice in choices if choice != correct
                    for _ in range(2)
                ]
                random.shuffle(cells)
                step['focus_cells'] = cells
        elif step['id'] == 'matching':
            step['match_questions'] = matching_questions
            step['match_question_ids'] = [
                question['id'] for question in matching_questions
            ]
            step['match_order'] = random.sample(range(6), k=6)
            step['practice_question'] = matching_questions[0]
        elif step['id'] in COGNITIVE_GAME_BY_ID:
            trial_count = len(step.get('cognitive_trials', [])) or COGNITIVE_TRIAL_COUNT
            step['cognitive_trials'] = _new_path_cognitive_challenges(
                step['id'],
                run.assessment,
                used_ids,
                trial_count=trial_count,
                topic=topic,
            )
            step['trial_answers'] = []
            step['trial_seconds'] = []
            step.pop('previous_cognitive_question_ids', None)

        step['path_topic'] = topic
        step['passed'] = False
        step['score'] = None
        step['attempts'] = 0
        step['scores'] = []
        step['seconds'] = []
        step['feedback'] = []
        step['started_at'] = now if index == first_unfinished_index else None
        if 'trial_started_at' in step:
            step['trial_started_at'] = (
                now if index == first_unfinished_index else None
            )

    first = updated[0]
    first['custom_topic'] = topic
    if topic in {item['topic'] for item in BANK[run.assessment.subject]}:
        first.pop('custom_topic', None)
        first['heba_topic'] = topic
    else:
        first.pop('heba_topic', None)
    if first.get('heba_exercise') is not None:
        first['heba_exercise']['topic_retargeted'] = True
    first['topic_change_history'] = (
        first.get('topic_change_history', []) + topic_history
    )
    first['assignment_audit'] = first.get('assignment_audit', {})
    coach_adjustments = first['assignment_audit'].get('coach_adjustments') or {}
    coach_adjustments[
        'focus_topic'
        if topic in {item['topic'] for item in BANK[run.assessment.subject]}
        else 'custom_topic'
    ] = topic
    first['assignment_audit']['coach_adjustments'] = coach_adjustments
    run.steps = updated
    run.save(update_fields=['steps', 'updated_at'])


@login_required
@require_POST
def learning_path(request):
    l = learner(request)
    data = payload(request)
    restart_path = data.get('action') == 'restart'
    latest = l.assessment_set.filter(
        completed_at__isnull=False,
    ).order_by('-completed_at', '-id').first()
    if not latest:
        return JsonResponse(
            {'error': 'Complete an assessment before starting your learning path.'},
            status=400,
        )
    existing_run = (
        LearningPathRun.objects.filter(
            learner=l,
            assessment=latest,
        ).first()
        if restart_path else None
    )
    if restart_path and existing_run is None:
        return JsonResponse(
            {'error': 'There is no saved learning path to restart yet.'},
            status=409,
        )
    initial_plan = l.preferences.get('coach_learning_plan', {})
    requested_topic = (
        initial_plan.get('focus_topic') or initial_plan.get('custom_topic')
        if (
            isinstance(initial_plan, dict)
            and initial_plan.get('path_pending')
            and not initial_plan.get('assessment_pending')
        )
        else None
    )
    custom_topic_request = (
        initial_plan.get('custom_topic')
        if (
            isinstance(initial_plan, dict)
            and initial_plan.get('path_pending')
            and not initial_plan.get('assessment_pending')
            and not initial_plan.get('focus_topic')
        )
        else None
    )
    topic_questions = []
    restart_topic = None
    if existing_run is not None:
        restart_topic = (
            existing_run.steps[0].get('custom_topic')
            or existing_run.steps[0].get('heba_topic')
            if existing_run.steps else None
        )
        if restart_topic is None:
            restart_topic = next(
                (
                    step.get('path_topic')
                    for step in existing_run.steps
                    if step.get('path_topic')
                ),
                None,
            )
        saved_questions = {}
        for step in existing_run.steps:
            candidates = [
                step.get('practice_question'),
                *step.get('match_questions', []),
            ]
            for question in candidates:
                if (
                    isinstance(question, dict)
                    and question.get('id')
                    and question.get('topic') == restart_topic
                ):
                    saved_questions[question['id']] = question
        topic_questions = list(saved_questions.values())
        if (
            restart_topic
            and restart_topic not in {
                question['topic'] for question in BANK[latest.subject]
            }
            and len(topic_questions) < 10
        ):
            if not l.preferences.get('ai') or not ai_provider_configured():
                return JsonResponse(
                    {
                        'error': (
                            'Fresh questions for this custom topic are not '
                            'available in the saved path. Enable AI coaching '
                            'to generate a new set, or choose a reviewed topic.'
                        )
                    },
                    status=503,
                )
            try:
                topic_questions = _generate_topic_path_questions(
                    latest,
                    restart_topic,
                    l.preferences.get('language', 'en'),
                )
            except CoachProviderError as exc:
                return JsonResponse({'error': str(exc)}, status=503)
    if not data and custom_topic_request:
        if not l.preferences.get('ai') or not ai_provider_configured():
            return JsonResponse(
                {
                    'error': (
                        'Your requested topic needs AI-generated exercises. '
                        'Enable AI coaching in Settings and configure an AI '
                        'provider, then reopen the learning path.'
                    )
                },
                status=503,
            )
        try:
            topic_questions = _generate_topic_path_questions(
                latest,
                custom_topic_request,
                l.preferences.get('language', 'en'),
            )
        except CoachProviderError as exc:
            return JsonResponse({'error': str(exc)}, status=503)
    try:
        with transaction.atomic():
            l = Learner.objects.select_for_update().get(pk=l.pk)
            run = LearningPathRun.objects.select_for_update().filter(
                learner=l,
                assessment=latest,
            ).first()
            if run is not None:
                _append_cognitive_path_steps(run)
            coach_plan = l.preferences.get('coach_learning_plan', {})
            path_change_pending = bool(
                isinstance(coach_plan, dict)
                and coach_plan.get('path_pending')
                and not coach_plan.get('assessment_pending')
            )
            requested_topic = (
                coach_plan.get('custom_topic') or coach_plan.get('focus_topic')
                if path_change_pending else None
            )
            if restart_path:
                archive = {
                    'restarted_at': timezone.now().isoformat(),
                    'assessment_id': run.assessment_id,
                    'steps': [
                        {
                            'step_id': step['id'],
                            'passed': step.get('passed', False),
                            'score': step.get('score'),
                            'scores': step.get('scores', []),
                            'attempts': step.get('attempts', 0),
                            'seconds': step.get('seconds', []),
                        }
                        for step in run.steps
                    ],
                }
                archived_runs = (
                    run.steps[0].get('archived_path_runs', [])
                    if run.steps else []
                )
                topic = restart_topic or requested_topic
                try:
                    fresh_steps = _create_path_steps(
                        latest,
                        learner=l,
                        previously_seen_question_ids={
                            question['id']
                            for question in latest.questions
                        },
                        topic_request=topic,
                        topic_questions=topic_questions,
                    )
                except ValueError as exc:
                    return JsonResponse({'error': str(exc)}, status=409)
                fresh_steps[0]['archived_path_runs'] = [
                    *archived_runs,
                    archive,
                ]
                fresh_steps[0]['heba_exercise']['topic_retargeted'] = True
                if topic:
                    is_custom_topic = topic not in {
                        item['topic'] for item in BANK[latest.subject]
                    }
                    fresh_steps[0][
                        'custom_topic' if is_custom_topic else 'heba_topic'
                    ] = topic
                    fresh_steps[0]['heba_exercise']['topic_retargeted'] = True
                    adjustments = (
                        fresh_steps[0]['assignment_audit'].get(
                            'coach_adjustments'
                        ) or {}
                    )
                    adjustments[
                        'custom_topic' if is_custom_topic else 'focus_topic'
                    ] = topic
                    fresh_steps[0]['assignment_audit'][
                        'coach_adjustments'
                    ] = adjustments
                run.steps = fresh_steps
                run.save(update_fields=['steps', 'updated_at'])
            elif run is not None and requested_topic:
                try:
                    _retarget_learning_path(
                        run,
                        l,
                        requested_topic,
                        topic_questions=topic_questions,
                    )
                except ValueError as exc:
                    return JsonResponse({'error': str(exc)}, status=409)
                run.refresh_from_db()
            elif run is None:
                steps = _create_path_steps(
                    latest,
                    learner=l,
                    previously_seen_question_ids=(
                        _seen_cognitive_question_ids(l, latest.subject)
                    ),
                    topic_request=requested_topic,
                    topic_questions=topic_questions,
                )
                if requested_topic:
                    is_custom_topic = requested_topic not in {
                        item['topic'] for item in BANK[latest.subject]
                    }
                    steps[0][
                        'custom_topic' if is_custom_topic else 'heba_topic'
                    ] = requested_topic
                    coach_adjustments = (
                        steps[0]['assignment_audit'].get('coach_adjustments')
                        or {}
                    )
                    coach_adjustments[
                        'custom_topic' if is_custom_topic else 'focus_topic'
                    ] = requested_topic
                    steps[0]['assignment_audit']['coach_adjustments'] = (
                        coach_adjustments
                    )
                run = LearningPathRun.objects.create(
                    learner=l,
                    assessment=latest,
                    steps=steps,
                )
            if path_change_pending:
                l.preferences.pop('coach_learning_plan', None)
                l.save(update_fields=['preferences'])
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    if not data or restart_path:
        if not restart_path:
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
                topic=active.get('path_topic'),
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
    active = l.assessment_set.filter(
        completed_at__isnull=True,
        abandoned_at__isnull=True,
    ).first()
    data = payload(request)
    replace_active = data.get('replace_active', False)
    if type(replace_active) is not bool:
        return JsonResponse(
            {'error': 'The assessment replacement choice is invalid.'},
            status=400,
        )
    coach_plan = l.preferences.get('coach_learning_plan', {})
    if active and not replace_active:
        return JsonResponse(serialize(active, l.preferences.get('language', 'en')))
    if replace_active and not (
        isinstance(coach_plan, dict)
        and coach_plan.get('assessment_pending')
    ):
        return JsonResponse({
            'error': (
                'Confirm a learning-plan change before replacing an active '
                'assessment.'
            ),
        }, status=409)
    requested_subject = (
        coach_plan.get('subject')
        if isinstance(coach_plan, dict) and coach_plan.get('assessment_pending')
        else None
    )
    subject = requested_subject or l.profile['subject']
    custom_assessment_topic = (
        coach_plan.get('custom_topic')
        if (
            isinstance(coach_plan, dict)
            and coach_plan.get('assessment_pending')
            and coach_plan.get('start_assessment')
            and not coach_plan.get('focus_topic')
            and coach_plan.get('custom_topic')
        )
        else None
    )
    if custom_assessment_topic:
        if not l.preferences.get('ai') or not ai_provider_configured():
            return JsonResponse({
                'error': (
                    'This focused assessment needs AI coaching enabled and a '
                    'configured AI provider. Your learning plan is saved; '
                    'enable AI coaching or choose a reviewed assessment topic.'
                ),
            }, status=409)
        examples = [
            {
                'title': question['topic'],
                'topic': question['topic'],
                'prompt': question['prompt'],
                'choices': question['choices'],
                'correct': question['correct'],
                'explanation': question['explanation'],
            }
            for question in BANK[subject][:6]
        ]
        try:
            selected = generate_learning_exercises(
                subject,
                [custom_assessment_topic],
                [custom_assessment_topic],
                l.preferences.get('language', 'en'),
                examples=examples,
            )
        except CoachProviderError as exc:
            return JsonResponse({'error': str(exc)}, status=502)
    else:
        selected = select_questions(l, subject)
    if l.preferences.pop('assessment_difficulty_boost', False):
        l.save(update_fields=['preferences'])
    delivery_audit = assessment_delivery_audit(selected)
    if isinstance(coach_plan, dict) and coach_plan.get('assessment_pending'):
        delivery_audit['coach_adjustments'] = {
            key: coach_plan[key]
            for key in ('subject', 'difficulty', 'focus_topic', 'custom_topic')
            if key in coach_plan
        }
    with transaction.atomic():
        if active and replace_active:
            active.abandoned_at = timezone.now()
            active.save(update_fields=['abandoned_at'])
        a = Assessment.objects.create(
            learner=l,
            subject=subject,
            questions=selected,
            engagement={'delivery_audit': delivery_audit},
            question_started_at=timezone.now(),
        )
    if isinstance(coach_plan, dict) and coach_plan.get('assessment_pending'):
        coach_plan['assessment_pending'] = False
        if coach_plan.get('focus_topic'):
            coach_plan.pop('custom_topic', None)
        for key in (
            'subject', 'difficulty', 'focus_topic', 'start_assessment',
        ):
            if key in ('subject', 'start_assessment') or (
                key == 'difficulty' and not coach_plan.get('path_pending')
            ):
                coach_plan.pop(key, None)
        if not coach_plan.get('path_pending'):
            l.preferences.pop('coach_learning_plan', None)
        else:
            l.preferences['coach_learning_plan'] = coach_plan
        l.save(update_fields=['preferences'])
    return JsonResponse(serialize(a, l.preferences.get('language', 'en')))


@login_required
@require_POST
def hint(request):
    l = learner(request)
    a = l.assessment_set.filter(
        completed_at__isnull=True,
        abandoned_at__isnull=True,
    ).first()
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
    telemetry = data.get('telemetry', {})
    if (
        not isinstance(telemetry, dict)
        or set(telemetry) - {
            'active_ms', 'stimulus_seen', 'tab_blurred', 'inactive_gap',
        }
        or (
            'active_ms' in telemetry
            and (
                type(telemetry['active_ms']) is not int
                or not 0 <= telemetry['active_ms'] <= 3600000
            )
        )
        or any(
            name in telemetry and type(telemetry[name]) is not bool
            for name in ('stimulus_seen', 'tab_blurred', 'inactive_gap')
        )
    ):
        return JsonResponse({'error': 'Engagement telemetry is invalid.'}, status=400)
    l = learner(request)
    assessment_id = data.get('assessment_id')
    with transaction.atomic():
        assessments = l.assessment_set.select_for_update()
        if type(assessment_id) is int:
            a = assessments.filter(pk=assessment_id).first()
        else:
            a = assessments.filter(
                completed_at__isnull=True,
                abandoned_at__isnull=True,
            ).first()
        index = data.get('index')
        if not a or type(index) is not int or index < 0:
            return JsonResponse({'error': 'This assessment is no longer available. Refresh to resume.'}, status=409)
        if index < len(a.answers):
            return JsonResponse(serialize(a, l.preferences.get('language', 'en')))
        if a.completed_at or a.abandoned_at or index != len(a.answers):
            return JsonResponse({'error': 'Your saved progress has changed. Refresh to resume.'}, status=409)
        if (a.engagement or {}).get('paused'):
            return JsonResponse(
                {'error': 'Pinky paused the assessment. Choose an option to continue.'},
                status=409,
            )
        items = a.questions or BANK[a.subject][:6]
        if index >= len(items):
            return JsonResponse({'error': 'This assessment is complete. Refresh to review your results.'}, status=409)
        q = items[index]
        now = timezone.now()
        answer_seconds = round((now - a.question_started_at).total_seconds(), 2)
        answer_record = dict(
            choice=choice,
            confidence=confidence,
            correct=choice == q['correct'],
            hint=a.hint_used,
            seconds=answer_seconds,
            topic=q['topic'],
            validity='reliable',
            engagement_prob=1.0,
            update_weight=1.0,
            telemetry=telemetry,
        )
        a.answers = a.answers + [answer_record]
        a.hint_used = False
        a.question_started_at = now

        analysis = _engagement_analysis(a)
        engagement = a.engagement or {}
        engagement.setdefault('sigma_multiplier', 1.0)
        engagement.setdefault(
            'engagement_sigma_multiplier',
            engagement.get('sigma_multiplier', 1.0),
        )
        engagement.setdefault('dispute_sigma_multiplier', 1.0)
        engagement.setdefault('normal_streak', 0)
        if analysis:
            event = analysis['event']
            event['locale'] = l.preferences.get('language', 'en')
            pinky_response = pinky_engagement_response(event)
            if pinky_response['action'] != 'NONE':
                flagged_indexes = list(range(analysis['start'], analysis['end']))
                for flagged_index in flagged_indexes:
                    a.answers[flagged_index].update(
                        validity='low_confidence',
                        engagement_prob=0.2,
                        update_weight=0.2,
                        engagement_flags=analysis['fired_signals'],
                    )
                for flagged_index in flagged_indexes:
                    requeued_question = dict(a.questions[flagged_index])
                    requeued_question['requeued_from'] = flagged_index
                    a.questions.append(requeued_question)
                engagement['engagement_sigma_multiplier'] = round(
                    engagement['engagement_sigma_multiplier'] * 1.5, 3,
                )
                engagement['sigma_multiplier'] = round(
                    engagement['engagement_sigma_multiplier']
                    * engagement['dispute_sigma_multiplier'],
                    3,
                )
                engagement['windows_flagged'] = (
                    engagement.get('windows_flagged', 0) + 1
                )
                engagement['interventions'] = (
                    engagement.get('interventions', 0) + 1
                )
                engagement['last_intervention_at'] = len(a.answers)
                engagement['last_window_at'] = len(a.answers)
                engagement['current_event'] = event
                engagement['current_response'] = pinky_response
                engagement['paused'] = pinky_response['ui']['pause_session']
                engagement['normal_streak'] = 0
                engagement.setdefault('audit', []).append({
                    'affected_indexes': flagged_indexes,
                    'affected_items': event['affected_items'],
                    'flags': analysis['fired_signals'],
                    'evidence': event['evidence'],
                    'sigma_multiplier': engagement['sigma_multiplier'],
                    'ability_estimate_unchanged': True,
                    'score_penalty': False,
                })
                a.engagement = engagement
        else:
            if _normally_engaged(answer_record, q, a.answers[:-1], a):
                engagement['normal_streak'] = engagement.get('normal_streak', 0) + 1
            else:
                engagement['normal_streak'] = 0
            if not engagement.get('paused'):
                engagement.pop('current_event', None)
                engagement.pop('current_response', None)
            if engagement['normal_streak'] >= 3:
                engagement['engagement_sigma_multiplier'] = 1.0
                engagement['sigma_multiplier'] = round(
                    engagement['dispute_sigma_multiplier'],
                    3,
                )
                engagement.pop('current_event', None)
                engagement.pop('current_response', None)
            a.engagement = engagement

        if len(a.answers) == len(a.questions):
            a.completed_at = now
        a.save()
        result = serialize(a, l.preferences.get('language', 'en'))
        if a.completed_at:
            verification_for = a.engagement.get('verification_for')
            if type(verification_for) is int:
                source = l.assessment_set.filter(pk=verification_for).first()
                if source:
                    source_engagement = source.engagement or {}
                    dispute = source_engagement.get('dispute')
                    if (
                        isinstance(dispute, dict)
                        and dispute.get('retest_assessment_id') == a.pk
                    ):
                        affected_indexes = dispute.get(
                            'affected_indexes',
                            [],
                        )
                        original_window = [
                            source.answers[index]
                            for index in affected_indexes
                            if (
                                type(index) is int
                                and 0 <= index < len(source.answers)
                            )
                        ]
                        retest_accuracy = (
                            mean(answer['correct'] for answer in a.answers)
                            if a.answers else 0.0
                        )
                        original_accuracy = (
                            mean(answer['correct'] for answer in original_window)
                            if original_window else None
                        )
                        if original_accuracy is not None:
                            if retest_accuracy >= original_accuracy + 0.25:
                                claim_outcome = 'corroborated'
                                adjustment = 0.1
                            elif retest_accuracy <= original_accuracy:
                                claim_outcome = 'not_corroborated'
                                adjustment = -0.05
                            else:
                                claim_outcome = 'inconclusive'
                                adjustment = 0
                            prior = l.preferences.get(
                                'claim_reliability_prior',
                                0.5,
                            )
                            if type(prior) not in (int, float) or not 0 <= prior <= 1:
                                prior = 0.5
                            l.preferences['claim_reliability_prior'] = round(
                                min(1.0, max(0.0, prior + adjustment)),
                                3,
                            )
                            l.save(update_fields=['preferences'])
                            dispute['claim_outcome'] = claim_outcome
                            dispute['claim_reliability_prior_after'] = (
                                l.preferences['claim_reliability_prior']
                            )
                            dispute['verification_topics'] = list(dict.fromkeys(
                                question.get('topic')
                                for question in a.questions
                                if question.get('topic')
                            ))
                        dispute['status'] = 'resolved_by_retest'
                        dispute['resolved_at'] = now.isoformat()
                        dispute['retest_score'] = result['score']
                        dispute['retest_accuracy'] = round(
                            retest_accuracy,
                            3,
                        )
                        dispute['uncertainty_resolved'] = True
                        source_engagement['dispute'] = dispute
                        source.engagement = source_engagement
                        source.save(update_fields=['engagement'])
                        result['verification'] = {
                            'assessment_id': source.pk,
                            'status': 'resolved_by_retest',
                            'retest_score': result['score'],
                            'latest_verified_result': True,
                            'historical_score_preserved': True,
                        }
            else:
                _decay_dispute_uncertainty(l, a)
            plateau = detect_assessment_plateau(l, a.subject, through=a)
            if plateau:
                result['plateau'] = plateau
        if analysis and a.engagement.get('current_response'):
            result['engagement_intervention'] = a.engagement['current_response']
        return JsonResponse(result)


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
def pushback_retest(request):
    data = payload(request)
    assessment_id = data.get('assessment_id')
    if set(data) != {'assessment_id'} or type(assessment_id) is not int:
        return JsonResponse(
            {'error': 'Choose a valid assessment to verify.'},
            status=400,
        )
    l = learner(request)
    try:
        verification = _create_verification_retest(l, assessment_id)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    return JsonResponse({
        'assessment': serialize(
            verification,
            l.preferences.get('language', 'en'),
        ),
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
def coach_learning_plan(request):
    data = payload(request)
    token = data.get('proposal')
    if not isinstance(token, str) or len(token) > 2000:
        return JsonResponse({'error': 'The learning-plan confirmation is invalid.'}, status=400)
    try:
        proposal = signing.loads(
            token,
            salt='learning.coach-plan',
            max_age=1800,
        )
    except signing.BadSignature:
        return JsonResponse(
            {'error': 'This learning-plan preview expired. Ask the coach again.'},
            status=400,
        )
    l = learner(request)
    if not isinstance(proposal, dict) or proposal.get('user_id') != l.pk:
        return JsonResponse({'error': 'This learning-plan preview is not valid for this account.'}, status=403)
    requested = proposal.get('plan')
    if not isinstance(requested, dict) or not requested:
        return JsonResponse({'error': 'The learning-plan preview has no supported changes.'}, status=400)
    allowed = {
        'subject',
        'difficulty',
        'focus_topic',
        'custom_topic',
        'activity_type',
        'start_assessment',
        'path_only',
    }
    if set(requested) - allowed:
        return JsonResponse({'error': 'The learning-plan preview contains unsupported changes.'}, status=400)
    topic_subject = requested.get(
        'subject',
        l.profile.get('subject'),
    )
    if (
        'subject' in requested
        and requested['subject'] not in SUBJECTS
        or 'start_assessment' in requested
        and requested['start_assessment'] is not True
        or 'path_only' in requested
        and type(requested['path_only']) is not bool
        or 'difficulty' in requested
        and requested['difficulty'] not in (
            'harder',
            'easier',
            'Foundation',
            'Building',
            'Stretch',
        )
        or 'activity_type' in requested
        and requested['activity_type'] not in ('cognitive', 'pedagogical')
        or 'custom_topic' in requested
        and (
            not isinstance(requested['custom_topic'], str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9 &'/-]{1,78}[A-Za-z0-9]",
                requested['custom_topic'],
            )
            or topic_subject == 'cyber'
            and UNSAFE_CYBER_TOPIC_PATTERN.search(requested['custom_topic'])
        )
    ):
        return JsonResponse({'error': 'The learning-plan preview contains an invalid choice.'}, status=400)
    if (
        'focus_topic' in requested
        and requested['focus_topic'] not in {
            item['topic'] for item in BANK.get(topic_subject, ())
        }
    ):
        return JsonResponse({'error': 'That practice topic is not available for this subject.'}, status=400)

    with transaction.atomic():
        l = Learner.objects.select_for_update().get(pk=l.pk)
        plan = l.preferences.get('coach_learning_plan', {})
        if not isinstance(plan, dict):
            plan = {}
        if 'focus_topic' in requested:
            plan.pop('custom_topic', None)
        elif 'custom_topic' in requested:
            plan.pop('focus_topic', None)
        plan.pop('start_assessment', None)
        plan.update({
            key: value
            for key, value in requested.items()
        })
        path_only = requested.get('path_only') is True
        plan['assessment_pending'] = not path_only
        plan['path_pending'] = True
        l.preferences['coach_learning_plan'] = plan
        l.save(update_fields=['preferences'])
    return JsonResponse({
        'message': (
            (
                'Plan confirmed. Unfinished activities will use the requested '
                'topic; completed activities and assessment results stay unchanged.'
            )
            if plan.get('path_only')
            else (
                'Plan confirmed. Start the focused assessment now; any '
                'in-progress assessment will be replaced, while completed '
                'results stay unchanged.'
            )
            if (
                requested.get('start_assessment')
                or any(key in requested for key in (
                    'subject', 'difficulty', 'focus_topic',
                ))
            )
            else (
                'Saved for your next assessment and the next learning path '
                'created from it.'
            )
        ),
        'plan': {
            key: plan[key]
            for key in (
                'subject', 'difficulty', 'focus_topic', 'custom_topic',
                'activity_type',
            )
            if key in plan
        },
    })


def _coach_learning_plan_request(learner, message, conversation=()):
    if _is_forecast_request(message, conversation):
        return None
    normalized = message.casefold()
    open_learning_path = bool(re.search(
        r'\b(?:open|start|begin|go to|take me to)\b.{0,50}'
        r'\b(?:learning path|path)\b',
        normalized,
    ))
    path_topic_request = bool(re.search(
        r'\b(?:learning path|path)\b.{0,70}\b(?:topic|focus)\b'
        r'|\b(?:topic|focus)\b.{0,70}\b(?:learning path|path)\b',
        normalized,
    ))
    follows_custom_topic_question = bool(
        conversation
        and isinstance(conversation[-1], dict)
        and conversation[-1].get('role') == 'assistant'
        and 'what specific cybersecurity concept would you like to learn' in
        conversation[-1].get('content', '').casefold()
    )
    custom_topic_match = re.search(
        r'\b(?:basics?|fundamentals?|introduction)\s+(?:of|to)\s+(.+?)'
        r'(?=\s+\b(?:today|please|for me|at beginner|at basic|at low)\b|[?.!]|$)',
        message,
        re.IGNORECASE,
    )
    if not custom_topic_match:
        custom_topic_match = re.search(
            r'\b(?:learn about|learn|teach me|study|focus on|cover)\s+'
            r'(.+?)(?=\s+\b(?:today|please|for me|at beginner|at basic|at low)\b|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    if not custom_topic_match:
        custom_topic_match = re.search(
            r'\b(?:about|on)\s+(?:the\s+)?(.+?)'
            r'(?=\s+\b(?:today|please|for me|at beginner|at basic|at low|'
            r'with low|with a low)\b|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    if not custom_topic_match:
        custom_topic_match = re.search(
            r'\b(?:assessment|assesment|quiz|test|questions?)\b'
            r'.{0,55}?\b(?:about|on|for|with|focused on)\s+(.+?)'
            r'(?=\s+\b(?:today|please|for me|at beginner|at basic|at low)\b'
            r'|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    if not custom_topic_match:
        custom_topic_match = re.search(
            r'\b(?:topic|focus)\b.{0,40}?\b(?:in|for|of)\s+'
            r'(?:the\s+)?(?:assessment|assesment|quiz|test)\s*'
            r'(?:to|about|on|:|,)\s*(.+?)'
            r'(?=\s+\b(?:today|please|for me|at beginner|at basic|at low)\b'
            r'|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    if not custom_topic_match:
        custom_topic_match = re.search(
            r'\b(?:assessment|assesment|quiz|test)\s*[:,]\s*'
            r'(.+?)(?=\s+\b(?:today|please|for me)\b|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    if not custom_topic_match and path_topic_request:
        custom_topic_match = re.search(
            r'\b(?:topic|focus)\s+(?:in|for)\s+(?:my\s+|the\s+)?'
            r'(?:learning path|path)\s+(?:to|about|on)\s+(.+?)'
            r'(?=\s+\b(?:today|please|for me)\b|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    if not custom_topic_match and path_topic_request:
        custom_topic_match = re.search(
            r'\b(?:topic|focus)\s+(?:to|about|on|of)?\s*'
            r'(.+?)(?=\s+\b(?:today|please|for me|in my path)\b|[?.!]|$)',
            message,
            re.IGNORECASE,
        )
    custom_topic = (
        re.sub(r'\s+', ' ', custom_topic_match.group(1)).strip(" \t\r\n.,!?")
        if custom_topic_match else None
    )
    if follows_custom_topic_question and not custom_topic:
        custom_topic = re.sub(
            r'\s+', ' ', message,
        ).strip(" \t\r\n.,!?")
    if custom_topic:
        generic_plan_topics = {
            'plan', 'study plan', 'learning plan', 'practice plan',
            'study routine', 'roadmap', 'schedule', 'routine',
        }
        if custom_topic.casefold() in generic_plan_topics:
            custom_topic = None
    if custom_topic:
        custom_topic = re.sub(r'^(?:the\s+)?', '', custom_topic, flags=re.IGNORECASE)
        if custom_topic.casefold() in (
            'cybersecurity',
            'cyber security',
            'security',
            'low level',
            'high level',
            'basic level',
            'beginner level',
            'foundation',
            'building',
            'stretch',
            'easy',
            'hard',
            'more examples',
            'worked examples',
            'subject practice',
            'reading practice',
            'cognitive games',
            'games',
            'different question',
            'another question',
            'new question',
        ):
            custom_topic = None
        if (
            custom_topic is not None
            and (
                len(custom_topic) < 3
                or len(custom_topic) > 80
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9 &'/-]*[A-Za-z0-9]",
                    custom_topic,
                )
            )
        ):
            custom_topic = None
    request_cues = (
        'switch',
        'change',
        'changing',
        'set ',
        'choose ',
        'select ',
        'replace',
        'make',
        'assess me on',
        'assessment on',
        'assessment to',
        'exercises on',
        'exercises about',
        'questions on',
        'questions about',
        'practice on',
        'practice about',
        'focus on',
        'work on',
        'study',
        'learn ',
        'want to',
        'want more',
        'start',
        'begin',
        'open',
        'teach me',
        'today',
        'assessment',
        'question',
        'topic',
    )
    subject = None
    if any(cue in normalized for cue in request_cues):
        subject_aliases = {
            'ml': ('machine learning',),
            'cyber': (
                'cybersecurity',
                'cyber security',
                'phishing attack',
                'phishing attacks',
                'phishing',
            ),
            'ai': ('artificial intelligence',),
        }
        for code, aliases in subject_aliases.items():
            if any(alias in normalized for alias in aliases):
                subject = code
                break

    plan = {}
    if subject and subject != learner.profile.get('subject'):
        plan['subject'] = subject
    subject_for_topics = subject or learner.profile.get('subject')
    if (
        custom_topic
        and subject_for_topics == 'cyber'
        and UNSAFE_CYBER_TOPIC_PATTERN.search(custom_topic)
    ):
        return {
            'mode': 'learning',
            'speaker': 'brain',
            'source': 'Cybersecurity learning scope',
            'message': (
                'I can help with defensive cybersecurity learning, such as '
                'awareness, prevention, and safe response. Tell me the specific '
                'protective skill you want to study.'
            ),
            'actions': [],
        }
    assessment_term = r'(?:assessment|assesment|quiz|test)'
    start_assessment = bool(
        re.search(
            rf'\b(?:open|pen|start|begin|launch|take)\b.{{0,80}}\b'
            rf'(?:(?:new|another|fresh)\s+)?'
            rf'{assessment_term}\b',
            normalized,
        )
        or re.search(
            r'\b(?:start|begin|take)\b.{0,50}\b'
            r'(?:a\s+)?(?:different|another|new)\s+questions?\b',
            normalized,
        )
    )
    explicit_level = re.search(
        r'\b(?:level|difficulty)\s*(?:to|at|of|=)?\s*'
        r'(beginner|foundation|basic|low|easy|intermediate|building|medium|'
        r'advanced|stretch|expert|high)\b'
        r'|\b(beginner|foundation|basic|low|easy|intermediate|building|'
        r'medium|advanced|stretch|expert|high)\s+(?:level|difficulty)\b',
        normalized,
    )
    if explicit_level:
        requested_level = explicit_level.group(1) or explicit_level.group(2)
        plan['difficulty'] = {
            'beginner': 'Foundation',
            'foundation': 'Foundation',
            'basic': 'Foundation',
            'low': 'Foundation',
            'easy': 'Foundation',
            'intermediate': 'Building',
            'building': 'Building',
            'medium': 'Building',
            'advanced': 'Stretch',
            'stretch': 'Stretch',
            'expert': 'Stretch',
            'high': 'Stretch',
        }[requested_level]
    elif re.search(r'\b(?:basics?|fundamentals?)\s+of\b', normalized):
        plan['difficulty'] = 'Foundation'
    elif re.search(r'\b(harder|more challenging|challenge me|increase the difficulty)\b', normalized):
        plan['difficulty'] = 'harder'
    elif re.search(r'\b(easier|less difficult|make it simpler|simpler questions)\b', normalized):
        plan['difficulty'] = 'easier'
    if re.search(r'\b(games|game-based|puzzles|cognitive games)\b', normalized):
        plan['activity_type'] = 'cognitive'
    elif re.search(r'\b(worked examples|more examples|subject practice|reading practice)\b', normalized):
        plan['activity_type'] = 'pedagogical'

    topic_names = sorted(
        {item['topic'] for item in BANK.get(subject_for_topics, ())},
        key=lambda topic_name: (-len(topic_name), topic_name.casefold()),
    )
    topic_aliases = {
        'access controls': 'Access control',
        'machine-learning': 'machine learning',
        'data-protection': 'data protection',
    }
    topic_search_text = normalized
    for alias, canonical in topic_aliases.items():
        topic_search_text = topic_search_text.replace(alias, canonical)
    if any(cue in normalized for cue in request_cues):
        for topic_name in topic_names:
            if re.search(
                rf'(?<!\w){re.escape(topic_name.casefold())}(?!\w)',
                topic_search_text,
            ):
                plan['focus_topic'] = topic_name
                break
    cyber_topic_aliases = {
        'phishing': 'Phishing awareness',
        'phishing attack': 'Phishing awareness',
        'phishing attacks': 'Phishing awareness',
        'phishing awareness': 'Phishing awareness',
        'password': 'Password safety',
        'passwords': 'Password safety',
        'password safety': 'Password safety',
        'password security': 'Password safety',
        'encryption': 'Encryption basics',
        'encryption basics': 'Encryption basics',
        'data encryption': 'Encryption basics',
    }
    if subject_for_topics == 'cyber':
        cyber_topic = (
            cyber_topic_aliases.get(custom_topic.casefold())
            if custom_topic else None
        )
        if not cyber_topic and any(cue in normalized for cue in request_cues):
            cyber_topic = next(
                (
                    topic
                    for alias, topic in sorted(
                        cyber_topic_aliases.items(),
                        key=lambda item: -len(item[0]),
                    )
                    if re.search(
                        rf'(?<!\w){re.escape(alias)}(?!\w)',
                        normalized,
                    )
                ),
                None,
            )
        if cyber_topic in topic_names:
            plan['focus_topic'] = cyber_topic
            custom_topic = None
            if follows_custom_topic_question and plan.get('difficulty') is None:
                plan['difficulty'] = 'Foundation'
    topic_learning_intent = bool(re.search(
        r'\b(?:learn|teach|study|basics?|fundamentals?|today)\b',
        normalized,
    ))
    if (
        topic_learning_intent
        and (subject == 'cyber' or learner.profile.get('subject') == 'cyber')
        and not plan.get('focus_topic')
        and not custom_topic
    ):
        return {
            'mode': 'learning',
            'speaker': 'brain',
            'source': 'Choose today’s focus',
            'message': (
                'What specific cybersecurity concept would you like to learn '
                'today? Choose a reviewed topic such as phishing awareness, '
                'password safety, encryption basics, access control, data '
                'protection, or secure development.'
            ),
            'actions': [],
        }
    if custom_topic and custom_topic.casefold() not in {
        topic.casefold() for topic in topic_names
    }:
        plan['custom_topic'] = custom_topic
        if plan.get('difficulty') is None and (
            follows_custom_topic_question
            or re.search(
                r'\b(?:basics?|fundamentals?|beginner|basic|low level)\b',
                normalized,
            )
        ):
            plan['difficulty'] = 'Foundation'
    custom_assessment_request = bool(
        custom_topic
        and re.search(
            r'\b(?:assessment|assesment|quiz|test|questions?)\b',
            normalized,
        )
    )
    if custom_assessment_request:
        start_assessment = True
    if start_assessment and plan:
        plan['start_assessment'] = True
    if not plan:
        generic_study_plan_request = bool(re.search(
            r'\b(?:make a study plan|study plan|learning plan|practice plan|'
            r'next step|what should i do)\b',
            normalized,
        ))
        is_content_request = bool(re.search(
            r'\b(?:topic|content|exercise|question|practice|learn|teach|'
            r'basics?|fundamentals?|today|cybersecurity|cyber\s+security)\b',
            normalized,
        ))
        if generic_study_plan_request and not is_content_request:
            return None
        if not is_content_request:
            return None
        if subject_for_topics == 'cyber' or learner.profile.get('subject') == 'cyber':
            return {
                'mode': 'learning',
                'speaker': 'brain',
                'source': 'Choose today’s focus',
                'message': (
                    'What specific cybersecurity concept would you like to learn '
                    'today? Choose a reviewed topic such as phishing awareness, '
                    'password safety, encryption basics, access control, data '
                    'protection, or secure development.'
                ),
                'actions': [],
            }
        available_topics = ', '.join(topic_names)
        return {
            'mode': 'learning',
            'speaker': 'brain',
            'source': 'Reviewed exercise library',
            'message': (
                'I can change the next assessment to a topic from the reviewed '
                f'{SUBJECTS.get(subject_for_topics, "learning")} exercise library. '
                f'Available topics are: {available_topics}. '
                'Tell me the topic you want. I can select from reviewed exercises, '
                'but I cannot invent or replace their answer keys.'
            ),
            'actions': [],
        }

    labels = {
        'subject': f"subject: {SUBJECTS[plan['subject']]}" if 'subject' in plan else None,
        'difficulty': f"{plan['difficulty']} questions" if 'difficulty' in plan else None,
        'focus_topic': f"exercise topic: {plan['focus_topic']}" if 'focus_topic' in plan else None,
        'custom_topic': f"today's focus: {plan['custom_topic']}" if 'custom_topic' in plan else None,
        'activity_type': (
            f"{plan['activity_type']} learning-path activities"
            if 'activity_type' in plan else None
        ),
    }
    summary = ', '.join(value for value in labels.values() if value)
    navigate_to_assessment = start_assessment or bool(re.search(
        r'\b(?:open|pen|go to|take me to|navigate to|show me|bring me to|visit)'
        r'\s+(?:the\s+)?(?:assessment|assesment|quiz|test)\b',
        normalized,
    ))
    if path_topic_request and not navigate_to_assessment and (
        plan.get('focus_topic') or plan.get('custom_topic')
    ):
        plan['path_only'] = True
    else:
        plan.pop('path_only', None)
    token = signing.dumps(
        {'user_id': learner.pk, 'plan': plan},
        salt='learning.coach-plan',
    )
    starts_assessment = bool(
        navigate_to_assessment
        or plan.get('start_assessment')
        or any(key in plan for key in ('subject', 'difficulty'))
        or 'focus_topic' in plan and not path_topic_request
        or 'custom_topic' in plan and not path_topic_request
    )
    return {
        'mode': 'learning',
        'speaker': 'brain',
        'source': 'Learning plan preview',
        'message': (
            (
                f"I can prepare a six-question assessment focused on "
                f"{plan['custom_topic']}"
                + (
                    f" at {plan['difficulty']} level"
                    if plan.get('difficulty') else ''
                )
                + ' and use it to shape your next learning '
                'path. This needs AI coaching enabled and a configured provider. '
                if 'custom_topic' in plan and plan.get('start_assessment')
                else (
                    f"I can prepare a focused AI lesson about "
                    f"{plan['custom_topic']} for your next learning path. "
                    'This needs AI coaching enabled and a configured provider. '
                    if 'custom_topic' in plan
                    else f"I can select reviewed exercises for your next session with {summary}. "
                )
            )
            + (
                (
                    'Applying this plan starts a new assessment and replaces any '
                    'in-progress assessment. Completed results stay unchanged. '
                    'Apply now?'
                )
                if starts_assessment else
                (
                    'This updates the topic in your current learning path without '
                    'replacing your assessment. Completed activities stay as they '
                    'are. Apply this plan?'
                    if path_topic_request and (
                        'focus_topic' in plan or 'custom_topic' in plan
                    )
                    else 'This change applies to your next learning path. Apply this plan?'
                )
            )
        ),
        'actions': [
            {
                'type': 'apply_learning_plan',
                'proposal': token,
                'label': (
                    'Apply now and start assessment'
                    if starts_assessment
                    else 'Apply to my learning path'
                    if path_topic_request or open_learning_path
                    else 'Apply to my next session'
                ),
                **(
                    {'after_apply': 'assessment'}
                    if starts_assessment
                    else {'after_apply': 'learning_path'}
                    if path_topic_request or open_learning_path
                    else {}
                ),
            },
            {'type': 'dismiss_learning_plan', 'label': 'Not now'},
        ],
    }


def _coach_site_action_preview(learner, message, operation, values, label):
    token = signing.dumps(
        {
            'user_id': learner.pk,
            'operation': operation,
            'values': values,
        },
        salt='learning.coach-site-action',
    )
    return {
        'mode': 'learning',
        'speaker': 'brain',
        'source': 'Site action preview',
        'message': message,
        'actions': [
            {
                'type': 'apply_site_action',
                'confirmation': token,
                'label': label,
            },
            {
                'type': 'dismiss_site_action',
                'label': 'Cancel',
            },
        ],
    }


def _coach_site_action_request(learner, message):
    normalized = message.casefold()
    if re.search(r'\b(?:export|download)\b.{0,30}\b(?:my )?(?:data|history)\b', normalized):
        return {
            'mode': 'learning',
            'speaker': 'brain',
            'source': 'Data export',
            'message': 'I can download a copy of your profile, goals, and learning history for you.',
            'actions': [{
                'type': 'download_data',
                'label': 'Download my data',
            }],
        }

    if re.search(
        r'\b(?:delete|erase|clear|remove)\b.{0,40}\b'
        r'(?:my )?(?:learning )?(?:data|history|profile|progress)\b',
        normalized,
    ):
        return _coach_site_action_preview(
            learner,
            'This will delete your learning profile, assessment and learning-path history, and local history memory. Your sign-in account remains. Confirm only if you want to permanently remove that learning data.',
            'delete_learning_data',
            {},
            'Delete my learning data',
        )

    preference_names = {
        'ai': r'ai coaching',
        'memory': r'(?:personal history )?memory',
        'voice': r'voice(?: feedback)?',
        'growth': r'(?:(?:share|sharing)(?: progress)? )?history(?: with ai coach)?',
        'mood': r'mood(?: check-in)?',
    }
    preference_changes = {}
    language_match = re.search(
        r'\b(?:set|change|update)\s+(?:my\s+)?(?:interface\s+)?language\s+'
        r'(?:to\s+)?(english|french|arabic)\b',
        normalized,
    )
    requested_language = (
        {'english': 'en', 'french': 'fr', 'arabic': 'ar'}[
            language_match.group(1)
        ]
        if language_match else learner.preferences.get('language', 'en')
    )
    for key, name in preference_names.items():
        patterns = (
            (rf'\b(?:enable|activate|turn on|switch on)\s+(?:the\s+)?{name}\b', True),
            (rf'\b(?:disable|deactivate|turn off|switch off)\s+(?:the\s+)?{name}\b', False),
            (rf'\b{ name }\s+(on|off)\b', None),
        )
        for pattern, enabled in patterns:
            match = re.search(pattern, normalized)
            if match:
                preference_changes[key] = (
                    match.group(1) == 'on'
                    if enabled is None else enabled
                )
                break
    if preference_changes or language_match:
        preferences = dict(learner.preferences)
        allowed_preferences = ('ai', 'voice', 'growth', 'mood', 'memory')
        for key in allowed_preferences:
            preferences[key] = preference_changes.get(
                key,
                preferences.get(key) is True,
            )
        preferences['language'] = requested_language
        if preference_changes.get('ai') is True and not ai_provider_configured():
            return {
                'mode': 'learning',
                'speaker': 'brain',
                'source': 'AI coaching unavailable',
                'message': 'AI coaching cannot be enabled because no AI provider is configured. You can still use the local coaching features.',
                'actions': [],
            }
        changed = ', '.join(
            f'{key} {"on" if value else "off"}'
            for key, value in preference_changes.items()
        )
        if language_match:
            changed = ', '.join(filter(None, (
                changed,
                f'interface language {language_match.group(1).title()}',
            )))
        extra = (
            ' Turning memory off removes its local history index; your account history stays.'
            if preference_changes.get('memory') is False
            else ''
        )
        return _coach_site_action_preview(
            learner,
            f'Confirm these settings: {changed}.{extra}',
            'update_preferences',
            {'preferences': preferences},
            'Save these settings',
        )

    if re.search(r'\b(?:set|change|update)\b.{0,40}\b(?:goal|target|practice days|sessions per week)\b', normalized):
        score_match = re.search(
            r'\b(?:score|target)(?:\s+score)?\s*(?:to|of|=)?\s*(\d{1,3})\b',
            normalized,
        )
        sessions_match = re.search(
            r'\b(\d{1,2})\s+(?:practice\s+)?(?:sessions|days)\s+'
            r'(?:per|a)\s+week\b',
            normalized,
        )
        date_match = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', normalized)
        if not score_match and not sessions_match and not date_match:
            return {
                'mode': 'learning',
                'speaker': 'brain',
                'source': 'Goal details needed',
                'message': 'Tell me the score target (50–100), the number of sessions per week (1–7), or a target date. I have not changed anything.',
                'actions': [],
            }
        existing_goal = learner.goal if isinstance(learner.goal, dict) else {}
        target_score = (
            int(score_match.group(1))
            if score_match else existing_goal.get('target_score', 80)
        )
        weekly_sessions = (
            int(sessions_match.group(1))
            if sessions_match else existing_goal.get('weekly_sessions', 3)
        )
        if type(weekly_sessions) is not int or not 1 <= weekly_sessions <= 7:
            return {
                'mode': 'learning',
                'speaker': 'brain',
                'source': 'Goal needs a valid schedule',
                'message': 'Choose 1 to 7 practice sessions per week. I have not changed anything.',
                'actions': [],
            }
        if not 50 <= target_score <= 100:
            return {
                'mode': 'learning',
                'speaker': 'brain',
                'source': 'Goal needs a valid target',
                'message': 'Choose a practice score goal from 50 to 100. I have not changed anything.',
                'actions': [],
            }
        if date_match:
            try:
                if date.fromisoformat(date_match.group(1)) < timezone.localdate():
                    raise ValueError
            except ValueError:
                return {
                    'mode': 'learning',
                    'speaker': 'brain',
                    'source': 'Goal needs a valid date',
                    'message': 'Choose today or a future date in YYYY-MM-DD format. I have not changed anything.',
                    'actions': [],
                }
        goal = {
            'target_score': target_score,
            'weekly_sessions': weekly_sessions,
            'target_date': (
                date_match.group(1)
                if date_match else existing_goal.get('target_date', '')
            ),
        }
        return _coach_site_action_preview(
            learner,
            f'Confirm your practice goal: {goal["target_score"]}/100 and {goal["weekly_sessions"]} sessions per week'
            + (f', by {goal["target_date"]}.' if goal['target_date'] else '.'),
            'update_goal',
            {'goal': goal},
            'Save my goal',
        )

    profile = learner.profile if isinstance(learner.profile, dict) else {}
    role_match = re.search(
        r'\b(?:set|change|update)\s+(?:my\s+)?(?:profile\s+)?'
        r'(?:role|occupation)\s+(?:to\s+)?'
        r'(student|professional|exploring a new field)\b',
        normalized,
    )
    if role_match and profile:
        occupation = {
            'student': 'Student',
            'professional': 'Professional',
            'exploring a new field': 'Exploring a new field',
        }[role_match.group(1)]
        updated_profile = {**profile, 'occupation': occupation}
        return _coach_site_action_preview(
            learner,
            f'Confirm changing your profile role to {occupation}.',
            'update_profile',
            {'profile': updated_profile},
            'Save profile',
        )

    motivation_match = re.search(
        r'\b(?:set|change|update)\s+my\s+profile goal\s+'
        r'(?:to\s+)?(.+?)\s*[.!?]?$',
        message.strip(),
        re.IGNORECASE,
    )
    if motivation_match and profile:
        motivation = motivation_match.group(1).strip(" \t\r\n.,!?")[:500]
        if motivation:
            updated_profile = {**profile, 'motivation': motivation}
            return _coach_site_action_preview(
                learner,
                'Confirm changing the goal in your learning profile.',
                'update_profile',
                {'profile': updated_profile},
                'Save profile',
            )

    name_match = re.search(
        r'\b(?:call me|change my name to|set my name to)\s+(.+?)\s*[.!?]?$',
        message.strip(),
        re.IGNORECASE,
    )
    if name_match and profile:
        new_name = name_match.group(1).strip(" \t\r\n.,!?")[:80]
        if new_name:
            updated_profile = {**profile, 'name': new_name}
            return _coach_site_action_preview(
                learner,
                f'Confirm changing your profile name to {new_name}.',
                'update_profile',
                {'profile': updated_profile},
                'Save profile',
            )

    minutes_match = re.search(
        r'\b(?:set|change|update)\s+(?:my\s+)?(?:daily\s+)?study time\s+'
        r'(?:to\s+)?(15|30|60)\s+minutes?\b',
        normalized,
    )
    if minutes_match and profile:
        updated_profile = {**profile, 'minutes': minutes_match.group(1)}
        return _coach_site_action_preview(
            learner,
            f'Confirm setting your daily study time to {minutes_match.group(1)} minutes.',
            'update_profile',
            {'profile': updated_profile},
            'Save profile',
        )

    level_match = re.search(
        r'\b(?:set|change|update)\s+(?:my\s+)?experience(?: level)?\s+'
        r'(?:to\s+)?(new to this|some foundations|comfortable|advanced)\b',
        normalized,
    )
    if level_match and profile:
        experience = {
            'new to this': 'New to this',
            'some foundations': 'Some foundations',
            'comfortable': 'Comfortable',
            'advanced': 'Advanced',
        }[level_match.group(1)]
        updated_profile = {**profile, 'experience': experience}
        return _coach_site_action_preview(
            learner,
            f'Confirm setting your starting experience to {updated_profile["experience"]}.',
            'update_profile',
            {'profile': updated_profile},
            'Save profile',
        )

    active = learner.assessment_set.filter(
        completed_at__isnull=True,
        abandoned_at__isnull=True,
    ).first()
    if active and re.search(r'\b(?:pause|take a break from)\b.{0,30}\b(?:assessment|quiz|test)\b', normalized):
        return _coach_site_action_preview(
            learner,
            'Your current question and saved answers will be kept. Confirm to pause here; you can resume this assessment later.',
            'pause_assessment',
            {'assessment_id': active.pk},
            'Pause assessment',
        )
    if active and re.search(r'\b(?:resume|continue)\b.{0,30}\b(?:assessment|quiz|test)\b', normalized):
        if not (active.engagement or {}).get('coach_user_paused'):
            return {
                'mode': 'assessment',
                'speaker': 'brain',
                'source': 'Assessment is not paused',
                'message': 'Your assessment is already open. I’ll leave your current question untouched.',
                'actions': [{
                    'type': 'navigate_page',
                    'destination': 'assessment',
                }],
            }
        return _coach_site_action_preview(
            learner,
            'Confirm resuming the assessment. The current question will be ready without counting your break as response time.',
            'resume_assessment',
            {'assessment_id': active.pk},
            'Resume assessment',
        )
    return None


def _coach_confirmation_action(learner, message, conversation):
    normalized = message.casefold().strip(" \t\r\n.,!?")
    if not re.fullmatch(
        r'(?:yes|yes apply(?: it| now)?|apply(?: it| now)?|confirm|'
        r'confirm it|do it|go ahead|please do|that is correct|sounds good)',
        normalized,
    ):
        return None
    if (
        not conversation
        or conversation[-1].get('role') != 'assistant'
        or not re.search(
            r'(?:apply this plan\?|apply now\?|confirm (?:these settings|your practice goal|'
            r'changing|setting|resuming)|confirm to pause|confirm only if)',
            conversation[-1].get('content', ''),
            re.IGNORECASE,
        )
    ):
        return None
    prior_request = next(
        (
            turn['content']
            for turn in reversed(conversation[:-1])
            if turn.get('role') == 'user'
        ),
        None,
    )
    if not prior_request:
        return None
    response = _coach_site_action_request(learner, prior_request)
    if response is None:
        response = _coach_learning_plan_request(
            learner,
            prior_request,
        )
    if not response or not response.get('actions'):
        return None
    action = response['actions'][0]
    if action.get('type') not in ('apply_site_action', 'apply_learning_plan'):
        return None
    response['confirm_now'] = True
    return response


def _coach_navigation_request(
    message,
    has_active_assessment=False,
    has_completed_assessment=False,
):
    normalized = message.casefold()
    wants_learning_path = bool(re.search(
        r'\b(?:open|start|begin|launch|go to|take me to|navigate to|show me|'
        r'bring me to|visit)\b.{0,50}\b(?:learning path|path)\b',
        normalized,
    ))
    if wants_learning_path and has_active_assessment:
        return {
            'mode': 'assessment',
            'speaker': 'brain',
            'source': 'Assessment in progress',
            'message': (
                'Your assessment is still in progress, so I can’t start the '
                'learning path yet. Finish this assessment first; your answers '
                'and progress are saved.'
            ),
            'actions': [],
        }
    if wants_learning_path and not has_completed_assessment:
        return {
            'mode': 'learning',
            'speaker': 'brain',
            'source': 'Assessment needed',
            'message': (
                'Complete an assessment first, and I can open the learning path '
                'created from it.'
            ),
            'actions': [{
                'type': 'navigate_page',
                'destination': 'assessment',
                'start_assessment': True,
            }],
        }
    routes = (
        (
            'assessment',
            r'\b(?:open|pen|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:(?:the|a|my)\s+)?(?:(?:new|another|fresh)\s+)?'
            r'(?:assessment|assesment|quiz|test)\b'
            r'|\b(?:start|begin|launch)\s+(?:(?:the|a|my)\s+)?(?:new\s+)?'
            r'(?:assessment|assesment|quiz|test)\b',
            '/assessment/',
            'your assessment',
        ),
        (
            'learning_path',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:the\s+)?(?:learning path|path)\b'
            r'|\b(?:start|begin|launch)\s+(?:my\s+|the\s+)?'
            r'(?:learning path|path)\b',
            '/learning-path/',
            'your learning path',
        ),
        (
            'profile',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:the\s+)?profile\b',
            '/profile/',
            'your profile',
        ),
        (
            'account_profile',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:my\s+)?account profile\b'
            r'|\b(?:change|update|edit)\s+(?:my\s+)?(?:account|email|password)\b',
            '/accounts/profile/',
            'your account profile',
        ),
        (
            'settings',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:the\s+)?settings\b',
            '/settings/',
            'Settings',
        ),
        (
            'brain_twin',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:the\s+)?brain twin\b',
            '/brain-twin/',
            'your Brain Twin',
        ),
        (
            'coach',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:the\s+)?(?:ai )?coach\b',
            '/ai-coach/',
            'the AI Coach',
        ),
        (
            'overview',
            r'\b(?:open|go to|take me to|navigate to|show me|bring me to|visit)'
            r'\s+(?:the\s+)?(?:dashboard|overview|home)\b',
            '/dashboard/',
            'your dashboard',
        ),
    )
    for destination, pattern, path, label in routes:
        if re.search(pattern, normalized):
            return {
                'mode': 'learning',
                'speaker': 'brain',
                'source': 'Website navigation',
                'message': f'Opening {label}.',
                'actions': [{
                    'type': 'navigate_page',
                    'destination': destination,
                    **(
                        {'start_assessment': True}
                        if destination == 'assessment' and re.search(
                            r'\b(?:open|pen|start|begin|launch|take)\b.{0,80}\b'
                            r'(?:assessment|assesment|quiz|test)\b',
                            normalized,
                        )
                        else {}
                    ),
                }],
            }
    return None


@login_required
@require_POST
def coach_site_action(request):
    data = payload(request)
    token = data.get('confirmation')
    if not isinstance(token, str) or len(token) > 4000:
        return JsonResponse({'error': 'The site-action confirmation is invalid.'}, status=400)
    try:
        proposal = signing.loads(
            token,
            salt='learning.coach-site-action',
            max_age=1800,
        )
    except signing.BadSignature:
        return JsonResponse(
            {'error': 'This site-action preview expired. Ask the coach again.'},
            status=400,
        )
    if not isinstance(proposal, dict) or set(proposal) != {
        'user_id', 'operation', 'values',
    }:
        return JsonResponse({'error': 'The site-action preview is invalid.'}, status=400)
    l = Learner.objects.filter(user=request.user).first()
    if not l:
        return JsonResponse({'error': 'The learning profile no longer exists.'}, status=409)
    if proposal.get('user_id') != l.pk:
        return JsonResponse({'error': 'This action is not valid for this account.'}, status=403)
    operation = proposal.get('operation')
    values = proposal.get('values')
    if not isinstance(values, dict):
        return JsonResponse({'error': 'The site-action preview is invalid.'}, status=400)

    if operation == 'update_preferences':
        if set(values) != {'preferences'} or not isinstance(values['preferences'], dict):
            return JsonResponse({'error': 'The settings preview is invalid.'}, status=400)
        requested = values['preferences']
        allowed = {'ai', 'voice', 'growth', 'mood', 'memory', 'language'}
        if (
            set(requested) != allowed
            or any(type(requested[key]) is not bool for key in allowed - {'language'})
            or requested['language'] not in ('en', 'fr', 'ar')
        ):
            return JsonResponse({'error': 'The settings preview contains an invalid choice.'}, status=400)
        if requested['ai'] and not ai_provider_configured():
            return JsonResponse({'error': 'AI coaching is unavailable because no provider is configured.'}, status=409)
        memory_enabled = requested['memory']
        if memory_enabled and not l.preferences.get('memory'):
            try:
                index_history(l, lambda item: serialize(item, requested['language']))
            except MemoryStoreUnavailable as exc:
                return JsonResponse({'error': str(exc)}, status=503)
        if l.preferences.get('memory') and not memory_enabled:
            try:
                forget_history(l)
            except MemoryStoreUnavailable as exc:
                return JsonResponse({'error': str(exc)}, status=503)
        collaboration_reflection = l.preferences.get('collaboration_reflection')
        l.preferences = {
            key: requested[key]
            for key in ('ai', 'voice', 'growth', 'mood', 'memory')
        }
        l.preferences['language'] = requested['language']
        if collaboration_reflection is not None:
            l.preferences['collaboration_reflection'] = collaboration_reflection
        l.save(update_fields=['preferences'])
        return JsonResponse({
            'message': 'Your settings are saved.',
            'preferences': l.preferences,
        })

    if operation == 'update_goal':
        if set(values) != {'goal'} or not isinstance(values['goal'], dict):
            return JsonResponse({'error': 'The goal preview is invalid.'}, status=400)
        goal = values['goal']
        target_score = goal.get('target_score')
        weekly_sessions = goal.get('weekly_sessions')
        target_date = goal.get('target_date', '')
        if (
            set(goal) != {'target_score', 'weekly_sessions', 'target_date'}
            or type(target_score) is not int or not 50 <= target_score <= 100
            or type(weekly_sessions) is not int or not 1 <= weekly_sessions <= 7
            or not isinstance(target_date, str) or len(target_date) not in (0, 10)
        ):
            return JsonResponse({'error': 'The goal preview contains an invalid choice.'}, status=400)
        if target_date:
            try:
                parsed_date = date.fromisoformat(target_date)
            except ValueError:
                return JsonResponse({'error': 'The goal date is invalid.'}, status=400)
            if parsed_date < timezone.localdate():
                return JsonResponse({'error': 'Choose today or a future goal date.'}, status=400)
        l.goal = goal
        l.save(update_fields=['goal'])
        if l.preferences.get('memory'):
            try:
                index_learner_context(l)
            except MemoryStoreUnavailable as exc:
                return JsonResponse({'error': str(exc)}, status=503)
        return JsonResponse({'message': 'Your practice goal is saved.', 'goal': l.goal})

    if operation == 'update_profile':
        if set(values) != {'profile'} or not isinstance(values['profile'], dict):
            return JsonResponse({'error': 'The profile preview is invalid.'}, status=400)
        profile = values['profile']
        limits = {
            'name': 80, 'subject': 20, 'occupation': 60,
            'motivation': 500, 'experience': 30, 'minutes': 10,
        }
        if set(profile) != set(limits):
            return JsonResponse({'error': 'The profile preview contains unsupported fields.'}, status=400)
        cleaned = {
            key: str(profile.get(key, '')).strip()[:limit]
            for key, limit in limits.items()
        }
        if (
            not all(cleaned.values())
            or cleaned['subject'] not in BANK
            or cleaned['occupation'] not in (
                'Student', 'Professional', 'Exploring a new field',
            )
            or cleaned['experience'] not in (
                'New to this', 'Some foundations', 'Comfortable', 'Advanced',
            )
            or cleaned['minutes'] not in ('15', '30', '60')
        ):
            return JsonResponse({'error': 'The profile preview contains an invalid choice.'}, status=400)
        l.profile = cleaned
        l.save(update_fields=['profile'])
        if l.preferences.get('memory'):
            try:
                index_learner_context(l)
            except MemoryStoreUnavailable as exc:
                return JsonResponse({'error': str(exc)}, status=503)
        return JsonResponse({'message': 'Your learning profile is saved.', 'profile': l.profile})

    if operation == 'delete_learning_data':
        if values:
            return JsonResponse({'error': 'The deletion preview is invalid.'}, status=400)
        if l.preferences.get('memory'):
            try:
                forget_history(l)
            except MemoryStoreUnavailable as exc:
                return JsonResponse({'error': str(exc)}, status=503)
        l.delete()
        return JsonResponse({'message': 'Your learning data was deleted. Your sign-in account remains.'})

    if operation in ('pause_assessment', 'resume_assessment'):
        if set(values) != {'assessment_id'} or type(values['assessment_id']) is not int:
            return JsonResponse({'error': 'The assessment action is invalid.'}, status=400)
        with transaction.atomic():
            assessment = l.assessment_set.select_for_update().filter(
                pk=values['assessment_id'],
                completed_at__isnull=True,
                abandoned_at__isnull=True,
            ).first()
            if not assessment:
                return JsonResponse({'error': 'That assessment is no longer active.'}, status=409)
            engagement = assessment.engagement or {}
            if operation == 'pause_assessment':
                if engagement.get('paused') and not engagement.get('coach_user_paused'):
                    return JsonResponse({'error': 'Pinky has paused this assessment. Please use the check-in to continue.'}, status=409)
                engagement['paused'] = True
                engagement['coach_user_paused'] = True
                assessment.engagement = engagement
                assessment.save(update_fields=['engagement'])
                return JsonResponse({
                    'message': 'Your assessment is paused. Your current question and saved answers are safe.',
                    'destination': 'dashboard',
                })
            if not engagement.get('coach_user_paused') or not engagement.get('paused'):
                return JsonResponse({'error': 'This assessment is not paused by you.'}, status=409)
            engagement['paused'] = False
            engagement.pop('coach_user_paused', None)
            assessment.engagement = engagement
            assessment.question_started_at = timezone.now()
            assessment.save(update_fields=['engagement', 'question_started_at'])
            return JsonResponse({
                'message': 'Your assessment is ready to continue.',
                'destination': 'assessment',
            })

    return JsonResponse({'error': 'This site action is not supported.'}, status=400)


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
    latest = l.assessment_set.filter(
        abandoned_at__isnull=True,
    ).order_by('-id').first()
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
    completed = l.assessment_set.filter(
        completed_at__isnull=False,
        abandoned_at__isnull=True,
    ).order_by('-completed_at', '-id').first()
    language = response_language or l.preferences.get('language', 'en')
    if completed:
        engagement = completed.engagement or {}
        pending_pushback = engagement.get('pending_pushback')
        if isinstance(pending_pushback, dict):
            reported_indexes = _pushback_item_indexes(
                message,
                len(completed.answers),
            )
            engagement.pop('pending_pushback', None)
            completed.engagement = engagement
            completed.save(update_fields=['engagement'])
            if not reported_indexes:
                return JsonResponse(
                    _pushback_clarification_response(language, closed=True),
                )
            combined_message = (
                pending_pushback.get('message', '') + ' ' + message.strip()
            ).strip()
            with transaction.atomic():
                assessment_for_update = (
                    l.assessment_set.select_for_update().get(pk=completed.pk)
                )
                dispute = _record_assessment_pushback(
                    assessment_for_update,
                    combined_message,
                )
            return JsonResponse(_assessment_pushback_response(
                assessment_for_update,
                dispute,
                language,
            ))
    open_dispute = _latest_open_dispute(l)
    if open_dispute and _is_verification_request(message):
        source, _ = open_dispute
        try:
            verification = _create_verification_retest(l, source.pk)
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=409)
        return JsonResponse({
            'mode': 'debrief',
            'speaker': 'pinky',
            'source': 'Assessment review',
            'message': 'A fresh three-question verification is ready. Open it whenever you are ready.',
            'actions': [{
                'type': 'open_assessment',
                'assessment_id': verification.pk,
                'label': 'Open verification check',
            }],
            'verification': {
                'assessment_id': verification.pk,
                'status': 'ready',
            },
        })
    if completed and _is_assessment_pushback(message):
        if _pushback_item_indexes(message, len(completed.answers)) is None:
            with transaction.atomic():
                assessment_for_update = (
                    l.assessment_set.select_for_update().get(pk=completed.pk)
                )
                engagement = assessment_for_update.engagement or {}
                engagement['pending_pushback'] = {
                    'message': message.strip(),
                    'created_at': timezone.now().isoformat(),
                }
                assessment_for_update.engagement = engagement
                assessment_for_update.save(update_fields=['engagement'])
            return JsonResponse(_pushback_clarification_response(language))
        with transaction.atomic():
            assessment_for_update = l.assessment_set.select_for_update().get(
                pk=completed.pk,
            )
            dispute = _record_assessment_pushback(
                assessment_for_update,
                message.strip(),
            )
        return JsonResponse(_assessment_pushback_response(
            assessment_for_update,
            dispute,
            language,
        ))
    active_assessment = l.assessment_set.filter(
        completed_at__isnull=True,
        abandoned_at__isnull=True,
    ).exists()
    confirmation = _coach_confirmation_action(
        l,
        message.strip(),
        conversation,
    )
    if confirmation:
        return JsonResponse(confirmation)
    site_action = _coach_site_action_request(l, message.strip())
    if site_action:
        return JsonResponse(site_action)
    learning_plan_proposal = _coach_learning_plan_request(
        l,
        message.strip(),
        conversation=conversation,
    )
    if learning_plan_proposal:
        return JsonResponse(learning_plan_proposal)
    navigation = _coach_navigation_request(
        message.strip(),
        has_active_assessment=active_assessment,
        has_completed_assessment=completed is not None,
    )
    if navigation:
        return JsonResponse(navigation)
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


@login_required
@require_POST
def engagement(request):
    data = payload(request)
    assessment_id = data.get('assessment_id')
    user_reply = data.get('user_reply')
    if (
        set(data) != {'assessment_id', 'user_reply'}
        or type(assessment_id) is not int
        or user_reply is not None
        and (not isinstance(user_reply, str) or len(user_reply) > 500)
    ):
        return JsonResponse({'error': 'The engagement reply is invalid.'}, status=400)
    l = learner(request)
    active = l.assessment_set.filter(
        pk=assessment_id,
        completed_at__isnull=True,
        abandoned_at__isnull=True,
    ).first()
    if not active:
        return JsonResponse(
            {'error': 'No active assessment is available for this event.'},
            status=409,
        )
    engagement = active.engagement or {}
    event = engagement.get('current_event')
    if not isinstance(event, dict):
        return JsonResponse(
            {'error': 'There is no active Pinky check-in to answer.'},
            status=409,
        )
    event['user_reply'] = user_reply
    if user_reply and user_reply.casefold() == 'resume':
        engagement['paused'] = False
        engagement.pop('current_event', None)
        engagement.pop('current_response', None)
        active.engagement = engagement
        active.save(update_fields=['engagement'])
        return JsonResponse({
            'action': 'NONE',
            'message': 'Your place is ready whenever you are.',
            'ui': {'pause_session': False, 'buttons': []},
            'report_to_layer_a': {
                'reason_self_reported': 'none',
                'requeue_flagged_items': False,
                'flag_items_for_wording_review': False,
                'intervention_logged': True,
            },
        })
    if user_reply and user_reply.casefold() in {
        'end session without penalty',
        'terminer la séance sans pénalité',
        'إنهاء الجلسة دون عقوبة',
        'نوقف الجلسة من غير عقوبة',
    }:
        active.abandoned_at = timezone.now()
        engagement['ended_without_penalty'] = True
        engagement['current_event'] = event
        active.engagement = engagement
        active.save(update_fields=['abandoned_at', 'engagement'])
        return JsonResponse({
            'action': 'END_OFFER',
            'message': 'This session is ended. Nothing is penalized.',
            'ui': {'pause_session': True, 'buttons': []},
            'report_to_layer_a': {
                'reason_self_reported': 'none',
                'requeue_flagged_items': False,
                'flag_items_for_wording_review': False,
                'intervention_logged': True,
            },
        })
    try:
        result = pinky_engagement_response(event)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    engagement['current_event'] = event
    engagement['current_response'] = result
    reported_reason = result['report_to_layer_a']['reason_self_reported']
    if reported_reason != 'none':
        engagement['last_reason_self_reported'] = reported_reason
        if engagement.get('audit'):
            engagement['audit'][-1]['reason_self_reported'] = reported_reason
    if result['ui']['pause_session']:
        engagement['paused'] = True
    else:
        engagement['paused'] = False
        if result['action'] != 'OFFER_OPTIONS':
            engagement.pop('current_event', None)
            engagement.pop('current_response', None)
    if result['report_to_layer_a']['requeue_flagged_items']:
        engagement['requeued_by_user'] = event['affected_items']
    if result['action'] == 'RAISE_DIFFICULTY':
        l.preferences['assessment_difficulty_boost'] = True
        l.save(update_fields=['preferences'])
    active.engagement = engagement
    active.save(update_fields=['engagement'])
    return JsonResponse(result)
