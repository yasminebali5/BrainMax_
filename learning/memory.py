"""Account-scoped local retrieval over completed learning sessions."""
import hashlib
import re
from functools import lru_cache

from django.conf import settings

from .questions import SUBJECTS


class MemoryStoreUnavailable(Exception):
    """Raised when the optional local Chroma store cannot be loaded."""


@lru_cache(maxsize=4)
def _collection_for(path):
    try:
        import chromadb
    except ImportError as exc:
        raise MemoryStoreUnavailable(
            "Local learning memory is unavailable. Install the project requirements."
        ) from exc

    client = chromadb.PersistentClient(path=path)
    return client.get_or_create_collection(
        name="learner_history",
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,
    )


def _embedding(text):
    tokens = re.findall(r"[a-z0-9]+", text.casefold())
    features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
    vector = [0.0] * 384
    for feature in features or ["empty"]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % len(vector)
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    magnitude = sum(value * value for value in vector) ** 0.5
    return [value / magnitude for value in vector]


def _document(assessment, serialize):
    summary = serialize(assessment)
    topics = ", ".join(f"{topic}: {score}%" for topic, score in summary["topics"].items())
    return (
        f"Completed {summary['date']} assessment in {summary['subject_name']}. "
        f"Independent score: {summary['score']}/100. Accuracy: {summary['accuracy']}%. "
        f"Section results: {topics}."
    )


def _path_document(run, final_result):
    activities = "; ".join(
        f"{step['id']} on {step.get('practice_question', {}).get('topic', 'a learning topic')}: "
        f"{step['score']}%, {step['attempts']} attempt(s), "
        f"scores {', '.join(map(str, step.get('scores', [])))}, "
        f"times {', '.join(str(value) if type(value) is int else 'not recorded' for value in step.get('seconds', []))} seconds, "
        f"coaching: {' '.join(item.get('text', '') for item in step.get('feedback', []))}"
        for step in run.steps
    )
    total_time = (
        f"{final_result['total_seconds']} seconds"
        if final_result['total_seconds'] is not None
        else "not fully recorded"
    )
    return (
        f"Completed learning path in {SUBJECTS.get(run.assessment.subject, run.assessment.subject)}. "
        f"Final average score: {final_result['score']}/100 across "
        f"{len(run.steps)} challenges. "
        f"Total time across attempts: {total_time}. "
        f"Total attempts: {final_result['attempts']}. Activities: {activities}."
    )


def _learner_context_document(learner):
    profile = learner.profile if isinstance(learner.profile, dict) else {}
    goal = learner.goal if isinstance(learner.goal, dict) else {}
    fields = []
    name = profile.get("name") or learner.user.get_full_name()
    if name:
        fields.append(f"Name: {name}.")
    subject = SUBJECTS.get(profile.get("subject"), profile.get("subject"))
    if subject:
        fields.append(f"Learning subject: {subject}.")
    for key, label in (
        ("occupation", "Role"),
        ("motivation", "Learning motivation"),
        ("experience", "Experience level"),
        ("minutes", "Preferred minutes per session"),
    ):
        value = profile.get(key)
        if value:
            fields.append(f"{label}: {value}.")
    if goal:
        fields.append(
            "Learning goals: "
            f"target score {goal.get('target_score', 'not set')}/100, "
            f"{goal.get('weekly_sessions', 'not set')} sessions per week, "
            f"target date {goal.get('target_date') or 'not set'}."
        )
    if not fields:
        return ""
    return "Learner profile and goals. " + " ".join(fields)


def _upsert_documents(documents, metadatas, ids):
    collection = _collection_for(str(settings.CHROMA_DB_PATH))
    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=[_embedding(document) for document in documents],
    )


def index_learner_context(learner):
    """Keep opted-in profile and goal context current in the local vector store."""
    document = _learner_context_document(learner)
    if not document:
        return
    _upsert_documents(
        [document],
        [{
            "learner_id": str(learner.pk),
            "record_type": "learner_context",
        }],
        [f"learner-{learner.pk}-profile"],
    )


def index_history(learner, serialize):
    """Upsert completed learning summaries when local memory is enabled."""
    assessments = list(
        learner.assessment_set.filter(completed_at__isnull=False).order_by("completed_at")
    )
    documents = [_document(item, serialize) for item in assessments]
    metadatas = [
        {
            "learner_id": str(learner.pk),
            "assessment_id": str(item.pk),
            "record_type": "assessment",
            "completed_at": item.completed_at.isoformat(),
        }
        for item in assessments
    ]
    ids = [f"learner-{learner.pk}-assessment-{item.pk}" for item in assessments]

    profile_document = _learner_context_document(learner)
    if profile_document:
        documents.append(profile_document)
        metadatas.append({
            "learner_id": str(learner.pk),
            "record_type": "learner_context",
        })
        ids.append(f"learner-{learner.pk}-profile")

    from .models import LearningPathRun

    for run in LearningPathRun.objects.filter(
        learner=learner,
    ).select_related('assessment').order_by('assessment__completed_at'):
        if not run.steps or not all(step.get('passed') for step in run.steps):
            continue
        scores = [step['score'] for step in run.steps]
        attempt_times = [
            seconds
            for step in run.steps
            for seconds in step.get('seconds', [])
        ]
        final_result = {
            'score': round(sum(scores) / len(scores)),
            'total_seconds': (
                sum(attempt_times)
                if all(type(seconds) is int for seconds in attempt_times)
                else None
            ),
            'attempts': sum(step.get('attempts', 0) for step in run.steps),
        }
        documents.append(_path_document(run, final_result))
        metadatas.append({
            "learner_id": str(learner.pk),
            "assessment_id": str(run.assessment_id),
            "record_type": "learning_path",
            "completed_at": run.assessment.completed_at.isoformat(),
        })
        ids.append(f"learner-{learner.pk}-learning-path-{run.assessment_id}")

    if documents:
        _upsert_documents(documents, metadatas, ids)


def index_learning_path(learner, run, final_result):
    """Upsert a completed learning path when local memory is enabled."""
    document = _path_document(run, final_result)
    _upsert_documents(
        [document],
        [{
            "learner_id": str(learner.pk),
            "assessment_id": str(run.assessment_id),
            "record_type": "learning_path",
            "completed_at": run.assessment.completed_at.isoformat(),
        }],
        [f"learner-{learner.pk}-learning-path-{run.assessment_id}"],
    )


def retrieve_history(learner, query, limit=4):
    collection = _collection_for(str(settings.CHROMA_DB_PATH))
    if collection.count() == 0:
        return []
    results = collection.query(
        query_embeddings=[_embedding(query)],
        n_results=limit,
        where={"learner_id": str(learner.pk)},
    )
    return results["documents"][0] if results.get("documents") else []


def forget_history(learner):
    _collection_for(str(settings.CHROMA_DB_PATH)).delete(
        where={"learner_id": str(learner.pk)}
    )
